import os
import sys
import types
import pathlib
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Provide TestClient with stubbed qdrant and restore state after test."""
    os.environ["DATABASE_URL"] = "sqlite:///./test.db"
    base_dir = pathlib.Path(__file__).resolve().parents[1]
    sys.path.append(str(base_dir))
    sys.path.append(str(base_dir / "backend"))

    from backend.schemas import ArticleSearchHit

    fake_qdrant = types.ModuleType("qdrant_utils")
    fake_qdrant.embed_text = lambda text: [0.0] * 256
    fake_qdrant.ensure_collection = lambda: None
    fake_qdrant.insert_vector = lambda *a, **kw: None
    fake_qdrant.delete_vector = lambda *a, **kw: None

    def _search_vector(vector, db, team_id, group_id=None, limit=5):
        hits = [
            ArticleSearchHit(
                id="1",
                title="T1",
                content="Content1",
                score=0.9,
                tags=[],
                group_id=None,
            ),
            ArticleSearchHit(
                id="2",
                title="T2",
                content="Content2",
                score=0.8,
                tags=[],
                group_id=None,
            ),
        ]
        return hits[:limit]

    fake_qdrant.search_vector = _search_vector
    fake_qdrant.rerank_with_llm = lambda q, h, prompt_template=None, model=None: h
    sys.modules["qdrant_utils"] = fake_qdrant

    spec = importlib.util.spec_from_file_location(
        "backend.main_test", base_dir / "backend" / "main.py"
    )
    main = importlib.util.module_from_spec(spec)
    sys.modules["backend.main_test"] = main
    spec.loader.exec_module(main)

    from backend.auth import init_roles

    Base, engine, app = main.Base, main.engine, main.app
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    init_roles()

    test_client = TestClient(app)

    yield test_client

    # no cleanup needed; module loaded under unique name


def auth_headers(token: str):
    return {"Authorization": f"Bearer {token}"}


def register(client: TestClient, email: str):
    r = client.post("/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 200
    return r.json()


def test_search_answer_returns_answer_without_sources(client: TestClient):
    user = register(client, "ans@example.com")
    token = user["access_token"]

    r = client.post(
        "/articles/search/answer",
        json={"q": "T1", "top_k": 2},
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["prompt_used"].startswith(
        "Сделай краткое резюме ответа на запрос, опираясь только на выдержки"
    )
    assert "answer" in data
    assert "sources" not in data

