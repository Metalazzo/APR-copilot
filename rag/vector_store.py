import hashlib
import json
import re
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


def _hub_sha(repo_id: str) -> str:
    """Sha (revision) du depot HuggingFace via l'API publique : quelques Ko,
    sans la lib huggingface_hub (donc sans le warning 'unauthenticated')."""
    import urllib.request
    url = f"https://huggingface.co/api/models/{repo_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "APR-Copilot/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return (json.loads(resp.read().decode("utf-8")) or {}).get("sha", "")


def _read_local_sha(meta_path: Path) -> str:
    try:
        return (json.loads(meta_path.read_text(encoding="utf-8")) or {}).get("sha", "")
    except Exception:
        return ""


def _write_local_sha(meta_path: Path, repo_id: str, sha: str) -> None:
    meta_path.write_text(
        json.dumps({"repo": repo_id, "sha": sha}, indent=2), encoding="utf-8"
    )


def get_embedding_model() -> SentenceTransformer:
    """Charge le modele d'embeddings en privilegiant la copie locale.

    Cycle voulu :
    - premier lancement : telechargement depuis le Hub, copie complete dans
      EMBEDDING_LOCAL_DIR, sha de la version enregistre dans _meta.json
    - lancements suivants : chargement local SANS reseau, puis verification
      legere du sha distant (quelques Ko) ; si le depot a change, mise a jour
      automatique de la copie locale
    - EMBEDDING_OFFLINE=true ou erreur reseau : verification ignoree, jamais
      bloquant (si la copie locale est absente en mode offline, erreur claire).
    """
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model

    local_dir = Path(app_config.rag.embedding_local_dir)
    repo = app_config.rag.embedding_model
    meta_path = local_dir / "_meta.json"
    offline = app_config.rag.embedding_offline

    if local_dir.exists():
        model = SentenceTransformer(str(local_dir))
        _embedding_model = model
        if offline:
            print("[embeddings] mode hors ligne : verification de mise a jour ignoree")
            return _embedding_model
        if app_config.rag.embedding_check_updates:
            try:
                remote_sha = _hub_sha(repo)
                local_sha = _read_local_sha(meta_path)
                if remote_sha and local_sha and remote_sha != local_sha:
                    print(f"[embeddings] mise a jour disponible ({repo}) -> telechargement...")
                    fresh = SentenceTransformer(repo)
                    fresh.save(str(local_dir))
                    _write_local_sha(meta_path, repo, remote_sha)
                    _embedding_model = fresh
                    print("[embeddings] copie locale mise a jour")
                else:
                    print("[embeddings] copie locale a jour")
            except Exception as exc:
                print(f"[embeddings] verification de mise a jour impossible (hors ligne ?) : {exc}")
        return _embedding_model

    if offline:
        raise RuntimeError(
            f"EMBEDDING_OFFLINE actif mais aucune copie locale du modele ({local_dir}). "
            "Relancez une fois en ligne pour la creer."
        )

    print(f"[embeddings] premiere installation : telechargement de {repo}...")
    model = SentenceTransformer(repo)
    local_dir.mkdir(parents=True, exist_ok=True)
    model.save(str(local_dir))
    try:
        sha = _hub_sha(repo)
        if sha:
            _write_local_sha(meta_path, repo, sha)
    except Exception:
        pass
    _embedding_model = model
    print(
        f"[embeddings] modele copie localement dans {local_dir} — les prochains "
        "lancements n'interrogeront plus le Hub (verification legere uniquement)."
    )
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


_PAGE_NUMBER_RE = re.compile(r"^\s*(page|p\.?)\s*[:.]?\s*\d+\s*(/|of|sur)?\s*\d*\s*$", re.IGNORECASE)


def _line_key(ln: str) -> str:
    """Cle de comparaison d'une ligne insensible aux chiffres, espaces et casse.

    Les extractions PDF varient legèrement d'une page a l'autre (mots coupes,
    numeros de page dans la ligne) : on compare la version alpha-only tronquee.
    """
    return re.sub(r"[^a-z]", "", ln.lower())[:80]


def _clean_pdf_text(pages: list) -> str:
    """Nettoie le texte extrait d'un PDF page par page.

    Retire les en-tetes/pieds de page repetes (lignes quasi identiques presentes
    sur au moins la moitie des pages : references, confidentialite, numerotation)
    et les numeros de page isoles. Ces lignes polluent les chunks et saturent la
    recherche hybride avec des fragments sans valeur.
    """
    n_pages = len(pages)
    if n_pages < 3:
        return "\n".join(pages)

    page_lines = []
    key_counts = {}
    for page in pages:
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in page.splitlines()]
        lines = [ln for ln in lines if ln]
        page_lines.append(lines)
        for key in {_line_key(ln) for ln in lines if len(_line_key(ln)) >= 10}:
            key_counts[key] = key_counts.get(key, 0) + 1

    threshold = max(2, int(n_pages * 0.5))
    repeated_keys = {k for k, cnt in key_counts.items() if cnt >= threshold}

    cleaned_pages = []
    for lines in page_lines:
        kept = [
            ln for ln in lines
            if _line_key(ln) not in repeated_keys and not _PAGE_NUMBER_RE.match(ln)
        ]
        cleaned_pages.append("\n".join(kept))
    return "\n".join(cleaned_pages)


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
        pages = [page.extract_text() or "" for page in reader.pages]
        return _clean_pdf_text(pages)
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

    # Fusion des micro-chunks : les datasheets/PDF produisent beaucoup de petits
    # paragraphes (125-300 car.) inutilisables pour la recherche hybride.
    min_chars = min(300, chunk_size)
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) < min_chars:
            candidate = (merged[-1] + "\n\n" + chunk).strip()
            if len(candidate) <= chunk_size + 200:
                merged[-1] = candidate
            else:
                merged.append(chunk)
        else:
            merged.append(chunk)
    if len(merged) >= 2 and len(merged[-1]) < min_chars:
        merged[-2] = (merged[-2] + "\n\n" + merged[-1]).strip()
        merged.pop()
    return merged
