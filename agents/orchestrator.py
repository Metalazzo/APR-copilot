import asyncio
import os
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_ext.models.openai import OpenAIChatCompletionClient

from config import ModelProfile, config as app_config
from rag.retriever import retrieve, format_retrieved_context
from state import AnalysisState

WORKFLOW_STEPS = [
    {
        "id": "cadrage",
        "name": "Etape 1 - Cadrage",
        "agent": "engineer",
        "reviewer": "quality",
        "checkpoint": 1,
        "query_hint": "méthode analyse préliminaire de risque APR industrielle",
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

IMPORTANT : appuie-toi sur le contexte documentaire (RAG) fourni dans la tache avant de repondre.""",
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
        "query_hint": "agressions environnementales électriques systèmes électriques ferroviaires",
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

Appuie-toi sur le contexte documentaire (RAG) fourni pour les agressions typiques.""",
        "reviewer_task": """Relis le filtrage des agressions/menaces avec le regard du client/utilisateur final. Challenge :

1. L'analyse reste-t-elle centree sur l'usage et l'integration prevus du produit, sans divergence du besoin client ?
2. Y a-t-il des agressions oubliees qui sont pertinentes pour ce systeme ?
3. Des agressions marquees 'non applicable' devraient-elles etre 'a confirmer' ?
4. Les justifications sont-elles convaincantes d'un point de vue operationnel ?
5. Le niveau de couverture est-il satisfaisant ?

Reponds avec :
- STATUT : [OK / A COMPLETER]
- DIVERGENCE : [non / oui + justification]
- RISQUES OUBLIES : [liste ou "aucun"]
- RECLASSIFICATIONS SUGGEREES : [liste ou "aucune"]""",
    },
    {
        "id": "scenarios",
        "name": "Etape 3 - Generation des scenarios de risque",
        "agent": "engineer",
        "reviewer": "quality",
        "checkpoint": 3,
        "query_hint": "scénarios de risque situations dangereuses exemples systèmes électriques",
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
- Appuie-toi sur le contexte documentaire (RAG) fourni pour les scenarios types du domaine.
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
        "query_hint": "barrières de sécurité prévention protection exemples systèmes électriques",
        "task": """Pour chaque scenario de risque identifie, choisis l'option de traitement du risque (ISO 27005) et propose les barrieres de reduction si retenue.

Options de traitement (une par risque) :
- REDUCTION : ajout/suppression/modification de barrieres jusqu'a un risque residuel acceptable
- MAINTIEN (acceptation) : aucune autre action, le risque est accepte tel quel
- REFUS (evitement) : suppression de la source du risque (arreter ou modifier l'activite)
- PARTAGE (transfert) : assurance, sous-traitance, clause contractuelle vers une partie capable de gerer le risque

Categories de barrieres a couvrir (pour l'option REDUCTION) :
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

Puis, pour chaque risque :
- Option de traitement retenue (REDUCTION / MAINTIEN / REFUS / PARTAGE) + justification courte
- Risque RESIDUEL estime apres mise en oeuvre des barrieres (grille fournie ; marquer "a confirmer" si incertain)

Appuie-toi sur le contexte documentaire (RAG) fourni pour les barrieres types.""",
        "reviewer_task": """Relis les barrieres proposees avec le regard du client/utilisateur final. Evalue :

1. Les barrieres et l'analyse restent-elles centrees sur l'usage et l'integration prevus du produit, sans divergence du besoin client ?
2. Les barrieres sont-elles realistes (budget, delais, competences) ?
3. Sont-elles suffisamment concretes ou trop generiques ?
4. Les barrieres proposees creent-elles de nouveaux risques ?
5. Distingue-t-on bien barrieres existantes vs recommandees ?
6. Y a-t-il des barrieres evidentes manquantes ?
7. Les recommandations sont-elles actionnables par les equipes ?

Reponds avec :
- STATUT : [OK / A REVOIR]
- DIVERGENCE : [non / oui + justification]
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
        "task": """Assemble l'analyse complete en 5 sections selon le format standard.

Produis le document final structure comme suit :

---
# ANALYSE PRELIMINAIRE DE RISQUE

## Bloc 1 - Resume executif et gouvernance
[Objet, perimetre, principales hypotheses, niveau de confiance global]
[Tableau RACI de la demarche :
- A (Approuve, proprietaire des risques) : decision finale d'acceptation - humain validateur
- R (Realise) : Ingenieur Technique SDF
- C (Consulte) : Animateur Qualite, Representant Client
- I (Informe) : Secretaire / livrable]

## Bloc 2 - Filtrage des agressions et menaces
[Tableau : item | statut | justification | point a valider]

## Bloc 3 - Analyse preliminaire de risque
[Tableau structure avec toutes les colonnes requises, incluant pour chaque risque
l'option de traitement (REDUCTION / MAINTIEN / REFUS / PARTAGE) et le risque residuel]

## Bloc 4 - Plan de traitement et decisions d'acceptation
[Pour chaque risque non reduit a un niveau acceptable :
- Option de traitement (REDUCTION / MAINTIEN / REFUS / PARTAGE)
- Mesures et conditions d'execution
- Decision requise : QUI doit accepter (proprietaire des risques), a quel niveau
- Conditions d'acceptation eventuelles (duree, en attendant une action...)
- Suivi prevu (revue periodique)]

## Bloc 5 - Points ouverts pour validation humaine
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
        engineer: AssistantAgent,
        quality: AssistantAgent,
        client: AssistantAgent,
        secretary: AssistantAgent,
        human_callback: Optional[Callable[..., Awaitable[str]]] = None,
        progress_callback: Optional[Callable[[dict], Awaitable[None]]] = None,
    ):
        self.engineer = engineer
        self.quality = quality
        self.client_agent = client
        self.secretary = secretary
        self.human_callback = human_callback
        # Callback optionnel de progression (GUI) : recoit des evenements
        # structures (step_start, production, review, checkpoint...).
        self.progress_callback = progress_callback
        self.state = OrchestratorState()

    async def _emit(self, event: dict) -> None:
        """Notifie la GUI si presente ; une erreur d'affichage ne casse jamais l'analyse."""
        if self.progress_callback is None:
            return
        try:
            await self.progress_callback(event)
        except Exception:
            pass

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

    def _step_context_limit(self) -> int:
        """Limite de caracteres par etape precedente reinjectee dans une tache.

        Reglable via STEP_CONTEXT_LIMIT (defaut 40000). Toute troncature est
        signalee en log/GUI : si elle survient, augmenter la limite (un modele
        a 100k+ tokens de contexte absorbe largement 4 x 40000)."""
        return int(os.getenv("STEP_CONTEXT_LIMIT", "40000"))

    async def _previous_outputs_section(self, exclude_step_id: str = "") -> str:
        """Assemble les productions des etapes deja realisees pour le contexte."""
        limit = self._step_context_limit()
        parts = []
        truncated = []
        for done_step in WORKFLOW_STEPS:
            if done_step["id"] == exclude_step_id:
                continue
            out = self.state.outputs.get(done_step["id"])
            if not out:
                continue
            trimmed = out[:limit]
            if len(out) > limit:
                trimmed += "\n[... tronque ...]"
                truncated.append((done_step["id"], len(out) - limit))
            parts.append(f"### {done_step['name']}\n{trimmed}")
        if truncated:
            detail = ", ".join(f"{sid} (-{lost} car.)" for sid, lost in truncated)
            msg = (
                f"[contexte] productions tronquees : {detail} — "
                f"des points risquent de sauter. Augmentez STEP_CONTEXT_LIMIT "
                f"(actuellement {limit})."
            )
            print(msg)
            await self._emit({
                "type": "context_truncated",
                "step_id": exclude_step_id or "livraison",
                "detail": detail,
                "limit": limit,
            })
        if not parts:
            return ""
        return "\n\n".join(parts)

    def _reviewer_for(self, step: dict) -> Optional[AssistantAgent]:
        if not step.get("reviewer"):
            return None
        return {
            "quality": self.quality,
            "client": self.client_agent,
        }.get(step["reviewer"])

    async def _context_size(self, agent: AssistantAgent) -> int:
        try:
            return len(await agent.model_context.get_messages())
        except Exception:
            return -1

    async def _clear_context(self, agent: AssistantAgent) -> int:
        """Vide la memoire interne d'un agent. Retourne sa taille avant purge.

        L'orchestrateur reinjecte deja tout le contexte utile dans chaque tache
        (etapes precedentes, description, RAG, production a corriger) : la memoire
        cumulee d'AutoGen (UnboundedChatCompletionContext par defaut) noierait les
        feedbacks humains dans l'historique et gonflerait le prefill.
        """
        size = await self._context_size(agent)
        try:
            await agent.model_context.clear()
        except Exception:
            pass
        return size

    async def _purge_step_contexts(self, step: dict) -> None:
        """Purge la memoire du producteur et du relecteur de l'etape."""
        producer = self.engineer if step["agent"] == "engineer" else self.secretary
        for role, agent in (("producteur", producer), ("relecteur", self._reviewer_for(step))):
            if agent is None:
                continue
            size_before = await self._clear_context(agent)
            if size_before > 0:
                print(f"[memoire] contexte {role} purge (contenait {size_before} messages)")
                await self._emit({
                    "type": "context_purged",
                    "step_id": step["id"],
                    "size_before": size_before,
                })

    @staticmethod
    def _feedback_section(feedbacks: list) -> str:
        """Formate l'historique des feedbacks humains de l'etape (tous a respecter)."""
        if not feedbacks:
            return ""
        lines = "\n".join(f"{n}. {f}" for n, f in enumerate(feedbacks, 1))
        return (
            f"\n\n## FEEDBACK HUMAIN A INTEGRER (par ordre chronologique, "
            f"tous a respecter)\n{lines}"
        )

    @staticmethod
    def _keywords(text: str, n: int = 4) -> str:
        """Extrait n mots-cles significatifs d'un texte (sans LLM)."""
        import re
        stopwords = {
            "apres", "entre", "leur", "elle", "nous", "vous", "ainsi", "etre",
            "avoir", "cette", "dont", "chez", "comme", "aussi", "tres", "alors",
            "donc", "mais", "tout", "toute", "tous", "ces", "ses", "son", "sa",
            "plus", "moins", "chaque", "pour", "avec", "dans", "sont", "est",
            "une", "les", "des", "aux", "que", "qui", "par", "sur", "indicator",
        }
        words = re.findall(r"[a-zA-ZÀ-ÿ]{5,}", (text or "").lower())
        freq: dict = {}
        for w in words:
            if w not in stopwords:
                freq[w] = freq.get(w, 0) + 1
        return " ".join(sorted(freq, key=freq.get, reverse=True)[:n])

    async def _web_section(self, step: dict) -> str:
        """Recherche web orchestree (etat de l'art), desactivee par defaut.

        Le LLM n'appelle jamais d'outil : l'orchestrateur cherche AVANT de
        construire la tache et injecte les resultats comme contexte. Tout
        echec est non bloquant (section omise, log)."""
        if os.getenv("WEB_SEARCH_ENABLED", "false").lower() not in ("1", "true", "yes"):
            return ""
        if step["agent"] != "engineer":
            return ""

        from web_search import get_web_config, search_web
        cfg = get_web_config()
        hint = step.get("query_hint") or step["id"]
        keywords = self._keywords(self.state.analysis_state.system_description or "")
        query = f"{hint} {keywords}".strip()

        try:
            results = await asyncio.to_thread(search_web, query)
        except Exception as exc:
            print(f"[web] recherche indisponible ({cfg.backend}) : {exc}")
            await self._emit({
                "type": "web_search", "step_id": step["id"],
                "query": query, "count": 0, "error": str(exc)[:200],
            })
            return ""

        await self._emit({
            "type": "web_search", "step_id": step["id"],
            "query": query, "count": len(results),
        })
        if not results:
            return ""
        lines = [f"- [{r['title']}]({r['url']}) : {r['snippet']}" for r in results]
        return (
            "## Etat de l'art — references web (NON VERIFIEES)\n"
            f"Requete : {query}\n" + "\n".join(lines) +
            "\n⚠️ Sources web publiques, non verifiees : pistes de reflexion "
            "uniquement, jamais une reference normative. A citer avec prudence "
            "et a faire valider par l'humain."
        )

    async def _run_engineer_step(
        self,
        step: dict,
        previous_production: str = "",
        human_feedback: str = "",
    ) -> str:
        description = self.state.analysis_state.system_description
        rag_query = f"Analyse de risque {step['id']}"
        if description:
            rag_query += f" {description}"

        rag_chunks = retrieve(rag_query, top_k=app_config.rag.top_k)
        rag_context = format_retrieved_context(rag_chunks)

        web_section = await self._web_section(step)

        enriched_task = (
            "## Contexte documentaire (RAG)\n"
            "(Si un template/tableau/matrice d'analyse propre au projet figure "
            "dans ces extraits, il prime sur le format par defaut de la tache.)\n\n"
            f"{rag_context}\n\n"
        )
        if web_section:
            enriched_task += f"{web_section}\n\n"
        if description:
            enriched_task += (
                f"## Description du systeme fournie par l'utilisateur (reference principale)\n"
                f"{description[:8000]}\n\n"
            )
        previous = await self._previous_outputs_section(exclude_step_id=step["id"])
        if previous:
            enriched_task += (
                f"## Travaux precedents de l'analyse (a respecter et prolonger)\n"
                f"{previous}\n\n"
            )
        if previous_production:
            enriched_task += (
                f"## Production precedente de cette etape (A CORRIGER selon le feedback)\n"
                f"{previous_production[: self._step_context_limit()]}\n\n"
            )
        if human_feedback:
            enriched_task += (
                f"## FEEDBACK HUMAIN A INTEGRER (prioritaire, a suivre a la lettre)\n"
                f"{human_feedback}\n\n"
            )
        enriched_task += f"## Tache\n{step['task']}"
        return await self._ask_agent(self.engineer, enriched_task)

    async def _secretary_task(
        self,
        step: dict,
        human_feedback: str = "",
        previous_production: str = "",
    ) -> str:
        """Constitue la tache du Secretaire : template + travaux precedents a assembler."""
        task = (
            f"{step['task']}\n\n"
            f"## Consignes de restitution\n"
            f"- Reponds UNIQUEMENT avec le document final, sans preambule, sans "
            f"commentaire meta, sans mention du processus ou du format genere.\n"
            f"- Reprends fidelement les travaux precedents fournis ci-dessous : "
            f"n'invente aucun risque, aucune barriere ni aucune donnee qui n'y "
            f"figure pas."
        )
        if previous_production:
            task += (
                f"\n\n## Version precedente du document (A CORRIGER selon le feedback)\n"
                f"{previous_production[: self._step_context_limit()]}"
            )
        if human_feedback:
            task += (
                f"\n\n## FEEDBACK HUMAIN A INTEGRER (prioritaire, a suivre a la lettre)\n"
                f"{human_feedback}"
            )
        previous = await self._previous_outputs_section(exclude_step_id=step["id"])

        if previous:
            task += (
                f"\n\n## Travaux precedents de l'analyse (source unique de verite)\n"
                f"{previous}"
            )
        return task

    async def _run_reviewer_step(
        self,
        step: dict,
        engineer_output: str,
        human_feedback: str = "",
    ) -> str:
        reviewer_name = step.get("reviewer")
        reviewer_task_template = step.get("reviewer_task", "")
        if not reviewer_name or not reviewer_task_template:
            return ""

        reviewer = self._reviewer_for(step)
        if reviewer is None:
            return ""

        review_task = (
            f"## Production de l'Ingenieur Technique a relire\n\n"
            f"{engineer_output}\n\n"
        )
        if human_feedback:
            review_task += (
                f"## Contexte humain\n"
                f"Le jury humain a demande les corrections suivantes : verifie qu'elles "
                f"sont bien integrees dans la production ci-dessus.\n"
                f"{human_feedback}\n\n"
            )
        review_task += f"## Consigne de relecture\n{reviewer_task_template}"
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
            await self._emit({
                "type": "step_start",
                "step_id": step["id"],
                "name": step["name"],
                "agent": step["agent"],
            })

            print(f"\n{'='*70}\n  {step['name']}\n{'='*70}")

            agent_name = step["agent"]
            feedbacks: list = []
            iteration = 0
            production = ""
            interrupted = False

            # Boucle de raffinement : production -> relecture -> checkpoint,
            # repetee tant que l'humain fournit un feedback (jusqu'a CONTINUER
            # ou QUITTER). Chaque tour repart d'une memoire d'agent purgee et
            # d'une tache auto-contenue (production precedente + feedbacks).
            while True:
                await self._purge_step_contexts(step)

                if iteration == 0:
                    if agent_name == "engineer":
                        production = await self._run_engineer_step(step)
                    else:
                        production = await self._ask_agent(
                            self.secretary, await self._secretary_task(step)
                        )
                else:
                    await self._emit({
                        "type": "step_retry",
                        "step_id": step["id"],
                        "iteration": iteration,
                    })
                    print(f"\n>> Re-generation (iteration {iteration}) avec "
                          f"{len(feedbacks)} feedback(s) humain(s)...")
                    if agent_name == "engineer":
                        production = await self._run_engineer_step(
                            step,
                            previous_production=production,
                            human_feedback=self._feedback_section(feedbacks),
                        )
                    else:
                        production = await self._ask_agent(
                            self.secretary,
                            await self._secretary_task(
                                step,
                                human_feedback=self._feedback_section(feedbacks),
                                previous_production=production,
                            ),
                        )

                all_outputs[step["id"]] = production
                self.state.outputs[step["id"]] = production
                await self._emit({
                    "type": "production",
                    "step_id": step["id"],
                    "agent": agent_name,
                    "text": production,
                })
                print(f"\n[Production - {agent_name}] (iteration {iteration})")
                print(production[:1500] + ("..." if len(production) > 1500 else ""))

                review_output = ""
                if step.get("reviewer"):
                    print(f"\n[Relecture - {step['reviewer']}]")
                    review_output = await self._run_reviewer_step(
                        step,
                        production,
                        human_feedback="\n".join(feedbacks),
                    )
                    self.state.reviews[step["id"]] = review_output
                    await self._emit({
                        "type": "review",
                        "step_id": step["id"],
                        "reviewer": step["reviewer"],
                        "text": review_output,
                    })
                    print(review_output[:1500] + ("..." if len(review_output) > 1500 else ""))

                if not step["checkpoint"]:
                    break  # etape finale (livraison) : pas de checkpoint humain

                await self._emit({"type": "checkpoint", "step_id": step["id"]})
                suffix = f" (iteration {iteration})" if iteration else ""
                print(f"\n{'─'*50}")
                print(f"  Point de controle {step['checkpoint']}{suffix} - Validation humaine")
                print(f"{'─'*50}")
                print("\nTaper : CONTINUER | QUITTER | ou un feedback pour correction")
                feedback = await self._human_checkpoint(step, production, review_output)
                await self._emit({
                    "type": "checkpoint_answer",
                    "step_id": step["id"],
                    "answer": feedback[:200],
                })

                answer = feedback.upper().strip()
                if answer == "CONTINUER":
                    self.state.human_validations[step["id"]] = "validated"
                    print(">> Etape validee.")
                    break
                if answer == "QUITTER":
                    self.state.human_validations[step["id"]] = "quit"
                    print(">> Analyse interrompue.")
                    interrupted = True
                    break

                feedbacks.append(feedback)
                self.state.human_validations[step["id"]] = feedback
                iteration += 1
                print(">> Feedback enregistre, nouvelle iteration...")

            if interrupted:
                break

        print(f"\n{'='*70}\n  Analyse terminee\n{'='*70}")
        await self._emit({"type": "done"})
        return all_outputs


def client_from_profile(
    profile: ModelProfile,
    function_calling: bool = True,
) -> OpenAIChatCompletionClient:
    """Construit un client AutoGen depuis un ModelProfile explicite (GUI, CLI...)."""
    if not profile.model or not profile.model.strip():
        raise ValueError("Aucun modele selectionne.")
    if not profile.base_url or not profile.base_url.strip():
        raise ValueError("Aucune URL de serveur LLM (base_url) configuree.")
    if not profile.api_key or not profile.api_key.strip():
        raise ValueError(
            "Aucune cle API configuree "
            "(pour un serveur local type LM Studio, une valeur quelconque suffit)."
        )
    return OpenAIChatCompletionClient(
        model=profile.model,
        base_url=profile.base_url,
        api_key=profile.api_key,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        model_info={
            # AutoGen exige un model_info explicite pour un modele hors famille GPT
            # (LM Studio, DeepSeek...). Le tool calling depend du modele/serveur.
            "vision": False,
            "function_calling": function_calling,
            "json_output": False,
            "structured_output": False,
            "family": "unknown",
        },
        # Kwarg transmis a l'AsyncOpenAI sous-jacent : indispensable en local.
        # 1800 s car sans streaming la generation entiere doit tenir dans le
        # timeout ; le streaming (model_client_stream=True cote agents) fait
        # que le timeout s'applique entre chunks.
        timeout=float(os.getenv("LLM_TIMEOUT", "1800")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "1")),
    )


def create_model_client(profile_name: str = "default") -> OpenAIChatCompletionClient:
    """Construit un client depuis la configuration par variables d'environnement."""
    profiles = app_config.profiles
    if profile_name == "default":
        profile_name = profiles.default

    profile_map = {
        "cloud": profiles.cloud,
        "local": profiles.local,
    }
    if profile_name not in profile_map:
        raise ValueError(
            f"Profil modele inconnu : {profile_name!r} (attendus : cloud, local)"
        )
    profile = profile_map[profile_name]
    if not profile.api_key or not profile.api_key.strip():
        env_var = "CLOUD_API_KEY" if profile_name == "cloud" else "LOCAL_API_KEY"
        raise ValueError(
            f"Aucune cle API configuree pour le profil {profile_name!r}. "
            f"Definissez la variable d'environnement {env_var} "
            f"(pour un serveur local type LM Studio, une valeur quelconque suffit)."
        )
    function_calling = os.getenv("LLM_FUNCTION_CALLING", "true").lower() in (
        "1", "true", "yes",
    )
    return client_from_profile(profile, function_calling=function_calling)


def create_model_clients(mode: str = "hybrid") -> dict[str, OpenAIChatCompletionClient]:
    # N'instancie que les profils reellement utilises : evite notamment de creer
    # le client cloud (qui exige une CLOUD_API_KEY valide) quand on tourne en
    # 100% local.
    clients: dict[str, OpenAIChatCompletionClient] = {}
    if mode in ("hybrid", "cloud"):
        clients["cloud"] = create_model_client("cloud")
    if mode in ("hybrid", "local"):
        clients["local"] = create_model_client("local")
    return clients
