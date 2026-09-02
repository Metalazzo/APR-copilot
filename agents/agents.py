import os
from pathlib import Path

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient

from rag.retriever import retrieve, format_retrieved_context

PROMPT_DIR = Path(__file__).parent.parent / "prompts"

# Jetons de controle du raisonnement, interpretes par le template Jinja cote
# serveur LM Studio (scannes dans les messages system/developer/user).
# Sur un template sans support de ces jetons, ils restent du texte inoffensif.
REASONING_TOKENS = {
    "off": "<|think_off|>",
    "low": "<|think_low|>",
    "medium": "<|think_medium|>",
    "high": "<|think_high|>",
    "xhigh": "<|think_xhigh|>",
}


def _reasoning_suffix() -> str:
    """Jeton de niveau de raisonnement ajoute au message systeme.

    LLM_REASONING : off (defaut) | low | medium | high | xhigh.
    'off' supprime la phase de thinking (gain de temps majeur en local).
    """
    level = os.getenv("LLM_REASONING", "off").strip().lower()
    token = REASONING_TOKENS.get(level)
    return f"\n\n{token}" if token else ""


def _load_prompt(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return text + _reasoning_suffix()


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
        # Outils DESACTIVES par defaut : avec certains templates/serveurs (LM
        # Studio + streaming), la phase de reflexion post-outil re-emet les
        # appels en texte brut <tool_call> au lieu d'appels structures, et la
        # production finale devient du XML au lieu de l'analyse. Le RAG est
        # pre-injecte par l'orchestrateur dans chaque tache. Re-activable via
        # LLM_ENGINEER_TOOLS=true (reflect_on_tool_use=True est alors requis :
        # defaut autogen 0.7 = False, la reponse serait le brut de l'outil).
        use_tools = os.getenv("LLM_ENGINEER_TOOLS", "false").lower() in (
            "1", "true", "yes",
        )
        stream_engineer = os.getenv("LLM_STREAM_ENGINEER", "true").lower() in (
            "1", "true", "yes",
        )
        self._agent = AssistantAgent(
            name="Ingenieur_Technique_SDF",
            model_client=model_client,
            model_client_stream=stream_engineer,
            system_message=system_message,
            tools=[_search_rag, _search_by_topic] if use_tools else None,
            reflect_on_tool_use=True if use_tools else None,
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
            model_client_stream=True,
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
            model_client_stream=True,
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
            model_client_stream=True,
            system_message=system_message,
            description="Mise en forme et restitution. Assemble les 4 blocs, formate en Markdown/CSV/JSON, vérifie conformité template.",
        )

    @property
    def agent(self) -> AssistantAgent:
        return self._agent
