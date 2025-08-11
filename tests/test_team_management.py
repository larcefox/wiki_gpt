import os, sys, pathlib, types

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
from backend.db import SessionLocal
from backend.models import User

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
init_roles()

from fastapi.testclient import TestClient

client = TestClient(app)

def auth_headers(token: str):
    return {"Authorization": f"Bearer {token}"}

def register(email: str):
    r = client.post("/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 200
    return r.json()

def create_article(token: str, title: str):
    r = client.post(
        "/articles/",
        json={"title": title, "content": "text", "tags": []},
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    return r.json()["id"]

def test_team_isolation_and_switch():
    u1 = register("u1@example.com")
    u2 = register("u2@example.com")
    t1 = u1["team_id"]
    token1 = u1["access_token"]
    token2 = u2["access_token"]

    art_a = create_article(token1, "A1")

    r = client.post(
        "/teams/",
        json={"name": "TeamB"},
        headers=auth_headers(token1),
    )
    assert r.status_code == 200
    team_b = r.json()["id"]

    art_b = create_article(token1, "B1")

    # switch back to first team
    r = client.post(
        "/teams/switch",
        json={"team_id": t1},
        headers=auth_headers(token1),
    )
    assert r.status_code == 200

    # article from team B not visible
    r = client.get(f"/articles/{art_b}", headers=auth_headers(token1))
    assert r.status_code == 404

    # switch to team B
    r = client.post(
        "/teams/switch",
        json={"team_id": team_b},
        headers=auth_headers(token1),
    )
    assert r.status_code == 200

    # article from team A not visible
    r = client.get(f"/articles/{art_a}", headers=auth_headers(token1))
    assert r.status_code == 404

    # invite second user to team B
    r = client.post(
        f"/teams/{team_b}/invite",
        json={"email": "u2@example.com"},
        headers=auth_headers(token1),
    )
    assert r.status_code == 200

    # second user switches to team B
    r = client.post(
        "/teams/switch",
        json={"team_id": team_b},
        headers=auth_headers(token2),
    )
    assert r.status_code == 200

    # second user can see article from team B
    r = client.get(f"/articles/{art_b}", headers=auth_headers(token2))
    assert r.status_code == 200

    # but not article from team A
    r = client.get(f"/articles/{art_a}", headers=auth_headers(token2))
    assert r.status_code == 404
