from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_ext.models.openai import OpenAIChatCompletionClient

from config import config as app_config
from rag.retriever import retrieve, format_retrieved_context
from state import AnalysisState

WORKFLOW_STEPS = [
    {
        "id": "cadrage",
        "name": "Etape 1 - Cadrage",
        "agent": "engineer",
        "reviewer": "quality",
        "checkpoint": 1,
        "task": """Effectue le cadrage de l'analyse de risque en te basant sur les documents RAG fournis.

A produire :
1. Description du systeme etudie
2. Limites du perimetre d'analyse
3. Phases de vie concernees
4. Fonctions principales identifiees
5. Interfaces importantes
6. Utilisateurs / operateurs / mainteneurs
7. Conditions d'environnement
8. Hypotheses structurantes

Pour chaque element, indique ta source (document RAG ou hypothese explicite).
Si le cadrage est incomplet, signale les informations manquantes.

IMPORTANT : utilise l'outil search_rag pour rechercher dans la base documentaire avant de repondre.""",
        "reviewer_task": """Relis le cadrage produit par l'Ingenieur Technique et verifie :
1. Toutes les rubriques attendues sont-elles renseignees ?
2. Les sources sont-elles citees ?
3. Les hypotheses sont-elles explicitement marquees ?
4. Les donnees manquantes sont-elles signalees ?
5. Le perimetre est-il coherent ?

Reponds avec :
- STATUT : [OK / A CORRIGER / INCOMPLET]
- PROBLEMES BLOQUANTS : [liste ou "aucun"]
- AVERTISSEMENTS : [liste ou "aucun"]
- SUGGESTIONS : [liste]""",
    },
    {
        "id": "filtrage",
        "name": "Etape 2 - Filtrage agressions/menaces",
        "agent": "engineer",
        "reviewer": "client",
        "checkpoint": 2,
        "task": """Effectue le filtrage des agressions et menaces generiques applicables au systeme.

Categories d'agressions a evaluer :
- Agressions environnementales (temperature, humidite, vibrations, chocs, poussiere, corrosion)
- Agressions electriques (surtensions, courts-circuits, decharges electrostatiques, CEM)
- Agressions mecaniques (usure, fatigue, rupture, grippage, corrosion sous contrainte)
- Agressions thermiques (surchauffe, choc thermique, incendie)
- Agressions CEM (perturbations conduites, rayonnees, foudre)
- Agressions humaines/organisationnelles (erreur de manipulation, maintenance inadequate, malveillance)

Menaces generiques a evaluer :
- Defaillance / perte de fonction
- Fonction intempestive
- Erreur humaine
- Mauvaise configuration
- Defaut d'interface
- Degradation progressive
- Action externe hostile (si pertinent)

Pour chaque item :
- Statut : applicable / non applicable / a confirmer
- Justification courte et tracable
- Lien avec le contexte du cadrage

Utilise l'outil search_rag pour rechercher des informations pertinentes sur les agressions typiques.""",
        "reviewer_task": """Relis le filtrage des agressions/menaces avec le regard du client/utilisateur final. Challenge :

1. Y a-t-il des agressions oubliees qui sont pertinentes pour ce systeme ?
2. Des agressions marquees 'non applicable' devraient-elles etre 'a confirmer' ?
3. Les justifications sont-elles convaincantes d'un point de vue operationnel ?
4. Le niveau de couverture est-il satisfaisant ?

Reponds avec :
- STATUT : [OK / A COMPLETER]
- RISQUES OUBLIES : [liste ou "aucun"]
- RECLASSIFICATIONS SUGGEREES : [liste ou "aucune"]""",
    },
    {
        "id": "scenarios",
        "name": "Etape 3 - Generation des scenarios de risque",
        "agent": "engineer",
        "reviewer": "quality",
        "checkpoint": 3,
        "task": """Genere les scenarios de risque en croisant les fonctions/elements avec les agressions/menaces applicables.

Pour chaque scenario, produire un tableau structure avec :
- ID unique (format : RISK-XXX)
- Fonction / element concerne
- Phase de vie
- Agression / menace
- Situation dangereuse (formulation precise)
- Evenement redoute
- Causes plausibles
- Consequences
- Gravite (estimation qualitative, marquer "a confirmer" si pas de matrice)
- Vraisemblance (estimation qualitative, marquer "a confirmer" si pas de matrice)
- Niveau de risque / criticite (provisoire)
- Justification

IMPORTANT : 
- Utilise search_rag pour enrichir avec des scenarios types du domaine.
- Marque explicitement ton niveau de confiance par scenario.
- Ne presente jamais comme certain un element non supporte par les documents.""",
        "reviewer_task": """Controle qualite des scenarios de risque generes :

1. Verifie la coherence : cause -> situation dangereuse -> evenement redoute -> consequence
2. Detecte les doublons manifestes
3. Verifie le respect du template (toutes les colonnes sont-elles renseignees ?)
4. Controle le vocabulaire (danger, situation dangereuse, risque, barriere)
5. Verifie que chaque proposition est tracable a une source
6. Les hypotheses sont-elles explicitement marquees ?
7. Y a-t-il des conclusions excessives non supportees ?

Reponds avec :
- STATUT : [OK / NON CONFORME / RESERVES]
- PROBLEMES BLOQUANTS : [liste ou "aucun"]
- DOUBLONS DETECTES : [liste ou "aucun"]
- INCOHERENCES : [liste ou "aucune"]
- POINTS A FAIRE VALIDER PAR L'HUMAIN : [liste]""",
    },
    {
        "id": "barrieres",
        "name": "Etape 4 - Proposition de barrieres",
        "agent": "engineer",
        "reviewer": "client",
        "checkpoint": 4,
        "task": """Pour chaque scenario de risque identifie, propose des barrieres de reduction du risque.

Categories de barrieres a couvrir :
1. Barrieres techniques electroniques (redondance, surveillance, watchdog, isolation)
2. Barrieres techniques mecaniques (capots, blindages, butees, dispositifs de securite)
3. Barrieres logicielles (controles de coherence, modes degrades, securites programmees)
4. Barrieres procedurales/organisationnelles (formations, procedures, maintenances preventives)
5. Barrieres de conception (choix d'architecture, principes fail-safe, diversity)

Pour chaque barriere :
- Type (prevention / detection / protection / recuperation)
- Description de la barriere
- Efficacite attendue
- Distinguer explicitement : barriere EXISTANTE (documentee) vs barriere RECOMMANDEE (proposee par l'agent)

Utilise search_rag pour rechercher des barrieres types dans les referentiels du domaine.""",
        "reviewer_task": """Relis les barrieres proposees avec le regard du client/utilisateur final. Evalue :

1. Les barrieres sont-elles realistes (budget, delais, competences) ?
2. Sont-elles suffisamment concretes ou trop generiques ?
3. Les barrieres proposees creent-elles de nouveaux risques ?
4. Distingue-t-on bien barrieres existantes vs recommandees ?
5. Y a-t-il des barrieres evidentes manquantes ?
6. Les recommandations sont-elles actionnables par les equipes ?

Reponds avec :
- STATUT : [OK / A REVOIR]
- BARRIERES IRREALISTES : [liste ou "aucune"]
- BARRIERES TROP GENERIQUES : [liste ou "aucune"]
- NOUVEAUX RISQUES INTRODUITS : [liste ou "aucun"]""",
    },
    {
        "id": "livraison",
        "name": "Etape 5 - Livraison et mise en forme",
        "agent": "secretary",
        "reviewer": None,
        "checkpoint": None,
        "task": """Assemble l'analyse complete en 4 blocs selon le format standard.

Produis le document final structure comme suit :

---
# ANALYSE PRELIMINAIRE DE RISQUE

## Bloc 1 - Resume executif
[Objet, perimetre, principales hypotheses, niveau de confiance global]

## Bloc 2 - Filtrage des agressions et menaces
[Tableau : item | statut | justification | point a valider]

## Bloc 3 - Analyse preliminaire de risque
[Tableau structure avec toutes les colonnes requises]

## Bloc 4 - Points ouverts pour validation humaine
[Liste priorisee : ambiguites, hypotheses critiques, elements manquants, decisions attendues]
---

Format : Markdown, pret a etre converti en document Word ou Excel.
Sois sobre, professionnel, structure. Pas de fioritures.""",
        "reviewer_task": None,
    },
]


@dataclass
class OrchestratorState:
    step_index: int = -1
    outputs: dict = field(default_factory=dict)
    reviews: dict = field(default_factory=dict)
    human_validations: dict = field(default_factory=dict)
    analysis_state: AnalysisState = field(default_factory=AnalysisState)


class RiskAnalysisOrchestrator:

    def __init__(
        self,
        model_client: OpenAIChatCompletionClient,
        engineer: AssistantAgent,
        quality: AssistantAgent,
        client: AssistantAgent,
        secretary: AssistantAgent,
        human_callback: Optional[Callable[..., Awaitable[str]]] = None,
    ):
        self.model_client = model_client
        self.engineer = engineer
        self.quality = quality
        self.client_agent = client
        self.secretary = secretary
        self.human_callback = human_callback
        self.state = OrchestratorState()

    async def _ask_agent(self, agent: AssistantAgent, task: str) -> str:
        team = RoundRobinGroupChat(
            participants=[agent],
            max_turns=1,
        )
        result = await team.run(task=task)
        messages = result.messages
        if messages:
            return str(messages[-1].content)
        return ""

    async def _run_engineer_step(self, step: dict) -> str:
        rag_query = f"Analyse de risque {step['id']}"
        if self.state.analysis_state.system_description:
            rag_query += f" {self.state.analysis_state.system_description}"

        rag_chunks = retrieve(rag_query, top_k=5)
        rag_context = format_retrieved_context(rag_chunks)

        enriched_task = (
            f"## Contexte documentaire (RAG)\n{rag_context}\n\n"
            f"## Tache\n{step['task']}\n\n"
            f"Tu peux aussi utiliser l'outil search_rag pour approfondir tes recherches."
        )
        return await self._ask_agent(self.engineer, enriched_task)

    async def _run_reviewer_step(self, step: dict, engineer_output: str) -> str:
        reviewer_name = step.get("reviewer")
        reviewer_task_template = step.get("reviewer_task", "")
        if not reviewer_name or not reviewer_task_template:
            return ""

        reviewer = {
            "quality": self.quality,
            "client": self.client_agent,
        }.get(reviewer_name)
        if not reviewer:
            return ""

        review_task = (
            f"## Production de l'Ingenieur Technique a relire\n\n"
            f"{engineer_output}\n\n"
            f"## Consigne de relecture\n{reviewer_task_template}"
        )
        return await self._ask_agent(reviewer, review_task)

    async def _human_checkpoint(self, step: dict, engineer_output: str, review_output: str) -> str:
        if self.human_callback:
            return await self.human_callback(
                step["id"], engineer_output, review_output, self.state.outputs
            )
        return "CONTINUER"

    async def run_full_analysis(self, initial_context: str = "") -> dict:
        all_outputs = {}

        if initial_context:
            self.state.analysis_state.system_description = initial_context

        for i, step in enumerate(WORKFLOW_STEPS):
            self.state.step_index = i

            header = f"\n{'='*70}\n  {step['name']}\n{'='*70}"
            print(header)

            agent_name = step["agent"]
            if agent_name == "engineer":
                production = await self._run_engineer_step(step)
            else:
                production = await self._ask_agent(self.secretary, step["task"])

            all_outputs[step["id"]] = production
            self.state.outputs[step["id"]] = production
            print(f"\n[Production - {agent_name}]")
            print(production[:1500] + ("..." if len(production) > 1500 else ""))

            review_output = ""
            if step.get("reviewer"):
                print(f"\n[Relecture - {step['reviewer']}]")
                review_output = await self._run_reviewer_step(step, production)
                self.state.reviews[step["id"]] = review_output
                print(review_output[:1500] + ("..." if len(review_output) > 1500 else ""))

            if step["checkpoint"]:
                print(f"\n{'─'*50}")
                print(f"  Point de controle {step['checkpoint']} - Validation humaine")
                print(f"{'─'*50}")
                print("\nTaper : CONTINUER | QUITTER | ou un feedback pour correction")
                feedback = await self._human_checkpoint(step, production, review_output)

                if feedback.upper().strip() == "CONTINUER":
                    self.state.human_validations[step["id"]] = "validated"
                    print(">> Etape validee.")
                elif feedback.upper().strip() == "QUITTER":
                    self.state.human_validations[step["id"]] = "quit"
                    print(">> Analyse interrompue.")
                    break
                else:
                    self.state.human_validations[step["id"]] = feedback
                    print(f">> Feedback integre, re-generation de l'etape...")
                    if agent_name == "engineer":
                        step_copy = dict(step)
                        step_copy["task"] = (
                            f"{step['task']}\n\n## FEEDBACK HUMAIN A INTEGRER\n{feedback}"
                        )
                        retry = await self._run_engineer_step(step_copy)
                    else:
                        retry = await self._ask_agent(
                            self.secretary,
                            f"{step['task']}\n\n## FEEDBACK HUMAIN A INTEGRER\n{feedback}",
                        )
                    all_outputs[step["id"]] = retry
                    self.state.outputs[step["id"]] = retry
                    print(">> Etape re-generee avec le feedback.")

        print(f"\n{'='*70}\n  Analyse terminee\n{'='*70}")
        return all_outputs


def create_model_client() -> OpenAIChatCompletionClient:
    return OpenAIChatCompletionClient(
        model=app_config.llm.model,
        base_url=app_config.llm.base_url,
        api_key=app_config.llm.api_key,
        temperature=app_config.llm.temperature,
        max_tokens=app_config.llm.max_tokens,
    )
