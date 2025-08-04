from fastapi import Body, Depends, FastAPI, HTTPException
from uuid import UUID
from typing import List
from sqlalchemy.orm import Session
from db import SessionLocal, engine
from models import Article, Base
from qdrant_utils import (embed_text, ensure_collection, insert_vector,
                          search_vector)
from schemas import ArticleCreate, ArticleOut, ArticleSearchHit, ArticleUpdate

Base.metadata.create_all(bind=engine)
ensure_collection()

app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/articles/", response_model=ArticleOut)
def create_article(article: ArticleCreate, db: Session = Depends(get_db)):
    db_article = Article(title=article.title, content=article.content)
    db.add(db_article)
    db.commit()
    db.refresh(db_article)

    embedding = embed_text(f"{article.title}\n{article.content}")
    insert_vector(db_article.id, embedding)

    return db_article


@app.put("/articles/{article_id}", response_model=ArticleOut)
def update_article(article_id: UUID, article: ArticleUpdate, db: Session = Depends(get_db)):
    db_article = db.query(Article).filter(Article.id == str(article_id)).first()
    if db_article is None:
        raise HTTPException(status_code=404, detail="Article not found")

    db_article.title = article.title
    db_article.content = article.content
    db.commit()
    db.refresh(db_article)

    embedding = embed_text(f"{article.title}\n{article.content}")
    insert_vector(db_article.id, embedding)

    return db_article


@app.post("/articles/search/", response_model=List[ArticleSearchHit])
def search_articles(q: str = Body(..., embed=True), db: Session = Depends(get_db)):
    query_embedding = embed_text(q)
    return search_vector(query_embedding, db=db)
