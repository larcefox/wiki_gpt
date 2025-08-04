from pydantic import BaseModel
from uuid import UUID

class ArticleCreate(BaseModel):
    title: str
    content: str


class ArticleUpdate(BaseModel):
    title: str
    content: str

class ArticleOut(BaseModel):
    id: str
    title: str
    content: str


class ArticleSearchHit(BaseModel):
    id: str
    title: str
    content: str
    score: float
