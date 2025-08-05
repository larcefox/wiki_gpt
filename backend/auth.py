import os
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from collections import deque
from uuid import UUID as UUID_cls

from db import get_db, SessionLocal
from models import User, Role, UserRole, Team
from schemas import (
    UserCreate,
    LoginRequest,
    Token,
    RefreshTokenRequest,
    UserOut,
    RegisterResponse,
)

SECRET_KEY = os.getenv("JWT_SECRET", "secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_MINUTES = int(os.getenv("REFRESH_TOKEN_EXPIRE_MINUTES", "43200"))

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
login_attempts: dict[str, deque[datetime]] = {}


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_token(data: dict, expires_delta: timedelta) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(user: User) -> str:
    data = {
        "sub": str(user.id),
        "email": user.email,
        "roles": [r.code for r in user.roles],
    }
    return create_token(data, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))


def create_refresh_token(user: User) -> str:
    return create_token({"sub": str(user.id)}, timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES))


def get_current_user(
    db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    user_uuid = UUID_cls(user_id)
    user = db.query(User).filter(User.id == user_uuid).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def require_roles(required_roles: List[str]):
    def role_checker(user: User = Depends(get_current_user)) -> User:
        user_roles = {role.code for role in user.roles}
        if "admin" in user_roles or set(required_roles).issubset(user_roles):
            return user
        raise HTTPException(status_code=403, detail="Not enough permissions")

    return role_checker


@router.post("/register", response_model=RegisterResponse)
def register(user: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == user.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    team = Team(name=f"Team of {user.email}")
    db.add(team)
    db.flush()

    db_user = User(
        email=user.email,
        password_hash=get_password_hash(user.password),
        team_id=team.id,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    for code in ["author", "reader"]:
        role = db.query(Role).filter(Role.code == code).first()
        if role:
            db.add(UserRole(user_id=db_user.id, role_code=role.code))
    db.commit()
    db.refresh(db_user)

    access = create_access_token(db_user)
    refresh = create_refresh_token(db_user)

    return RegisterResponse(
        user_id=db_user.id,
        email=db_user.email,
        team_id=db_user.team_id,
        access_token=access,
        refresh_token=refresh,
    )


@router.post("/login", response_model=Token)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    attempts = login_attempts.setdefault(data.email, deque())
    while attempts and now - attempts[0] > timedelta(minutes=1):
        attempts.popleft()
    if len(attempts) >= 5:
        raise HTTPException(status_code=429, detail="Too many login attempts")
    user = db.query(User).filter(User.email == data.email).first()
    if not user or not verify_password(data.password, user.password_hash):
        attempts.append(now)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    attempts.clear()
    access = create_access_token(user)
    refresh = create_refresh_token(user)
    return Token(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=Token)
def refresh(token: RefreshTokenRequest, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    access = create_access_token(user)
    refresh_token = create_refresh_token(user)
    return Token(access_token=access, refresh_token=refresh_token)


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return UserOut(
        id=current_user.id,
        email=current_user.email,
        is_active=current_user.is_active,
        roles=[r.code for r in current_user.roles],
    )


def init_roles():
    db = SessionLocal()
    try:
        for code in ["admin", "author", "reader"]:
            if not db.query(Role).filter(Role.code == code).first():
                db.add(Role(code=code))
        db.commit()
    finally:
        db.close()
