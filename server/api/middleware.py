"""HTTP middleware shared by the server API."""

import asyncio
import secrets
import time

from fastapi import Request

from server.logger import correlation_id_ctx, get_logger

logger = get_logger("server")

# ponytail: one process-local gate is enough for the local server; use upload sessions if concurrent imports matter.
_original_import_lock = asyncio.Lock()


async def serialize_original_manga_imports(request: Request, call_next):
    if request.url.path in {"/results/import", "/api/results/import"}:
        async with _original_import_lock:
            return await call_next(request)
    return await call_next(request)


async def correlation_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id")
    if not request_id:
        request_id = f"req-{secrets.token_hex(4)}"
    token = correlation_id_ctx.set(request_id)
    start_time = time.perf_counter()
    client_ip = request.client.host if request.client else "-"
    logger.info(f"{request.method} {request.url.path} from {client_ip}")

    try:
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({elapsed_ms:.1f}ms)")
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.error(f"{request.method} {request.url.path} -> Exception: {exc} ({elapsed_ms:.1f}ms)")
        raise
    finally:
        correlation_id_ctx.reset(token)
