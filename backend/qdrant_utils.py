import os

import requests
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams
from typing import List, Dict
from uuid import UUID as UUID_cls
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


def delete_vector(article_id: str):
    """Remove vector representation of an article from Qdrant.

    The previous implementation tried to pass a dictionary with a ``points``
    key as the selector which is no longer supported by the version of the
    ``qdrant-client`` library used in this project.  The client now expects the
    list of point IDs directly as ``points_selector``.  Passing the dictionary
    caused a ``ValueError`` and ultimately a 500 error when deleting an
    article.  By providing the list of IDs directly we ensure the vector is
    deleted correctly.
    """

    # ``points_selector`` accepts a list of ids to delete.  The article id is
    # already a string UUID, so we can forward it as is.
    client.delete(collection_name=COLLECTION_NAME, points_selector=[article_id])


def search_vector(vector: List[float], db: Session, team_id, limit: int = 5) -> List[ArticleSearchHit]:
    hits = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=vector,
        limit=limit,
        with_payload=True,
    )

    ids = [UUID_cls(str(hit.id)) for hit in hits]
    scores = {str(hit.id): hit.score for hit in hits}

    articles = (
        db.query(Article)
        .filter(
            Article.id.in_(ids),
            Article.is_deleted == False,
            Article.team_id == team_id,
        )
        .all()
    )
    return [
        ArticleSearchHit(
            id=str(a.id),
            title=a.title,
            content=a.content,
            score=scores[str(a.id)],
            tags=a.tags.split(",") if a.tags else [],
            group_id=a.group_id,
        )
        for a in articles
    ]


def rerank_with_llm(query: str, hits: List[ArticleSearchHit]) -> List[ArticleSearchHit]:
    """Re-rank search hits using YandexGPT if credentials are set."""
    if not (YANDEX_OAUTH_TOKEN and YANDEX_FOLDER_ID) or not hits:
        return hits

    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {
        "Authorization": f"Bearer {YANDEX_OAUTH_TOKEN}",
        "Content-Type": "application/json",
    }

    parts = []
    for idx, hit in enumerate(hits, 1):
        parts.append(f"{idx}. id={hit.id} title={hit.title}\n{hit.content}")
    prompt = (
        "Ты – поисковый ранжировщик. По запросу пользователя упорядочи статьи по релевантности."
        " Верни JSON-массив ID в порядке убывания релевантности.\n"
        f"Запрос: {query}\n\n" + "\n\n".join(parts)
    )

    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {"stream": False, "temperature": 0.0, "maxTokens": 200},
        "messages": [{"role": "user", "text": prompt}],
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            return hits
        data = r.json()
        alternatives = data.get("result", {}).get("alternatives") or data.get("alternatives")
        text = alternatives[0]["message"].get("text", "") if alternatives else ""
        order = [s.strip() for s in text.split() if s.strip() in {h.id for h in hits}]
        if order:
            id_to_hit: Dict[str, ArticleSearchHit] = {h.id: h for h in hits}
            return [id_to_hit[i] for i in order if i in id_to_hit]
    except Exception:
        return hits
    return hits

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
