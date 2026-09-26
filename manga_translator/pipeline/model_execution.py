"""Model-call execution policy used by the translator facade."""

import asyncio
import threading
import torch


class _MPSLock:
    def __init__(self):
        self._lock = threading.Lock()

    async def __aenter__(self):
        await asyncio.to_thread(self._lock.acquire)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if torch.backends.mps.is_available():
            try:
                torch.mps.synchronize()
            except Exception:
                pass
        self._lock.release()

_GLOBAL_MPS_LOCK = _MPSLock()


async def mps_call(owner, coro_fn, args, kwargs, *, logger, get_model_executor_fn, global_mps_lock):
    async def _execute():
        try:
            return await coro_fn(*args, **kwargs)
        except (RuntimeError, NotImplementedError) as exc:
            err_str = str(exc)
            unsupported_mps_keywords = (
                'not implemented for', 'not supported on mps',
                'operator does not have a metal kernel',
            )
            if owner.device == 'mps' and any(kw in err_str.lower() for kw in unsupported_mps_keywords):
                logger.warning(
                    f'MPS not supported for this model ({type(exc).__name__}: {exc!s}). '
                    'Falling back to CPU for this model call.'
                )
                # Replace the device argument in the call and retry on CPU without permanently overriding owner.device
                new_args = tuple('cpu' if isinstance(a, str) and a == 'mps' else a for a in args)
                new_kwargs = {k: ('cpu' if isinstance(v, str) and v == 'mps' else v) for k, v in kwargs.items()}
                return await coro_fn(*new_args, **new_kwargs)
            raise

    # Keep the standalone MPS guard separate from shared in-process model work.
    if owner.device == 'mps' and get_model_executor_fn() is None:
        async with global_mps_lock:
            return await _execute()
    else:
        return await _execute()
