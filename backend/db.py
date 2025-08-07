from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
import os
import time
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def wait_for_db(max_attempts: int = 10, delay: int = 1) -> None:
    """Attempt to connect to the database until it is ready."""
    for _ in range(max_attempts):
        try:
            with engine.connect():
                return
        except OperationalError:
            time.sleep(delay)
    raise RuntimeError("Database is not ready")
