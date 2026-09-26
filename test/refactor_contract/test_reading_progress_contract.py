from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api.routes.reading_progress import create_reading_progress_router
from server.main import _validate_installation_id


class ProgressStore:
    def __init__(self):
        self.saved = None

    async def resolve_group_id(self, value):
        return "group-1"

    async def resolve_group_title(self, value):
        return "Series One"

    async def get_progress(self, installation_id, group_id):
        return None

    async def save_progress(self, payload):
        self.saved = payload
        return payload


def test_reading_progress_routes_keep_fallback_and_save_contracts():
    store = ProgressStore()
    router, handlers = create_reading_progress_router(lambda: store, _validate_installation_id)
    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        read = client.get(
            "/api/reading-progress",
            params={"installationId": "install-123", "groupId": "series"},
        )
        assert read.status_code == 200
        assert read.json() == {
            "installationId": "install-123",
            "groupId": "group-1",
            "mangaTitle": "Series One",
            "pageId": None,
            "page": None,
            "scrollTop": 0,
            "complete": False,
            "updatedAt": None,
        }

        saved = client.put(
            "/reading-progress",
            json={
                "installationId": "install-123",
                "groupId": "group-1",
                "mangaTitle": "Series One",
                "pageId": "page-4",
                "page": 4,
                "scrollTop": 12,
                "complete": False,
            },
        )
        assert saved.status_code == 200
        assert saved.json()["pageId"] == "page-4"
        assert saved.json()["page"] == 4
        assert store.saved["groupId"] == "group-1"
        assert store.saved["installationId"] == "install-123"
        assert handlers[0].__name__ == "get_reading_progress"
        assert handlers[1].__name__ == "save_reading_progress"
