import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
import app as webapp


def test_dummy_batch_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "DATA", tmp_path / "batches")
    monkeypatch.setattr(webapp, "render_video_mp4", lambda output, *_args, **_kwargs: output.write_bytes(b"fake-mp4"))
    client = TestClient(webapp.app)
    settings = '{"dummy_mode":true,"rewrite_script":true,"generate_images":true,"preview_seconds":0}'
    response = client.post(
        "/api/batches",
        data={"settings_json": settings, "openai_api_key": "secret-openai", "gemini_api_key": "secret-gemini"},
        files=[("text_files", ("sample.txt", "안녕 세상\n더미 테스트", "text/plain"))],
    )
    assert response.status_code == 200
    batch_id = response.json()["batch_id"]
    for _ in range(100):
        status = client.get(f"/api/batches/{batch_id}").json()
        if status["state"] in {"done", "error"}:
            break
        time.sleep(0.03)
    assert status["state"] == "done", status
    assert "source_path" not in status["items"][0]
    disk_status = (tmp_path / "batches" / batch_id / "status.json").read_text(encoding="utf-8")
    assert "secret-openai" not in disk_status
    assert "secret-gemini" not in disk_status
    assert client.get(status["items"][0]["mp4_url"]).content == b"fake-mp4"
    assert client.get(status["all_results_url"]).status_code == 200
