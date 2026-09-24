import gc
import logging
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
_MEMORY_LOGGER = logging.getLogger('manga_translator.memory')


def empty_device_cache(
    device: Optional[str] = None,
    synchronize: bool = True,
    collect_twice: bool = False,
    memory_label: Optional[str] = None,
    batch_id: Optional[str] = None,
    page_id: Optional[str] = None,
) -> None:
    """
    Safely releases unused cached memory across all supported PyTorch accelerators
    (CUDA, Apple Silicon MPS, Intel XPU) and runs Python garbage collection.
    """
    before_gc = log_memory_stats(
        f"{memory_label}:gc_before", device=device, batch_id=batch_id, page_id=page_id
    ) if memory_label else None
    gc.collect()
    if memory_label:
        log_memory_stats(
            f"{memory_label}:gc_collect",
            device=device,
            batch_id=batch_id,
            page_id=page_id,
            before=before_gc,
        )

    if torch is None:
        if collect_twice:
            before_gc = log_memory_stats(
                f"{memory_label}:gc_second_before", device=device, batch_id=batch_id, page_id=page_id
            ) if memory_label else None
            gc.collect()
            if memory_label:
                log_memory_stats(
                    f"{memory_label}:gc_second_collect",
                    device=device,
                    batch_id=batch_id,
                    page_id=page_id,
                    before=before_gc,
                )
        return

    with DEVICE_MEMORY_LOCK:
        # 1. CUDA (NVIDIA)
        if torch.cuda.is_available() and (device is None or str(device).startswith('cuda')):
            try:
                if synchronize:
                    torch.cuda.synchronize()
                torch.cuda.empty_cache()
            except Exception:
                pass

        # 2. MPS (Apple Silicon Metal)
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available() and (device is None or str(device) == 'mps'):
            try:
                if hasattr(torch, 'mps') and hasattr(torch.mps, 'empty_cache'):
                    before_mps = log_memory_stats(
                        f"{memory_label}:mps_before",
                        device=device,
                        batch_id=batch_id,
                        page_id=page_id,
                    ) if memory_label else None
                    if synchronize and hasattr(torch.mps, 'synchronize'):
                        torch.mps.synchronize()
                        if memory_label:
                            before_mps = log_memory_stats(
                                f"{memory_label}:mps_synchronized",
                                device=device,
                                batch_id=batch_id,
                                page_id=page_id,
                                before=before_mps,
                            )
                    torch.mps.empty_cache()
                    if memory_label:
                        log_memory_stats(
                            f"{memory_label}:mps_empty_cache",
                            device=device,
                            batch_id=batch_id,
                            page_id=page_id,
                            before=before_mps,
                        )
                elif synchronize and hasattr(torch, 'mps') and hasattr(torch.mps, 'synchronize'):
                    torch.mps.synchronize()
            except Exception:
                pass

        # 3. XPU (Intel)
        if hasattr(torch.backends, 'xpu') and torch.xpu.is_available() and (device is None or str(device).startswith('xpu')):
            try:
                if synchronize:
                    torch.xpu.synchronize()
                torch.xpu.empty_cache()
            except Exception:
                pass

    if collect_twice:
        before_gc = log_memory_stats(
            f"{memory_label}:gc_second_before", device=device, batch_id=batch_id, page_id=page_id
        ) if memory_label else None
        gc.collect()
        if memory_label:
            log_memory_stats(
                f"{memory_label}:gc_second_collect",
                device=device,
                batch_id=batch_id,
                page_id=page_id,
                before=before_gc,
            )

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

    stats['python_gc_objects'] = len(gc.get_objects())

    if psutil is not None:
        try:
            stats['process_rss_mb'] = round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 2)
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


def log_memory_stats(
    stage: str,
    *,
    device: Optional[str] = None,
    batch_id: Optional[str] = None,
    page_id: Optional[str] = None,
    before: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Log a memory boundary and return its snapshot for a later comparison."""
    try:
        after = get_memory_stats(device)
        rss_before = before.get('process_rss_mb') if before else None
        rss_after = after.get('process_rss_mb')
        delta = rss_after - rss_before if rss_before is not None and rss_after is not None else None
        fields = [
            f"batch_id={batch_id or '-'}",
            f"page_id={page_id or '-'}",
            f"stage={stage}",
            f"rss_before={rss_before if rss_before is not None else '-'}MB",
            f"rss_after={rss_after if rss_after is not None else '-'}MB",
            f"rss_delta={delta:+.2f}MB" if delta is not None else "rss_delta=-",
            f"gc_objects={after.get('python_gc_objects', '-')}",
        ]
        for label, key in (
            ('mps_allocated', 'mps_allocated_mb'),
            ('mps_driver_allocated', 'mps_driver_allocated_mb'),
        ):
            value = after.get(key)
            previous = before.get(key) if before else None
            fields.append(f"{label}_before={previous if previous is not None else '-'}MB")
            fields.append(f"{label}_after={value if value is not None else '-'}MB")
        _MEMORY_LOGGER.info('[MEM] %s', ' '.join(fields))
        return after
    except Exception:
        _MEMORY_LOGGER.debug('Unable to collect memory stats for %s', stage, exc_info=True)
        return {}
