import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent


@dataclass
class LLMConfig:
    model: str = os.getenv("MISTRAL_MODEL", "mistral-large")
    base_url: str = os.getenv("MISTRAL_BASE_URL", "http://localhost:8080/v1")
    api_key: str = os.getenv("MISTRAL_API_KEY", "not-needed")
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))


@dataclass
class RAGConfig:
    persist_directory: str = os.getenv("CHROMA_PERSIST_DIR", str(PROJECT_ROOT / "chroma_db"))
    collection_name: str = os.getenv("CHROMA_COLLECTION", "sdf_documents")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-large")
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "1000"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "200"))
    top_k: int = int(os.getenv("RAG_TOP_K", "8"))
    hybrid_alpha: float = float(os.getenv("HYBRID_ALPHA", "0.5"))


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    input_dir: Path = PROJECT_ROOT / "test" / "sample_docs"
    output_dir: Path = PROJECT_ROOT / "output"


config = AppConfig()
