import os
import sys
import types
import pathlib
import importlib
from fastapi.testclient import TestClient

# Configure environment before importing app
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
base_dir = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(base_dir))
sys.path.append(str(base_dir / "backend"))

fake_qdrant = types.ModuleType("qdrant_utils")
fake_qdrant.embed_text = lambda text: [0.0] * 256
fake_qdrant.ensure_collection = lambda: None
fake_qdrant.insert_vector = lambda *a, **kw: None
fake_qdrant.delete_vector = lambda *a, **kw: None
fake_qdrant.search_vector = lambda *a, **kw: []
fake_qdrant.rerank_with_llm = lambda *a, **kw: []
sys.modules["qdrant_utils"] = fake_qdrant

import backend.main as main
importlib.reload(main)
from backend.main import app, Base, engine
from backend.auth import init_roles

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
init_roles()

client = TestClient(app)


def auth_headers(token: str):
    return {"Authorization": f"Bearer {token}"}


def register(email: str):
    r = client.post("/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 200
    return r.json()


def test_article_list_basic():
    user = register("list@example.com")
    token = user["access_token"]

    r1 = client.post(
        "/articles/",
        json={"title": "A", "content": "Alpha", "tags": ["x"]},
        headers=auth_headers(token),
    )
    assert r1.status_code == 200

    r2 = client.post(
        "/articles/",
        json={"title": "B", "content": "Beta", "tags": []},
        headers=auth_headers(token),
    )
    assert r2.status_code == 200

    r = client.get("/articles/list", headers=auth_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert "content" not in data[0]

    r = client.get("/articles/list?q=Alpha", headers=auth_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1 and data[0]["title"] == "A"

    r = client.get("/articles/list?tags=x", headers=auth_headers(token))
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1 and data[0]["title"] == "A"
