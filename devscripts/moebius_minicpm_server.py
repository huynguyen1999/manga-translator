#!/usr/bin/env python3
"""Single-file GPU inference server hosting:
1. hustvl/Moebius (0.22B diffusion inpainting model)
2. Ollama minicpm-v4.6 (https://ollama.com/library/minicpm-v4.6) for visual understanding

Key capabilities:
- Batch inference endpoints for both Moebius image inpainting and MiniCPM-V 4.6 visual understanding.
- Dynamic micro-batching queue for concurrent single-image inpainting requests so bursty single
  inference calls are coalesced into batched GPU forward passes.
- Strict GPU work controller & VRAM threshold limiter: all inference runs on the GPU (never silently
  falling back to slow CPU under load), while bounding concurrent GPU tasks, batch sizes, and VRAM
  utilization so the GPU is never overloaded.
- Built-in Cloudflared Quick Tunnel (trycloudflare.com) or token tunnel support so remote machines
  can connect immediately via a public HTTPS URL.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import io
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import numpy as np
    from PIL import Image
    from fastapi import FastAPI, HTTPException, Request as FastAPIRequest
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
    import uvicorn
except ImportError:
    if "--bootstrap" in sys.argv or os.environ.get("AUTO_BOOTSTRAP") == "1" or Path("/content").exists():
        print("[Bootstrap] Installing required Python packages on fresh runtime ...", flush=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-q",
                "fastapi",
                "uvicorn",
                "pydantic",
                "pillow",
                "numpy",
                "opencv-python-headless",
                "diffusers",
                "transformers",
                "accelerate",
                "huggingface_hub",
                "einops",
                "timm",
                "omegaconf",
                "pyyaml",
                "safetensors",
            ],
            check=True,
        )
        import numpy as np
        from PIL import Image
        from fastapi import FastAPI, HTTPException, Request as FastAPIRequest
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel, Field
        import uvicorn
    else:
        raise


# ==============================================================================
# Configuration & Secret / .env Loader
# ==============================================================================

KNOWN_SECRET_KEYS = (
    "TAILSCALE_AUTHKEY",
    "TAILSCALE_HOSTNAME",
    "TAILSCALE_FUNNEL",
    "ENABLE_TAILSCALE",
    "CLOUDFLARED_TOKEN",
    "CLOUDFLARED_PROTOCOL",
    "ENABLE_CLOUDFLARED",
    "OLLAMA_MODEL",
    "HF_TOKEN",
)


def load_dotenv_and_secrets(env_file: str | Path | None = None) -> list[str]:
    """Load environment variables from `.env` files, Google Colab Secrets (`google.colab.userdata`),
    or Kaggle Secrets (`kaggle_secrets.UserSecretsClient`) without requiring external packages."""
    loaded_sources: list[str] = []
    candidate_files: list[Path] = []
    if env_file:
        candidate_files.append(Path(env_file))
    candidate_files.extend(
        [
            Path("ephemeral-inpainter-vlm.env"),
            Path("/content/ephemeral-inpainter-vlm.env"),
            Path(".env"),
            Path("devscripts/.env"),
            Path("/content/.env"),
            Path("/content/drive/MyDrive/ephemeral-inpainter-vlm.env"),
            Path("/content/drive/MyDrive/.env"),
            Path("/content/drive/MyDrive/moebius.env"),
            Path("/kaggle/working/.env"),
        ]
    )
    for path in candidate_files:
        try:
            if path.is_file():
                for raw_line in path.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[len("export ") :].strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'").strip('"')
                        if k and k not in os.environ:
                            os.environ[k] = v
                loaded_sources.append(f"file:{path}")
        except OSError:
            pass

    # Try Google Colab Secrets (`google.colab.userdata`)
    try:
        from google.colab import userdata  # type: ignore

        for key in KNOWN_SECRET_KEYS:
            if not os.environ.get(key):
                with contextlib.suppress(Exception):
                    val = userdata.get(key)
                    if val:
                        os.environ[key] = str(val).strip()
                        loaded_sources.append(f"colab_secret:{key}")
    except ImportError:
        pass

    # Try Kaggle Secrets (`kaggle_secrets`)
    try:
        from kaggle_secrets import UserSecretsClient  # type: ignore

        client = UserSecretsClient()
        for key in KNOWN_SECRET_KEYS:
            if not os.environ.get(key):
                with contextlib.suppress(Exception):
                    val = client.get_secret(key)
                    if val:
                        os.environ[key] = str(val).strip()
                        loaded_sources.append(f"kaggle_secret:{key}")
    except ImportError:
        pass

    return loaded_sources


load_dotenv_and_secrets()

DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "minicpm-v4.6")
DEFAULT_OLLAMA_HOST = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")
DEFAULT_MOEBIUS_REPO = Path(os.environ.get("MOEBIUS_REPO", "./Moebius")).resolve()
DEFAULT_CLOUDFLARED_URL_FILE = Path(os.environ.get("CLOUDFLARED_URL_FILE", "./cloudflared_url.txt")).resolve()

MAX_GPU_ACTIVE = int(os.environ.get("MAX_GPU_ACTIVE", "2"))
MAX_INPAINT_BATCH = int(os.environ.get("MAX_INPAINT_BATCH", "4"))
BATCH_WINDOW_MS = int(os.environ.get("BATCH_WINDOW_MS", "50"))
MAX_VISION_CONCURRENCY = int(os.environ.get("MAX_VISION_CONCURRENCY", "2"))
MAX_VISION_BATCH = int(os.environ.get("MAX_VISION_BATCH", "4"))
GPU_VRAM_THRESHOLD = float(os.environ.get("GPU_VRAM_THRESHOLD", "0.85"))
MAX_QUEUE_SIZE = int(os.environ.get("MAX_QUEUE_SIZE", "64"))


# ==============================================================================
# Image & Network Utilities
# ==============================================================================

def download_or_read_bytes(source: str, timeout: int = 30) -> bytes:
    """Load bytes from a data URI, raw base64 string, HTTP(S) URL, or local file path."""
    source = source.strip()
    if source.startswith("data:"):
        _, b64_part = source.split(",", 1)
        return base64.b64decode(b64_part)
    if source.startswith(("http://", "https://")):
        req = Request(source, headers={"User-Agent": "Mozilla/5.0 (MoebiusMiniCPMServer/1.0)"})
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {err.code} fetching {source}: {detail}") from err
        except URLError as err:
            raise RuntimeError(f"Failed to reach {source}: {err.reason}") from err

    if len(source) < 1024 and "\n" not in source:
        try:
            path = Path(source)
            if path.is_file():
                return path.read_bytes()
        except OSError:
            pass

    try:
        return base64.b64decode(source)
    except Exception as err:
        raise ValueError(f"Invalid image source (not a valid URL, file path, or base64 payload): {err}") from err


def decode_pil_image(source: str, mode: str = "RGB") -> Image.Image:
    raw = download_or_read_bytes(source)
    return Image.open(io.BytesIO(raw)).convert(mode)


def encode_pil_to_base64(img: Image.Image, fmt: str = "PNG", include_data_uri: bool = True) -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    if include_data_uri:
        mime = "image/png" if fmt.upper() == "PNG" else "image/jpeg"
        return f"data:{mime};base64,{b64}"
    return b64


def normalize_to_raw_base64(source: str) -> str:
    """Convert any image source (URL, file, data URI, raw base64) to raw base64 for Ollama."""
    if source.startswith("data:"):
        return source.split(",", 1)[1].strip()
    if source.startswith(("http://", "https://")) or (len(source) < 1024 and Path(source).is_file()):
        img = decode_pil_image(source, mode="RGB")
        return encode_pil_to_base64(img, fmt="PNG", include_data_uri=False)
    return source.strip()


def pad_to_square(image: Image.Image, mask: Image.Image) -> tuple[Image.Image, Image.Image, tuple[int, int, int, int]]:
    """Pad non-square image and mask to a centered square as required by Moebius LλMI blocks."""
    w, h = image.size
    if mask.size != (w, h):
        mask = mask.resize((w, h), Image.Resampling.NEAREST)
    max_dim = max(w, h)
    pad_left = (max_dim - w) // 2
    pad_top = (max_dim - h) // 2

    square_img = Image.new("RGB", (max_dim, max_dim), (255, 255, 255))
    square_img.paste(image, (pad_left, pad_top))

    square_mask = Image.new("L", (max_dim, max_dim), 0)
    square_mask.paste(mask, (pad_left, pad_top))

    box = (pad_left, pad_top, pad_left + w, pad_top + h)
    return square_img, square_mask, box


# ==============================================================================
# GPU Device & Memory Threshold Controller
# ==============================================================================

def detect_accelerator_device(preferred: str | None = None) -> str:
    if preferred and preferred != "auto":
        return preferred
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def get_gpu_memory_stats() -> dict[str, Any]:
    """Query live GPU VRAM usage and return structured statistics."""
    try:
        import torch
        if torch.cuda.is_available():
            idx = torch.cuda.current_device()
            free_bytes, total_bytes = torch.cuda.mem_get_info(idx)
            used_bytes = total_bytes - free_bytes
            allocated_bytes = torch.cuda.memory_allocated(idx)
            reserved_bytes = torch.cuda.memory_reserved(idx)
            utilization = round(used_bytes / max(total_bytes, 1), 4)
            return {
                "device": "cuda",
                "device_name": torch.cuda.get_device_name(idx),
                "used_mb": round(used_bytes / (1024 * 1024), 1),
                "free_mb": round(free_bytes / (1024 * 1024), 1),
                "total_mb": round(total_bytes / (1024 * 1024), 1),
                "torch_allocated_mb": round(allocated_bytes / (1024 * 1024), 1),
                "torch_reserved_mb": round(reserved_bytes / (1024 * 1024), 1),
                "utilization_ratio": utilization,
            }
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            alloc = torch.mps.current_allocated_memory() if hasattr(torch, "mps") else 0
            return {
                "device": "mps",
                "device_name": "Apple Silicon MPS",
                "torch_allocated_mb": round(alloc / (1024 * 1024), 1),
                "utilization_ratio": 0.0,
            }
    except Exception as exc:
        return {"device": "unknown", "error": str(exc), "utilization_ratio": 0.0}
    return {"device": "cpu", "device_name": "CPU", "utilization_ratio": 0.0}


def release_gpu_cache() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
            torch.mps.empty_cache()
    except Exception:
        pass


# ==============================================================================
# Moebius Setup, Patching & Model Loader
# ==============================================================================

def patch_moebius_repo_for_inference(repo_dir: Path) -> None:
    """Apply safe inference-mode patches to upstream hustvl/Moebius so it does not
    require training-only dependencies (like flash-linear-attention) and resolves VAE cleanly."""
    model_lib_init = repo_dir / "model_lib" / "__init__.py"
    if model_lib_init.is_file():
        content = model_lib_init.read_text(encoding="utf-8")
        target = "from .nets.unet_gla import UNet2DGLAConditionModel"
        if target in content and "try:\n    from .nets.unet_gla" not in content:
            patched = content.replace(
                target,
                "try:\n    from .nets.unet_gla import UNet2DGLAConditionModel\nexcept Exception:\n    UNet2DGLAConditionModel = None",
            )
            model_lib_init.write_text(patched, encoding="utf-8")

    utils_train_py = repo_dir / "utils_train.py"
    if utils_train_py.is_file():
        content = utils_train_py.read_text(encoding="utf-8")
        old_fn = 'def build_vae(model_cfg: Dict) -> AutoencoderKL:\n    vae = AutoencoderKL.from_pretrained(model_cfg["vae"][\'model_dir\'])\n    return vae'
        new_fn = (
            "def build_vae(model_cfg: Dict) -> AutoencoderKL:\n"
            '    vae_path = model_cfg["vae"].get("model_dir", "./weight/vae")\n'
            '    if osp.isdir(vae_path) and osp.isfile(osp.join(vae_path, "config.json")):\n'
            "        return AutoencoderKL.from_pretrained(vae_path)\n"
            "    try:\n"
            "        return AutoencoderKL.from_pretrained(vae_path)\n"
            "    except Exception:\n"
            '        return AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix")'
        )
        if old_fn in content:
            utils_train_py.write_text(content.replace(old_fn, new_fn), encoding="utf-8")


def ensure_moebius_repo_and_weights(repo_dir: Path, weight_type: str = "pretrained") -> tuple[Path, Path]:
    """Clone hustvl/Moebius and download both Moebius UNet and SDXL-VAE weights if missing."""
    repo_dir = repo_dir.resolve()
    if not (repo_dir / "infer" / "infer_moebius.py").is_file():
        print(f"[Moebius Setup] Cloning https://github.com/hustvl/Moebius.git into {repo_dir} ...")
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "https://github.com/hustvl/Moebius.git", str(repo_dir)],
            check=True,
        )

    patch_moebius_repo_for_inference(repo_dir)

    from huggingface_hub import hf_hub_download

    weight_subpath = f"{weight_type}/diffusion_pytorch_model.bin"
    local_weight_file = repo_dir / "weight" / "Moebius" / weight_subpath
    if not local_weight_file.is_file():
        print(f"[Moebius Setup] Downloading {weight_subpath} from Hugging Face (hustvl/Moebius) ...")
        local_weight_file.parent.mkdir(parents=True, exist_ok=True)
        downloaded = hf_hub_download(
            repo_id="hustvl/Moebius",
            filename=weight_subpath,
            local_dir=str(repo_dir / "weight" / "Moebius"),
        )
        local_weight_file = Path(downloaded)

    vae_dir = repo_dir / "weight" / "vae"
    vae_dir.mkdir(parents=True, exist_ok=True)
    for vae_filename in ("diffusion_pytorch_model.bin", "config.json"):
        target_vae_file = vae_dir / vae_filename
        if not target_vae_file.is_file():
            print(f"[Moebius Setup] Downloading VAE {vae_filename} from madebyollin/sdxl-vae-fp16-fix ...")
            hf_hub_download(
                repo_id="madebyollin/sdxl-vae-fp16-fix",
                filename=vae_filename,
                local_dir=str(vae_dir),
            )

    return repo_dir, local_weight_file


# ==============================================================================
# Ollama Service & MiniCPM-V 4.6 Manager
# ==============================================================================

class OllamaManager:
    """Manages local Ollama daemon lifecycle and ensures minicpm-v4.6 is pulled and ready on GPU."""

    def __init__(
        self,
        host_url: str = DEFAULT_OLLAMA_HOST,
        model_name: str = DEFAULT_OLLAMA_MODEL,
        num_parallel: int = MAX_VISION_CONCURRENCY,
    ) -> None:
        self.host_url = host_url.rstrip("/")
        self.model_name = model_name
        self.num_parallel = max(1, num_parallel)
        self.process: subprocess.Popen | None = None
        self.ready: bool = False
        self.error: str | None = None

    def is_server_reachable(self, timeout: float = 2.5) -> bool:
        try:
            req = Request(f"{self.host_url}/api/tags")
            with urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            req = Request(f"{self.host_url}/api/tags")
            with urlopen(req, timeout=5.0) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                return [m.get("name", "") for m in payload.get("models", [])]
        except Exception:
            return []

    def ensure_ollama_installed(self, auto_install: bool = False) -> bool:
        if shutil.which("ollama"):
            return True
        if not auto_install:
            return False
        if platform.system().lower() != "linux":
            print("[Ollama Setup] Auto-install is only supported on Linux. Please install Ollama manually.")
            return False
        print("[Ollama Setup] Installing Ollama on Linux ...")
        if not shutil.which("zstd"):
            subprocess.run(["bash", "-c", "apt-get update -qq && apt-get install -y -qq zstd pciutils lshw"], check=False)
        res = subprocess.run("curl -fsSL https://ollama.com/install.sh | sh", shell=True, check=False)
        return res.returncode == 0 and shutil.which("ollama") is not None

    def ensure_started_and_model_pulled(self, auto_install: bool = False, pull_model: bool = True) -> None:
        try:
            if not self.is_server_reachable():
                if not self.ensure_ollama_installed(auto_install=auto_install):
                    raise RuntimeError("`ollama` binary not found in PATH and Ollama server is not reachable.")
                env = os.environ.copy()
                env.setdefault("OLLAMA_NUM_PARALLEL", str(self.num_parallel))
                env.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")
                env.setdefault("OLLAMA_FLASH_ATTENTION", "1")
                env.setdefault("OLLAMA_KEEP_ALIVE", "24h")
                print(
                    f"[Ollama Setup] Starting `ollama serve` (OLLAMA_NUM_PARALLEL={env['OLLAMA_NUM_PARALLEL']}) ..."
                )
                self.process = subprocess.Popen(
                    ["ollama", "serve"],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                for _ in range(30):
                    if self.is_server_reachable(timeout=1.5):
                        break
                    time.sleep(1.0)
                if not self.is_server_reachable():
                    raise RuntimeError("Timed out waiting for `ollama serve` to start.")

            if pull_model:
                installed = self.list_models()
                if not any(m == self.model_name or m.startswith(f"{self.model_name}:") for m in installed):
                    print(f"[Ollama Setup] Pulling vision model '{self.model_name}' ...")
                    if shutil.which("ollama"):
                        subprocess.run(["ollama", "pull", self.model_name], check=True)
                    else:
                        req = Request(
                            f"{self.host_url}/api/pull",
                            data=json.dumps({"name": self.model_name, "stream": False}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(req, timeout=1800) as resp:
                            _ = resp.read()
            self.ready = True
            self.error = None
            print(f"[Ollama Setup] Model '{self.model_name}' is ready at {self.host_url}!")
        except Exception as exc:
            self.ready = False
            self.error = str(exc)
            print(f"[Ollama Warning] {exc}", file=sys.stderr)

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()


# ==============================================================================
# Cloudflared Tunnel Manager
# ==============================================================================

class CloudflaredManager:
    """Starts a Cloudflared tunnel so external machines can connect to this server."""

    TRYCLOUDFLARE_REGEX = re.compile(r"https://[-a-zA-Z0-9]+\.trycloudflare\.com")

    def __init__(
        self,
        local_port: int,
        token: str | None = None,
        protocol: str = "http2",
        url_file: Path = DEFAULT_CLOUDFLARED_URL_FILE,
    ) -> None:
        self.local_port = local_port
        self.token = token
        self.protocol = protocol
        self.url_file = url_file
        self.process: subprocess.Popen | None = None
        self.public_url: str | None = None
        self.error: str | None = None

    @staticmethod
    def ensure_binary(target_dir: Path = Path(".")) -> str:
        existing = shutil.which("cloudflared")
        if existing:
            return existing
        local_bin = (target_dir / "cloudflared").resolve()
        if local_bin.is_file() and os.access(local_bin, os.X_OK):
            return str(local_bin)

        sys_name = platform.system().lower()
        machine = platform.machine().lower()
        if sys_name == "linux":
            arch = "arm64" if machine in ("aarch64", "arm64") else "amd64"
            dl_url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
            print(f"[Cloudflared] Downloading {dl_url} -> {local_bin} ...")
            data = download_or_read_bytes(dl_url, timeout=60)
            local_bin.write_bytes(data)
            local_bin.chmod(0o755)
            return str(local_bin)
        elif sys_name == "darwin":
            if shutil.which("brew"):
                print("[Cloudflared] Installing cloudflared via Homebrew ...")
                subprocess.run(["brew", "install", "cloudflared"], check=True)
                found = shutil.which("cloudflared")
                if found:
                    return found
        raise RuntimeError("cloudflared binary not found and could not be auto-downloaded for this OS.")

    def start(self, timeout_seconds: float = 30.0) -> str | None:
        try:
            bin_path = self.ensure_binary()
            base_flags = [
                bin_path,
                "tunnel",
                "--no-autoupdate",
                "--protocol",
                self.protocol,
                "--edge-ip-version",
                "4",
            ]
            if self.token:
                cmd = [*base_flags, "run", "--token", self.token]
            else:
                cmd = [*base_flags, "--url", f"http://127.0.0.1:{self.local_port}"]

            print(
                f"[Cloudflared] Starting tunnel for http://127.0.0.1:{self.local_port} "
                f"(protocol={self.protocol}, IPv4) ..."
            )
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            if self.token:
                self.public_url = "custom-cloudflare-token-tunnel"
                return self.public_url

            deadline = time.monotonic() + timeout_seconds
            assert self.process.stdout is not None
            import select
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    break
                ready_to_read, _, _ = select.select([self.process.stdout], [], [], 0.5)
                if ready_to_read:
                    line = self.process.stdout.readline()
                    if not line:
                        continue
                    match = self.TRYCLOUDFLARE_REGEX.search(line)
                    if match:
                        self.public_url = match.group(0)
                        self.url_file.parent.mkdir(parents=True, exist_ok=True)
                        self.url_file.write_text(self.public_url + "\n", encoding="utf-8")
                        print("\n" + "=" * 72)
                        print(f"🌐 CLOUDFLARED PUBLIC URL: {self.public_url}")
                        print(f"📄 Saved tunnel URL to   : {self.url_file}")
                        print("=" * 72 + "\n")
                        import threading
                        threading.Thread(target=self._drain_output, daemon=True).start()
                        return self.public_url

            raise RuntimeError("Timed out waiting for trycloudflare.com URL from cloudflared.")
        except Exception as exc:
            self.error = str(exc)
            print(f"[Cloudflared Error] {exc}", file=sys.stderr)
            return None

    def _drain_output(self) -> None:
        if not self.process or not self.process.stdout:
            return
        try:
            for _ in self.process.stdout:
                pass
        except Exception:
            pass

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()


class TailscaleManager:
    """Starts Tailscale in userspace-networking + ephemeral in-memory mode (`--state=mem:`)
    for unprivileged Google Colab & Kaggle containers:
    - Uses `--authkey` (reusable + ephemeral key) for zero-touch registration without manual approval.
    - Uses `--state=mem:` + `atexit` logout so nodes are automatically and immediately cleaned up
      when the Colab/Kaggle session disconnects or aborts.
    - Exposes a static MagicDNS hostname (`http://moebius-gpu:8000`) so clients never need to update IPs.
    """

    def __init__(
        self,
        local_port: int,
        auth_key: str | None = None,
        authkey: str | None = None,
        hostname: str = "moebius-gpu",
        enable_funnel: bool = False,
        state_dir: Path = Path("/tmp/tailscale"),
    ) -> None:
        self.local_port = local_port
        self.authkey = auth_key or authkey or os.environ.get("TAILSCALE_AUTHKEY")
        self.hostname = hostname
        self.enable_funnel = enable_funnel
        self.state_dir = state_dir
        self.daemon_proc: subprocess.Popen | None = None
        self.tailscale_ip: str | None = None
        self.tailscale_url: str | None = None
        self.magicdns_url: str | None = None
        self.fqdn_url: str | None = None
        self.funnel_url: str | None = None
        self.error: str | None = None

    @staticmethod
    def ensure_installed() -> None:
        if shutil.which("tailscale") and shutil.which("tailscaled"):
            return
        if platform.system().lower() != "linux":
            raise RuntimeError("Tailscale auto-install is only supported on Linux. Install Tailscale manually.")
        print("[Tailscale] Installing Tailscale on Linux ...")
        subprocess.run("curl -fsSL https://tailscale.com/install.sh | sh", shell=True, check=True)

    def start(self) -> str | None:
        try:
            self.ensure_installed()
            self.state_dir.mkdir(parents=True, exist_ok=True)
            sock_path = str(self.state_dir / "tailscaled.sock")

            # `--tun=userspace-networking` works in unprivileged Colab/Kaggle containers without /dev/net/tun.
            # `--state=mem:` keeps state strictly in RAM so tailscaled automatically deregisters the ephemeral
            # node with the Tailscale control plane as soon as the container/process terminates.
            print("[Tailscale] Starting tailscaled (--tun=userspace-networking --state=mem:) ...")
            self.daemon_proc = subprocess.Popen(
                [
                    "tailscaled",
                    "--tun=userspace-networking",
                    "--state=mem:",
                    f"--socket={sock_path}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            import atexit
            atexit.register(self.stop)
            time.sleep(2.0)

            up_cmd = [
                "tailscale",
                f"--socket={sock_path}",
                "up",
                f"--hostname={self.hostname}",
                "--accept-dns=false",
            ]
            if self.authkey:
                up_cmd.append(f"--authkey={self.authkey}")

            print(f"[Tailscale] Registering ephemeral node '{self.hostname}' via authkey ...")
            subprocess.run(up_cmd, check=True, timeout=30)

            ip_res = subprocess.run(
                ["tailscale", f"--socket={sock_path}", "ip", "-4"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.tailscale_ip = ip_res.stdout.strip().splitlines()[0]
            self.tailscale_url = f"http://{self.tailscale_ip}:{self.local_port}"
            self.magicdns_url = f"http://{self.hostname}:{self.local_port}"

            status_res = subprocess.run(
                ["tailscale", f"--socket={sock_path}", "status", "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            dns_name = ""
            if status_res.returncode == 0:
                dns_name = json.loads(status_res.stdout).get("Self", {}).get("DNSName", "").rstrip(".")
                if dns_name:
                    short_name = dns_name.split(".")[0]
                    self.magicdns_url = f"http://{short_name}:{self.local_port}"
                    self.fqdn_url = f"http://{dns_name}:{self.local_port}"

            if self.enable_funnel:
                subprocess.run(
                    ["tailscale", f"--socket={sock_path}", "funnel", "--bg", str(self.local_port)],
                    check=False,
                )
                if dns_name:
                    self.funnel_url = f"https://{dns_name}"

            print("\n" + "=" * 72)
            print(f"⚡ TAILSCALE MAGICDNS URL: {self.magicdns_url}")
            if self.fqdn_url:
                print(f"⚡ TAILSCALE FQDN URL    : {self.fqdn_url}")
            print(f"⚡ TAILSCALE DIRECT IP   : {self.tailscale_url}")
            if self.funnel_url:
                print(f"🌐 TAILSCALE FUNNEL URL  : {self.funnel_url}")
            print("=" * 72 + "\n")
            return self.funnel_url or self.magicdns_url or self.tailscale_url
        except Exception as exc:
            self.error = str(exc)
            print(f"[Tailscale Error] {exc}", file=sys.stderr)
            return None

    def stop(self) -> None:
        sock_path = str(self.state_dir / "tailscaled.sock")
        if self.daemon_proc and self.daemon_proc.poll() is None:
            with contextlib.suppress(Exception):
                subprocess.run(["tailscale", f"--socket={sock_path}", "logout"], timeout=4, check=False)
            self.daemon_proc.terminate()
            try:
                self.daemon_proc.wait(timeout=5)
            except Exception:
                self.daemon_proc.kill()


# ==============================================================================
# Request/Response Schemas
# ==============================================================================

class InpaintItem(BaseModel):
    image_base64: str | None = None
    image_url: str | None = None
    mask_base64: str | None = None
    mask_url: str | None = None
    prompt: str = ""
    weight_type: str = "pretrained"
    cfg: float = 2.5
    mask_dilation: int = 8
    num_steps: int = 20
    seed: int = -1
    paste: bool = True


class InpaintRequest(BaseModel):
    image_base64: str | None = None
    image_url: str | None = None
    mask_base64: str | None = None
    mask_url: str | None = None
    prompt: str = ""
    weight_type: str = "pretrained"
    cfg: float = 2.5
    mask_dilation: int = 8
    num_steps: int = 20
    seed: int = -1
    paste: bool = True
    items: list[InpaintItem] | None = None
    images_base64: list[str] | None = None
    masks_base64: list[str] | None = None


class InpaintBatchRequest(BaseModel):
    items: list[InpaintItem] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list, description="List of image base64 or URLs")
    masks: list[str] = Field(default_factory=list, description="List of mask base64 or URLs")
    weight_type: str = "pretrained"
    cfg: float = 2.5
    mask_dilation: int = 8
    num_steps: int = 20
    seed: int = -1
    paste: bool = True


class VisionItem(BaseModel):
    image_base64: str | None = None
    image_url: str | None = None
    images: list[str] = Field(default_factory=list)
    prompt: str = "Describe this image in detail, including characters, actions, panels, and visible text."
    system_prompt: str | None = None
    temperature: float = 0.2
    max_tokens: int = 1024
    json_mode: bool = False


class VisionRequest(BaseModel):
    image_base64: str | None = None
    image_url: str | None = None
    images: list[str] = Field(default_factory=list, description="Multiple images for batch or multi-image prompt")
    prompt: str = "Describe this image in detail, including characters, actions, panels, and visible text."
    system_prompt: str | None = None
    model: str = DEFAULT_OLLAMA_MODEL
    temperature: float = 0.2
    max_tokens: int = 1024
    json_mode: bool = False
    batch_mode: Literal["per_image", "multi_image"] = "per_image"


class VisionBatchRequest(BaseModel):
    items: list[VisionItem] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    prompt: str = "Describe this image in detail, including characters, actions, panels, and visible text."
    system_prompt: str | None = None
    model: str = DEFAULT_OLLAMA_MODEL
    temperature: float = 0.2
    max_tokens: int = 1024
    json_mode: bool = False
    mode: Literal["per_image", "multi_image"] = "per_image"


# ==============================================================================
# GPU Work Controller & Dynamic Micro-Batch Engine
# ==============================================================================

@dataclass
class _QueuedInpaintJob:
    image: Image.Image
    mask: Image.Image
    weight_type: str
    cfg: float
    mask_dilation: int
    num_steps: int
    seed: int
    paste: bool
    future: asyncio.Future
    enqueued_at: float = field(default_factory=time.perf_counter)


class GPUInferenceEngine:
    """Coordinates GPU execution across Moebius inpainting and MiniCPM-V 4.6 visual understanding.

    Guarantees:
    1. All requests execute on the GPU (queued cleanly when busy; never dropped to CPU).
    2. Concurrent single-image inpainting requests arriving within `batch_window_ms` are coalesced
       into a single batched GPU forward pass up to `max_inpaint_batch`.
    3. Total simultaneous GPU tasks across models are bounded by `max_gpu_active` and throttled
       when VRAM usage exceeds `gpu_vram_threshold`.
    """

    def __init__(
        self,
        moebius_repo: Path = DEFAULT_MOEBIUS_REPO,
        ollama_manager: OllamaManager | None = None,
        device: str = "auto",
        max_gpu_active: int = MAX_GPU_ACTIVE,
        max_inpaint_batch: int = MAX_INPAINT_BATCH,
        batch_window_ms: int = BATCH_WINDOW_MS,
        max_vision_concurrency: int = MAX_VISION_CONCURRENCY,
        max_vision_batch: int = MAX_VISION_BATCH,
        gpu_vram_threshold: float = GPU_VRAM_THRESHOLD,
        max_queue_size: int = MAX_QUEUE_SIZE,
    ) -> None:
        self.moebius_repo = moebius_repo.resolve()
        self.device = detect_accelerator_device(device)
        self.max_gpu_active = max(1, max_gpu_active)
        self.max_inpaint_batch = max(1, max_inpaint_batch)
        self.batch_window_ms = max(5, batch_window_ms)
        self.max_vision_concurrency = max(1, max_vision_concurrency)
        self.max_vision_batch = max(1, max_vision_batch)
        self.gpu_vram_threshold = min(max(gpu_vram_threshold, 0.3), 0.98)
        self.max_queue_size = max(4, max_queue_size)

        self.ollama = ollama_manager or OllamaManager(num_parallel=self.max_vision_concurrency)

        # Async primitives lazily bound to the active running event loop
        self._bound_loop: asyncio.AbstractEventLoop | None = None
        self._gpu_global_sem: asyncio.Semaphore | None = None
        self._inpaint_gpu_lock: asyncio.Lock | None = None
        self._vision_sem: asyncio.Semaphore | None = None
        self._inpaint_queue: asyncio.Queue[_QueuedInpaintJob] | None = None
        self._batcher_task: asyncio.Task | None = None

        # Runtime state & telemetry
        self.pipeline_cache: dict[str, Any] = {}
        self.moebius_ready: bool = False
        self.moebius_error: str | None = None
        self.active_gpu_tasks: int = 0
        self.active_inpaint_jobs: int = 0
        self.active_vision_jobs: int = 0
        self.queued_vision_jobs: int = 0
        self.total_inpainted_images: int = 0
        self.total_inpaint_batches: int = 0
        self.total_vision_images: int = 0
        self.vram_throttle_events: int = 0

    def _ensure_async_primitives(self) -> None:
        loop = asyncio.get_running_loop()
        if self._bound_loop is not loop:
            self._bound_loop = loop
            self._gpu_global_sem = asyncio.Semaphore(self.max_gpu_active)
            self._inpaint_gpu_lock = asyncio.Lock()
            self._vision_sem = asyncio.Semaphore(self.max_vision_concurrency)
            self._inpaint_queue = asyncio.Queue(maxsize=self.max_queue_size)
            self._batcher_task = None

    def start_background_workers(self) -> None:
        self._ensure_async_primitives()
        if self._batcher_task is None or self._batcher_task.done():
            self._batcher_task = asyncio.create_task(self._inpaint_dynamic_batcher_loop())

    async def stop_background_workers(self) -> None:
        if self._batcher_task and not self._batcher_task.done():
            self._batcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._batcher_task
        self._batcher_task = None

    def get_or_load_moebius(self, weight_type: str = "pretrained") -> Any:
        if weight_type in self.pipeline_cache:
            return self.pipeline_cache[weight_type]

        print(f"[Moebius Loader] Loading Moebius pipeline ({weight_type}) onto {self.device} ...")
        repo_dir, weight_file = ensure_moebius_repo_and_weights(self.moebius_repo, weight_type=weight_type)
        repo_str = str(repo_dir)
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)

        prev_cwd = os.getcwd()
        try:
            os.chdir(repo_dir)
            from infer.utils import build_pipeline
            args = SimpleNamespace(
                model_config=str(repo_dir / "config/model_cfg/moebius.yaml"),
                model_weight=str(weight_file),
                device=self.device,
            )
            pipe = build_pipeline(args)
        finally:
            os.chdir(prev_cwd)

        self.pipeline_cache[weight_type] = pipe
        self.moebius_ready = True
        self.moebius_error = None
        print(f"[Moebius Loader] Pipeline ({weight_type}) ready in {self.device} memory!")
        return pipe

    def compute_effective_batch_limit(self, requested_max: int) -> int:
        """Check GPU VRAM utilization against threshold; shrink micro-batch size if near limit."""
        stats = get_gpu_memory_stats()
        util = float(stats.get("utilization_ratio", 0.0))
        if util >= self.gpu_vram_threshold:
            self.vram_throttle_events += 1
            release_gpu_cache()
            stats_after = get_gpu_memory_stats()
            if float(stats_after.get("utilization_ratio", 0.0)) >= self.gpu_vram_threshold:
                return 1
            return max(1, requested_max // 2)
        return requested_max

    @contextlib.asynccontextmanager
    async def _acquire_gpu_slot(self):
        """Acquire a bounded GPU execution slot and ensure VRAM is below safety threshold."""
        self._ensure_async_primitives()
        assert self._gpu_global_sem is not None
        await self._gpu_global_sem.acquire()
        self.active_gpu_tasks += 1
        try:
            stats = get_gpu_memory_stats()
            if float(stats.get("utilization_ratio", 0.0)) >= self.gpu_vram_threshold:
                self.vram_throttle_events += 1
                release_gpu_cache()
            yield
        finally:
            self.active_gpu_tasks -= 1
            self._gpu_global_sem.release()

    def _run_moebius_microbatch_sync(
        self,
        jobs: list[_QueuedInpaintJob],
    ) -> list[tuple[Image.Image, float, int]]:
        """Execute a micro-batch of homogeneous-config Moebius jobs in one GPU forward pass."""
        import torch

        if not jobs:
            return []

        first = jobs[0]
        pipe = self.get_or_load_moebius(first.weight_type)
        seed_val = first.seed if first.seed >= 0 else random.randint(1, 2147483647)

        sq_imgs: list[Image.Image] = []
        sq_masks: list[Image.Image] = []
        crop_boxes: list[tuple[int, int, int, int]] = []
        orig_sizes: list[tuple[int, int]] = []

        for job in jobs:
            sq_img, sq_mask, box = pad_to_square(job.image, job.mask)
            sq_imgs.append(sq_img)
            sq_masks.append(sq_mask)
            crop_boxes.append(box)
            orig_sizes.append(job.image.size)

        t0 = time.perf_counter()
        with torch.inference_mode():
            out_sq_list = pipe(
                sq_imgs,
                sq_masks,
                guidance_scale=float(first.cfg),
                mask_dilate_kernel_size=int(first.mask_dilation),
                num_steps=int(first.num_steps),
                retry=int(seed_val),
                paste=bool(first.paste),
                compensate=False,
                noise_offset=0.0357,
            )
        batch_elapsed = time.perf_counter() - t0
        per_item_elapsed = round(batch_elapsed / max(len(jobs), 1), 3)

        outputs: list[tuple[Image.Image, float, int]] = []
        for idx, out_sq in enumerate(out_sq_list):
            sq_size = sq_imgs[idx].size
            orig_size = orig_sizes[idx]
            crop_box = crop_boxes[idx]
            res_sq_full = out_sq.resize(sq_size, Image.Resampling.LANCZOS)
            res_img = res_sq_full.crop(crop_box).resize(orig_size, Image.Resampling.LANCZOS)
            outputs.append((res_img, per_item_elapsed, seed_val))

        return outputs

    async def _execute_inpaint_jobs_on_gpu(self, jobs: list[_QueuedInpaintJob]) -> None:
        """Chunk jobs by effective GPU batch threshold and run on the GPU."""
        if not jobs:
            return
        self._ensure_async_primitives()
        assert self._inpaint_gpu_lock is not None

        groups: dict[tuple[str, float, int, int, bool], list[_QueuedInpaintJob]] = {}
        for job in jobs:
            key = (job.weight_type, float(job.cfg), int(job.mask_dilation), int(job.num_steps), bool(job.paste))
            groups.setdefault(key, []).append(job)

        for _, group_jobs in groups.items():
            idx = 0
            while idx < len(group_jobs):
                async with self._acquire_gpu_slot():
                    async with self._inpaint_gpu_lock:
                        eff_batch = self.compute_effective_batch_limit(self.max_inpaint_batch)
                        chunk = group_jobs[idx : idx + eff_batch]
                        idx += len(chunk)
                        self.active_inpaint_jobs += len(chunk)
                        try:
                            results = await asyncio.to_thread(self._run_moebius_microbatch_sync, chunk)
                            self.total_inpainted_images += len(chunk)
                            self.total_inpaint_batches += 1
                            for job, res_tuple in zip(chunk, results):
                                if not job.future.done():
                                    job.future.set_result(res_tuple)
                        except Exception as exc:
                            for job in chunk:
                                if not job.future.done():
                                    job.future.set_exception(exc)
                        finally:
                            self.active_inpaint_jobs -= len(chunk)

    async def _inpaint_dynamic_batcher_loop(self) -> None:
        """Background loop that coalesces concurrent single inference calls into GPU batches."""
        self._ensure_async_primitives()
        assert self._inpaint_queue is not None
        queue = self._inpaint_queue
        while True:
            first_job = await queue.get()
            batch = [first_job]
            deadline = time.perf_counter() + (self.batch_window_ms / 1000.0)

            while len(batch) < self.max_inpaint_batch:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                try:
                    next_job = await asyncio.wait_for(queue.get(), timeout=remaining)
                    batch.append(next_job)
                except asyncio.TimeoutError:
                    break

            try:
                await self._execute_inpaint_jobs_on_gpu(batch)
            finally:
                for _ in batch:
                    queue.task_done()

    async def submit_single_inpaint(
        self,
        image: Image.Image,
        mask: Image.Image,
        weight_type: str = "pretrained",
        cfg: float = 2.5,
        mask_dilation: int = 8,
        num_steps: int = 20,
        seed: int = -1,
        paste: bool = True,
    ) -> tuple[Image.Image, float, int]:
        """Enqueue a single image+mask pair into the dynamic GPU batcher."""
        self.start_background_workers()
        assert self._inpaint_queue is not None
        if self._inpaint_queue.full():
            raise HTTPException(
                status_code=429,
                detail=f"GPU queue is full ({self.max_queue_size} pending requests). Please retry shortly.",
            )
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        job = _QueuedInpaintJob(
            image=image,
            mask=mask,
            weight_type=weight_type,
            cfg=cfg,
            mask_dilation=mask_dilation,
            num_steps=num_steps,
            seed=seed,
            paste=paste,
            future=fut,
        )
        await self._inpaint_queue.put(job)
        return await fut

    async def submit_batch_inpaint(
        self,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Process a batch of images+masks on the GPU, chunked by `max_inpaint_batch` & VRAM threshold."""
        self._ensure_async_primitives()
        qsize = self._inpaint_queue.qsize() if self._inpaint_queue else 0
        if len(items) + qsize > self.max_queue_size:
            raise HTTPException(
                status_code=429,
                detail=f"Batch of {len(items)} exceeds available GPU queue capacity ({self.max_queue_size}).",
            )
        loop = asyncio.get_running_loop()
        jobs: list[_QueuedInpaintJob] = []
        for item in items:
            fut = loop.create_future()
            jobs.append(
                _QueuedInpaintJob(
                    image=item["image"],
                    mask=item["mask"],
                    weight_type=item.get("weight_type", "pretrained"),
                    cfg=float(item.get("cfg", 2.5)),
                    mask_dilation=int(item.get("mask_dilation", 8)),
                    num_steps=int(item.get("num_steps", 20)),
                    seed=int(item.get("seed", -1)),
                    paste=bool(item.get("paste", True)),
                    future=fut,
                )
            )

        await self._execute_inpaint_jobs_on_gpu(jobs)
        output_list: list[dict[str, Any]] = []
        for idx, job in enumerate(jobs):
            res_img, infer_sec, used_seed = await job.future
            output_list.append(
                {
                    "index": idx,
                    "result_base64": encode_pil_to_base64(res_img, fmt="PNG", include_data_uri=True),
                    "duration_seconds": infer_sec,
                    "seed": used_seed,
                    "width": res_img.size[0],
                    "height": res_img.size[1],
                }
            )
        return output_list

    def _call_ollama_chat_sync(
        self,
        images_b64: list[str],
        prompt: str,
        model: str = DEFAULT_OLLAMA_MODEL,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        """Invoke Ollama `/api/chat` with MiniCPM-V 4.6 for single or multi-image understanding."""
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        user_msg: dict[str, Any] = {"role": "user", "content": prompt}
        if images_b64:
            user_msg["images"] = images_b64
        messages.append(user_msg)

        payload: dict[str, Any] = {
            "model": model or self.ollama.model_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": float(temperature),
                "num_predict": int(max_tokens),
            },
        }
        if json_mode:
            payload["format"] = "json"

        t0 = time.perf_counter()
        req = Request(
            f"{self.ollama.host_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=300) as resp:
                raw_resp = json.loads(resp.read().decode("utf-8"))
        except HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama HTTP {err.code}: {detail}") from err
        except URLError as err:
            raise RuntimeError(f"Could not reach Ollama at {self.ollama.host_url}: {err.reason}") from err

        elapsed = round(time.perf_counter() - t0, 3)
        msg_content = raw_resp.get("message", {}).get("content", "")
        return {
            "model": raw_resp.get("model", model),
            "response": msg_content,
            "duration_seconds": elapsed,
            "eval_count": raw_resp.get("eval_count"),
            "prompt_eval_count": raw_resp.get("prompt_eval_count"),
        }

    async def run_vision_single(
        self,
        images_b64: list[str],
        prompt: str,
        model: str = DEFAULT_OLLAMA_MODEL,
        system_prompt: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        """Run a vision understanding call gated by both the vision concurrency semaphore and GPU controller."""
        self._ensure_async_primitives()
        assert self._vision_sem is not None
        if self.queued_vision_jobs >= self.max_queue_size:
            raise HTTPException(
                status_code=429,
                detail=f"Vision GPU queue is full ({self.max_queue_size} waiting). Please retry shortly.",
            )
        self.queued_vision_jobs += 1
        try:
            async with self._vision_sem:
                async with self._acquire_gpu_slot():
                    self.active_vision_jobs += 1
                    try:
                        res = await asyncio.to_thread(
                            self._call_ollama_chat_sync,
                            images_b64,
                            prompt,
                            model,
                            system_prompt,
                            temperature,
                            max_tokens,
                            json_mode,
                        )
                        self.total_vision_images += max(1, len(images_b64))
                        return res
                    finally:
                        self.active_vision_jobs -= 1
        finally:
            self.queued_vision_jobs -= 1

    async def run_vision_batch(
        self,
        items: list[dict[str, Any]],
        model: str = DEFAULT_OLLAMA_MODEL,
    ) -> list[dict[str, Any]]:
        """Process multiple vision items in bounded GPU chunks."""
        self._ensure_async_primitives()
        if len(items) + self.queued_vision_jobs > self.max_queue_size:
            raise HTTPException(
                status_code=429,
                detail=f"Vision batch of {len(items)} exceeds available queue capacity ({self.max_queue_size}).",
            )
        results: list[dict[str, Any]] = []
        idx = 0
        while idx < len(items):
            eff_chunk = self.compute_effective_batch_limit(self.max_vision_batch)
            chunk = items[idx : idx + eff_chunk]
            idx += len(chunk)
            coros = [
                self.run_vision_single(
                    images_b64=item["images_b64"],
                    prompt=item["prompt"],
                    model=model,
                    system_prompt=item.get("system_prompt"),
                    temperature=item.get("temperature", 0.2),
                    max_tokens=item.get("max_tokens", 1024),
                    json_mode=item.get("json_mode", False),
                )
                for item in chunk
            ]
            chunk_outputs = await asyncio.gather(*coros)
            for out in chunk_outputs:
                out_with_idx = {"index": len(results), **out}
                results.append(out_with_idx)
        return results

    def status_snapshot(
        self,
        tunnel_url: str | None = None,
        tailscale_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        qsize = self._inpaint_queue.qsize() if self._inpaint_queue is not None else 0
        return {
            "status": "ok",
            "public_url": tunnel_url or (tailscale_info.get("url") if tailscale_info else None),
            "tailscale": tailscale_info,
            "gpu": {
                **get_gpu_memory_stats(),
                "configured_device": self.device,
                "vram_threshold_ratio": self.gpu_vram_threshold,
                "vram_throttle_events": self.vram_throttle_events,
            },
            "thresholds": {
                "max_gpu_active": self.max_gpu_active,
                "max_inpaint_batch": self.max_inpaint_batch,
                "batch_window_ms": self.batch_window_ms,
                "max_vision_concurrency": self.max_vision_concurrency,
                "max_vision_batch": self.max_vision_batch,
                "max_queue_size": self.max_queue_size,
            },
            "workload": {
                "active_gpu_tasks": self.active_gpu_tasks,
                "active_inpaint_jobs": self.active_inpaint_jobs,
                "queued_inpaint_jobs": qsize,
                "active_vision_jobs": self.active_vision_jobs,
                "queued_vision_jobs": self.queued_vision_jobs,
                "total_inpainted_images": self.total_inpainted_images,
                "total_inpaint_batches": self.total_inpaint_batches,
                "total_vision_images": self.total_vision_images,
            },
            "models": {
                "moebius": {
                    "ready": self.moebius_ready,
                    "loaded_variants": list(self.pipeline_cache.keys()),
                    "repo_dir": str(self.moebius_repo),
                    "error": self.moebius_error,
                },
                "minicpm_v": {
                    "model": self.ollama.model_name,
                    "ollama_host": self.ollama.host_url,
                    "ready": self.ollama.ready,
                    "error": self.ollama.error,
                },
            },
        }


# ==============================================================================
# FastAPI Application & Route Handlers
# ==============================================================================

ENGINE = GPUInferenceEngine()
TUNNEL_MANAGER: CloudflaredManager | None = None
TAILSCALE_MANAGER: TailscaleManager | None = None
PRELOAD_ON_STARTUP = os.environ.get("SKIP_PRELOAD", "0") != "1"
AUTO_INSTALL_OLLAMA = os.environ.get("AUTO_BOOTSTRAP", "0") == "1"


def _background_startup_init() -> None:
    try:
        ENGINE.get_or_load_moebius("pretrained")
    except Exception as exc:
        ENGINE.moebius_error = str(exc)
        print(f"[Startup Warning] Moebius preload deferred/failed: {exc}", file=sys.stderr)

    ENGINE.ollama.ensure_started_and_model_pulled(
        auto_install=AUTO_INSTALL_OLLAMA,
        pull_model=True,
    )


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    ENGINE.start_background_workers()
    if PRELOAD_ON_STARTUP:
        import threading
        threading.Thread(target=_background_startup_init, daemon=True).start()
    yield
    await ENGINE.stop_background_workers()
    if TUNNEL_MANAGER is not None:
        TUNNEL_MANAGER.stop()
    if TAILSCALE_MANAGER is not None:
        TAILSCALE_MANAGER.stop()
    ENGINE.ollama.stop()


app = FastAPI(
    title="Moebius Inpainting + MiniCPM-V 4.6 Vision GPU Server",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _resolve_inpaint_pair(item: InpaintItem | InpaintRequest) -> tuple[Image.Image, Image.Image]:
    img_src = item.image_base64 or item.image_url
    mask_src = item.mask_base64 or item.mask_url
    if not img_src or not mask_src:
        raise ValueError("Both image (image_base64 or image_url) and mask (mask_base64 or mask_url) are required.")
    return decode_pil_image(img_src, "RGB"), decode_pil_image(mask_src, "L")


def _get_tailscale_snapshot() -> dict[str, Any] | None:
    if TAILSCALE_MANAGER is None:
        return None
    return {
        "ip": TAILSCALE_MANAGER.tailscale_ip,
        "url": TAILSCALE_MANAGER.magicdns_url or TAILSCALE_MANAGER.tailscale_url,
        "ip_url": TAILSCALE_MANAGER.tailscale_url,
        "hostname_url": TAILSCALE_MANAGER.magicdns_url,
        "fqdn_url": TAILSCALE_MANAGER.fqdn_url,
        "funnel_url": TAILSCALE_MANAGER.funnel_url,
        "error": TAILSCALE_MANAGER.error,
    }


@app.get("/")
@app.get("/health")
@app.get("/api/status")
async def get_status():
    tunnel_url = TUNNEL_MANAGER.public_url if TUNNEL_MANAGER else None
    return ENGINE.status_snapshot(
        tunnel_url=tunnel_url,
        tailscale_info=_get_tailscale_snapshot(),
    )


@app.get("/api/tunnel")
async def get_tunnel():
    cf_url = TUNNEL_MANAGER.public_url if TUNNEL_MANAGER else None
    ts_info = _get_tailscale_snapshot()
    ts_url = (ts_info.get("funnel_url") or ts_info.get("url")) if ts_info else None
    active_url = cf_url or ts_url
    if active_url:
        return {
            "status": "active",
            "public_url": active_url,
            "cloudflared_url": cf_url,
            "tailscale": ts_info,
        }
    return {
        "status": "disabled",
        "public_url": None,
        "cloudflared_url": None,
        "tailscale": ts_info,
        "error": (TUNNEL_MANAGER.error if TUNNEL_MANAGER else None) or (TAILSCALE_MANAGER.error if TAILSCALE_MANAGER else None),
    }


@app.post("/api/inpaint")
async def inpaint_endpoint(req: InpaintRequest):
    """Single or batch Moebius inpainting endpoint.
    Single calls are automatically routed through the Dynamic GPU Micro-Batcher so concurrent
    requests share a single batched GPU pass up to `max_inpaint_batch`."""
    t0 = time.perf_counter()
    try:
        if req.items or req.images_base64:
            batch_items: list[dict[str, Any]] = []
            if req.items:
                for it in req.items:
                    img, msk = _resolve_inpaint_pair(it)
                    batch_items.append(
                        {
                            "image": img,
                            "mask": msk,
                            "weight_type": it.weight_type or req.weight_type,
                            "cfg": it.cfg,
                            "mask_dilation": it.mask_dilation,
                            "num_steps": it.num_steps,
                            "seed": it.seed,
                            "paste": it.paste,
                        }
                    )
            elif req.images_base64 and req.masks_base64:
                if len(req.images_base64) != len(req.masks_base64):
                    raise ValueError("images_base64 and masks_base64 must have the same length.")
                for img_s, msk_s in zip(req.images_base64, req.masks_base64):
                    batch_items.append(
                        {
                            "image": decode_pil_image(img_s, "RGB"),
                            "mask": decode_pil_image(msk_s, "L"),
                            "weight_type": req.weight_type,
                            "cfg": req.cfg,
                            "mask_dilation": req.mask_dilation,
                            "num_steps": req.num_steps,
                            "seed": req.seed,
                            "paste": req.paste,
                        }
                    )
            results = await ENGINE.submit_batch_inpaint(batch_items)
            return {
                "batch_size": len(results),
                "results": results,
                "total_duration_seconds": round(time.perf_counter() - t0, 3),
                "device": ENGINE.device,
            }

        image, mask = _resolve_inpaint_pair(req)
        result_img, infer_sec, seed_val = await ENGINE.submit_single_inpaint(
            image=image,
            mask=mask,
            weight_type=req.weight_type,
            cfg=req.cfg,
            mask_dilation=req.mask_dilation,
            num_steps=req.num_steps,
            seed=req.seed,
            paste=req.paste,
        )
        return {
            "result_base64": encode_pil_to_base64(result_img, fmt="PNG", include_data_uri=True),
            "duration_seconds": infer_sec,
            "total_duration_seconds": round(time.perf_counter() - t0, 3),
            "seed": seed_val,
            "device": ENGINE.device,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/inpaint/batch")
async def inpaint_batch_endpoint(req: InpaintBatchRequest):
    """Dedicated batch inpainting endpoint for multiple images."""
    t0 = time.perf_counter()
    try:
        batch_items: list[dict[str, Any]] = []
        if req.items:
            for it in req.items:
                img, msk = _resolve_inpaint_pair(it)
                batch_items.append(
                    {
                        "image": img,
                        "mask": msk,
                        "weight_type": it.weight_type or req.weight_type,
                        "cfg": it.cfg,
                        "mask_dilation": it.mask_dilation,
                        "num_steps": it.num_steps,
                        "seed": it.seed,
                        "paste": it.paste,
                    }
                )
        elif req.images and req.masks:
            if len(req.images) != len(req.masks):
                raise ValueError("`images` and `masks` lists must have the same length.")
            for img_s, msk_s in zip(req.images, req.masks):
                batch_items.append(
                    {
                        "image": decode_pil_image(img_s, "RGB"),
                        "mask": decode_pil_image(msk_s, "L"),
                        "weight_type": req.weight_type,
                        "cfg": req.cfg,
                        "mask_dilation": req.mask_dilation,
                        "num_steps": req.num_steps,
                        "seed": req.seed,
                        "paste": req.paste,
                    }
                )
        else:
            raise ValueError("Provide either `items` or matching `images` and `masks` lists.")

        results = await ENGINE.submit_batch_inpaint(batch_items)
        return {
            "batch_size": len(results),
            "results": results,
            "total_duration_seconds": round(time.perf_counter() - t0, 3),
            "device": ENGINE.device,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/vision")
async def vision_endpoint(req: VisionRequest):
    """Visual understanding endpoint powered by Ollama `minicpm-v4.6` on GPU.
    Supports single image, multi-image in a single prompt, or per-image batch execution."""
    t0 = time.perf_counter()
    try:
        raw_sources: list[str] = list(req.images)
        if req.image_base64:
            raw_sources.insert(0, req.image_base64)
        elif req.image_url:
            raw_sources.insert(0, req.image_url)

        if not raw_sources:
            raise ValueError("Provide at least one image via `image_base64`, `image_url`, or `images`.")

        b64_images = [normalize_to_raw_base64(src) for src in raw_sources]

        if len(b64_images) > 1 and req.batch_mode == "per_image":
            batch_items = [
                {
                    "images_b64": [img_b64],
                    "prompt": req.prompt,
                    "system_prompt": req.system_prompt,
                    "temperature": req.temperature,
                    "max_tokens": req.max_tokens,
                    "json_mode": req.json_mode,
                }
                for img_b64 in b64_images
            ]
            results = await ENGINE.run_vision_batch(batch_items, model=req.model)
            return {
                "batch_size": len(results),
                "results": results,
                "total_duration_seconds": round(time.perf_counter() - t0, 3),
                "device": ENGINE.device,
            }

        res = await ENGINE.run_vision_single(
            images_b64=b64_images,
            prompt=req.prompt,
            model=req.model,
            system_prompt=req.system_prompt,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            json_mode=req.json_mode,
        )
        return {
            **res,
            "total_duration_seconds": round(time.perf_counter() - t0, 3),
            "device": ENGINE.device,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/vision/batch")
async def vision_batch_endpoint(req: VisionBatchRequest):
    """Dedicated batch visual understanding endpoint for MiniCPM-V 4.6."""
    t0 = time.perf_counter()
    try:
        batch_items: list[dict[str, Any]] = []
        if req.items:
            for it in req.items:
                srcs = list(it.images)
                if it.image_base64:
                    srcs.insert(0, it.image_base64)
                elif it.image_url:
                    srcs.insert(0, it.image_url)
                if not srcs:
                    raise ValueError("Each VisionItem must include `image_base64`, `image_url`, or `images`.")
                batch_items.append(
                    {
                        "images_b64": [normalize_to_raw_base64(s) for s in srcs],
                        "prompt": it.prompt or req.prompt,
                        "system_prompt": it.system_prompt or req.system_prompt,
                        "temperature": it.temperature,
                        "max_tokens": it.max_tokens,
                        "json_mode": it.json_mode,
                    }
                )
        elif req.images:
            if req.mode == "multi_image":
                batch_items.append(
                    {
                        "images_b64": [normalize_to_raw_base64(s) for s in req.images],
                        "prompt": req.prompt,
                        "system_prompt": req.system_prompt,
                        "temperature": req.temperature,
                        "max_tokens": req.max_tokens,
                        "json_mode": req.json_mode,
                    }
                )
            else:
                for s in req.images:
                    batch_items.append(
                        {
                            "images_b64": [normalize_to_raw_base64(s)],
                            "prompt": req.prompt,
                            "system_prompt": req.system_prompt,
                            "temperature": req.temperature,
                            "max_tokens": req.max_tokens,
                            "json_mode": req.json_mode,
                        }
                    )
        else:
            raise ValueError("Provide either `items` or `images` in VisionBatchRequest.")

        results = await ENGINE.run_vision_batch(batch_items, model=req.model)
        return {
            "batch_size": len(results),
            "results": results,
            "total_duration_seconds": round(time.perf_counter() - t0, 3),
            "device": ENGINE.device,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/chat")
@app.post("/v1/chat/completions")
async def proxy_ollama_chat(request: FastAPIRequest):
    """GPU-gated proxy to Ollama `/api/chat` or `/v1/chat/completions` so standard Ollama/OpenAI
    clients can query `minicpm-v4.6` over the Cloudflared/Tailscale tunnel while respecting GPU thresholds."""
    ENGINE._ensure_async_primitives()
    assert ENGINE._vision_sem is not None
    body = await request.body()
    path = request.url.path
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        payload = {}
    payload.setdefault("model", ENGINE.ollama.model_name)
    if path == "/api/chat":
        payload.setdefault("stream", False)

    if ENGINE.queued_vision_jobs >= ENGINE.max_queue_size:
        raise HTTPException(status_code=429, detail="Vision GPU queue is full.")

    ENGINE.queued_vision_jobs += 1
    try:
        async with ENGINE._vision_sem:
            async with ENGINE._acquire_gpu_slot():
                ENGINE.active_vision_jobs += 1
                try:
                    def _forward():
                        req = Request(
                            f"{ENGINE.ollama.host_url}{path}",
                            data=json.dumps(payload).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(req, timeout=300) as resp:
                            return json.loads(resp.read().decode("utf-8"))

                    data = await asyncio.to_thread(_forward)
                    ENGINE.total_vision_images += 1
                    return JSONResponse(content=data)
                except Exception as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
                finally:
                    ENGINE.active_vision_jobs -= 1
    finally:
        ENGINE.queued_vision_jobs -= 1


# ==============================================================================
# Bootstrap & CLI Entrypoint
# ==============================================================================

def bootstrap_dependencies() -> None:
    """Install required Python packages if running on a fresh cloud VM with `--bootstrap`."""
    pkgs = [
        "fastapi",
        "uvicorn",
        "pydantic",
        "pillow",
        "numpy",
        "opencv-python-headless",
        "diffusers",
        "transformers",
        "accelerate",
        "huggingface_hub",
        "einops",
        "timm",
        "omegaconf",
        "pyyaml",
        "safetensors",
    ]
    print("[Bootstrap] Ensuring Python dependencies are installed ...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs], check=True)


def main(argv: list[str] | None = None) -> int:
    global ENGINE, TUNNEL_MANAGER, TAILSCALE_MANAGER, PRELOAD_ON_STARTUP, AUTO_INSTALL_OLLAMA

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="Server bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Server bind port (default: 8000)")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "mps", "cpu"], help="Accelerator device")
    parser.add_argument("--moebius-repo", default=str(DEFAULT_MOEBIUS_REPO), help="Path to hustvl/Moebius checkout")
    parser.add_argument("--ollama-host", default=DEFAULT_OLLAMA_HOST, help="Local Ollama daemon URL")
    parser.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL, help="Ollama vision model tag (default: minicpm-v4.6)")
    parser.add_argument("--max-gpu-active", type=int, default=MAX_GPU_ACTIVE, help="Max simultaneous GPU tasks across models")
    parser.add_argument("--max-inpaint-batch", type=int, default=MAX_INPAINT_BATCH, help="Max images per Moebius GPU forward pass")
    parser.add_argument("--batch-window-ms", type=int, default=BATCH_WINDOW_MS, help="Dynamic micro-batch coalescing window (ms)")
    parser.add_argument("--max-vision-concurrency", type=int, default=MAX_VISION_CONCURRENCY, help="Max parallel MiniCPM-V 4.6 GPU requests")
    parser.add_argument("--max-vision-batch", type=int, default=MAX_VISION_BATCH, help="Max images per MiniCPM-V 4.6 batch chunk")
    parser.add_argument("--gpu-vram-threshold", type=float, default=GPU_VRAM_THRESHOLD, help="Max GPU VRAM utilization ratio (0.3-0.98)")
    parser.add_argument("--max-queue-size", type=int, default=MAX_QUEUE_SIZE, help="Max pending requests before HTTP 429 backpressure")
    parser.add_argument("--cloudflared", action="store_true", help="Expose server publicly via Cloudflared tunnel")
    parser.add_argument("--cloudflared-token", default=os.environ.get("CLOUDFLARED_TOKEN"), help="Optional Cloudflare Zero Trust tunnel token")
    parser.add_argument("--cloudflared-protocol", default=os.environ.get("CLOUDFLARED_PROTOCOL", "http2"), choices=["http2", "quic", "auto"], help="Cloudflared transport protocol (default: http2 for fast Colab/Kaggle startup)")
    parser.add_argument("--cloudflared-url-file", default=str(DEFAULT_CLOUDFLARED_URL_FILE), help="File to write public trycloudflare URL")
    parser.add_argument("--tailscale", action="store_true", help="Expose server over Tailscale (uses userspace-networking for Colab/Kaggle containers)")
    parser.add_argument("--tailscale-authkey", default=os.environ.get("TAILSCALE_AUTHKEY"), help="Tailscale reusable/ephemeral auth key (tskey-auth-...)")
    parser.add_argument("--tailscale-hostname", default=os.environ.get("TAILSCALE_HOSTNAME", "moebius-gpu"), help="Tailscale node hostname (default: moebius-gpu)")
    parser.add_argument("--tailscale-funnel", action="store_true", help="Enable public HTTPS Tailscale Funnel on port 443")
    parser.add_argument("--env-file", default=os.environ.get("ENV_FILE"), help="Path to .env file with secrets (e.g. TAILSCALE_AUTHKEY, CLOUDFLARED_TOKEN)")
    parser.add_argument("--bootstrap", action="store_true", help="Auto-install Python deps, Ollama, and tunnel binaries on startup")
    parser.add_argument("--skip-preload", action="store_true", help="Skip background model preloading at startup")

    args = parser.parse_args(argv)

    if args.env_file:
        load_dotenv_and_secrets(args.env_file)
        args.cloudflared_token = args.cloudflared_token or os.environ.get("CLOUDFLARED_TOKEN")
        args.tailscale_authkey = args.tailscale_authkey or os.environ.get("TAILSCALE_AUTHKEY")
        if os.environ.get("TAILSCALE_HOSTNAME") and args.tailscale_hostname == "moebius-gpu":
            args.tailscale_hostname = os.environ["TAILSCALE_HOSTNAME"]

    if args.bootstrap:
        AUTO_INSTALL_OLLAMA = True
        bootstrap_dependencies()

    PRELOAD_ON_STARTUP = not args.skip_preload

    ollama_mgr = OllamaManager(
        host_url=args.ollama_host,
        model_name=args.ollama_model,
        num_parallel=args.max_vision_concurrency,
    )
    ENGINE = GPUInferenceEngine(
        moebius_repo=Path(args.moebius_repo),
        ollama_manager=ollama_mgr,
        device=args.device,
        max_gpu_active=args.max_gpu_active,
        max_inpaint_batch=args.max_inpaint_batch,
        batch_window_ms=args.batch_window_ms,
        max_vision_concurrency=args.max_vision_concurrency,
        max_vision_batch=args.max_vision_batch,
        gpu_vram_threshold=args.gpu_vram_threshold,
        max_queue_size=args.max_queue_size,
    )

    enable_cf = args.cloudflared or os.environ.get("ENABLE_CLOUDFLARED", "0") == "1"
    if enable_cf:
        TUNNEL_MANAGER = CloudflaredManager(
            local_port=args.port,
            token=args.cloudflared_token,
            url_file=Path(args.cloudflared_url_file),
            protocol=args.cloudflared_protocol,
        )
        TUNNEL_MANAGER.start(timeout_seconds=35.0)

    enable_ts = args.tailscale or os.environ.get("ENABLE_TAILSCALE", "0") == "1" or bool(args.tailscale_authkey)
    if enable_ts:
        TAILSCALE_MANAGER = TailscaleManager(
            local_port=args.port,
            auth_key=args.tailscale_authkey,
            hostname=args.tailscale_hostname,
            enable_funnel=args.tailscale_funnel or os.environ.get("TAILSCALE_FUNNEL", "0") == "1",
        )
        TAILSCALE_MANAGER.start()

    print("\n" + "=" * 72)
    print(f"🚀 Moebius + MiniCPM-V 4.6 GPU Server starting on http://{args.host}:{args.port}")
    print(f"   • Accelerator Device    : {ENGINE.device}")
    print(f"   • Max Active GPU Tasks  : {ENGINE.max_gpu_active} (VRAM Threshold: {int(ENGINE.gpu_vram_threshold * 100)}%)")
    print(f"   • Moebius Inpaint Batch : max {ENGINE.max_inpaint_batch} images/pass ({ENGINE.batch_window_ms}ms coalescing window)")
    print(f"   • MiniCPM-V 4.6 Vision  : model={ollama_mgr.model_name}, max_concurrency={ENGINE.max_vision_concurrency}")
    if TUNNEL_MANAGER and TUNNEL_MANAGER.public_url:
        print(f"   • Cloudflared Public URL: {TUNNEL_MANAGER.public_url}")
    if TAILSCALE_MANAGER and TAILSCALE_MANAGER.tailscale_url:
        print(f"   • Tailscale Direct URL  : {TAILSCALE_MANAGER.tailscale_url} (http://{TAILSCALE_MANAGER.hostname}:{args.port})")
    if TAILSCALE_MANAGER and TAILSCALE_MANAGER.funnel_url:
        print(f"   • Tailscale Funnel URL  : {TAILSCALE_MANAGER.funnel_url}")
    print("=" * 72 + "\n")

    try:
        asyncio.get_running_loop()
        loop_already_running = True
    except RuntimeError:
        loop_already_running = False

    if loop_already_running:
        import threading
        srv_thread = threading.Thread(
            target=uvicorn.run,
            args=(app,),
            kwargs={"host": args.host, "port": args.port},
            daemon=True,
        )
        srv_thread.start()
        srv_thread.join()
    else:
        uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
