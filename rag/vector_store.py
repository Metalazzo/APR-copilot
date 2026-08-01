from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from config import config as app_config

_client: Optional[chromadb.PersistentClient] = None
_collection: Optional[chromadb.Collection] = None
_embedding_model: Optional[SentenceTransformer] = None


def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(app_config.rag.embedding_model)
    return _embedding_model


def get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=app_config.rag.persist_directory,
            settings=Settings(anonymized_telemetry=False),
        )
    return _client


def get_collection() -> chromadb.Collection:
    global _collection
    if _collection is None:
        client = get_client()
        model = get_embedding_model()
        emb_dim = model.get_sentence_embedding_dimension()
        _collection = client.get_or_create_collection(
            name=app_config.rag.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def ingest_documents(
    directory: Path,
    glob_pattern: str = "**/*.{txt,md,pdf,docx}",
    reset: bool = False,
) -> int:
    model = get_embedding_model()
    collection = get_collection()

    if reset:
        client = get_client()
        client.delete_collection(app_config.rag.collection_name)
        global _collection
        _collection = client.get_or_create_collection(
            name=app_config.rag.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    paths = list(directory.rglob("*"))
    text_paths = [
        p for p in paths
        if p.suffix.lower() in {".txt", ".md", ".pdf", ".docx"}
        and not p.name.startswith("~")
    ]

    total_chunks = 0
    for file_path in text_paths:
        content = _extract_text(file_path)
        if not content.strip():
            continue
        chunks = _chunk_text(content)
        if not chunks:
            continue

        ids = [f"{file_path.stem}_{file_path.suffix.lstrip('.')}_c{i}" for i in range(len(chunks))]
        metadatas = [
            {"source": str(file_path), "filename": file_path.name, "chunk_index": i}
            for i in range(len(chunks))
        ]
        embeddings = model.encode(chunks).tolist()

        collection.add(
            ids=ids,
            documents=chunks,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        total_chunks += len(chunks)

    return total_chunks


def _extract_text(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return file_path.read_text(encoding="utf-8")
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(file_path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            return ""
    elif suffix == ".docx":
        try:
            from docx import Document
            doc = Document(str(file_path))
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            return ""
    return ""


def _chunk_text(text: str) -> list[str]:
    chunk_size = app_config.rag.chunk_size
    overlap = app_config.rag.chunk_overlap
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        test = current + ("\n\n" if current else "") + para
        if len(test) > chunk_size:
            if current:
                chunks.append(current)
            current = para

            while len(current) > chunk_size:
                split_idx = current.rfind(".", 0, chunk_size)
                if split_idx == -1:
                    split_idx = current.rfind(" ", 0, chunk_size)
                if split_idx == -1:
                    split_idx = chunk_size
                chunks.append(current[:split_idx + 1].strip())
                current = current[split_idx + 1 - overlap:].strip()
        else:
            current = test

    if current.strip():
        chunks.append(current.strip())
    return chunks
