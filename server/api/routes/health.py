from fastapi import APIRouter

from server.instance import executor_instances
from server.myqueue import task_queue

router = APIRouter()


@router.post("/queue-size", response_model=int, tags=["api", "json"])
@router.get("/queue-size", response_model=int, tags=["api", "json"])
async def queue_size() -> int:
    return len(task_queue.queue)


@router.get("/status", tags=["api"])
@router.get("/workers", tags=["api"])
@router.get("/api/status", tags=["api"])
@router.get("/api/workers", tags=["api"])
async def get_server_status():
    return {
        "status": "online",
        **executor_instances.get_status(),
        "queue_size": len(task_queue.queue),
    }
