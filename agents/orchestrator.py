import asyncio
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_ext.models.openai import OpenAIChatCompletionClient

from config import ModelProfile, config as app_config
from rag.retriever import retrieve, format_retrieved_context
from state import AnalysisState

# ---------------------------------------------------------------------------
# Gestion automatique du contexte du modele local (detecte via l'API LM Studio)
# ---------------------------------------------------------------------------

_AUTO_CONTEXT_CACHE: dict = {"done": False, "limit": None, "ctx": 0}


def _auto_context_limit() -> Optional[int]:
    """Detecte le contexte reellement charge du modele local (API LM Studio
    /api/v0/models) et calcule la limite d'injection par etape :

        injectable_tokens = (ctx - LOCAL_MAX_TOKENS - overhead) * securite
        limite_par_etape  = injectable_tokens * car_par_token / 4 etapes

    Retourne None si la detection echoue (serveur non-LM Studio, hors ligne,
    cloud pur) -> repli sur STEP_CONTEXT_LIMIT. Resultat mis en cache pour
    l'analyse en cours (cache reinitialise a chaque run_full_analysis)."""
    if _AUTO_CONTEXT_CACHE["done"]:
        return _AUTO_CONTEXT_CACHE["limit"]
    limit = None
    ctx = 0
    try:
        base = app_config.profiles.local.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        url = base + "/api/v0/models"
        req = urllib.request.Request(url, headers={"User-Agent": "APR-Copilot/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        for m in data.get("data", []):
            if m.get("state") == "loaded":
                ctx = int(m.get("loaded_context_length") or 0)
                break
        if ctx > 0:
            out_tokens = int(os.getenv("LOCAL_MAX_TOKENS", "32768"))
            overhead = int(os.getenv("CONTEXT_OVERHEAD_TOKENS", "15000"))
            safety = float(os.getenv("CONTEXT_SAFETY", "0.9"))
            chars_per_token = float(os.getenv("CONTEXT_CHARS_PER_TOKEN", "3.5"))
            injectable = max(0, int((ctx - out_tokens - overhead) * safety))
            limit = max(1000, int(injectable * chars_per_token / 4))
    except Exception:
        limit = None
    _AUTO_CONTEXT_CACHE["done"] = True
    _AUTO_CONTEXT_CACHE["limit"] = limit
    _AUTO_CONTEXT_CACHE["ctx"] = ctx
    return limit


# ---------------------------------------------------------------------------
# Qualifications humaines par point de relecture (traçabilité inter-iterations)
# ---------------------------------------------------------------------------

_POINT_LINE_RE = re.compile(
    r"^-\s*\[(A CORRIGER|SANS OBJET|DEJA TRAITE)\]\s*"
    r"(?:\[(P?\d+)\]\s*)?(?P<text>.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_point_feedback(feedback: str, iteration: int = 0) -> list[dict]:
    """Extrait les qualifications humaines par point d'un feedback structure.

    Format produit par la GUI (send_feedback) :
        - [A CORRIGER] [P1] Titre du point — detail
        - [SANS OBJET] Point sans identifiant (repli non structure) — detail

    Retourne une liste de dicts {id, text, detail, decision, iteration}.
    """
    decisions = []
    for m in _POINT_LINE_RE.finditer(feedback or ""):
        decision = m.group(1).upper()
        raw_id = m.group(2)
        pid = None
        if raw_id and raw_id[1:].isdigit():
            pid = f"P{int(raw_id[1:])}"
        elif raw_id:
            pid = raw_id.upper()
        line = m.group("text").strip().rstrip(".")
        detail = ""
        if " — " in line:
            line, detail = line.split(" — ", 1)
        decisions.append({
            "id": pid,
            "text": line.strip(),
            "detail": detail.strip(),
            "decision": decision,
            "iteration": iteration,
        })
    return decisions


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

Menaces generiques a evaluer (mecanismes de defaillance) :
- Defaillance / perte de fonction
- Fonction intempestive
- Erreur humaine
- Mauvaise configuration
- Defaut d'interface
- Degradation progressive
- Action externe hostile (si pertinent)

Menaces EMISES par le systeme vers son environnement (le systeme source de danger) :
- Degazage / emanations chaudes ou toxiques (ex. batterie : degazement tres chaud -> menace haute temperature vers l'environnement)
- Echauffement emis / flamme / projection incandescente
- Fuite de fluide, explosion, ejection de pieces
- Danger electrique par contact, CEM emis, incendie propage, bruit emis, rayonnement
- Pour chaque fonction/composant, identifier ce que le systeme peut EMETTRE ou PROVOQUER, et les cibles : environnement proche, autres equipements, personnel

L'analyse est BIDIRECTIONNELLE : ce que le systeme SUBIT (agressions recues) ET ce qu'il FAIT SUBIR a son environnement (menaces emises). Ne pas se limiter au volet dysfonctionnel.

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
- Suivi prevu (revue periodique)
- Colonne 'Decision humaine' (OK/KO + detail) d'apres la section Decisions
  humaines enregistrees, si fournie]

## Bloc 5 - Points ouverts pour validation humaine
[Liste priorisee : ambiguites, hypotheses critiques, elements manquants, decisions
attendues. Pour chaque point, ajouter la 'Decision humaine' (OK/KO + detail)
d'apres la section Decisions humaines enregistrees, si fournie]
---

Format : Markdown, pret a etre converti en document Word ou Excel.
Sois sobre, professionnel, structure. Pas de fioritures.""",
        "reviewer_task": None,
    },
]


LIVRAISON_BLOCS = [
    {
        "id": "bloc1",
        "titre": "Bloc 1 — Résumé exécutif et gouvernance",
        "sources": ["cadrage"],
        "consigne": """Produis UNIQUEMENT le Bloc 1 du livrable : resume executif et gouvernance.
- Objet, perimetre, principales hypotheses, niveau de confiance global (reprends ceux du cadrage, ne les reinvente pas, n'en perds aucun)
- Tableau RACI de la demarche :
  A (Approuve, proprietaire des risques) : decision finale d'acceptation - humain validateur
  R (Realise) : Ingenieur Technique SDF
  C (Consulte) : Animateur Qualite, Representant Client
  I (Informe) : Secretaire / livrable""",
    },
    {
        "id": "bloc2",
        "titre": "Bloc 2 — Filtrage des agressions et menaces",
        "sources": ["filtrage"],
        "consigne": """Produis UNIQUEMENT le Bloc 2 du livrable : le tableau de filtrage des agressions et menaces.
Reprends TOUTES les lignes du filtrage fourni (agressions recues ET menaces emises) — AUCUNE ligne ne doit disparaitre, ne resume pas.
Colonnes : item | statut | justification | point a valider""",
    },
    {
        "id": "bloc3",
        "titre": "Bloc 3 — Analyse préliminaire de risque",
        "sources": ["scenarios", "barrieres"],
        "consigne": """Produis UNIQUEMENT le Bloc 3 du livrable : les tableaux d'analyse preliminaire de risque.
Source principale : les scenarios de risque ; complete la colonne barrieres/traitement avec les barrieres.
Colonnes requises : ID | Fonction | Phase de vie | Agression/Menace | Situation dangereuse | Evenement redoute | Causes | Consequences | Barrieres existantes | Barrieres recommandees | Gravite | Vraisemblance | Niveau de risque | Option de traitement | Risque residuel | Justification | Confiance | Points a valider
Reprends TOUS les scenarios fournis — AUCUN ne doit disparaitre. Ne resume pas, ne fusionne pas arbitrariairement.""",
    },
    {
        "id": "bloc4",
        "titre": "Bloc 4 — Plan de traitement et décisions d'acceptation",
        "sources": ["barrieres"],
        "consigne": """Produis UNIQUEMENT le Bloc 4 du livrable : plan de traitement et decisions d'acceptation.
Pour chaque risque non reduit a un niveau acceptable :
- Option de traitement (REDUCTION / MAINTIEN / REFUS / PARTAGE)
- Mesures et conditions d'execution
- Decision requise : QUI doit accepter (proprietaire des risques), a quel niveau
- Conditions d'acceptation eventuelles (duree, en attendant une action...)
- Suivi prevu (revue periodique)
- Colonne 'Decision humaine' (OK/KO + detail) d'apres la section Decisions humaines, si fournie""",
    },
    {
        "id": "bloc5",
        "titre": "Bloc 5 — Points ouverts pour validation humaine",
        "sources": ["cadrage", "filtrage", "scenarios", "barrieres"],
        "consigne": """Produis UNIQUEMENT le Bloc 5 du livrable : points ouverts pour validation humaine.
Liste priorisee : ambiguites, hypotheses critiques, elements manquants, decisions attendues — avec la 'Decision humaine' (OK/KO + detail) par point d'apres la section Decisions humaines, si fournie.
Reprends tous les points a valider mentionnes dans les sections fournies.""",
    },
]


@dataclass
class OrchestratorState:
    step_index: int = -1
    outputs: dict = field(default_factory=dict)
    reviews: dict = field(default_factory=dict)
    human_validations: dict = field(default_factory=dict)
    # Qualifications humaines PAR POINT de relecture, persistant entre les
    # iterations d'une meme etape : {step_id: {point_id: {decision, text,
    # detail, iteration}}}. Les points deja decides ne sont pas re-soumis a
    # l'humain et sont traces dans le livrable (Bloc 5).
    point_decisions: dict = field(default_factory=dict)
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

        Priorite : detection automatique du contexte charge du modele local
        (CONTEXT_AUTO, defaut true — via l'API LM Studio) ; sinon repli sur
        STEP_CONTEXT_LIMIT (defaut 40000). Toute troncature est signalee en
        log/GUI."""
        if os.getenv("CONTEXT_AUTO", "true").lower() in ("1", "true", "yes"):
            auto = _auto_context_limit()
            if auto:
                return auto
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

    def _step_name(self, sid: str) -> str:
        for s in WORKFLOW_STEPS:
            if s["id"] == sid:
                return s["name"]
        return sid

    def _livraison_bloc_task(
        self,
        bloc: dict,
        previous_production: str = "",
        human_feedback: str = "",
    ) -> str:
        """Tache focalisee pour UN bloc de la livraison : sources dediees + regles."""
        limit = self._step_context_limit()
        parts = [f"## Tache\n{bloc['consigne']}"]

        for sid in bloc["sources"]:
            out = self.state.outputs.get(sid)
            if out:
                parts.append(
                    f"## Section source : {self._step_name(sid)}\n{out[:limit]}"
                )

        decisions = [
            f"### Etape '{sid}'\n{txt}"
            for sid, txt in self.state.human_validations.items()
            if txt and txt not in ("validated", "quit")
        ]
        if decisions:
            # Bloc 5 : la table de traçabilité par point (le « pourquoi » des
            # conclusions) est la source principale ; les textes bruts completent.
            if bloc["id"] == "bloc5":
                trace = self._points_trace_section()
                if trace:
                    parts.append(trace)
            parts.append(
                "## Decisions et feedbacks humains enregistres aux points de controle\n"
                + "\n\n".join(decisions)
            )

        if previous_production and human_feedback:
            parts.append(
                f"## Livrable precedent (A CORRIGER selon le feedback humain)\n"
                f"{previous_production[: self._step_context_limit()]}"
            )
            parts.append(
                f"## FEEDBACK HUMAIN A INTEGRER (prioritaire, a suivre a la lettre)\n"
                f"{human_feedback}"
            )

        parts.append(
            "## Regles\n"
            "- Reponds UNIQUEMENT avec le contenu du bloc demande, sans preambule, "
            "sans commentaire meta, sans mention du processus ou du format genere.\n"
            "- Reprends fidelement les sections fournies : ne resume pas, ne perds "
            "aucune ligne ni aucun point, n'invente rien.\n"
            "- Français, Markdown, tableaux complets."
        )
        return "\n\n".join(parts)

    async def _run_secretary_step(
        self,
        step: dict,
        previous_production: str = "",
        human_feedback: str = "",
    ) -> str:
        """Etape livraison : assemblage PAR BLOCS en plusieurs appels focalises.

        Un seul appel ne peut pas retranscrire fidelement ~200k caracteres de
        productions (sortie plafonnee a max_tokens) : la generation s'arretait
        en cours de route (livrable ampute du Bloc 4/5). Chaque bloc est
        desormais produit par un appel dedie, puis les blocs sont concatenes.
        """
        outputs = []
        total = len(LIVRAISON_BLOCS)
        for i, bloc in enumerate(LIVRAISON_BLOCS, 1):
            await self._emit({
                "type": "delivery_bloc",
                "step_id": step["id"],
                "bloc": f"{i}/{total}",
                "titre": bloc["titre"],
            })
            task = self._livraison_bloc_task(bloc, previous_production, human_feedback)
            out = await self._ask_agent(self.secretary, task)
            outputs.append(f"## {bloc['titre']}\n\n{out.strip()}")
            print(f"[livraison] {bloc['titre']} : {len(out)} caracteres ({i}/{total})")
        return "\n\n".join(outputs)

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
        previous_points = self.state.point_decisions.get(step["id"], {})
        if previous_points:
            rows = "\n".join(
                f"- [{p.get('id') or key}] decision : {p.get('decision', '')} "
                f"— {p.get('text', '')[:100]}{(' (detail : ' + p['detail'][:100] + ')') if p.get('detail') else ''}"
                for key, p in previous_points.items()
            )
            review_task += (
                f"## Points deja qualifies par l'humain (iterations precedentes)\n"
                f"{rows}\n"
                f"Regles :\n"
                f"- Si un de ces points PERSISTE dans la production actuelle, re-emets-le "
                f"avec le MEME identifiant [P#] et mentionne 'PERSISTE' dans sa justification.\n"
                f"- S'il est resolu, ne le mentionne pas.\n"
                f"- Les nouveaux points prennent la premiere numerotation libre.\n\n"
            )
        review_task += (
            f"## Consigne de relecture\n{reviewer_task_template}\n\n"
            f"## Format des points\n"
            f"Chaque point souleve suit le format canonique defini dans ton prompt "
            f"systeme :\n"
            f"### [P#] Titre court du point\n"
            f"- Localisation : <section du livrable et ID de ligne concernes>\n"
            f"- Extrait : « citation VERBATIM du passage concerne (100-250 caracteres) »\n"
            f"- Verdict : BLOQUANT | IMPORTANT | MINEUR\n"
            f"- Justification : ...\n"
            f"- Correction proposee : ...\n"
            f"Tout point doit citer verbatim le passage concerne et etre ancre a une "
            f"section ou un ID de la production — jamais de paraphrase hors contexte."
        )
        return await self._ask_agent(reviewer, review_task)

    async def _human_checkpoint(self, step: dict, engineer_output: str, review_output: str) -> str:
        if self.human_callback:
            return await self.human_callback(
                step["id"], engineer_output, review_output, self.state.outputs,
                self.state.point_decisions.get(step["id"], {}),
            )
        return "CONTINUER"

    def _record_point_decisions(self, step_id: str, feedback: str, iteration: int) -> None:
        """Enregistre les qualifications humaines par point (persistant entre
        iterations : un point decide n'est jamais re-soumis a l'humain)."""
        store = self.state.point_decisions.setdefault(step_id, {})
        for pd in parse_point_feedback(feedback, iteration):
            key = pd["id"] or f"p_{len(store) + 1}"
            store[key] = pd

    def _points_trace_section(self) -> str:
        """Table de traçabilité des qualifications humaines par point.

        C'est la justification tracée des conclusions : pour chaque point
        soulevé par les relectures, la qualification humaine et la version de
        la production où elle a été donnée."""
        rows = []
        for step_id, points in self.state.point_decisions.items():
            step_name = self._step_name(step_id)
            for key, p in points.items():
                pid = p.get("id") or key
                text = (p.get("text") or "").replace("|", "/")[:110]
                detail = (p.get("detail") or "").replace("|", "/")[:110]
                rows.append(
                    f"| {step_name} | {pid} | {p.get('decision', '')} | "
                    f"{text}{(' — ' + detail) if detail else ''} | "
                    f"V{p.get('iteration', 0) + 1} |"
                )
        if not rows:
            return ""
        return (
            "## Traçabilité des points de contrôle (qualifications humaines)\n"
            "Table de décision : pour chaque point soulevé par les relectures, la "
            "qualification humaine (à corriger / sans objet / déjà traité), le détail "
            "donné et la version où elle a été prise — c'est le « pourquoi » des "
            "conclusions du rapport.\n\n"
            "| Etape | Point | Decision | Detail | Version |\n"
            "| --- | --- | --- | --- | --- |\n" + "\n".join(rows)
        )

    async def run_full_analysis(self, initial_context: str = "") -> dict:
        all_outputs = {}

        if initial_context:
            self.state.analysis_state.system_description = initial_context

        # Gestion automatique du contexte : re-detection a chaque analyse
        _AUTO_CONTEXT_CACHE["done"] = False
        if os.getenv("CONTEXT_AUTO", "true").lower() in ("1", "true", "yes"):
            auto_limit = _auto_context_limit()
            ctx_loaded = _AUTO_CONTEXT_CACHE.get("ctx", 0)
            if auto_limit:
                print(f"[contexte] auto : {ctx_loaded} tokens charges -> limite d'injection "
                      f"{auto_limit} caracteres par etape precedente")
                await self._emit({"type": "context_auto", "ctx": ctx_loaded, "limit": auto_limit})
            else:
                print("[contexte] auto : detection impossible -> limite manuelle (STEP_CONTEXT_LIMIT)")

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
                        production = await self._run_secretary_step(step)
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
                        production = await self._run_secretary_step(
                            step,
                            previous_production=production,
                            human_feedback=self._feedback_section(feedbacks),
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
                    self.state.human_validations[step["id"]] = (
                        self._feedback_section(feedbacks) if feedbacks else "validated"
                    )
                    print(">> Etape validee.")
                    break
                if answer == "QUITTER":
                    self.state.human_validations[step["id"]] = "quit"
                    print(">> Analyse interrompue.")
                    interrupted = True
                    break

                if feedback.strip().upper().startswith("SANS CORRECTION"):
                    # Points de la relecture qualifies par l'humain en
                    # 'Sans objet' / 'Deja traite' : rien a corriger, l'etape
                    # est validee et les decisions sont tracees.
                    self._record_point_decisions(step["id"], feedback, iteration)
                    self.state.human_validations[step["id"]] = (
                        self._feedback_section(feedbacks + [feedback])
                    )
                    print(">> Etape validee (points de la relecture qualifies, sans correction).")
                    break

                self._record_point_decisions(step["id"], feedback, iteration)
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
