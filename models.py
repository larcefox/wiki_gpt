from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import uuid

Base = declarative_base()

class Article(Base):
    __tablename__ = "articles"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class ArticleVersion(Base):
    """Historical version of an article."""

    __tablename__ = "article_versions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    article_id = Column(String, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
