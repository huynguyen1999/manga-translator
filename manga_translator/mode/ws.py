import asyncio
import io
import logging
import os
from threading import Thread
from typing import Tuple

import cv2
import numpy as np
from PIL import Image

from manga_translator import logger, Context, MangaTranslator, Config
from manga_translator.utils import PriorityLock, Throttler
from .constants import (
    WS_STATUS_PENDING,
    WS_STATUS_DOWNLOADING,
    WS_STATUS_ERROR_DOWNLOAD,
    WS_STATUS_PREPARING,
    WS_STATUS_SAVING,
    WS_STATUS_UPLOADING,
    WS_STATUS_ERROR_UPLOAD,
    WS_HEADER_SECRET,
    WS_MSG_TYPE_NEW_TASK,
    WS_MAX_MESSAGE_SIZE,
    WS_DEFAULT_TIMEOUT_SEC,
    WS_THROTTLE_INTERVAL_SEC,
    WS_FILE_FINAL,
    WS_FILE_RENDER_IN,
    WS_FILE_RENDER_OUT,
    WS_FILE_MASK,
    WS_FILE_INMASK,
    WS_FILE_OUTPUT,
)


class MangaTranslatorWS(MangaTranslator):
    def __init__(self, params: dict = None):
        super().__init__(params)
        self.url = params.get('ws_url')
        self.secret = params.get('ws_secret', os.getenv('WS_SECRET', ''))
        self.ignore_errors = params.get('ignore_errors', True)

        self._task_id = None
        self._websocket = None
        self._server_loop = None
        self.task_lock = None
        self.counter = 0
        self.send_throttler = None
        self._send_and_yield = None

    @staticmethod
    async def _send_and_yield_raw(websocket, msg):
        # send message and yield control to the event loop (to actually send the message)
        await websocket.send(msg)
        await asyncio.sleep(0)

    @staticmethod
    async def _send_status(websocket, task_id: str, status: str):
        from ..server import ws_pb2
        msg = ws_pb2.WebSocketMessage()
        msg.status.id = task_id
        msg.status.status = status
        await websocket.send(msg.SerializeToString())
        await asyncio.sleep(0)

    async def _sync_state(self, state, finished):
        if self._websocket is None:
            return
        from ..server import ws_pb2
        msg = ws_pb2.WebSocketMessage()
        msg.status.id = self._task_id
        msg.status.status = state
        self._server_loop.call_soon_threadsafe(
            asyncio.create_task,
            self._send_and_yield(self._websocket, msg.SerializeToString())
        )

    async def _translate_ws_task(self, task_id, websocket, image, params):
        async with self.task_lock((1 << 31) - params['ws_count']):
            self._task_id = task_id
            self._websocket = websocket
            result = await self.translate(image, params)
            self._task_id = None
            self._websocket = None
        return result

    async def _download_task_image(self, session, task, websocket, logger_task):
        logger_task.info(f'-- Downloading image from {task.source_image}')
        await self._send_status(websocket, task.id, WS_STATUS_DOWNLOADING)
        async with session.get(task.source_image) as resp:
            if resp.status != 200:
                await self._send_status(websocket, task.id, WS_STATUS_ERROR_DOWNLOAD)
                return None
            return await resp.read()

    def _build_task_params(self, task, translation_params):
        params = {
            'target_lang': task.target_language,
            'skip_lang': task.skip_language,
            'detector': task.detector,
            'direction': task.direction,
            'translator': task.translator,
            'size': task.size,
            'ws_event_loop': asyncio.get_event_loop(),
            'ws_count': self.counter,
        }
        self.counter += 1

        if translation_params:
            for p, default_value in translation_params.items():
                current_value = params.get(p)
                params[p] = current_value if current_value is not None else default_value
        return params

    async def _upload_task_result(self, session, task, websocket, output, ori_w, ori_h, logger_task) -> bool:
        await self._send_status(websocket, task.id, WS_STATUS_SAVING)

        output = output.resize((ori_w, ori_h), resample=Image.LANCZOS)

        img = io.BytesIO()
        output.save(img, format='PNG')
        if self.verbose:
            output.save(self._result_path(WS_FILE_FINAL))

        img_bytes = img.getvalue()
        logger_task.info(f'-- Uploading result to {task.translation_mask}')
        await self._send_status(websocket, task.id, WS_STATUS_UPLOADING)
        async with session.put(task.translation_mask, data=img_bytes) as resp:
            if resp.status != 200:
                logger_task.error(f'-- Failed to upload result:')
                logger_task.error(f'{resp.status}: {resp.reason}')
                await self._send_status(websocket, task.id, WS_STATUS_ERROR_UPLOAD)
                return False
        return True

    async def _process_task_inner(self, main_loop, logger_task, session, websocket, task, translation_params) -> Tuple[bool, bool]:
        import aioshutil
        from aiofiles import os as aio_os

        logger_task.info(f'-- Processing task {task.id}')
        await self._send_status(websocket, task.id, WS_STATUS_PENDING)

        if self.verbose:
            await aioshutil.rmtree(f'result/{task.id}', ignore_errors=True)
            await aio_os.makedirs(f'result/{task.id}', exist_ok=True)

        params = self._build_task_params(task, translation_params)

        source_image = await self._download_task_image(session, task, websocket, logger_task)
        if source_image is None:
            return False, False

        logger_task.info(f'-- Translating image')
        image = Image.open(io.BytesIO(source_image))

        (ori_w, ori_h) = image.size
        if max(ori_h, ori_w) > 1200:
            params['upscale_ratio'] = 1

        await self._send_status(websocket, task.id, WS_STATUS_PREPARING)
        translation_dict = await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(
                self._translate_ws_task(task.id, websocket, image, params),
                main_loop
            )
        )
        await self.send_throttler.flush()

        output: Image.Image = translation_dict.result
        if output is not None:
            uploaded = await self._upload_task_result(session, task, websocket, output, ori_w, ori_h, logger_task)
            if not uploaded:
                return False, False

        return True, output is not None

    async def _process_task(self, main_loop, session, websocket, task, translation_params):
        from ..server import ws_pb2
        logger_task = logger.getChild(f'{task.id}')
        try:
            (success, has_translation_mask) = await self._process_task_inner(
                main_loop, logger_task, session, websocket, task, translation_params
            )
        except Exception as e:
            logger_task.error(f'-- Task failed with exception:')
            logger_task.error(f'{e.__class__.__name__}: {e}', exc_info=e if self.verbose else None)
            (success, has_translation_mask) = False, False
        finally:
            result = ws_pb2.WebSocketMessage()
            result.finish_task.id = task.id
            result.finish_task.success = success
            result.finish_task.has_translation_mask = has_translation_mask
            await websocket.send(result.SerializeToString())
            await asyncio.sleep(0)
            logger_task.info(f'-- Task finished')

    async def _async_server_thread(self, main_loop, translation_params):
        from aiohttp import ClientSession, ClientTimeout
        import websockets
        from ..server import ws_pb2

        timeout = ClientTimeout(total=WS_DEFAULT_TIMEOUT_SEC)
        async with ClientSession(timeout=timeout) as session:
            logger_conn = logger.getChild('connection')
            if self.verbose:
                logger_conn.setLevel(logging.DEBUG)
            async for websocket in websockets.connect(
                self.url,
                extra_headers={
                    WS_HEADER_SECRET: self.secret,
                },
                max_size=WS_MAX_MESSAGE_SIZE,
                logger=logger_conn
            ):
                bg_tasks = set()
                try:
                    logger.info('-- Connected to websocket server')

                    async for raw in websocket:
                        msg = ws_pb2.WebSocketMessage()
                        msg.ParseFromString(raw)
                        if msg.WhichOneof('message') != WS_MSG_TYPE_NEW_TASK:
                            continue
                        task = msg.new_task
                        bg_task = asyncio.create_task(
                            self._process_task(main_loop, session, websocket, task, translation_params)
                        )
                        bg_tasks.add(bg_task)
                        bg_task.add_done_callback(bg_tasks.discard)

                except Exception as e:
                    logger.error(f'{e.__class__.__name__}: {e}', exc_info=e if self.verbose else None)

                finally:
                    logger.info('-- Disconnected from websocket server')
                    for bg_task in bg_tasks:
                        bg_task.cancel()

    def _run_server_thread(self, future, main_loop, server_loop, translation_params):
        asyncio.set_event_loop(server_loop)
        try:
            server_loop.run_until_complete(self._async_server_thread(main_loop, translation_params))
        finally:
            future.set_result(None)

    async def listen(self, translation_params: dict = None):
        self._server_loop = asyncio.new_event_loop()
        self.task_lock = PriorityLock()
        self.counter = 0
        self.send_throttler = Throttler(WS_THROTTLE_INTERVAL_SEC)
        self._send_and_yield = self.send_throttler.wrap(self._send_and_yield_raw)

        self.add_progress_hook(self._sync_state)

        future = asyncio.Future()
        Thread(
            target=self._run_server_thread,
            args=(future, asyncio.get_running_loop(), self._server_loop, translation_params),
            daemon=True
        ).start()

        # create a future that is never done
        await future

    async def _run_text_translation(self, config: Config, ctx: Context):
        coroutine = super()._run_text_translation(config, ctx)
        if config.translator.translator_gen.has_offline():
            return await coroutine

        task_id = self._task_id
        websocket = self._websocket
        await self.task_lock.release()
        result = await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(
                coroutine,
                ctx.ws_event_loop
            )
        )
        await self.task_lock.acquire((1 << 30) - ctx.ws_count)
        self._task_id = task_id
        self._websocket = websocket
        return result

    async def _run_text_rendering(self, config: Config, ctx: Context):
        render_mask = (ctx.mask >= 127).astype(np.uint8)[:, :, None]

        output = await super()._run_text_rendering(config, ctx)
        render_mask[np.sum(ctx.img_rgb != output, axis=2) > 0] = 1
        ctx.render_mask = render_mask
        if self.verbose:
            cv2.imwrite(self._result_path(WS_FILE_RENDER_IN), cv2.cvtColor(ctx.img_rgb, cv2.COLOR_RGB2BGR))
            cv2.imwrite(self._result_path(WS_FILE_RENDER_OUT), cv2.cvtColor(output, cv2.COLOR_RGB2BGR))
            cv2.imwrite(self._result_path(WS_FILE_MASK), render_mask * 255)

        # only keep sections in mask
        if self.verbose:
            cv2.imwrite(self._result_path(WS_FILE_INMASK), cv2.cvtColor(ctx.img_rgb, cv2.COLOR_RGB2BGRA) * render_mask)
        output = cv2.cvtColor(output, cv2.COLOR_RGB2RGBA) * render_mask
        if self.verbose:
            cv2.imwrite(self._result_path(WS_FILE_OUTPUT), cv2.cvtColor(output, cv2.COLOR_RGBA2BGRA) * render_mask)

        return output
