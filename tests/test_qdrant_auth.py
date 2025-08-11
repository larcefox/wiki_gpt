import importlib
import pathlib
import sys


def test_embed_text_adds_api_key_header(monkeypatch):
    token = "test-token"
    folder = "folder-id"
    monkeypatch.setenv("YANDEX_OAUTH_TOKEN", token)
    monkeypatch.setenv("YANDEX_FOLDER_ID", folder)

    base_dir = pathlib.Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(base_dir / "backend"))
    monkeypatch.syspath_prepend(str(base_dir))
    monkeypatch.delitem(sys.modules, "qdrant_utils", raising=False)

    qdrant_utils = importlib.import_module("qdrant_utils")
    importlib.reload(qdrant_utils)

    captured = {}

    class DummyResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embedding": [0.0]}

    def fake_post(url, headers=None, json=None):
        captured["headers"] = headers
        return DummyResp()

    monkeypatch.setattr(qdrant_utils.requests, "post", fake_post)

    qdrant_utils.embed_text("hello")

    assert captured["headers"]["Authorization"] == f"Api-Key {token}"
