import tempfile
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api.routes.presets import create_preset_router
from server.preset_repository import PresetRepository


def test_preset_api_endpoints():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        repo = PresetRepository(store=None, result_root=root)
        router, _ = create_preset_router(lambda: repo)

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            # 1. GET empty list
            res = client.get("/api/presets")
            assert res.status_code == 200
            assert res.json() == {"presets": []}

            # 2. POST create preset
            res = client.post(
                "/api/presets",
                json={
                    "name": "Quick Offline",
                    "description": "Fast offline model setup",
                    "settings": {"translator": "sugoi", "ocr": "48px"},
                    "isDefault": True,
                },
            )
            assert res.status_code == 201
            p1 = res.json()
            assert p1["id"].startswith("preset-")
            assert p1["name"] == "Quick Offline"
            assert p1["isDefault"] is True

            # 3. POST duplicate name returns 409
            res = client.post(
                "/api/presets",
                json={
                    "name": "quick offline",
                    "settings": {},
                },
            )
            assert res.status_code == 409

            # 4. GET by ID
            res = client.get(f"/api/presets/{p1['id']}")
            assert res.status_code == 200
            assert res.json()["name"] == "Quick Offline"

            # 5. GET not found
            res = client.get("/api/presets/nonexistent-id")
            assert res.status_code == 404

            # 6. POST second preset
            res = client.post(
                "/api/presets",
                json={
                    "name": "Color Manhwa Pro",
                    "settings": {"colorizer": "mc2"},
                    "isDefault": False,
                },
            )
            assert res.status_code == 201
            p2 = res.json()

            # 7. PUT update preset
            res = client.put(
                f"/api/presets/{p2['id']}",
                json={
                    "name": "Color Manhwa HD",
                    "description": "Updated description",
                    "settings": {"colorizer": "mc2", "upscaler": "esrgan"},
                },
            )
            assert res.status_code == 200
            assert res.json()["name"] == "Color Manhwa HD"
            assert res.json()["settings"]["upscaler"] == "esrgan"

            # 8. POST set default
            res = client.post(f"/api/presets/{p2['id']}/default")
            assert res.status_code == 200
            assert res.json()["isDefault"] is True

            # Verify p1 is no longer default
            res_p1 = client.get(f"/api/presets/{p1['id']}")
            assert res_p1.json()["isDefault"] is False

            # 9. DELETE preset
            res = client.delete(f"/api/presets/{p1['id']}")
            assert res.status_code == 200
            assert res.json() == {"status": "deleted", "id": p1["id"]}

            # 10. List should now only contain p2
            res = client.get("/api/presets")
            assert len(res.json()["presets"]) == 1
            assert res.json()["presets"][0]["id"] == p2["id"]
