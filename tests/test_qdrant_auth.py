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

    def fake_post(url, headers=None, json=None, **kwargs):
        captured["headers"] = headers
        return DummyResp()

    monkeypatch.setattr(qdrant_utils.requests, "post", fake_post)

    qdrant_utils.embed_text("hello")

    assert captured["headers"]["Authorization"] == f"Api-Key {token}"


def test_embed_text_without_credentials(monkeypatch):
    base_dir = pathlib.Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(base_dir / "backend"))
    monkeypatch.syspath_prepend(str(base_dir))
    monkeypatch.delenv("YANDEX_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)
    monkeypatch.delitem(sys.modules, "qdrant_utils", raising=False)

    qdrant_utils = importlib.import_module("qdrant_utils")
    importlib.reload(qdrant_utils)

    called = {}

    def fake_post(*args, **kwargs):
        called["called"] = True
        raise AssertionError("external request should not be used")

    monkeypatch.setattr(qdrant_utils.requests, "post", fake_post)

    vec = qdrant_utils.embed_text("local text")
    assert len(vec) == qdrant_utils.VECTOR_SIZE
    assert "called" not in called
