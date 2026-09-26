"""Worker process setup and resource sizing for the API server."""

import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from argparse import Namespace
from typing import Any, Callable

from server.instance import ExecutorInstance


def generate_nonce() -> str:
    return secrets.token_hex(16)


def initialize_server_environment(
    args,
    *,
    result_root,
    upload_cache_dir,
    nonce_factory: Callable[[], str],
    set_nonce: Callable[[str], None],
) -> None:
    if args.nonce is None:
        set_nonce(os.getenv("MT_WEB_NONCE", nonce_factory()))
    else:
        set_nonce(args.nonce)
    os.environ["MANGA_RESULT_ROOT"] = str(result_root)
    if os.path.exists(upload_cache_dir):
        import shutil
        shutil.rmtree(upload_cache_dir)
    os.makedirs(upload_cache_dir, exist_ok=True)


def cpu_threads_per_worker(cpu_count: int, num_workers: int) -> int:
    """Leave one CPU available for the API while translation is running."""
    return max(1, (max(1, cpu_count) - 1) // max(1, num_workers))


def cpu_stage_worker_count(
    num_workers: int, configured: int | None = None, cpu_count: int | None = None,
) -> int:
    workers = max(1, num_workers)
    if configured is not None:
        return min(workers, max(1, configured))
    # ponytail: keep two page-stage slots when two pipelines are requested; the API shares those cores.
    cpu_budget = max(2, (cpu_count or os.cpu_count() or 4) - 1)
    return min(workers, 3, cpu_budget)


def start_translator_client_proc(
    host: str,
    port: int,
    nonce: str,
    params: Namespace,
    worker_id: int = 0,
    gpu_id: str | None = None,
    *,
    result_root,
    register_executor: Callable[[ExecutorInstance], Any],
):
    cmds = [
        sys.executable,
        "-m", "manga_translator",
        "shared",
        "--host", host,
        "--port", str(port),
        "--nonce", nonce,
    ]
    if params.use_gpu:
        cmds.append("--use-gpu")
    if params.use_gpu_limited:
        cmds.append("--use-gpu-limited")
    if params.ignore_errors:
        cmds.append("--ignore-errors")
    if params.verbose:
        cmds.append("--verbose")
    cmds.append("--models-ttl=%s" % getattr(params, "models_ttl", 120))
    if getattr(params, "pre_dict", None):
        cmds.extend(["--pre-dict", params.pre_dict])
    if getattr(params, "post_dict", None):
        cmds.extend(["--post-dict", params.post_dict])
    base_path = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(base_path)

    env = os.environ.copy()
    env["MANGA_RESULT_ROOT"] = str(result_root)
    if gpu_id is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    # Prevent PyTorch/OpenCV in worker from exhausting all CPU cores and starving the main server
    env.setdefault("OMP_NUM_THREADS", "4")
    env.setdefault("OPENBLAS_NUM_THREADS", "4")
    env.setdefault("MKL_NUM_THREADS", "4")
    env.setdefault("VECLIB_MAXIMUM_THREADS", "4")
    env.setdefault("NUMEXPR_NUM_THREADS", "4")

    proc = subprocess.Popen(cmds, cwd=parent, env=env)
    register_executor(ExecutorInstance(ip=host, port=port, worker_id=worker_id))
    return proc


def setup_inprocess_workers(
    args,
    num_workers: int,
    model_concurrency: int,
    *,
    result_root,
    register_executor: Callable[[Any], Any],
    logger,
    cpu_threads_per_worker_fn: Callable[[int, int], int],
):
    import torch
    import cv2
    from server.in_process_executor import InProcessExecutorInstance
    from manga_translator.utils.model_cache import SharedModelExecutor

    cpu_count = os.cpu_count() or 4
    threads_per_worker = cpu_threads_per_worker_fn(cpu_count, num_workers)
    try:
        torch.set_num_threads(threads_per_worker)
        torch.set_num_interop_threads(1)
    except Exception:
        pass
    try:
        cv2.setNumThreads(1)
    except Exception:
        pass

    inpainting_concurrency = getattr(args, "inpainting_concurrency", 0)
    if inpainting_concurrency > 0:
        from manga_translator.inpainting import set_inpainting_concurrency
        set_inpainting_concurrency(inpainting_concurrency)

    translator_params = {
        "verbose": args.verbose,
        "ignore_errors": args.ignore_errors,
        "models_ttl": args.models_ttl,
        "pre_dict": getattr(args, "pre_dict", None),
        "post_dict": getattr(args, "post_dict", None),
        "use_gpu": args.use_gpu,
        "use_gpu_limited": args.use_gpu_limited,
        "result_root": str(result_root),
    }

    model_executor = SharedModelExecutor(max_concurrent_calls=model_concurrency)
    logger.info(
        f"Starting {num_workers} in-process image pipeline(s) with shared models, "
        f"up to {model_concurrency} concurrent model calls, and "
        f"{threads_per_worker} CPU thread(s) per pipeline..."
    )
    for i in range(num_workers):
        instance = InProcessExecutorInstance(
            worker_id=i,
            translator_params=translator_params,
            model_executor=model_executor,
        )
        register_executor(instance)

    logger.info(f"Ready: {num_workers} in-process parallel translator slot(s) active.")
    return []


def supervise_subprocess_workers(
    worker_procs: list[dict[str, Any]],
    *,
    is_shutting_down: Callable[[], bool],
    get_nonce: Callable[[], str],
    start_worker: Callable[..., Any],
    logger,
    interval: float,
) -> None:
    while not is_shutting_down():
        time.sleep(interval)
        if is_shutting_down():
            break
        for worker in worker_procs:
            if is_shutting_down():
                break
            proc = worker["proc"]
            if proc.poll() is not None:
                logger.warning(
                    f"[Worker Supervisor] Worker {worker['worker_id']} "
                    f"(port {worker['port']}) exited with code {proc.returncode}. Restarting..."
                )
                try:
                    new_proc = start_worker(
                        worker["host"],
                        worker["port"],
                        get_nonce(),
                        worker["args"],
                        worker_id=worker["worker_id"],
                        gpu_id=worker["gpu_id"],
                    )
                    worker["proc"] = new_proc
                    logger.info(f"[Worker Supervisor] Worker {worker['worker_id']} restarted successfully.")
                except Exception as error:
                    logger.error(f"[Worker Supervisor] Error restarting worker {worker['worker_id']}: {error}")


def setup_subprocess_workers(
    args,
    num_workers: int,
    *,
    get_nonce: Callable[[], str],
    worker_procs: list[dict[str, Any]],
    start_worker: Callable[..., Any],
    supervise: Callable[[], None],
    set_shutting_down: Callable[[], None],
    logger,
):
    gpu_ids = None
    if getattr(args, "gpu_ids", None):
        gpu_ids = [gpu.strip() for gpu in args.gpu_ids.split(",") if gpu.strip()]

    logger.info(f"Starting {num_workers} parallel translator worker process(es)...")
    for i in range(num_workers):
        worker_port = args.port + 1 + i
        gpu_id = gpu_ids[i % len(gpu_ids)] if gpu_ids else None
        worker_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
        proc = start_worker(
            worker_host,
            worker_port,
            get_nonce(),
            args,
            worker_id=i,
            gpu_id=gpu_id,
        )
        worker_procs.append({
            "proc": proc,
            "worker_id": i,
            "port": worker_port,
            "gpu_id": gpu_id,
            "host": worker_host,
            "args": args,
        })

    supervisor_thread = threading.Thread(target=supervise, daemon=True)
    supervisor_thread.start()

    def handle_exit_signals(signum, frame):
        set_shutting_down()
        logger.info("Shutting down translator worker processes...")
        for worker in worker_procs:
            try:
                worker["proc"].terminate()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit_signals)
    signal.signal(signal.SIGTERM, handle_exit_signals)

    return [worker["proc"] for worker in worker_procs]
