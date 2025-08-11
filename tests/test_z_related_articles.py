import os
import sys
import types
import pathlib
import importlib
from fastapi.testclient import TestClient
from backend.schemas import ArticleSearchHit

# Configure environment before importing app
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
base_dir = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(base_dir))
sys.path.append(str(base_dir / "backend"))

article_ids = {}
captured = {}


def setup_client():
    fake_qdrant = types.ModuleType("qdrant_utils")
    fake_qdrant.embed_text = lambda text: [0.0] * 256
    fake_qdrant.ensure_collection = lambda: None
    fake_qdrant.insert_vector = lambda *a, **kw: None
    fake_qdrant.delete_vector = lambda *a, **kw: None

    def _search_vector(vector, db, team_id, group_id=None, limit=5):
        captured["group_id"] = group_id
        return [
            ArticleSearchHit(
                id=str(article_ids.get("a")),
                title="A",
                content="A",
                score=1.0,
                tags=[],
                group_id=group_id,
            ),
            ArticleSearchHit(
                id=str(article_ids.get("b")),
                title="B",
                content="B",
                score=0.9,
                tags=[],
                group_id=group_id,
            ),
        ]

    fake_qdrant.search_vector = _search_vector
    fake_qdrant.rerank_with_llm = lambda *a, **kw: []
    prev_qdrant = sys.modules.get("qdrant_utils")
    sys.modules["qdrant_utils"] = fake_qdrant

    import backend.main as main
    importlib.reload(main)
    from backend.main import app, Base, engine
    from backend.auth import init_roles

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    init_roles()

    return TestClient(app), prev_qdrant


def auth_headers(token: str):
    return {"Authorization": f"Bearer {token}"}


def register(client, email: str):
    r = client.post("/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 200
    return r.json()


def test_related_articles():
    client, prev_qdrant = setup_client()
    user = register(client, "related@example.com")
    token = user["access_token"]

    # Create two articles in same group
    r1 = client.post(
        "/articles/",
        json={"title": "A", "content": "Alpha", "tags": [], "group": {"name": "G"}},
        headers=auth_headers(token),
    )
    assert r1.status_code == 200
    article_ids["a"] = r1.json()["id"]
    group_id = r1.json()["group_id"]

    r2 = client.post(
        "/articles/",
        json={"title": "B", "content": "Beta", "tags": [], "group_id": group_id},
        headers=auth_headers(token),
    )
    assert r2.status_code == 200
    article_ids["b"] = r2.json()["id"]

    r = client.get(
        f"/articles/{article_ids['a']}/related",
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["id"] == article_ids["b"]
    assert str(captured.get("group_id")) == group_id

    # restore previous qdrant module for other tests
    if prev_qdrant is not None:
        sys.modules["qdrant_utils"] = prev_qdrant
        import backend.main as main
        importlib.reload(main)
