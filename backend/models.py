from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Boolean, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()


class Team(Base):
    __tablename__ = "teams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="team")
    articles = relationship("Article", back_populates="team")


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"))

    team = relationship("Team", back_populates="users")
    roles = relationship("Role", secondary="user_roles", back_populates="users")


class Role(Base):
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String, unique=True, nullable=False)

    users = relationship("User", secondary="user_roles", back_populates="roles")


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    role_code = Column(String, ForeignKey("roles.code"), primary_key=True)


class ArticleGroup(Base):
    __tablename__ = "article_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("article_groups.id"), nullable=True)
    prompt_template = Column(Text, nullable=True)
    order = Column(Integer, nullable=True)

    parent = relationship("ArticleGroup", remote_side=[id], backref="children")

    articles = relationship("Article", back_populates="group")


class Article(Base):
    __tablename__ = "articles"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    group_id = Column(UUID(as_uuid=True), ForeignKey("article_groups.id"), nullable=True)
    team_id = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True)
    is_deleted = Column(Boolean, default=False)

    group = relationship("ArticleGroup", back_populates="articles")
    team = relationship("Team", back_populates="articles")


class ArticleVersion(Base):
    """Historical version of an article."""

    __tablename__ = "article_versions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id = Column(UUID(as_uuid=True), index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
