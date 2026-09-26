from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from server.instance import ExecutorInstance


def create_instance_router(
    get_nonce: Callable[[], str | None],
    executors: Any,
) -> tuple[APIRouter, Callable[..., Any]]:
    router = APIRouter()

    @router.post("/register", response_description="no response", tags=["internal-api"])
    async def register_instance(
        instance: ExecutorInstance,
        req: Request,
        req_nonce: str = Header(alias="X-Nonce"),
    ):
        if req_nonce != get_nonce():
            raise HTTPException(401, detail="Invalid nonce")
        instance.ip = req.client.host
        executors.register(instance)

    return router, register_instance
