from fastapi import Body, Depends, FastAPI, HTTPException, APIRouter
from uuid import UUID
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import inspect, text
from db import get_db, SessionLocal, engine, wait_for_db
from models import Article, ArticleVersion, ArticleGroup, Base, User, Role
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
    ArticleGroupIn,
    ArticleGroupOut,
    ArticleGroupTreeNode,
    AdminUserOut,
    RoleUpdateRequest,
    PasswordResetRequest,
)

wait_for_db()
Base.metadata.create_all(bind=engine)


def ensure_columns():
    inspector = inspect(engine)
    with engine.begin() as conn:
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

app = FastAPI()
app.include_router(auth_router, prefix="/auth")

admin_router = APIRouter(prefix="/admin")


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


app.include_router(admin_router)


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


@app.post("/articles/", response_model=ArticleOut)
def create_article(
    article: ArticleCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles(["author"])),
):
    db_article = Article(
        title=article.title,
        content=article.content,
        tags=",".join(article.tags),
        group_id=article.group_id,
        team_id=current_user.team_id,
    )
    db.add(db_article)
    db.commit()
    db.refresh(db_article)

    embedding = embed_text(f"{article.title}\n{article.content}")
    insert_vector(str(db_article.id), embedding)

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
    insert_vector(str(db_article.id), embedding)

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
    hits = search_vector(query_embedding, db=db, team_id=current_user.team_id)
    if query.tags:
        required = set(query.tags)
        hits = [h for h in hits if required.issubset(set(h.tags))]
    if query.group_id:
        hits = [h for h in hits if h.group_id == query.group_id]
    hits = rerank_with_llm(query.q, hits)
    return hits


def save_version(article: Article, db: Session):
    version = ArticleVersion(
        article_id=article.id,
        title=article.title,
        content=article.content,
        tags=article.tags,
    )
    db.add(version)
    db.commit()
