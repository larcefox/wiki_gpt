from pydantic import BaseModel
from uuid import UUID
from typing import List, Optional

class ArticleCreate(BaseModel):
    title: str
    content: str
    tags: List[str] = []
    group_id: Optional[UUID] = None


class ArticleUpdate(BaseModel):
    title: str
    content: str
    tags: List[str] = []
    group_id: Optional[UUID] = None

class ArticleOut(BaseModel):
    id: UUID
    title: str
    content: str
    tags: List[str] = []
    group_id: Optional[UUID]

    class Config:
        orm_mode = True

class ArticleSearchHit(BaseModel):
    id: str
    title: str
    content: str
    score: float
    tags: List[str] = []


class ArticleVersionOut(BaseModel):
    id: UUID
    article_id: UUID
    title: str
    content: str
    tags: List[str] = []
    created_at: str


class ArticleSearchQuery(BaseModel):
    q: str
    tags: Optional[List[str]] = None


class ArticleGroupCreate(BaseModel):
    name: str
    description: Optional[str] = None


class ArticleGroupOut(BaseModel):
    id: UUID
    name: str
    description: Optional[str]

    class Config:
        orm_mode = True
