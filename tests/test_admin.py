import os, sys, types, pathlib
import pytest
from fastapi.testclient import TestClient

# configure environment and stub qdrant
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
from backend.models import User, Role, UserRole, DEFAULT_BASE_PROMPT

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


def test_admin_panel():
    admin = register("admin@example.com")
    user = register("user@example.com")

    # grant admin role to admin user
    db = SessionLocal()
    try:
        admin_db = db.query(User).filter(User.email == "admin@example.com").first()
        role_admin = db.query(Role).filter(Role.code == "admin").first()
        db.add(UserRole(user_id=admin_db.id, role_code=role_admin.code))
        db.commit()
    finally:
        db.close()

    # login again to get token with admin role
    r = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "password123"}
    )
    assert r.status_code == 200
    admin_token = r.json()["access_token"]

    # non-admin cannot access
    r = client.get("/admin/users", headers=auth_headers(user["access_token"]))
    assert r.status_code == 403

    # admin can list users
    r = client.get("/admin/users", headers=auth_headers(admin_token))
    assert r.status_code == 200
    users = r.json()
    assert any(u["email"] == "user@example.com" for u in users)

    # update roles
    r = client.post(
        f"/admin/users/{user['user_id']}/roles",
        json={"roles": ["reader"]},
        headers=auth_headers(admin_token),
    )
    assert r.status_code == 200

    # check roles updated
    r = client.post(
        "/auth/login", json={"email": "user@example.com", "password": "password123"}
    )
    token = r.json()["access_token"]
    r = client.get("/auth/me", headers=auth_headers(token))
    assert r.json()["roles"] == ["reader"]

    # reset password
    r = client.post(
        f"/admin/users/{user['user_id']}/password",
        json={"new_password": "NewPass123"},
        headers=auth_headers(admin_token),
    )
    assert r.status_code == 200

    # old password no longer works
    r = client.post(
        "/auth/login", json={"email": "user@example.com", "password": "password123"}
    )
    assert r.status_code == 401

    # new password works
    r = client.post(
        "/auth/login", json={"email": "user@example.com", "password": "NewPass123"}
    )
    assert r.status_code == 200

    # admin can list teams
    r = client.get("/admin/teams", headers=auth_headers(admin_token))
    assert r.status_code == 200
    teams = r.json()
    assert any(t["id"] == admin["team_id"] for t in teams)

    # update team model
    r = client.post(
        f"/admin/teams/{admin['team_id']}/model",
        json={"llm_model": "yandexgpt"},
        headers=auth_headers(admin_token),
    )
    assert r.status_code == 200

    # verify model updated
    r = client.get("/admin/teams", headers=auth_headers(admin_token))
    team = next(t for t in r.json() if t["id"] == admin["team_id"])
    assert team["llm_model"] == "yandexgpt"
    assert team["base_prompt"] == DEFAULT_BASE_PROMPT

    # update team base prompt
    new_prompt = "Новый базовый промпт"
    r = client.post(
        f"/admin/teams/{admin['team_id']}/prompt",
        json={"base_prompt": new_prompt},
        headers=auth_headers(admin_token),
    )
    assert r.status_code == 200

    # verify base prompt updated
    r = client.get("/admin/teams", headers=auth_headers(admin_token))
    team = next(t for t in r.json() if t["id"] == admin["team_id"])
    assert team["base_prompt"] == new_prompt
