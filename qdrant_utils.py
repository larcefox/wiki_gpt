import os

import requests
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams
from typing import List
from sqlalchemy.orm import Session
from models import Article
from schemas import ArticleSearchHit

load_dotenv()

YANDEX_OAUTH_TOKEN = os.getenv("YANDEX_OAUTH_TOKEN")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")
QDRANT_URL = os.getenv("QDRANT_URL")
YANDEX_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/textEmbedding"

client = QdrantClient(url=QDRANT_URL)

COLLECTION_NAME = "articles"
VECTOR_SIZE = 256

# Function to get Yandex embedding for a given text
def get_yandex_embedding(text: str, token: str, folder_id: str) -> list[float]:
    url = YANDEX_API_URL
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "modelUri": f"emb://{folder_id}/text-search-query/latest",
        "text": text
    }
    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()["embedding"]

def ensure_collection():
    if COLLECTION_NAME not in [c.name for c in client.get_collections().collections]:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=VECTOR_SIZE, distance=Distance.COSINE)
        )


def insert_vector(article_id: str, embedding: list[float]):
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=article_id, vector=embedding, payload={})]
    )


def search_vector(vector: List[float], db: Session, limit: int = 5) -> List[ArticleSearchHit]:
    hits = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=vector,
        limit=limit,
        with_payload=True
    )

    ids = [hit.id for hit in hits]
    scores = {str(hit.id): hit.score for hit in hits}

    articles = db.query(Article).filter(Article.id.in_(ids)).all()
    return [
        ArticleSearchHit(
            id=str(a.id),
            title=a.title,
            content=a.content,
            score=scores[str(a.id)]
        )
        for a in articles
    ]

def embed_text(text: str) -> list[float]:
    headers = {
        "Authorization": f"Bearer {YANDEX_OAUTH_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "modelUri": f"emb://{YANDEX_FOLDER_ID}/text-search-query/latest",
        "text": text
    }
    response = requests.post(YANDEX_API_URL, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()["embedding"]
