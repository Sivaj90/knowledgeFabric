"""Embed step (HLD §7.3): call landmark-text-embedding-3-large via the
Landmark LiteLLM proxy (OpenAI-compatible API), only for chunks whose
content_hash changed (dedup — caller decides which chunks need embedding
before calling this; this module just does the API call).

CRITICAL: must always pass dimensions=EMBEDDING_DIM (1536). pgvector 0.6.2
hard-caps HNSW/IVFFlat indexes at 2000 dims, but this model's native output
is 3072 -- see kb_fabric.models.EMBEDDING_DIM docstring for the full story.
Omitting `dimensions` here would silently produce 3072-dim vectors that
don't fit the `chunks.embedding` column and break every write.
"""

from openai import OpenAI

from kb_fabric.config import get_settings
from kb_fabric.models import EMBEDDING_DIM


def get_embedding_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(base_url=settings.litellm_base_url, api_key=settings.litellm_api_key)


def embed_texts(texts: list[str], client: OpenAI | None = None) -> list[list[float]]:
    """Embed a batch of chunk texts. Returns one EMBEDDING_DIM-length vector
    per input text, in the same order."""
    if not texts:
        return []

    settings = get_settings()
    client = client or get_embedding_client()
    response = client.embeddings.create(
        model=settings.embedding_model,
        input=texts,
        dimensions=EMBEDDING_DIM,
    )
    # OpenAI API guarantees response.data is ordered to match input order.
    return [item.embedding for item in response.data]
