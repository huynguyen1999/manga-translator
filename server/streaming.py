import asyncio
import pickle

async def stream(messages):
    while True:
        message = await messages.get()
        yield message
        if message[0] == 0 or message[0] == 2:
            break

def notify(code: int, data: bytes, transform_to_bytes, messages: asyncio.Queue):
    if code == 0:
        def _process():
            try:
                obj = pickle.loads(data)
                return transform_to_bytes(obj)
            except Exception as e:
                return str(e).encode('utf-8')

        async def _async_process():
            result_bytes = await asyncio.to_thread(_process)
            encoded_result = b'\x00' + len(result_bytes).to_bytes(4, 'big') + result_bytes
            await messages.put(encoded_result)

        asyncio.create_task(_async_process())
    else:
        encoded_result = code.to_bytes(1, 'big') + len(data).to_bytes(4, 'big') + data
        messages.put_nowait(encoded_result)