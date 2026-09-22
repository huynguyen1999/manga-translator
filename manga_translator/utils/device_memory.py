import gc
import os
import sys
import threading
from typing import Optional, Dict, Any

try:
    import torch
except ImportError:
    torch = None

try:
    import psutil
except ImportError:
    psutil = None

DEVICE_MEMORY_LOCK = threading.RLock()


def empty_device_cache(device: Optional[str] = None, synchronize: bool = True) -> None:
    """
    Safely releases unused cached memory across all supported PyTorch accelerators
    (CUDA, Apple Silicon MPS, Intel XPU) and runs Python garbage collection.
    """
    gc.collect()

    if torch is None:
        return

    with DEVICE_MEMORY_LOCK:
        # 1. CUDA (NVIDIA)
        if torch.cuda.is_available() and (device is None or str(device).startswith('cuda')):
            try:
                torch.cuda.empty_cache()
                if synchronize:
                    torch.cuda.synchronize()
            except Exception:
                pass

        # 2. MPS (Apple Silicon Metal)
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available() and (device is None or str(device) == 'mps'):
            try:
                if hasattr(torch, 'mps') and hasattr(torch.mps, 'empty_cache'):
                    torch.mps.empty_cache()
                if synchronize and hasattr(torch, 'mps') and hasattr(torch.mps, 'synchronize'):
                    torch.mps.synchronize()
            except Exception:
                pass

        # 3. XPU (Intel)
        if hasattr(torch.backends, 'xpu') and torch.xpu.is_available() and (device is None or str(device).startswith('xpu')):
            try:
                torch.xpu.empty_cache()
                if synchronize:
                    torch.xpu.synchronize()
            except Exception:
                pass

    # Windows working set trim
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.kernel32.SetProcessWorkingSetSize(-1, -1, -1)
        except Exception:
            pass


def configure_device_memory_limits(device: Optional[str] = None) -> None:
    """
    Configures device memory limits to prevent runaway memory usage and swap thrashing.
    """
    if torch is None:
        return

    # Apple Silicon MPS memory limit configuration
    if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available() and (device is None or str(device) == 'mps'):
        try:
            if hasattr(torch, 'mps') and hasattr(torch.mps, 'set_per_process_memory_fraction'):
                fraction_str = os.getenv('MANGA_MPS_MEMORY_FRACTION', '0.75')
                fraction = float(fraction_str)
                if 0.1 <= fraction <= 1.0:
                    torch.mps.set_per_process_memory_fraction(fraction)
        except Exception:
            pass


def get_memory_stats(device: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns diagnostic memory usage statistics for logging.
    """
    stats = {}

    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
            stats['system_percent'] = vm.percent
            stats['system_available_mb'] = vm.available // (1024 * 1024)
            stats['system_used_mb'] = vm.used // (1024 * 1024)
        except Exception:
            pass

    if torch is not None:
        if torch.cuda.is_available() and (device is None or str(device).startswith('cuda')):
            try:
                stats['cuda_allocated_mb'] = round(torch.cuda.memory_allocated() / (1024 * 1024), 2)
                stats['cuda_reserved_mb'] = round(torch.cuda.memory_reserved() / (1024 * 1024), 2)
            except Exception:
                pass

        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available() and (device is None or str(device) == 'mps'):
            try:
                if hasattr(torch, 'mps'):
                    if hasattr(torch.mps, 'current_allocated_memory'):
                        stats['mps_allocated_mb'] = round(torch.mps.current_allocated_memory() / (1024 * 1024), 2)
                    if hasattr(torch.mps, 'driver_allocated_memory'):
                        stats['mps_driver_allocated_mb'] = round(torch.mps.driver_allocated_memory() / (1024 * 1024), 2)
            except Exception:
                pass

    return stats
