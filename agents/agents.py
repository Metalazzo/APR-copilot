from pathlib import Path

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

from rag.retriever import retrieve, format_retrieved_context

PROMPT_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


async def _search_rag(query: str, top_k: int = 5) -> str:
    chunks = retrieve(query, top_k=top_k)
    return format_retrieved_context(chunks)


async def _search_by_topic(topic: str, doc_type: str = "all", top_k: int = 5) -> str:
    query = f"{topic} {doc_type}".strip()
    chunks = retrieve(query, top_k=top_k)
    return format_retrieved_context(chunks)


class EngineerAgent:
    def __init__(self, model_client: OpenAIChatCompletionClient):
        self.model_client = model_client
        system_message = _load_prompt("engineer")
        self._agent = AssistantAgent(
            name="Ingenieur_Technique_SDF",
            model_client=model_client,
            system_message=system_message,
            tools=[_search_rag, _search_by_topic],
            description="Expert SDF/RAMS. Exécute les étapes 1-6: cadrage, extraction, filtrage, scénarios, cotation, barrières. Utilise search_rag pour chercher dans la base documentaire.",
        )

    @property
    def agent(self) -> AssistantAgent:
        return self._agent

    @staticmethod
    def search_rag_sync(query: str, top_k: int = 5) -> str:
        chunks = retrieve(query, top_k=top_k)
        return format_retrieved_context(chunks)


class QualityAgent:
    def __init__(self, model_client: OpenAIChatCompletionClient):
        self.model_client = model_client
        system_message = _load_prompt("quality")
        self._agent = AssistantAgent(
            name="Animateur_Qualite",
            model_client=model_client,
            system_message=system_message,
            description="Contrôle qualité interne. Vérifie cohérence, traçabilité, vocabulaire, conformité template. Prépare la validation humaine.",
        )

    @property
    def agent(self) -> AssistantAgent:
        return self._agent


class ClientAgent:
    def __init__(self, model_client: OpenAIChatCompletionClient):
        self.model_client = model_client
        system_message = _load_prompt("client")
        self._agent = AssistantAgent(
            name="Representant_Client",
            model_client=model_client,
            system_message=system_message,
            description="Point de vue utilisateur final. Challenge le filtrage, évalue le réalisme des barrières, vérifie l'actionnabilité, détecte les angles morts.",
        )

    @property
    def agent(self) -> AssistantAgent:
        return self._agent


class SecretaryAgent:
    def __init__(self, model_client: OpenAIChatCompletionClient):
        self.model_client = model_client
        system_message = _load_prompt("secretary")
        self._agent = AssistantAgent(
            name="Secretaire",
            model_client=model_client,
            system_message=system_message,
            description="Mise en forme et restitution. Assemble les 4 blocs, formate en Markdown/CSV/JSON, vérifie conformité template.",
        )

    @property
    def agent(self) -> AssistantAgent:
        return self._agent
