from typing import Optional

from rank_bm25 import BM25Okapi
import chromadb
from sentence_transformers import SentenceTransformer

from config import config as app_config
from rag.vector_store import get_collection, get_embedding_model

_bm25_corpus: Optional[list[str]] = None
_bm25_index: Optional[BM25Okapi] = None
_bm25_ids: Optional[list[str]] = None


def _ensure_bm25_index():
    global _bm25_corpus, _bm25_index, _bm25_ids
    if _bm25_index is not None:
        return

    collection = get_collection()
    results = collection.get(include=["documents", "metadatas"])

    if not results["ids"]:
        _bm25_corpus = []
        _bm25_index = None
        _bm25_ids = []
        return

    _bm25_corpus = results["documents"] or []
    _bm25_ids = results["ids"]
    tokenized = [doc.lower().split() for doc in _bm25_corpus]
    _bm25_index = BM25Okapi(tokenized)


def invalidate_bm25_cache():
    global _bm25_corpus, _bm25_index, _bm25_ids
    _bm25_corpus = None
    _bm25_index = None
    _bm25_ids = None


class DocumentChunk:
    def __init__(self, doc_id: str, content: str, metadata: dict, score: float):
        self.doc_id = doc_id
        self.content = content
        self.metadata = metadata
        self.score = score

    def __repr__(self):
        return f"DocumentChunk(source={self.metadata.get('source')}, score={self.score:.3f})"


def retrieve(query: str, top_k: int = None, alpha: float = None) -> list[DocumentChunk]:
    if top_k is None:
        top_k = app_config.rag.top_k
    if alpha is None:
        alpha = app_config.rag.hybrid_alpha

    semantic_results = _semantic_search(query, top_k * 2)
    lexical_results = _lexical_search(query, top_k * 2)

    merged = _reciprocal_rank_fusion(semantic_results, lexical_results, k=60)

    sorted_results = sorted(merged.values(), key=lambda x: x.score, reverse=True)
    return sorted_results[:top_k]


def _semantic_search(query: str, top_k: int) -> list[DocumentChunk]:
    model = get_embedding_model()
    collection = get_collection()
    query_embedding = model.encode([query]).tolist()

    results = collection.query(
        query_embeddings=query_embedding,
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    if results["ids"] and results["ids"][0]:
        for i, doc_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][i] if results.get("distances") else 0.0
            sim_score = 1.0 - min(distance, 1.0)
            chunks.append(DocumentChunk(
                doc_id=doc_id,
                content=results["documents"][0][i],
                metadata=results["metadatas"][0][i],
                score=sim_score,
            ))
    return chunks


def _lexical_search(query: str, top_k: int) -> list[DocumentChunk]:
    _ensure_bm25_index()
    if _bm25_index is None:
        return []

    tokenized_query = query.lower().split()
    scores = _bm25_index.get_scores(tokenized_query)
    indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]

    max_score = max(scores) if len(scores) > 0 else 1.0

    chunks = []
    for idx, score in indexed:
        chunks.append(DocumentChunk(
            doc_id=_bm25_ids[idx],
            content=_bm25_corpus[idx],
            metadata={"source": "bm25", "chunk_index": idx},
            score=score / max_score if max_score > 0 else 0.0,
        ))
    return chunks


def _reciprocal_rank_fusion(
    semantic: list[DocumentChunk],
    lexical: list[DocumentChunk],
    k: int = 60,
) -> dict[str, DocumentChunk]:
    merged: dict[str, DocumentChunk] = {}

    for rank, chunk in enumerate(semantic):
        rrf_score = 1.0 / (k + rank + 1)
        if chunk.doc_id in merged:
            merged[chunk.doc_id].score += rrf_score * 0.5
        else:
            new_chunk = DocumentChunk(chunk.doc_id, chunk.content, chunk.metadata, rrf_score * 0.5)
            merged[chunk.doc_id] = new_chunk

    for rank, chunk in enumerate(lexical):
        rrf_score = 1.0 / (k + rank + 1)
        if chunk.doc_id in merged:
            merged[chunk.doc_id].score += rrf_score * 0.5
        else:
            new_chunk = DocumentChunk(chunk.doc_id, chunk.content, chunk.metadata, rrf_score * 0.5)
            merged[chunk.doc_id] = new_chunk

    return merged


def format_retrieved_context(chunks: list[DocumentChunk]) -> str:
    if not chunks:
        return "Aucun document pertinent trouvé."

    lines = []
    for i, chunk in enumerate(chunks):
        source = chunk.metadata.get("source", chunk.metadata.get("filename", "inconnu"))
        lines.append(f"--- Document {i+1} (source: {source}, pertinence: {chunk.score:.2f}) ---")
        lines.append(chunk.content)
        lines.append("")
    return "\n".join(lines)
