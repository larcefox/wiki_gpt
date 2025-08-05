from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()


class ArticleGroup(Base):
    __tablename__ = "article_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    articles = relationship("Article", back_populates="group")

class Article(Base):
    __tablename__ = "articles"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    group_id = Column(UUID(as_uuid=True), ForeignKey("article_groups.id"), nullable=True)

    group = relationship("ArticleGroup", back_populates="articles")


class ArticleVersion(Base):
    """Historical version of an article."""

    __tablename__ = "article_versions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id = Column(UUID(as_uuid=True), index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
