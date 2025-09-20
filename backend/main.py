import os
import inspect
import logging
import requests
from fastapi import Body, Depends, FastAPI, HTTPException, APIRouter, Response
from uuid import UUID
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import inspect as sa_inspect, text, or_
from db import get_db, SessionLocal, engine, wait_for_db
from models import Article, ArticleVersion, ArticleGroup, Base, User, Role, Team, UserTeam
from auth import router as auth_router, require_roles, init_roles, check_admin_role, get_password_hash
from qdrant_utils import (
    embed_text,
    ensure_collection,
    insert_vector,
    search_vector,
    delete_vector,
    rerank_with_llm,
)
from schemas import (
    ArticleCreate,
    ArticleOut,
    ArticleSearchHit,
    ArticleUpdate,
    ArticleVersionOut,
    ArticleSearchQuery,
    SearchAnswerRequest,
    SearchAnswerResponse,
    ArticleListItem,
    ArticleGroupIn,
    ArticleGroupOut,
    ArticleGroupTreeNode,
    AdminUserOut,
    RoleUpdateRequest,
    PasswordResetRequest,
    TeamCreate,
    TeamOut,
    TeamWithUsers,
    TeamInviteRequest,
    TeamUserAction,
    TeamSwitchRequest,
    TeamModelUpdate,
)

logger = logging.getLogger(__name__)

YANDEX_OAUTH_TOKEN = os.getenv("YANDEX_OAUTH_TOKEN")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

wait_for_db()
Base.metadata.create_all(bind=engine)


def ensure_columns():
    inspector = sa_inspect(engine)
    with engine.begin() as conn:
        team_cols = [c["name"] for c in inspector.get_columns("teams")]
        if "llm_model" not in team_cols:
            conn.execute(
                text(
                    "ALTER TABLE teams ADD COLUMN llm_model TEXT DEFAULT 'yandexgpt-lite'"
                )
            )

        user_cols = [c["name"] for c in inspector.get_columns("users")]
        if "team_id" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN team_id UUID"))

        article_cols = [c["name"] for c in inspector.get_columns("articles")]
        if "tags" not in article_cols:
            conn.execute(text("ALTER TABLE articles ADD COLUMN tags TEXT DEFAULT ''"))
        if "group_id" not in article_cols:
            conn.execute(text("ALTER TABLE articles ADD COLUMN group_id UUID"))
        if "team_id" not in article_cols:
            conn.execute(text("ALTER TABLE articles ADD COLUMN team_id UUID"))
        if "is_deleted" not in article_cols:
            conn.execute(
                text(
                    "ALTER TABLE articles ADD COLUMN is_deleted BOOLEAN DEFAULT FALSE"
                )
            )

        version_cols = [c["name"] for c in inspector.get_columns("article_versions")]
        if "tags" not in version_cols:
            conn.execute(
                text("ALTER TABLE article_versions ADD COLUMN tags TEXT DEFAULT ''")
            )

        group_cols = [c["name"] for c in inspector.get_columns("article_groups")]
        if "parent_id" not in group_cols:
            conn.execute(text("ALTER TABLE article_groups ADD COLUMN parent_id UUID"))
        if "prompt_template" not in group_cols:
            conn.execute(text("ALTER TABLE article_groups ADD COLUMN prompt_template TEXT"))
        if "order" not in group_cols:
            conn.execute(text('ALTER TABLE article_groups ADD COLUMN "order" INT'))


ensure_columns()
ensure_collection()
init_roles()


DEFAULT_GROUP_PROMPT = os.getenv("DEFAULT_GROUP_PROMPT", "You are a helpful assistant")


def resolve_prompt(db: Session, group_id: Optional[UUID]) -> str:
    if group_id:
        group = db.query(ArticleGroup).filter(ArticleGroup.id == group_id).first()
        if group and group.prompt_template:
            return group.prompt_template
    return DEFAULT_GROUP_PROMPT


def _search_with_optional_group(vector, db, team_id, group_id=None, limit=5):
    params = inspect.signature(search_vector).parameters
    if "group_id" in params:
        return search_vector(
            vector, db=db, team_id=team_id, group_id=group_id, limit=limit
        )
    hits = search_vector(vector, db=db, team_id=team_id, limit=limit)
    if group_id:
        hits = [h for h in hits if h.group_id == group_id]
    return hits


def ensure_user_team_memberships():
    db = SessionLocal()
    try:
        users = db.query(User).all()
        for u in users:
            if u.team_id:
                exists = (
                    db.query(UserTeam)
                    .filter_by(user_id=u.id, team_id=u.team_id)
                    .first()
                )
                if not exists:
                    db.add(UserTeam(user_id=u.id, team_id=u.team_id))
        db.commit()
    finally:
        db.close()


ensure_user_team_memberships()

app = FastAPI()
app.include_router(auth_router, prefix="/auth")

admin_router = APIRouter(prefix="/admin")
team_router = APIRouter(prefix="/teams")


@admin_router.get("/users", response_model=List[AdminUserOut])
def list_users(db: Session = Depends(get_db), current_user=Depends(check_admin_role)):
    users = db.query(User).all()
    return [
        AdminUserOut(
            id=u.id,
            email=u.email,
            roles=[r.code for r in u.roles],
            is_active=u.is_active,
            created_at=u.created_at,
        )
        for u in users
    ]


@admin_router.post("/users/{user_id}/roles")
def update_user_roles(
    user_id: UUID,
    req: RoleUpdateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(check_admin_role),
):
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot modify own roles")
    allowed = {"admin", "author", "reader"}
    if not set(req.roles).issubset(allowed):
        raise HTTPException(status_code=400, detail="Unknown roles")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.roles = []
    for code in req.roles:
        role = db.query(Role).filter(Role.code == code).first()
        if role:
            user.roles.append(role)
    db.commit()
    return {"status": "ok"}


@admin_router.post("/users/{user_id}/password")
def reset_user_password(
    user_id: UUID,
    req: PasswordResetRequest,
    db: Session = Depends(get_db),
    current_user=Depends(check_admin_role),
):
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot modify own password")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = get_password_hash(req.new_password)
    db.commit()
    return {"status": "ok"}


@admin_router.get("/teams", response_model=List[TeamOut])
def admin_list_teams(
    db: Session = Depends(get_db), current_user=Depends(check_admin_role)
):
    return db.query(Team).all()


@admin_router.post("/teams/{team_id}/model")
def admin_update_team_model(
    team_id: UUID,
    req: TeamModelUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(check_admin_role),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    team.llm_model = req.llm_model
    db.commit()
    return {"status": "ok"}

@team_router.get("/", response_model=List[TeamOut])
def list_my_teams(db: Session = Depends(get_db), current_user=Depends(require_roles(["reader"]))):
    return current_user.teams


@team_router.post("/", response_model=TeamOut)
def create_team(
    team: TeamCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    db_team = Team(name=team.name)
    db.add(db_team)
    db.flush()
    db.add(UserTeam(user_id=current_user.id, team_id=db_team.id))
    current_user.team_id = db_team.id
    db.commit()
    db.refresh(db_team)
    return db_team


@team_router.get("/{team_id}", response_model=TeamWithUsers)
def get_team(
    team_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team or team not in current_user.teams:
        raise HTTPException(status_code=404, detail="Team not found")

    # When returning SQLAlchemy models, we need to explicitly validate them
    # against the Pydantic schema so that nested relationships (like the team
    # users) are converted properly.  Constructing ``TeamWithUsers`` directly
    # with ``team.users`` would pass through the ``models.User`` instances
    # unchanged, leading to a validation error under Pydantic v2.  Using
    # ``model_validate`` with ``from_attributes=True`` tells Pydantic to read
    # data from object attributes and recursively convert nested models.
    return TeamWithUsers.model_validate(team, from_attributes=True)


@team_router.post("/{team_id}/invite")
def invite_user(
    team_id: UUID,
    req: TeamInviteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team or team not in current_user.teams:
        raise HTTPException(status_code=404, detail="Team not found")
    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if team not in user.teams:
        db.add(UserTeam(user_id=user.id, team_id=team.id))
        db.commit()
    return {"status": "ok"}


@team_router.post("/{team_id}/remove")
def remove_user(
    team_id: UUID,
    req: TeamUserAction,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team or team not in current_user.teams:
        raise HTTPException(status_code=404, detail="Team not found")
    assoc = db.query(UserTeam).filter_by(user_id=req.user_id, team_id=team_id).first()
    if not assoc:
        raise HTTPException(status_code=404, detail="User not in team")
    db.delete(assoc)
    user = db.query(User).filter(User.id == req.user_id).first()
    if user and user.team_id == team_id:
        new_assoc = db.query(UserTeam).filter_by(user_id=req.user_id).first()
        user.team_id = new_assoc.team_id if new_assoc else None
    db.commit()
    return {"status": "ok"}


@team_router.post("/switch")
def switch_team(
    req: TeamSwitchRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    assoc = db.query(UserTeam).filter_by(user_id=current_user.id, team_id=req.team_id).first()
    if not assoc:
        raise HTTPException(status_code=403, detail="Not a member of team")
    current_user.team_id = req.team_id
    db.commit()
    return {"status": "ok"}


app.include_router(admin_router)
app.include_router(team_router)


@app.post("/article-groups/", response_model=ArticleGroupOut)
def create_group(
    group: ArticleGroupIn,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["admin"])),
):
    db_group = ArticleGroup(
        name=group.name,
        description=group.description,
        parent_id=group.parent_id,
        prompt_template=group.prompt_template,
        order=group.order,
    )
    db.add(db_group)
    db.commit()
    db.refresh(db_group)
    return db_group


@app.get("/article-groups/flat", response_model=List[ArticleGroupOut])
def list_groups(
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    return db.query(ArticleGroup).order_by(ArticleGroup.order).all()


def _build_group_tree(groups, articles):
    nodes = {
        g.id: ArticleGroupTreeNode(
            id=g.id,
            name=g.name,
            description=g.description,
            parent_id=g.parent_id,
            prompt_template=g.prompt_template,
            order=g.order,
            children=[],
            articles=[],
        )
        for g in groups
    }

    for a in articles:
        node = nodes.get(a.group_id)
        if node:
            node.articles.append(
                ArticleOut(
                    id=a.id,
                    title=a.title,
                    content=a.content,
                    tags=a.tags.split(",") if a.tags else [],
                    group_id=a.group_id,
                )
            )

    roots: List[ArticleGroupTreeNode] = []
    for g in groups:
        node = nodes[g.id]
        if g.parent_id and g.parent_id in nodes:
            nodes[g.parent_id].children.append(node)
        else:
            roots.append(node)
    return roots


@app.get("/article-groups/tree", response_model=List[ArticleGroupTreeNode])
def groups_tree(
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    groups = db.query(ArticleGroup).order_by(ArticleGroup.order).all()
    articles = (
        db.query(Article)
        .filter(
            Article.is_deleted == False,
            Article.team_id == current_user.team_id,
        )
        .all()
    )
    return _build_group_tree(groups, articles)


@app.put("/article-groups/{group_id}", response_model=ArticleGroupOut)
def update_group(
    group_id: UUID,
    group: ArticleGroupIn,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["admin"])),
):
    db_group = db.query(ArticleGroup).filter(ArticleGroup.id == group_id).first()
    if not db_group:
        raise HTTPException(status_code=404, detail="Group not found")
    db_group.name = group.name
    db_group.description = group.description
    db_group.parent_id = group.parent_id
    db_group.prompt_template = group.prompt_template
    db_group.order = group.order
    db.commit()
    db.refresh(db_group)
    return db_group


@app.delete("/article-groups/{group_id}")
def delete_group(
    group_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["admin"])),
):
    db_group = db.query(ArticleGroup).filter(ArticleGroup.id == group_id).first()
    if not db_group:
        raise HTTPException(status_code=404, detail="Group not found")
    db.delete(db_group)
    db.commit()
    return {"status": "deleted"}


class AssignGroupRequest(BaseModel):
    group_id: Optional[UUID] = None


@app.post("/articles/{article_id}/assign-group")
def assign_group(
    article_id: UUID,
    req: AssignGroupRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["author"])),
):
    article = (
        db.query(Article)
        .filter(
            Article.id == article_id,
            Article.team_id == current_user.team_id,
            Article.is_deleted == False,
        )
        .first()
    )
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    article.group_id = req.group_id
    db.commit()
    db.refresh(article)
    return {"status": "ok", "group_id": article.group_id}


@app.get("/articles/", response_model=List[ArticleOut])
def list_articles(
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    articles = (
        db.query(Article)
        .filter(
            Article.is_deleted == False,
            Article.team_id == current_user.team_id,
        )
        .all()
    )
    return [
        ArticleOut(
            id=a.id,
            title=a.title,
            content=a.content,
            tags=a.tags.split(",") if a.tags else [],
            group_id=a.group_id,
        )
        for a in articles
    ]


@app.get("/articles/list", response_model=List[ArticleListItem])
def list_articles_brief(
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    tags: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    query = (
        db.query(Article)
        .filter(
            Article.is_deleted == False,
            Article.team_id == current_user.team_id,
        )
    )
    if q:
        ilike = f"%{q}%"
        query = query.filter(or_(Article.title.ilike(ilike), Article.content.ilike(ilike)))
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        for t in tag_list:
            query = query.filter(Article.tags.ilike(f"%{t}%"))
    articles = (
        query.order_by(Article.created_at.desc()).offset(offset).limit(limit).all()
    )
    return [
        ArticleListItem(
            id=a.id,
            title=a.title,
            tags=a.tags.split(",") if a.tags else [],
            created_at=a.created_at.isoformat(),
        )
        for a in articles
    ]


@app.post("/articles/", response_model=ArticleOut)
def create_article(
    article: ArticleCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["author"])),
):
    group_id = article.group_id
    if group_id is None and article.group is not None:
        db_group = ArticleGroup(
            name=article.group.name,
            description=article.group.description,
            parent_id=article.group.parent_id,
            prompt_template=article.group.prompt_template,
            order=article.group.order,
        )
        db.add(db_group)
        db.flush()
        group_id = db_group.id

    db_article = Article(
        title=article.title,
        content=article.content,
        tags=",".join(article.tags),
        group_id=group_id,
        team_id=current_user.team_id,
    )
    db.add(db_article)
    db.commit()
    db.refresh(db_article)

    embedding = embed_text(f"{article.title}\n{article.content}")
    insert_vector(str(db_article.id), embedding, group_id=str(db_article.group_id) if db_article.group_id else None)

    save_version(db_article, db)

    return ArticleOut(
        id=db_article.id,
        title=db_article.title,
        content=db_article.content,
        tags=db_article.tags.split(",") if db_article.tags else [],
        group_id=db_article.group_id,
    )

@app.put("/articles/{article_id}", response_model=ArticleOut)
def update_article(
    article_id: UUID,
    article: ArticleUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["author"])),
):
    db_article = (
        db.query(Article)
        .filter(
            Article.id == article_id,
            Article.is_deleted == False,
            Article.team_id == current_user.team_id,
        )
        .first()
    )
    if db_article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    db_article.title = article.title
    db_article.content = article.content
    db_article.tags = ",".join(article.tags)
    db_article.group_id = article.group_id
    db.commit()
    db.refresh(db_article)

    embedding = embed_text(f"{article.title}\n{article.content}")
    insert_vector(str(db_article.id), embedding, group_id=str(db_article.group_id) if db_article.group_id else None)

    save_version(db_article, db)

    return ArticleOut(
        id=db_article.id,
        title=db_article.title,
        content=db_article.content,
        tags=db_article.tags.split(",") if db_article.tags else [],
        group_id=db_article.group_id,
    )


@app.get("/articles/{article_id}", response_model=ArticleOut)
def get_article(
    article_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    db_article = (
        db.query(Article)
        .filter(
            Article.id == article_id,
            Article.is_deleted == False,
            Article.team_id == current_user.team_id,
        )
        .first()
    )
    if db_article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return ArticleOut(
        id=db_article.id,
        title=db_article.title,
        content=db_article.content,
        tags=db_article.tags.split(",") if db_article.tags else [],
        group_id=db_article.group_id,
    )


@app.get("/articles/{article_id}/related", response_model=List[ArticleSearchHit])
def related_articles(
    article_id: UUID,
    response: Response,
    limit: int = 5,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    db_article = (
        db.query(Article)
        .filter(
            Article.id == article_id,
            Article.is_deleted == False,
            Article.team_id == current_user.team_id,
        )
        .first()
    )
    if db_article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    try:
        embedding = embed_text(f"{db_article.title}\n{db_article.content}")
    except RuntimeError as e:
        logger.warning("Related articles embedding failed: %s", e)
        response.headers["X-Embeddings-Warning"] = str(e)
        return []
    hits = _search_with_optional_group(
        embedding,
        db=db,
        team_id=current_user.team_id,
        group_id=db_article.group_id,
        limit=limit + 1,
    )
    hits = [h for h in hits if h.id != str(article_id)]
    return hits[:limit]


@app.delete("/articles/{article_id}")
def delete_article(
    article_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["author"])),
):
    db_article = (
        db.query(Article)
        .filter(
            Article.id == article_id,
            Article.is_deleted == False,
            Article.team_id == current_user.team_id,
        )
        .first()
    )
    if db_article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    db_article.is_deleted = True
    db.commit()
    delete_vector(str(article_id))
    return {"status": "deleted"}


@app.get("/articles/{article_id}/history", response_model=List[ArticleVersionOut])
def article_history(
    article_id: UUID,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    article_exists = (
        db.query(Article)
        .filter(
            Article.id == article_id,
            Article.team_id == current_user.team_id,
            Article.is_deleted == False,
        )
        .first()
    )
    if not article_exists:
        raise HTTPException(status_code=404, detail="Article not found")
    versions = (
        db.query(ArticleVersion)
        .filter(ArticleVersion.article_id == article_id)
        .order_by(ArticleVersion.created_at.desc())
        .all()
    )
    return [
        ArticleVersionOut(
            id=v.id,
            article_id=v.article_id,
            title=v.title,
            content=v.content,
            tags=v.tags.split(",") if v.tags else [],
            created_at=v.created_at.isoformat(),
        )
        for v in versions
    ]



@app.post("/articles/search/", response_model=List[ArticleSearchHit])
def search_articles(
    query: ArticleSearchQuery = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    query_embedding = embed_text(query.q)
    hits = _search_with_optional_group(
        query_embedding,
        db=db,
        team_id=current_user.team_id,
        group_id=query.group_id,
    )
    if query.tags:
        required = set(query.tags)
        hits = [h for h in hits if required.issubset(set(h.tags))]
    group_prompt = resolve_prompt(db, query.group_id)
    team_model = (
        db.query(Team.llm_model)
        .filter(Team.id == current_user.team_id)
        .scalar()
        or "yandexgpt-lite"
    )
    hits = rerank_with_llm(
        query.q, hits, prompt_template=group_prompt, model=team_model
    )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits


@app.post("/articles/search/answer", response_model=SearchAnswerResponse)
def search_answer(
    req: SearchAnswerRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["reader"])),
):
    query_embedding = embed_text(req.q)
    hits = _search_with_optional_group(
        query_embedding,
        db=db,
        team_id=current_user.team_id,
        group_id=req.group_id,
        limit=req.top_k,
    )
    if req.tags:
        required = set(req.tags)
        hits = [h for h in hits if required.issubset(set(h.tags))]
    group_prompt = resolve_prompt(db, req.group_id)
    team_model = (
        db.query(Team.llm_model)
        .filter(Team.id == current_user.team_id)
        .scalar()
        or "yandexgpt-lite"
    )
    hits = rerank_with_llm(
        req.q, hits, prompt_template=group_prompt, model=team_model
    )
    hits.sort(key=lambda h: h.score, reverse=True)

    snippets: List[ArticleSearchHit] = []
    for h in hits:
        snippet = h.content[:200]
        snippets.append(ArticleSearchHit(**{**h.dict(), "content": snippet}))

    parts = [f"[{h.title}](wiki://{h.id})\n{h.content}" for h in snippets]
    context = "\n\n".join(parts)
    base_prompt = (
        "Сформулируй единый и связный ответ на запрос пользователя, "
        "опираясь только на приведённые выдержки."
    )
    prompt = f"{base_prompt}\n\nЗапрос: {req.q}\n\n{context}" if context else base_prompt

    answer = ""
    if context and YANDEX_OAUTH_TOKEN and YANDEX_FOLDER_ID:
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {
            "Authorization": f"Api-Key {YANDEX_OAUTH_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/{team_model}/latest",
            "completionOptions": {"stream": False, "temperature": 0.3, "maxTokens": 500},
            "messages": [{"role": "user", "text": prompt}],
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                data = r.json()
                alternatives = data.get("result", {}).get("alternatives") or data.get(
                    "alternatives"
                )
                answer = (
                    alternatives[0]["message"].get("text", "") if alternatives else ""
                )
        except Exception as e:
            logger.warning("LLM summary failed: %s", e)
    if not answer:
        if snippets:
            summary = "\n".join([f"{h.title}: {h.content}" for h in snippets])
            answer = f"На основе найденных статей:\n{summary}"
        else:
            answer = "Не удалось найти релевантные статьи."

    references = []
    if hits:
        references = [f"- [{h.title}](wiki://{h.id})" for h in hits]
    if references:
        answer = f"{answer.strip()}\n\nСсылки на статьи:\n" + "\n".join(references)

    logger.info("search_answer q=%s snippets=%s", req.q, [h.id for h in snippets])
    return SearchAnswerResponse(
        answer=answer,
        prompt_used=prompt,
        used_group_id=req.group_id,
    )


def save_version(article: Article, db: Session):
    version = ArticleVersion(
        article_id=article.id,
        title=article.title,
        content=article.content,
        tags=article.tags,
    )
    db.add(version)
    db.commit()
