from pydantic import BaseModel, EmailStr, constr
from uuid import UUID
from typing import List, Optional
from datetime import datetime

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


class UserCreate(BaseModel):
    email: EmailStr
    password: constr(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    is_active: bool
    roles: List[str] = []

    class Config:
        orm_mode = True


class AdminUserOut(BaseModel):
    id: UUID
    email: EmailStr
    roles: List[str] = []
    is_active: bool
    created_at: datetime

    class Config:
        orm_mode = True


class RoleUpdateRequest(BaseModel):
    roles: List[str]


class PasswordResetRequest(BaseModel):
    new_password: constr(min_length=8)


class RegisterResponse(BaseModel):
    user_id: UUID
    email: EmailStr
    team_id: UUID
    access_token: str
    refresh_token: str
