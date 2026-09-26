import unittest
import base64
import json
import io
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from unittest.mock import AsyncMock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from devscripts.pipeline_case import add_prompt_ids, inspect_case, main, page_reference, rerun_case
from server.api.routes.pipeline_reruns import create_pipeline_rerun_router
from server.batch_config import config_for


class PipelineCaseUrlTest(unittest.TestCase):
    def test_copied_page_url_resolves_folder_and_origin(self):
        self.assertEqual(
            page_reference("http://localhost:6868/gallery/pages/folder%201"),
            ("http://localhost:6868", "folder 1"),
        )

    def test_result_asset_url_resolves_folder(self):
        self.assertEqual(
            page_reference("https://studio.example/result/page-123/final.jpg"),
            ("https://studio.example", "page-123"),
        )

    def test_relative_or_non_http_urls_are_rejected(self):
        with self.assertRaises(ValueError):
            page_reference("/gallery/pages/page-123")

    @patch("devscripts.pipeline_case.inspect_case", return_value={"page": {"id": "page-id"}})
    def test_get_command_exports_json_and_keeps_inspect_alias(self, inspect_case):
        for command in ("get", "inspect"):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([command, "http://localhost/gallery/pages/folder"]), 0)
            self.assertEqual(json.loads(output.getvalue()), {"page": {"id": "page-id"}})
        self.assertEqual(inspect_case.call_count, 2)

    @patch("devscripts.pipeline_case.request_json", return_value={"page": {"id": "page-id"}})
    def test_inspect_uses_one_json_case_endpoint(self, request_json):
        result = inspect_case("http://localhost", "folder-id")

        self.assertEqual(result["page"]["id"], "page-id")
        request_json.assert_called_once_with("http://localhost", "/api/pipeline-cases/folder-id/data")

    def test_config_for_accepts_legacy_enum_repr_in_saved_page_settings(self):
        settings = {
            "renderAlignment": "Alignment.auto",
            "renderTextDirection": "Direction.auto",
        }

        config = config_for({"settings": settings}, {"settings": settings})

        self.assertEqual(config.render.alignment.value, "auto")
        self.assertEqual(config.render.direction.value, "auto")

    def test_stage_records_get_type_prefixed_prompt_ids(self):
        detections = add_prompt_ids("detection", [{}, {"index": 4}, {"region_id": "source-6"}])
        self.assertEqual(
            detections,
            [
                {"id": "detection_1"},
                {"index": 4, "id": "detection_5"},
                {"region_id": "source-6", "id": "source-6"},
            ],
        )

    @patch("devscripts.pipeline_case.request_json")
    def test_rerun_returns_render_and_writes_it_to_review_path(self, request_json):
        image = b"rendered-image"
        request_json.side_effect = [
            {"id": "db-page-id"},
            {"mode": "translation_typesetting", "imageBase64": base64.b64encode(image).decode(), "artifacts": {}},
        ]

        patch = {"artifacts": {"text_regions.json": [{"id": "text_region_1"}]}}
        with tempfile.TemporaryDirectory() as temp:
            render_path = Path(temp) / "preview.jpg"
            result = rerun_case(
                "http://localhost", "folder-id", "translation", patch=patch,
                render_output=render_path,
            )

            self.assertEqual(result["mode"], "translation_typesetting")
            self.assertEqual(result["renderFile"], str(render_path))
            self.assertEqual(render_path.read_bytes(), image)
        self.assertEqual(request_json.call_args_list[1].args[1], "/api/pipeline-cases/preview")
        self.assertEqual(request_json.call_args_list[1].kwargs["body"], {
            "pageId": "db-page-id", "mode": "translation_typesetting", **patch
        })

    @patch("server.pipeline_rerun.run_temporary_pipeline_case", new_callable=AsyncMock)
    def test_rerun_reads_page_data_without_creating_a_saved_case_or_batch(self, run_case):
        run_case.return_value = {
            "mode": "typesetting", "sourcePageId": "db-page-id", "image": b"render",
            "artifacts": {"text_regions.json": [{"id": "text_region_1"}]},
        }
        with tempfile.TemporaryDirectory() as root:
            result_root = Path(root) / "results"
            source_dir = result_root / "page-folder"
            source_dir.mkdir(parents=True)
            (source_dir / "input.png").write_bytes(b"source-image")
            (source_dir / "meta.json").write_text(
                json.dumps({"settings": {"fontSize": 10}}), encoding="utf-8"
            )

            class Store:
                async def page_detail(self, _page_id):
                    return {
                        "id": "db-page-id", "folder": "page-folder", "groupId": "group-1",
                        "settings": {"fontSize": 10}, "hasTextRegions": True,
                    }

                async def get_documents(self, _folder):
                    return {"text_regions.json": [{"id": "text_region_1", "translation": "old"}]}

            class BatchStore:
                async def put_batch(self, *_args):
                    raise AssertionError("temporary reruns must not create a batch record")

            class Scheduler:
                class Executors:
                    async def find_executor(self):
                        return object()

                    async def free_executor(self, _instance):
                        pass

                executors = Executors()

            store = Store()
            batches = BatchStore()
            router, _ = create_pipeline_rerun_router(
                lambda: store,
                lambda: result_root,
                lambda: Path(root) / "legacy",
                lambda *_args, **_kwargs: {"items": []},
                lambda: batches,
                Scheduler,
                lambda error: HTTPException(500, detail=str(error)),
            )
            app = FastAPI()
            app.include_router(router)
            response = TestClient(app).post("/api/pipeline-cases/preview", json={
                "pageId": "db-page-id",
                "mode": "typesetting",
                "settingsOverrides": {"fontSize": 14},
                "artifacts": {"text_regions.json": [{"id": "text_region_1", "translation": "fixed"}]},
            })

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["imageBase64"], base64.b64encode(b"render").decode())
            self.assertEqual(response.json()["sourcePageId"], "db-page-id")
            self.assertFalse((source_dir / "text_regions.json").exists())
            self.assertEqual(list(result_root.iterdir()), [source_dir])
            run_case.assert_awaited_once()

    def test_case_data_endpoint_returns_artifacts_without_running_pipeline(self):
        with tempfile.TemporaryDirectory() as root:
            result_root = Path(root) / "results"
            source_dir = result_root / "page-folder"
            source_dir.mkdir(parents=True)
            (source_dir / "input.png").write_bytes(b"image")

            class Store:
                async def page_detail(self, _page_ref):
                    return {
                        "id": "page-id", "folder": "page-folder",
                        "settings": {"targetLanguage": "ENG"},
                    }

                async def get_documents(self, _folder):
                    return {
                        "pipeline_manifest.json": {"kind": "pipeline-run"},
                        "text_regions.json": [{"id": "region-1", "translation": "HELLO"}],
                    }

            router, _ = create_pipeline_rerun_router(
                lambda: Store(), lambda: result_root, lambda: Path(root) / "legacy",
                lambda *_args, **_kwargs: {"items": []}, lambda: None, lambda: None,
                lambda error: HTTPException(500, detail=str(error)),
            )
            app = FastAPI()
            app.include_router(router)

            response = TestClient(app).get("/api/pipeline-cases/page-id/data")

            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["page"], {
                "id": "page-id", "folder": "page-folder", "url": "/result/page-folder/final.jpg",
            })
            self.assertEqual(payload["pipeline"], {"kind": "pipeline-run"})
            self.assertEqual(payload["artifacts"]["text_regions"], [
                {"id": "region-1", "translation": "HELLO"},
            ])
            self.assertIn("layout.json", payload["missingArtifacts"])

if __name__ == "__main__":
    unittest.main()
