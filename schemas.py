from pydantic import BaseModel

class ArticleCreate(BaseModel):
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
