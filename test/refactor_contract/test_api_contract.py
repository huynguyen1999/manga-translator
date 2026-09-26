from pathlib import Path

import server.main as main
from fastapi.testclient import TestClient


def test_extracted_routes_keep_their_paths_and_methods():
    routes = {
        (method.upper(), path)
        for path, operations in main.app.openapi()["paths"].items()
        for method in operations
    }

    assert {
        ("GET", "/queue-size"),
        ("POST", "/queue-size"),
        ("GET", "/status"),
        ("GET", "/workers"),
        ("GET", "/manual"),
        ("GET", "/api/status"),
        ("GET", "/api/workers"),
        ("POST", "/translate/json"),
        ("POST", "/translate/bytes"),
        ("POST", "/translate/image"),
        ("POST", "/translate/json/stream"),
        ("POST", "/translate/bytes/stream"),
        ("POST", "/translate/image/stream"),
        ("POST", "/translate/with-form/json"),
        ("POST", "/translate/with-form/bytes"),
        ("POST", "/translate/with-form/image"),
        ("POST", "/translate/with-form/json/stream"),
        ("POST", "/translate/with-form/bytes/stream"),
        ("POST", "/translate/with-form/image/stream"),
        ("POST", "/translate/with-form/image/stream/web"),
        ("POST", "/api/translate/with-form/image/stream/web"),
        ("POST", "/simple_execute/translate_batch"),
        ("POST", "/execute/translate_batch"),
        ("GET", "/pipeline-runs/{folder_name}/manifest"),
        ("GET", "/api/pipeline-runs/{folder_name}/manifest"),
        ("GET", "/result/{folder_name}/{file_name}"),
        ("HEAD", "/result/{folder_name}/{file_name}"),
        ("GET", "/api/result/{folder_name}/{file_name}"),
        ("HEAD", "/api/result/{folder_name}/{file_name}"),
        ("GET", "/result/{folder_name}/final.png"),
        ("HEAD", "/result/{folder_name}/final.png"),
        ("GET", "/api/result/{folder_name}/final.png"),
        ("HEAD", "/api/result/{folder_name}/final.png"),
        ("POST", "/translate/batch/json"),
        ("POST", "/translate/batch/images"),
        ("GET", "/results/group/summary"),
        ("GET", "/api/results/group/summary"),
        ("GET", "/results/group/summary/config"),
        ("GET", "/api/results/group/summary/config"),
        ("GET", "/results/group/summary/jobs"),
        ("GET", "/api/results/group/summary/jobs"),
        ("GET", "/results/group/summary/jobs/events"),
        ("GET", "/api/results/group/summary/jobs/events"),
        ("POST", "/results/group/summary/dismiss"),
        ("POST", "/api/results/group/summary/dismiss"),
        ("POST", "/results/group/summary/pause"),
        ("POST", "/api/results/group/summary/pause"),
        ("POST", "/results/group/summary/resume"),
        ("POST", "/api/results/group/summary/resume"),
        ("POST", "/results/group/summary/stop"),
        ("POST", "/api/results/group/summary/stop"),
        ("POST", "/results/group/summary"),
        ("POST", "/api/results/group/summary"),
        ("POST", "/results/rerun"),
        ("POST", "/api/results/rerun"),
        ("POST", "/results/rerender"),
        ("POST", "/api/results/rerender"),
        ("POST", "/result/{folder_name}/layout-preview"),
        ("POST", "/api/result/{folder_name}/layout-preview"),
        ("POST", "/result/{folder_name}/save_edits"),
        ("POST", "/api/result/{folder_name}/save_edits"),
        ("PUT", "/api/pages/{folder_name}/edits"),
        ("PATCH", "/pages/{folder_name}/review"),
        ("PATCH", "/api/pages/{folder_name}/review"),
        ("GET", "/batches"),
        ("GET", "/api/batches"),
        ("GET", "/batches/events"),
        ("GET", "/api/batches/events"),
        ("GET", "/batches/{batch_id}"),
        ("GET", "/api/batches/{batch_id}"),
        ("PUT", "/batches/{batch_id}"),
        ("PUT", "/api/batches/{batch_id}"),
        ("GET", "/batches/{batch_id}/items/{item_id}/input"),
        ("GET", "/api/batches/{batch_id}/items/{item_id}/input"),
        ("POST", "/batches/{batch_id}/pause"),
        ("POST", "/api/batches/{batch_id}/pause"),
        ("POST", "/batches/{batch_id}/resume"),
        ("POST", "/api/batches/{batch_id}/resume"),
        ("POST", "/batches/{batch_id}/dismiss"),
        ("POST", "/api/batches/{batch_id}/dismiss"),
        ("DELETE", "/batches/{batch_id}"),
        ("DELETE", "/api/batches/{batch_id}"),
        ("PATCH", "/batches/{batch_id}"),
        ("PATCH", "/api/batches/{batch_id}"),
        ("POST", "/batches/{batch_id}/items/{item_id}/retry"),
        ("POST", "/api/batches/{batch_id}/items/{item_id}/retry"),
        ("PATCH", "/batches/{batch_id}/items/{item_id}"),
        ("PATCH", "/api/batches/{batch_id}/items/{item_id}"),
        ("DELETE", "/batches/{batch_id}/items/{item_id}"),
        ("DELETE", "/api/batches/{batch_id}/items/{item_id}"),
        ("GET", "/series"),
        ("GET", "/api/series"),
        ("GET", "/series/{series_id}"),
        ("GET", "/api/series/{series_id}"),
        ("POST", "/series"),
        ("POST", "/api/series"),
        ("PATCH", "/series/{series_id}"),
        ("PATCH", "/api/series/{series_id}"),
        ("PUT", "/series/{series_id}/members"),
        ("PUT", "/api/series/{series_id}/members"),
        ("POST", "/series/{series_id}/members/add"),
        ("POST", "/api/series/{series_id}/members/add"),
        ("POST", "/manga/{manga_id}/series/move"),
        ("POST", "/api/manga/{manga_id}/series/move"),
        ("DELETE", "/manga/{manga_id}/series"),
        ("DELETE", "/api/manga/{manga_id}/series"),
        ("DELETE", "/series/{series_id}"),
        ("DELETE", "/api/series/{series_id}"),
        ("GET", "/manga/{manga_id}/series"),
        ("GET", "/api/manga/{manga_id}/series"),
        ("GET", "/results/groups"),
        ("GET", "/api/results/groups"),
        ("GET", "/api/manga"),
        ("GET", "/results/list"),
        ("GET", "/api/results/list"),
        ("POST", "/results/import"),
        ("POST", "/api/results/import"),
        ("GET", "/api/manga/{manga_id}/pages"),
        ("PUT", "/manga/{manga_id}/pages/order"),
        ("PUT", "/api/manga/{manga_id}/pages/order"),
        ("POST", "/results/update-meta"),
        ("POST", "/api/results/update-meta"),
        ("PATCH", "/api/manga"),
        ("DELETE", "/results/group"),
        ("DELETE", "/api/results/group"),
        ("DELETE", "/api/manga/{title}"),
        ("GET", "/results/export/cbz"),
        ("GET", "/api/results/export/cbz"),
        ("POST", "/results/export/cbz"),
        ("POST", "/api/results/export/cbz"),
        ("DELETE", "/results/clear"),
        ("DELETE", "/api/results/clear"),
        ("GET", "/results/{folder_name}"),
        ("GET", "/api/results/{folder_name}"),
        ("GET", "/api/pages/{folder_name}"),
        ("DELETE", "/results/{folder_name}"),
        ("DELETE", "/api/results/{folder_name}"),
        ("DELETE", "/api/pages/{folder_name}"),
        ("POST", "/results/batch-delete"),
        ("POST", "/api/results/batch-delete"),
        ("GET", "/reading-progress"),
        ("GET", "/api/reading-progress"),
        ("PUT", "/reading-progress"),
        ("PUT", "/api/reading-progress"),
    } <= routes


def test_manual_route_serves_the_same_html_asset():
    response = TestClient(main.app).get("/manual")

    assert response.status_code == 200
    assert response.text == (Path(__file__).resolve().parents[2] / "server" / "manual.html").read_text(encoding="utf-8")


def test_static_result_deletes_stay_ahead_of_the_dynamic_page_delete():
    paths = list(main.app.openapi()["paths"])

    for static_path, dynamic_path in (
        ("/results/group", "/results/{folder_name}"),
        ("/api/results/group", "/api/results/{folder_name}"),
        ("/results/clear", "/results/{folder_name}"),
        ("/api/results/clear", "/api/results/{folder_name}"),
    ):
        assert paths.index(static_path) < paths.index(dynamic_path)


def test_rerun_routes_keep_the_missing_selection_error():
    client = TestClient(main.app)

    for path in ("/results/rerun", "/api/results/rerun", "/results/rerender", "/api/results/rerender"):
        response = client.post(path, json={})

        assert response.status_code == 400
        assert response.json() == {"detail": "pageIds or groupId is required"}
