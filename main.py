from fastapi import Body, Depends, FastAPI
from typing import List
from sqlalchemy.orm import Session
from db import SessionLocal, engine
from models import Article, Base
from qdrant_utils import (embed_text, ensure_collection, insert_vector,
                          search_vector)
from schemas import ArticleCreate, ArticleOut, ArticleSearchHit

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


@app.post("/articles/search/", response_model=List[ArticleSearchHit])
def search_articles(q: str = Body(..., embed=True), db: Session = Depends(get_db)):
    query_embedding = embed_text(q)
    return search_vector(query_embedding, db=db)
