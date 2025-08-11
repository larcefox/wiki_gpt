import os
import sys
import types
import pathlib
import importlib
import pytest
from fastapi.testclient import TestClient

# Configure environment and stub external dependencies before importing app
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
base_dir = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(base_dir))
sys.path.append(str(base_dir / "backend"))

captured = {}
fake_qdrant = types.ModuleType("qdrant_utils")
fake_qdrant.embed_text = lambda text: [0.0] * 256
fake_qdrant.ensure_collection = lambda: None
fake_qdrant.insert_vector = lambda *a, **kw: None
fake_qdrant.delete_vector = lambda *a, **kw: None
fake_qdrant.search_vector = lambda vector, db, team_id, limit=5: []

def _rerank_with_llm(query, hits, prompt_template=None, model=None):
    captured["prompt_template"] = prompt_template
    captured["model"] = model
    return hits

fake_qdrant.rerank_with_llm = _rerank_with_llm
sys.modules["qdrant_utils"] = fake_qdrant

import backend.main as main
importlib.reload(main)
from backend.main import app, Base, engine
from backend.auth import init_roles

# Reset database and roles
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


def test_search_uses_group_prompt():
    user = register("prompt@example.com")
    token = user["access_token"]

    r = client.post(
        "/articles/",
        json={
            "title": "Title",
            "content": "Content",
            "tags": [],
            "group": {"name": "G1", "prompt_template": "Custom"},
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    group_id = r.json()["group_id"]

    r = client.post(
        "/articles/search/",
        json={"q": "Title", "group_id": group_id},
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    assert captured.get("prompt_template") == "Custom"
