from pydantic import BaseModel
from uuid import UUID
from typing import List, Optional

class ArticleCreate(BaseModel):
    title: str
    content: str
    tags: List[str] = []


class ArticleUpdate(BaseModel):
    title: str
    content: str
    tags: List[str] = []

class ArticleOut(BaseModel):
    id: str
    title: str
    content: str
    tags: List[str] = []


class ArticleSearchHit(BaseModel):
    id: str
    title: str
    content: str
    score: float
    tags: List[str] = []


class ArticleVersionOut(BaseModel):
    id: str
    article_id: str
    title: str
    content: str
    tags: List[str] = []
    created_at: str


class ArticleSearchQuery(BaseModel):
    q: str
    tags: Optional[List[str]] = None
