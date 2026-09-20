from typing import Optional
import os
import re
import unicodedata

from rank_bm25 import BM25Okapi
import chromadb
from sentence_transformers import SentenceTransformer

from config import config as app_config
from rag.vector_store import get_collection, get_embedding_model

_bm25_corpus: Optional[list[str]] = None
_bm25_index: Optional[BM25Okapi] = None
_bm25_ids: Optional[list[str]] = None
_bm25_metadatas: Optional[list[dict]] = None

# Stop-words francais courts : sans eux, BM25 sature sur "de la", "et", "pour"...
_FR_STOPWORDS = {
    "au", "aux", "avec", "ce", "ces", "cette", "dans", "de", "des", "du", "elle",
    "en", "et", "est", "etre", "eu", "elle", "en", "et", "eux", "il", "ils",
    "je", "que", "qui", "quoi", "le", "la", "les", "l", "leur", "lui", "ma",
    "mais", "me", "meme", "mes", "moi", "mon", "ne", "nos", "notre", "nous",
    "on", "ou", "par", "pas", "pour", "qu", "que", "qui", "sa", "se", "ses",
    "son", "sur", "ta", "te", "tes", "toi", "ton", "tu", "un", "une", "vos",
    "votre", "vous", "d", "l", "s", "n", "m", "t", "y", "a", "et", "ou",
}


def _fold(text: str) -> str:
    """Minuscules + suppression des accents/ponctuation (e5/BM25 tolerant)."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", text)


def _tokenize(text: str) -> list[str]:
    """Tokenisation BM25 normalisee (accents plies, stop-words FR retires)."""
    tokens = [_fold(tok) for tok in (text or "").split()]
    return [t for t in tokens if len(t) > 1 and t not in _FR_STOPWORDS]


def _ensure_bm25_index():
    global _bm25_corpus, _bm25_index, _bm25_ids, _bm25_metadatas
    if _bm25_index is not None:
        return

    collection = get_collection()
    results = collection.get(include=["documents", "metadatas"])

    if not results["ids"]:
        _bm25_corpus = []
        _bm25_index = None
        _bm25_ids = []
        _bm25_metadatas = []
        return

    _bm25_corpus = results["documents"] or []
    _bm25_ids = results["ids"]
    _bm25_metadatas = results["metadatas"] or [{} for _ in _bm25_ids]
    tokenized = [_tokenize(doc) for doc in _bm25_corpus]
    _bm25_index = BM25Okapi(tokenized)


def invalidate_bm25_cache():
    global _bm25_corpus, _bm25_index, _bm25_ids, _bm25_metadatas
    _bm25_corpus = None
    _bm25_index = None
    _bm25_ids = None
    _bm25_metadatas = None


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

    # HYBRID_ALPHA (0.5 par defaut) : poids RRF semantique vs lexical
    w_sem = float(alpha)
    w_lex = 1.0 - w_sem
    merged = _reciprocal_rank_fusion(
        semantic_results, lexical_results, k=60, w_sem=w_sem, w_lex=w_lex
    )

    sorted_results = sorted(
        [c for c in merged.values() if c.score > 0], key=lambda x: x.score, reverse=True
    )
    return _dedup_adjacent(sorted_results, top_k)


def _dedup_adjacent(chunks: list[DocumentChunk], top_k: int) -> list[DocumentChunk]:
    """Retire les chunks adjacents du meme fichier deja retenus : evite de
    bruler le budget de contexte avec des voisins quasi-identiques."""
    if os.getenv("RAG_DEDUP_ADJACENT", "true").lower() not in ("1", "true", "yes"):
        return chunks[:top_k]
    selected: list[DocumentChunk] = []
    seen_keys: set = set()
    for c in chunks:
        src = str((c.metadata or {}).get("source", ""))
        idx = (c.metadata or {}).get("chunk_index")
        key = (src, idx)
        adjacent = any(
            src and src == s and isinstance(idx, int) and isinstance(i2, int)
            and abs(idx - i2) <= 1
            for (s, i2) in seen_keys
        )
        if adjacent:
            continue
        seen_keys.add(key)
        selected.append(c)
        if len(selected) >= top_k:
            break
    return selected


def _semantic_search(query: str, top_k: int) -> list[DocumentChunk]:
    from rag.vector_store import check_embedding_signature, get_prefixes

    check_embedding_signature()
    q_prefix, _ = get_prefixes()
    model = get_embedding_model()
    collection = get_collection()
    query_embedding = model.encode([f"{q_prefix}{query}"]).tolist()

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

    tokenized_query = _tokenize(query)
    if not tokenized_query:
        return []
    scores = _bm25_index.get_scores(tokenized_query)
    indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]

    max_score = max(scores) if len(scores) > 0 else 1.0

    chunks = []
    for idx, score in indexed:
        if score <= 0:
            continue
        # Metadonnees REELLES du chunk (fichier source) — pas un faux "bm25"
        meta = dict(_bm25_metadatas[idx]) if _bm25_metadatas else {}
        chunks.append(DocumentChunk(
            doc_id=_bm25_ids[idx],
            content=_bm25_corpus[idx],
            metadata=meta,
            score=score / max_score if max_score > 0 else 0.0,
        ))
    return chunks


def _reciprocal_rank_fusion(
    semantic: list[DocumentChunk],
    lexical: list[DocumentChunk],
    k: int = 60,
    w_sem: float = 0.5,
    w_lex: float = 0.5,
) -> dict[str, DocumentChunk]:
    merged: dict[str, DocumentChunk] = {}

    for rank, chunk in enumerate(semantic):
        rrf_score = w_sem / (k + rank + 1)
        if chunk.doc_id in merged:
            merged[chunk.doc_id].score += rrf_score
        else:
            new_chunk = DocumentChunk(chunk.doc_id, chunk.content, chunk.metadata, rrf_score)
            merged[chunk.doc_id] = new_chunk

    for rank, chunk in enumerate(lexical):
        rrf_score = w_lex / (k + rank + 1)
        if chunk.doc_id in merged:
            merged[chunk.doc_id].score += rrf_score
        else:
            new_chunk = DocumentChunk(chunk.doc_id, chunk.content, chunk.metadata, rrf_score)
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
