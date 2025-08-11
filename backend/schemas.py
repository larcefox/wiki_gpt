from pydantic import BaseModel, EmailStr, constr
from uuid import UUID
from typing import List, Optional
from datetime import datetime

class ArticleCreate(BaseModel):
    title: str
    content: str
    tags: List[str] = []
    group_id: Optional[UUID] = None
    group: Optional["ArticleGroupIn"] = None


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
    group_id: Optional[UUID] = None


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
    group_id: Optional[UUID] = None


class ArticleGroupIn(BaseModel):
    name: str
    description: Optional[str] = None
    parent_id: Optional[UUID] = None
    prompt_template: Optional[str] = None
    order: Optional[int] = None


class ArticleGroupOut(ArticleGroupIn):
    id: UUID

    class Config:
        orm_mode = True


class ArticleGroupTreeNode(ArticleGroupOut):
    children: List["ArticleGroupTreeNode"] = []
    articles: List["ArticleOut"] = []


class ArticleWithGroup(ArticleOut):
    group: Optional[ArticleGroupOut] = None


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
    team_id: Optional[UUID] = None

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


class TeamCreate(BaseModel):
    name: str


class TeamOut(BaseModel):
    id: UUID
    name: str

    class Config:
        orm_mode = True


class TeamUserOut(BaseModel):
    id: UUID
    email: EmailStr

    class Config:
        orm_mode = True


class TeamWithUsers(TeamOut):
    users: List[TeamUserOut] = []


class TeamInviteRequest(BaseModel):
    email: EmailStr


class TeamUserAction(BaseModel):
    user_id: UUID


class TeamSwitchRequest(BaseModel):
    team_id: UUID


ArticleGroupTreeNode.update_forward_refs()
ArticleWithGroup.update_forward_refs()
ArticleCreate.update_forward_refs()
