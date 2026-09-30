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

from devscripts.pipeline_case import (
    add_prompt_ids, benchmark_cases, inspect_case, main, page_reference, rerun_case,
)
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

    @patch("devscripts.pipeline_case.request_json")
    def test_benchmark_repeats_profiled_temporary_previews_as_one_artifact(self, request_json):
        profile = {"workload": {"dp_invocations": 2}, "regions": []}
        request_json.side_effect = [
            {"id": "db-page-id"},
            {"imageBase64": "cmVuZGVy", "layoutProfile": profile, "artifacts": {"layout.json": {"regions": []}}},
            {"imageBase64": "cmVuZGVy", "layoutProfile": profile, "artifacts": {"layout.json": {"regions": []}}},
        ]

        result = benchmark_cases(["http://localhost/gallery/pages/page-folder"], repeat=2)

        self.assertEqual(result["repeat"], 2)
        self.assertEqual([run["run"] for run in result["runs"]], [1, 2])
        self.assertEqual(result["runs"][0]["layoutProfile"], profile)
        self.assertEqual(result["runs"][0]["rendered_image_sha256"], result["runs"][1]["rendered_image_sha256"])
        self.assertEqual(result["runs"][0]["layout_snapshot"], {"regions": []})
        self.assertNotIn("imageBase64", result["runs"][0])
        for call in request_json.call_args_list[1:]:
            self.assertEqual(call.kwargs["body"]["includeLayoutProfile"], True)

    def test_benchmark_requires_a_positive_repeat_count(self):
        with self.assertRaisesRegex(ValueError, "repeat must be at least 1"):
            benchmark_cases(["http://localhost/gallery/pages/page-folder"], repeat=0)

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
            self.assertNotIn("layoutProfile", response.json())
            profile = {"workload": {"dp_invocations": 1}, "regions": []}
            run_case.return_value["layoutProfile"] = profile
            profiled_response = TestClient(app).post("/api/pipeline-cases/preview", json={
                "pageId": "db-page-id", "mode": "typesetting", "includeLayoutProfile": True,
            })
            self.assertEqual(profiled_response.status_code, 200, profiled_response.text)
            self.assertEqual(profiled_response.json()["layoutProfile"], profile)
            self.assertTrue(run_case.await_args.kwargs["include_layout_profile"])
            self.assertFalse((source_dir / "text_regions.json").exists())
            self.assertEqual(list(result_root.iterdir()), [source_dir])
            self.assertEqual(run_case.await_count, 2)

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


class PipelineCaseLocalWorkflowTest(unittest.TestCase):
    def _fake_execute_pipeline(
        self,
        image_path: Path,
        *,
        case_name: str,
        start_stage: str,
        output_dir: Path,
        baseline_dir: Path,
        reused_from_dir: Path | None,
        reused_from_json: str | None,
        translator_name: str = "sugoi",
        patch=None,
        include_layout_profile: bool = True,
        render_output: Path | None = None,
        add_prompt_ids_fn=add_prompt_ids,
    ) -> dict:
        output_dir.mkdir(parents=True, exist_ok=True)
        if start_stage == "input":
            (output_dir / "input.png").write_bytes(image_path.read_bytes())
            (output_dir / "inpainted.png").write_bytes(b"inpainted-bytes")
            (output_dir / "mask_final.png").write_bytes(b"mask-bytes")
            regions = [{"id": "text_region_1", "region_id": "r1", "text": "원문", "translation": "Hello", "font_size": 24}]
            layout_lines = [{"text": "Hello", "x": 10, "y": 10, "width": 50, "height": 20}]
            render_bytes = b"baseline-render"
        else:
            prev_case = json.loads(Path(reused_from_json).read_text(encoding="utf-8"))
            prev_regions = prev_case.get("artifacts", {}).get("text_regions") if isinstance(prev_case, dict) else prev_case
            font_size = int(((patch or {}).get("settingsOverrides") or {}).get("fontSize", 28))
            regions = [
                {**dict(r), "font_size": font_size}
                for r in (prev_regions or [])
            ]
            layout_lines = [{"text": "Hello", "x": 12, "y": 14, "width": 56, "height": 24}]
            render_bytes = f"stage-render-{start_stage}-{font_size}".encode("utf-8")

        layout_doc = {
            "regions": {
                "r1": {
                    "region_id": "r1",
                    "font_size": regions[0]["font_size"],
                    "placement_mode": "BUBBLE",
                    "solver_status": "ok",
                    "lines": layout_lines,
                }
            }
        }
        (output_dir / "rendered.png").write_bytes(render_bytes)
        (output_dir / "final.jpg").write_bytes(render_bytes)
        for fname, doc in (
            ("text_regions.json", regions),
            ("regions.json", regions),
            ("translations.json", regions),
            ("layout.json", layout_doc),
            ("bubble_detections.json", []),
            ("ocr.json", regions),
            ("detection.json", regions),
        ):
            (output_dir / fname).write_text(json.dumps(doc), encoding="utf-8")
        meta = {
            "caseName": case_name,
            "sourcePath": str(image_path),
            "startStage": start_stage,
            "baselineDir": str(baseline_dir),
            "reusedFromDir": str(reused_from_dir) if reused_from_dir else None,
            "reusedFromJson": reused_from_json,
        }
        (output_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        payload = {
            "caseName": case_name,
            "mode": "full" if start_stage == "input" else start_stage,
            "startStage": start_stage,
            "sourcePath": str(image_path),
            "caseDir": str(output_dir),
            "baselineDir": str(baseline_dir),
            "reusedFromDir": str(reused_from_dir) if reused_from_dir else None,
            "reusedFromJson": reused_from_json,
            "renderFile": str(output_dir / "final.jpg"),
            "artifacts": {
                "text_regions": regions,
                "translations": regions,
                "layout": layout_doc,
                "bubble_detections": [],
            },
            "layoutProfile": {"workload": {"dp_invocations": 3 if start_stage == "input" else 1}},
            "timingMs": {"total": 10.0, "stages": {start_stage: 10.0}},
        }
        (output_dir / "case.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def test_first_run_executes_full_pipeline_and_second_run_reuses_json_for_stage_in_separate_dir(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            slow_dir = root_path / "slow"
            slow_dir.mkdir()
            img_path = slow_dir / "001_page.png"
            img_path.write_bytes(b"fake-png-data")
            data_dir = root_path / "data"

            # 1. Check before any run -> hasRunBefore is False, recommends run_full_pipeline
            check_out = io.StringIO()
            with redirect_stdout(check_out):
                self.assertEqual(
                    main(["check", str(slow_dir), "--stage", "layout", "--data-dir", str(data_dir)]),
                    0,
                )
            status_before = json.loads(check_out.getvalue())
            self.assertFalse(status_before["allRunBefore"])
            self.assertEqual(status_before["recommendedAction"], "run_full_pipeline")
            self.assertFalse(status_before["cases"][0]["hasRunBefore"])

            with patch("devscripts.pipeline_case_local.execute_local_pipeline", side_effect=self._fake_execute_pipeline) as exec_mock:
                # 2. First run with --stage layout -> runs full pipeline from scratch and saves to data_dir
                run1_out = io.StringIO()
                with redirect_stdout(run1_out):
                    self.assertEqual(
                        main(["run", str(slow_dir), "--stage", "layout", "--data-dir", str(data_dir)]),
                        0,
                    )
                run1 = json.loads(run1_out.getvalue())
                self.assertEqual(run1["actionTaken"], "ran_full_pipeline")
                self.assertEqual(run1["executedFromStage"], "input")
                baseline_dir = Path(run1["baselineDir"])
                self.assertEqual(Path(run1["outputDir"]), baseline_dir)
                self.assertTrue((baseline_dir / "case.json").is_file())
                self.assertTrue((baseline_dir / "input.png").is_file())
                baseline_case_bytes = (baseline_dir / "case.json").read_bytes()
                baseline_render_bytes = (baseline_dir / "rendered.png").read_bytes()

                # 3. Check after first run -> hasRunBefore is True, recommends rerun_stage
                check2_out = io.StringIO()
                with redirect_stdout(check2_out):
                    self.assertEqual(
                        main(["check", str(slow_dir), "--stage", "layout", "--data-dir", str(data_dir)]),
                        0,
                    )
                status_after = json.loads(check2_out.getvalue())
                self.assertTrue(status_after["allRunBefore"])
                self.assertEqual(status_after["recommendedAction"], "rerun_stage")
                self.assertEqual(status_after["cases"][0]["lastRunJson"], str(baseline_dir / "case.json"))

                # 4. Second run with --stage layout -> reuses last run JSON, runs layout only, writes to separate dir
                separate_dir = root_path / "rerun_after_change"
                run2_out = io.StringIO()
                with redirect_stdout(run2_out):
                    self.assertEqual(
                        main([
                            "run", str(slow_dir),
                            "--stage", "layout",
                            "--data-dir", str(data_dir),
                            "--output-dir", str(separate_dir),
                        ]),
                        0,
                    )
                run2 = json.loads(run2_out.getvalue())
                self.assertEqual(run2["actionTaken"], "reran_stage")
                self.assertEqual(run2["executedFromStage"], "layout")
                self.assertEqual(run2["reusedFromDir"], str(baseline_dir))
                self.assertEqual(run2["reusedFromJson"], str(baseline_dir / "case.json"))
                self.assertNotEqual(Path(run2["outputDir"]).resolve(), baseline_dir.resolve())
                self.assertTrue(Path(run2["caseJson"]).is_file())
                self.assertTrue(Path(run2["comparisonFile"]).is_file())
                self.assertEqual(run2["comparison"]["changedRegionCount"], 1)
                self.assertEqual(
                    run2["comparison"]["changedRegions"][0]["changes"]["fontSize"],
                    {"before": 24, "after": 28},
                )

                # Verify original baseline files were NOT modified or overwritten
                self.assertEqual((baseline_dir / "case.json").read_bytes(), baseline_case_bytes)
                self.assertEqual((baseline_dir / "rendered.png").read_bytes(), baseline_render_bytes)
                self.assertEqual(exec_mock.call_count, 2)

                # 5. Compare subcommand works directly on baseline vs separate rerun dir
                cmp_out = io.StringIO()
                with redirect_stdout(cmp_out):
                    self.assertEqual(
                        main(["compare", str(baseline_dir), run2["outputDir"], "--data-dir", str(data_dir)]),
                        0,
                    )
                cmp_data = json.loads(cmp_out.getvalue())
                self.assertEqual(cmp_data["changedRegionCount"], 1)
                self.assertTrue(cmp_data["renderedImageChanged"])

    def test_stage_rerun_refuses_to_overwrite_baseline_directory(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            img_path = root_path / "page.png"
            img_path.write_bytes(b"fake-png")
            data_dir = root_path / "data"

            with patch("devscripts.pipeline_case_local.execute_local_pipeline", side_effect=self._fake_execute_pipeline):
                out1 = io.StringIO()
                with redirect_stdout(out1):
                    self.assertEqual(main(["run", str(img_path), "--stage", "layout", "--data-dir", str(data_dir)]), 0)
                first = json.loads(out1.getvalue())
                baseline_dir = first["baselineDir"]

                # Attempting to pass baseline_dir as --output-dir on a stage rerun must fail with exit code 2
                err = io.StringIO()
                with patch("sys.stderr", err):
                    code = main([
                        "run", str(img_path),
                        "--stage", "layout",
                        "--data-dir", str(data_dir),
                        "--output-dir", baseline_dir,
                    ])
                self.assertEqual(code, 2)
                self.assertIn("Refusing to overwrite baseline directory", err.getvalue())


if __name__ == "__main__":
    unittest.main()

