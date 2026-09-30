"""Asynchronous CPU input-stage prefetching for pipelined batch execution."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Sequence
from PIL import Image

from manga_translator.config import Config


def _load_single_image(source: Any) -> Image.Image:
    """Synchronously load, decode, and normalize an image on CPU."""
    if callable(source):
        result = source()
        if isinstance(result, Image.Image):
            result.load()
            return result
        source = result

    if isinstance(source, (str, Path)):
        with Image.open(source) as opened:
            img = opened.convert("RGB")
            img.load()
            return img

    if isinstance(source, Image.Image):
        source.load()
        return source

    raise TypeError(f"Unsupported image input type: {type(source).__name__}")


async def prefetch_input_stream(
    items_with_configs: Sequence[tuple[Any, Config]],
    *,
    start_index: int = 0,
    max_prefetch: int = 2,
) -> AsyncIterator[tuple[int, Image.Image | None, Config, Exception | None]]:
    """
    Prefetch and decode images asynchronously on CPU worker threads while downstream
    stages (like GPU text detection) process preceding images.

    Yields (global_index, image, config, error).
    """
    if not items_with_configs:
        return

    queue: asyncio.Queue[tuple[int, Image.Image | None, Config, Exception | None] | None] = asyncio.Queue(
        maxsize=max(1, max_prefetch)
    )

    async def producer() -> None:
        for offset, (source, config) in enumerate(items_with_configs):
            idx = start_index + offset
            try:
                img = await asyncio.to_thread(_load_single_image, source)
                await queue.put((idx, img, config, None))
            except Exception as exc:
                await queue.put((idx, None, config, exc))
        await queue.put(None)

    producer_task = asyncio.create_task(producer())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        if not producer_task.done():
            producer_task.cancel()
            try:
                await producer_task
            except (asyncio.CancelledError, Exception):
                pass
