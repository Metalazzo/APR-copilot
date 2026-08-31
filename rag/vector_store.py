import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from config import config as app_config

_client: Optional[chromadb.PersistentClient] = None
_collection: Optional[chromadb.Collection] = None
_embedding_model: Optional[SentenceTransformer] = None

SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}


@dataclass
class IngestReport:
    """Bilan d'une ingestion : fichiers lus, ignores (avec raison), chunks indexes."""

    files_ok: int = 0
    files_skipped: list = field(default_factory=list)  # [(chemin, raison)]
    chunks: int = 0

    def summary(self) -> str:
        lines = [
            f"Ingestion terminee : {self.files_ok} fichier(s) lu(s), "
            f"{self.chunks} chunk(s) indexes."
        ]
        if self.files_skipped:
            lines.append(f"{len(self.files_skipped)} fichier(s) ignores :")
            for path, reason in self.files_skipped:
                lines.append(f"  - {path} : {reason}")
        return "\n".join(lines)


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
        _collection = client.get_or_create_collection(
            name=app_config.rag.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def ingest_documents(directory: Path, reset: bool = False) -> IngestReport:
    """Indexe recursivement les documents supportes du repertoire.

    Un fichier en erreur n'interrompt jamais l'ingestion : il est journalise
    dans le rapport (raison) et on passe au suivant. Re-ingestion sans reset
    possible (upsert, pas de doublon d'ID).
    """
    report = IngestReport()
    model = get_embedding_model()
    collection = get_collection()

    if reset:
        client = get_client()
        client.delete_collection(app_config.rag.collection_name)
        global _collection
        _collection = None
        collection = get_collection()

    directory = Path(directory)
    text_paths = [
        p for p in directory.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_SUFFIXES
        and not p.name.startswith("~")
    ]

    for file_path in sorted(text_paths):
        try:
            content = _extract_text(file_path)
        except Exception as exc:
            report.files_skipped.append(
                (str(file_path), f"erreur d'extraction : {exc}")
            )
            continue

        if not content.strip():
            if file_path.suffix.lower() == ".pdf":
                reason = "aucun texte extractible (PDF probablement scanne, OCR requis)"
            else:
                reason = "fichier vide"
            report.files_skipped.append((str(file_path), reason))
            continue

        try:
            chunks = _chunk_text(content)
            if not chunks:
                report.files_skipped.append((str(file_path), "decoupage sans resultat"))
                continue

            # Hash du chemin : evite les collisions d'ID entre fichiers homonymes
            # de dossiers differents, et rend l'ID stable pour re-ingestion.
            rel_id = hashlib.md5(str(file_path).encode("utf-8")).hexdigest()[:8]
            ids = [
                f"{rel_id}_{file_path.stem}_{file_path.suffix.lstrip('.')}_c{i}"
                for i in range(len(chunks))
            ]
            metadatas = [
                {"source": str(file_path), "filename": file_path.name, "chunk_index": i}
                for i in range(len(chunks))
            ]
            embeddings = model.encode(chunks).tolist()

            # upsert (et non add) : re-ingestion sans --reset sans erreur d'ID duplique
            collection.upsert(
                ids=ids,
                documents=chunks,
                metadatas=metadatas,
                embeddings=embeddings,
            )
        except Exception as exc:
            report.files_skipped.append(
                (str(file_path), f"erreur d'indexation : {exc}")
            )
            continue

        report.files_ok += 1
        report.chunks += len(chunks)

    return report


def _extract_text(file_path: Path) -> str:
    """Extrait le texte brut d'un document. Peut lever : l'erreur est journalisee
    par l'appelant et le fichier est ignore sans casser l'ingestion."""
    suffix = file_path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return file_path.read_text(encoding="utf-8")
    elif suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(file_path))
        if reader.is_encrypted:
            try:
                # Tente un mot de passe vide (PDF proteges en edition uniquement)
                reader.decrypt("")
            except Exception:
                raise ValueError("PDF chiffre : mot de passe requis")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    elif suffix == ".docx":
        from docx import Document
        doc = Document(str(file_path))
        return "\n".join(p.text for p in doc.paragraphs)
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
                # Progression stricte garantie : si le dernier separateur tombe
                # exactement a l'indice overlap-1, current[split_idx+1-overlap:]
                # vaudrait current[0:] et la boucle ne terminerait jamais
                # (MemoryError observe sur de vrais PDF). On avance d'au moins 1.
                start = max(split_idx + 1 - overlap, 1)
                start = min(start, len(current) - 1)
                current = current[start:].strip()
        else:
            current = test

    if current.strip():
        chunks.append(current.strip())
    return chunks
