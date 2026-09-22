import asyncio
import json
import pickle
from typing import Mapping, Optional, Callable

import aiohttp
from PIL.Image import Image
from fastapi import HTTPException

from manga_translator import Config

NotifyType = Optional[Callable[[int, Optional[bytes]], None]]

_client_session: Optional[aiohttp.ClientSession] = None

def get_client_session() -> aiohttp.ClientSession:
    global _client_session
    if _client_session is None or _client_session.closed:
        _client_session = aiohttp.ClientSession()
    return _client_session

async def close_client_session():
    global _client_session
    if _client_session is not None and not _client_session.closed:
        await _client_session.close()
        _client_session = None

async def fetch_data_stream(url, image, config: Optional[Config] = None, sender: NotifyType = None, headers: Mapping[str, str] = None):
    if headers is None:
        headers = {}
    if isinstance(image, dict):
        attributes = image
    else:
        attributes = {"image": image, "config": config}
    data = await asyncio.to_thread(pickle.dumps, attributes)

    session = get_client_session()
    async with session.post(url, data=data, headers=headers) as response:
        if response.status == 200:
            await process_stream(response, sender)
        else:
            raise HTTPException(response.status, detail=await response.text())

async def fetch_data(url, image, config: Optional[Config] = None, headers: Mapping[str, str] = None):
    if headers is None:
        headers = {}
    if isinstance(image, dict):
        attributes = image
    else:
        attributes = {"image": image, "config": config}
    data = await asyncio.to_thread(pickle.dumps, attributes)

    session = get_client_session()
    async with session.post(url, data=data, headers=headers) as response:
        if response.status == 200:
            raw_bytes = await response.read()
            try:
                return await asyncio.to_thread(pickle.loads, raw_bytes)
            except Exception as pe:
                try:
                    return json.loads(raw_bytes.decode('utf-8'))
                except Exception as je:
                    raise HTTPException(502, detail=f'Invalid response from upstream: {pe}')
        else:
            raise HTTPException(response.status, detail=await response.text())

async def process_stream(response, sender: NotifyType):
    buffer = b''

    async for chunk in response.content.iter_any():
        if chunk:
            buffer += chunk
            buffer = handle_buffer(buffer, sender)



def handle_buffer(buffer, sender: NotifyType):
    while len(buffer) >= 5:
        status, expected_size = extract_header(buffer)

        if len(buffer) >= 5 + expected_size:
            data = buffer[5:5 + expected_size]
            sender(status, data)
            buffer = buffer[5 + expected_size:]
        else:
            break
    return buffer


def extract_header(buffer):
    """Extract the status and expected size from the buffer."""
    status = int.from_bytes(buffer[0:1], byteorder='big')
    expected_size = int.from_bytes(buffer[1:5], byteorder='big')
    return status, expected_size

