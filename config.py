import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent


@dataclass
class LLMConfig:
    model: str = os.getenv("LLM_MODEL", "mistral-large")
    base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:8080/v1")
    api_key: str = os.getenv("LLM_API_KEY", "not-needed")
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
class ModelProfile:
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.3
    max_tokens: int = 4096


@dataclass
class ModelProfiles:
    cloud: ModelProfile = field(default_factory=lambda: ModelProfile(
        model=os.getenv("CLOUD_MODEL", "deepseek-v4-flash"),
        base_url=os.getenv("CLOUD_BASE_URL", "https://api.deepseek.com/v1"),
        api_key=os.getenv("CLOUD_API_KEY", ""),
        temperature=float(os.getenv("CLOUD_TEMPERATURE", "0.3")),
        max_tokens=int(os.getenv("CLOUD_MAX_TOKENS", "8192")),
    ))
    local: ModelProfile = field(default_factory=lambda: ModelProfile(
        model=os.getenv("LOCAL_MODEL", "gemma-4-12b-qat"),
        base_url=os.getenv("LOCAL_BASE_URL", "http://localhost:1234/v1"),
        api_key=os.getenv("LOCAL_API_KEY", "not-needed"),
        temperature=float(os.getenv("LOCAL_TEMPERATURE", "0.3")),
        max_tokens=int(os.getenv("LOCAL_MAX_TOKENS", "4096")),
    ))
    default: str = os.getenv("DEFAULT_PROFILE", "cloud")


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    profiles: ModelProfiles = field(default_factory=ModelProfiles)
    rag: RAGConfig = field(default_factory=RAGConfig)
    input_dir: Path = PROJECT_ROOT / "test" / "sample_docs"
    output_dir: Path = PROJECT_ROOT / "output"


config = AppConfig()
