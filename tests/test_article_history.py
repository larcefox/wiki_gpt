import os
import sys
import types
import pathlib
import uuid
from fastapi.testclient import TestClient

# Configure environment and stub external dependencies before importing app
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
base_dir = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(base_dir))
sys.path.append(str(base_dir / "backend"))

fake_qdrant = types.ModuleType("qdrant_utils")
fake_qdrant.embed_text = lambda text: [0.0] * 256
fake_qdrant.ensure_collection = lambda: None
fake_qdrant.insert_vector = lambda *a, **kw: None
fake_qdrant.delete_vector = lambda *a, **kw: None
fake_qdrant.search_vector = lambda vector, db, team_id, limit=5: []
fake_qdrant.rerank_with_llm = lambda *a, **kw: []
sys.modules["qdrant_utils"] = fake_qdrant

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

def test_article_history_versions():
    user = register("history@example.com")
    token = user["access_token"]

    # Create article
    r = client.post(
        "/articles/",
        json={"title": "First", "content": "One", "tags": []},
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    article_id = r.json()["id"]

    # Update article
    r = client.put(
        f"/articles/{article_id}",
        json={"title": "Second", "content": "Two", "tags": [], "group_id": None},
        headers=auth_headers(token),
    )
    assert r.status_code == 200

    # Fetch history
    r = client.get(f"/articles/{article_id}/history", headers=auth_headers(token))
    assert r.status_code == 200
    history = r.json()
    assert [h["title"] for h in history] == ["Second", "First"]


def test_article_history_missing_article():
    user = register("missing@example.com")
    token = user["access_token"]
    missing_id = str(uuid.uuid4())

    r = client.get(f"/articles/{missing_id}/history", headers=auth_headers(token))
    assert r.status_code == 404
