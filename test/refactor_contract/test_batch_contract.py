import io
import zipfile
from unittest.mock import AsyncMock, patch

from PIL import Image
from starlette.testclient import TestClient

import server.main as main
from manga_translator.utils.generic import Context
from server.api.routes import translation_batch


def test_batch_json_route_keeps_error_response_contract():
    result = Context(translation_error="translation failed")
    with patch.object(
        translation_batch,
        "get_batch_ctx",
        new=AsyncMock(return_value=[result]),
    ):
        response = TestClient(main.app).post(
            "/translate/batch/json",
            json={"images": ["unused-by-mock"]},
        )

    assert response.status_code == 200
    assert response.json() == [{"translations": [], "error": "translation failed", "debug_folder": None}]


def test_batch_images_route_keeps_zip_response_contract():
    image = Image.new("RGB", (2, 2), "white")
    result = Context(result=image, translation_error=None)
    with patch.object(
        translation_batch,
        "get_batch_ctx",
        new=AsyncMock(return_value=[result]),
    ):
        response = TestClient(main.app).post(
            "/translate/batch/images",
            json={"images": ["unused-by-mock"]},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["translated_1.png"]
