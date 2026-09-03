"""Interface graphique (NiceGUI) pour le Risk Analysis Copilot APR.

Lancement :
    python gui.py            (ouvre le navigateur sur http://localhost:8080)
    python gui.py --no-show  (sans ouvrir le navigateur)
    python gui.py --port 8090

Fonctions :
- Choix du mode d'affectation (hybride / cloud / local)
- Modeles LM Studio listes en direct depuis le serveur (bouton rafraichir)
- Parametres : temperature, max_tokens, tool calling, timeout
- Gestion de la base documentaire RAG (ingestion, statut)
- Lancement d'une analyse avec affichage en direct des etapes
- Points de controle humains interactifs (CONTINUER / QUITTER / feedback)

La CLI (main.py) reste inchangee et fonctionnelle.
"""

import argparse
import asyncio
import json
import os
import urllib.request
from pathlib import Path

from nicegui import ui

from agents.agents import EngineerAgent, QualityAgent, ClientAgent, SecretaryAgent
from agents.orchestrator import (
    WORKFLOW_STEPS,
    RiskAnalysisOrchestrator,
    client_from_profile,
)
from config import ModelProfile, config as app_config
from output_utils import save_analysis_outputs
from rag.retriever import invalidate_bm25_cache
from rag.vector_store import get_collection, ingest_documents


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _list_lmstudio_models(base_url: str) -> list[str]:
    """Liste les identifiants de modeles exposes par le serveur LM Studio."""
    url = base_url.rstrip("/") + "/models"
    with urllib.request.urlopen(url, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [m["id"] for m in data.get("data", [])]


def _collection_stats() -> str:
    try:
        collection = get_collection()
        return f"{collection.count()} chunks indexes dans '{collection.name}'"
    except Exception as exc:
        return f"Index indisponible : {exc}"


# ---------------------------------------------------------------------------
# Etat global (outil mono-utilisateur en local : une seule analyse a la fois)
# ---------------------------------------------------------------------------

pending_checkpoint: dict = {}          # {"future": Future, "step_id": str}
analysis_running: bool = False
step_cards: dict[str, dict] = {}       # step_id -> elements UI
log = None                             # ui.log, cree dans build_page()
checkpoint_dialog = None               # ui.dialog, creee dans build_page()


# ---------------------------------------------------------------------------
# Handlers checkpoint (popup de relecture / decision)
# ---------------------------------------------------------------------------

def _safe_notify(message: str, type_: str = "info") -> None:
    """ui.notify exige un contexte NiceGUI (slot) ; depuis une tache de fond
    (orchestrateur) ce contexte peut manquer -> fallback sur le journal."""
    try:
        ui.notify(message, type=type_)
    except RuntimeError:
        log.push(f"[notif] {message}")


async def gui_checkpoint(step_id: str, production: str, review: str, all_outputs: dict) -> str:
    """human_callback : ouvre la popup de relecture/decision et attend la decision."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    pending_checkpoint["future"] = fut
    pending_checkpoint["step_id"] = step_id
    card = step_cards[step_id]
    card["badge"].set_text("Checkpoint en attente")
    card["badge"].props("color=deep-orange")
    log.push(f"[Checkpoint] {step_id} : decision requise dans la popup")
    _safe_notify(f"Point de controle atteint : {step_id}")
    _open_checkpoint_dialog(step_id)
    return await fut


def _open_checkpoint_dialog(step_id: str) -> None:
    """Ouvre la popup pour l'etape donnee ; les boutons de decision ne sont
    visibles que si un checkpoint est en attente sur CETTE etape."""
    card = step_cards.get(step_id)
    if card is None:
        return
    pending_here = (
        pending_checkpoint.get("step_id") == step_id
        and pending_checkpoint.get("future") is not None
    )
    dlg_title.set_text(f"Point de controle — {card['name']}")
    dlg_prod_md.set_content(card.get("production_text") or "*Production non generee*")
    dlg_review_md.set_content(card.get("review_text") or "*Aucune relecture*")
    for btn in (btn_continue, btn_quit, btn_feedback):
        btn.set_visibility(pending_here)
    dlg_feedback.set_visibility(pending_here)
    dlg_status.set_text(
        "Decision attendue : CONTINUER, QUITTER, ou feedback — la popup se fermera "
        "automatiquement une fois le choix envoye."
        if pending_here
        else "Consultation. Aucune decision en attente sur cette etape."
    )
    checkpoint_dialog.open()


def decide(value: str) -> None:
    """Resout le checkpoint en attente et ferme automatiquement la popup."""
    fut = pending_checkpoint.get("future")
    if fut is None or fut.done():
        return
    step_id = pending_checkpoint.pop("step_id", None)
    pending_checkpoint.pop("future", None)
    checkpoint_dialog.close()
    fut.set_result(value)
    log.push(f">> Checkpoint {step_id} : {value[:80]}")


def send_feedback() -> None:
    text = (dlg_feedback.value or "").strip()
    if not text:
        ui.notify("Saisissez un feedback (ou utilisez CONTINUER / QUITTER).", type="warning")
        return
    dlg_feedback.set_value("")
    decide(text)


# ---------------------------------------------------------------------------
# Callback de progression (branche les evenements de l'orchestrateur sur l'UI)
# ---------------------------------------------------------------------------

async def on_progress(event: dict) -> None:
    etype = event.get("type")
    card = step_cards.get(event.get("step_id", ""))

    if etype == "step_start" and card:
        card["badge"].set_text("En cours…")
        card["badge"].props("color=amber")
        log.push(f"=== {event['name']} ===")
    elif etype == "production" and card:
        card["prod_md"].set_content(event["text"])
        card["production_text"] = event["text"]
        card["badge"].set_text("Production recue")
        card["badge"].props("color=teal")
        log.push(f"[{event['agent']}] production : {len(event['text'])} caracteres")
    elif etype == "review" and card:
        card["review_text"] = event["text"]
        log.push(f"[{event.get('reviewer')}] relecture : {len(event.get('text', ''))} caracteres")
    elif etype == "step_retry" and card:
        n = event.get("iteration", 1)
        card["badge"].set_text(f"Re-generation #{n}…")
        card["badge"].props("color=orange")
        log.push(f">> Feedback integre : re-generation #{n} de {event['step_id']}")
    elif etype == "context_purged":
        log.push(f"[memoire] contexte purge ({event.get('size_before', '?')} messages)")
    elif etype == "web_search":
        if event.get("error"):
            log.push(f"[web] recherche indisponible : {event['error'][:80]}")
        else:
            log.push(f"[web] '{event.get('query', '')[:60]}' : {event.get('count', 0)} resultats")
    elif etype == "checkpoint_answer":
        answer = event.get("answer", "").upper().strip()
        if card:
            if answer == "CONTINUER":
                card["badge"].set_text("Validee")
                card["badge"].props("color=green")
            elif answer == "QUITTER":
                card["badge"].set_text("Interrompue")
                card["badge"].props("color=red")
        log.push(f">> {event['step_id']} : {event['answer'][:80]}")
    elif etype == "done":
        log.push("=== Analyse terminee ===")


# ---------------------------------------------------------------------------
# Handlers RAG
# ---------------------------------------------------------------------------

async def list_models():
    try:
        ids = await asyncio.to_thread(_list_lmstudio_models, local_base_url.value)
        local_model.options = ids
        local_model.update()
        if ids and not local_model.value:
            local_model.value = ids[0]
        log.push(f"[LM Studio] {len(ids)} modele(s) detects")
        ui.notify(f"{len(ids)} modele(s) charges sur le serveur.", type="positive")
    except Exception as exc:
        ui.notify(f"Serveur LM Studio injoignable : {exc}", type="negative")


async def do_ingest():
    directory = Path(ingest_dir.value or "")
    if not directory.exists():
        ui.notify(f"Repertoire introuvable : {directory}", type="negative")
        return
    ingest_btn.disable()
    ingest_progress.set_visibility(True)
    log.push(f"[RAG] Ingestion de {directory} (reset={ingest_reset.value})…")
    try:
        report = await asyncio.to_thread(
            ingest_documents, directory, reset=ingest_reset.value
        )
        invalidate_bm25_cache()
        for line in report.summary().splitlines():
            log.push(f"[RAG] {line}")
        ui.notify(f"Ingestion terminee : {report.chunks} chunks indexes.", type="positive")
    except Exception as exc:
        log.push(f"[RAG][ERREUR] {exc}")
        ui.notify(f"Erreur pendant l'ingestion : {exc}", type="negative")
    finally:
        ingest_btn.enable()
        ingest_progress.set_visibility(False)


async def show_stats():
    info = await asyncio.to_thread(_collection_stats)
    log.push(f"[RAG] {info}")
    ui.notify(info, type="info")


def on_context_upload(e):
    try:
        text = e.content.read().decode("utf-8", errors="replace")
    except Exception as exc:
        ui.notify(f"Lecture du fichier impossible : {exc}", type="negative")
        return
    context_input.value = text
    ui.notify(f'Fichier "{e.name}" charge ({len(text)} caracteres).', type="positive")


# ---------------------------------------------------------------------------
# Lancement de l'analyse
# ---------------------------------------------------------------------------

async def start_analysis():
    global analysis_running
    if analysis_running:
        ui.notify("Une analyse est deja en cours.", type="warning")
        return
    mode = mode_radio.value
    context = (context_input.value or "").strip()
    if not context:
        ui.notify("Fournissez un contexte (texte ou fichier).", type="negative")
        return
    project = (project_input.value or "").strip() or "Sans_Nom"

    # Parametres avances lus via env par client_from_profile / _load_prompt
    os.environ["LLM_TIMEOUT"] = str(int(timeout_input.value or 1800))
    os.environ["LLM_MAX_RETRIES"] = str(int(retries_input.value or 1))
    os.environ["LLM_REASONING"] = str(reasoning_select.value or "off")
    os.environ["WEB_SEARCH_ENABLED"] = "true" if web_search_switch.value else "false"
    os.environ["WEB_SEARCH_BACKEND"] = str(web_backend.value or "ddg")
    os.environ["SEARXNG_URL"] = str(searxng_url_input.value or "")
    os.environ["TAVILY_API_KEY"] = str(tavily_key_input.value or "")

    def _local_profile() -> ModelProfile:
        return ModelProfile(
            model=local_model.value or "",
            base_url=local_base_url.value or "",
            api_key=local_api_key.value or "not-needed",
            temperature=float(local_temp.value or 0.3),
            max_tokens=int(local_max_tokens.value or 16384),
        )

    def _cloud_profile() -> ModelProfile:
        return ModelProfile(
            model=cloud_model.value or "",
            base_url=cloud_base_url.value or "",
            api_key=cloud_api_key.value or "",
            temperature=float(cloud_temp.value or 0.3),
            max_tokens=int(cloud_max_tokens.value or 8192),
        )

    try:
        if mode == "cloud":
            cloud_client = local_client = client_from_profile(
                _cloud_profile(), function_calling=function_calling_switch.value
            )
        elif mode == "local":
            cloud_client = local_client = client_from_profile(
                _local_profile(), function_calling=function_calling_switch.value
            )
        else:  # hybrid
            cloud_client = client_from_profile(
                _cloud_profile(), function_calling=function_calling_switch.value
            )
            local_client = client_from_profile(
                _local_profile(), function_calling=function_calling_switch.value
            )
    except Exception as exc:
        ui.notify(f"Configuration modele invalide : {exc}", type="negative")
        return

    engineer_wrapper = EngineerAgent(cloud_client)
    quality_wrapper = QualityAgent(cloud_client)
    client_wrapper = ClientAgent(cloud_client)
    secretary_wrapper = SecretaryAgent(local_client)

    orchestrator = RiskAnalysisOrchestrator(
        engineer=engineer_wrapper.agent,
        quality=quality_wrapper.agent,
        client=client_wrapper.agent,
        secretary=secretary_wrapper.agent,
        human_callback=gui_checkpoint,
        progress_callback=on_progress,
    )
    orchestrator.state.analysis_state.project_name = project

    # Remise a zero visuelle des cartes
    for card in step_cards.values():
        card["badge"].set_text("En attente")
        card["badge"].props("color=grey")
        card["prod_md"].set_content("*Production non generee*")
        card["production_text"] = ""
        card["review_text"] = ""

    analysis_running = True
    run_btn.disable()
    run_progress.set_visibility(True)
    log.push(f"=== Analyse demarree : {project} (mode {mode}) ===")

    try:
        outputs = await orchestrator.run_full_analysis(initial_context=context)
        md_path, json_path = await asyncio.to_thread(
            save_analysis_outputs, outputs, project, app_config.output_dir
        )
        log.push(f">> Livrable sauvegarde : {md_path}")
        ui.notify(f"Analyse terminee — {md_path}", type="positive")
    except Exception as exc:
        log.push(f"[ERREUR] {type(exc).__name__}: {exc}")
        if "timed out" in str(exc).lower() or "APITimeoutError" in type(exc).__name__:
            timeout_s = int(float(os.getenv("LLM_TIMEOUT", "1800")))
            log.push("[ERREUR] Timeout depasse. Pistes :")
            log.push(f"  - augmenter le timeout (actuellement {timeout_s} s)")
            log.push("  - reduire max_tokens")
            log.push("  - desactiver le Thinking du modele dans LM Studio (gain majeur)")
            log.push("  - activer Flash Attention + KV cache q8_0 au chargement")
            ui.notify(
                f"Generation trop longue : timeout de {timeout_s} s depasse. "
                "Pistes dans le journal : augmenter le timeout, reduire max_tokens, "
                "desactiver le Thinking dans LM Studio, activer Flash Attention.",
                type="negative",
                multi_line=True,
            )
        else:
            ui.notify(f"Erreur pendant l'analyse : {exc}", type="negative")
    finally:
        analysis_running = False
        run_btn.enable()
        run_progress.set_visibility(False)


# ---------------------------------------------------------------------------
# Construction de la page
# ---------------------------------------------------------------------------

def build_page() -> None:
    global log, mode_radio, local_base_url, local_model, local_api_key, local_temp, local_max_tokens
    global cloud_model, cloud_base_url, cloud_api_key, cloud_temp, cloud_max_tokens
    global function_calling_switch, timeout_input, retries_input, reasoning_select
    global web_search_switch, web_backend, searxng_url_input, tavily_key_input
    global checkpoint_dialog, dlg_title, dlg_prod_md, dlg_review_md, dlg_status
    global dlg_feedback, btn_continue, btn_quit, btn_feedback
    global ingest_dir, ingest_reset, ingest_btn, ingest_progress
    global project_input, context_input, run_btn, run_progress

    with ui.row().classes("w-full items-center justify-between"):
        with ui.column().classes("gap-0"):
            ui.label("Risk Analysis Copilot — APR").classes("text-h5")
            ui.label("Analyse Preliminaire de Risque multi-agents (LM Studio / cloud)").classes(
                "text-subtitle2 text-grey"
            )
        ui.badge("APR Copilot", color="blue-grey")

    with ui.row().classes("w-full items-start"):
        # ------------------------- Colonne reglages -------------------------
        with ui.column().classes("col-grow"):

            with ui.card().classes("w-full"):
                ui.label("Affectation des modeles").classes("text-subtitle1")
                mode_radio = ui.radio(
                    {
                        "hybrid": "Hybride (cloud + local)",
                        "cloud": "Tout sur le cloud",
                        "local": "Tout en local",
                    },
                    value=app_config.profiles.agent_profile,
                ).props("dense")

            with ui.card().classes("w-full"):
                ui.label("Modele local (LM Studio)").classes("text-subtitle1")
                local_base_url = ui.input(
                    "Base URL", value=app_config.profiles.local.base_url
                ).classes("w-full")
                with ui.row().classes("w-full items-center"):
                    local_model = ui.select(
                        [], with_input=True, label="Modele charge"
                    ).classes("grow")
                    ui.button(icon="refresh", on_click=list_models).props("flat dense")
                local_api_key = ui.input(
                    "Cle API", value=app_config.profiles.local.api_key or "not-needed"
                ).classes("w-full")
                with ui.row().classes("w-full items-center"):
                    local_temp = ui.number(
                        "Temperature", value=app_config.profiles.local.temperature,
                        min=0, max=2, step=0.05,
                    ).props("label-always")
                    local_max_tokens = ui.number(
                        "Max tokens", value=app_config.profiles.local.max_tokens,
                        format="%.0f", min=256,
                    ).props("label-always")

            with ui.card().classes("w-full"):
                ui.label("Modele cloud").classes("text-subtitle1")
                cloud_model = ui.input(
                    "Modele", value=app_config.profiles.cloud.model
                ).classes("w-full")
                cloud_base_url = ui.input(
                    "Base URL", value=app_config.profiles.cloud.base_url
                ).classes("w-full")
                cloud_api_key = ui.input(
                    "Cle API", value=app_config.profiles.cloud.api_key,
                    password=True,
                ).classes("w-full")
                with ui.row().classes("w-full items-center"):
                    cloud_temp = ui.number(
                        "Temperature", value=app_config.profiles.cloud.temperature,
                        min=0, max=2, step=0.05,
                    ).props("label-always")
                    cloud_max_tokens = ui.number(
                        "Max tokens", value=app_config.profiles.cloud.max_tokens,
                        format="%.0f", min=256,
                    ).props("label-always")

            with ui.expansion("Parametres avances", icon="tune").classes("w-full"):
                function_calling_switch = ui.switch("Tool calling", value=True)
                reasoning_select = ui.select(
                    {
                        "off": "Off (rapide)",
                        "low": "Low",
                        "medium": "Medium",
                        "high": "High",
                        "xhigh": "XHigh",
                    },
                    value=os.getenv("LLM_REASONING", "off"),
                    label="Niveau de raisonnement",
                ).classes("w-full")
                web_search_switch = ui.switch(
                    "Recherche web (etat de l'art)", value=False
                )
                web_backend = ui.select(
                    {
                        "ddg": "DuckDuckGo (sans cle)",
                        "searxng": "SearXNG (auto-heberge)",
                        "tavily": "Tavily (cle cloud)",
                    },
                    value=os.getenv("WEB_SEARCH_BACKEND", "ddg"),
                    label="Backend de recherche",
                ).classes("w-full")
                searxng_url_input = ui.input(
                    "URL SearXNG", value=os.getenv("SEARXNG_URL", "")
                ).classes("w-full")
                tavily_key_input = ui.input(
                    "Cle Tavily", value=os.getenv("TAVILY_API_KEY", ""), password=True
                ).classes("w-full")
                with ui.row().classes("w-full items-center"):
                    timeout_input = ui.number(
                        "Timeout appel LLM (s)",
                        value=float(os.getenv("LLM_TIMEOUT", "1800")),
                        format="%.0f", min=30,
                    ).props("label-always")
                    retries_input = ui.number(
                        "Retries", value=float(os.getenv("LLM_MAX_RETRIES", "3")),
                        format="%.0f", min=0,
                    ).props("label-always")

            with ui.card().classes("w-full"):
                ui.label("Base documentaire (RAG)").classes("text-subtitle1")
                ingest_dir = ui.input(
                    "Dossier de documents", value=str(app_config.input_dir)
                ).classes("w-full")
                ingest_reset = ui.switch(
                    "Reinitialiser l'index avant ingestion", value=False
                )
                with ui.row().classes("w-full items-center"):
                    ingest_btn = ui.button(
                        "Ingestion", icon="upload_file", on_click=do_ingest
                    )
                    ingest_progress = ui.linear_progress(show_value=False).props(
                        "indeterminate instant-feedback"
                    ).classes("grow")
                    ingest_progress.set_visibility(False)
                ui.button(
                    "Statut de l'index", icon="storage", on_click=show_stats
                ).props("flat dense")

            with ui.card().classes("w-full"):
                ui.label("Analyse").classes("text-subtitle1")
                project_input = ui.input("Nom du projet", value="Test_Projet").classes("w-full")
                context_input = ui.textarea(
                    "Contexte / description du systeme", value=""
                ).classes("w-full")
                ui.upload(
                    on_upload=on_context_upload, auto_upload=True
                ).props('accept=.txt,.md label="Charger un fichier de contexte"').classes("w-full")
                run_btn = ui.button(
                    "Lancer l'analyse", icon="play_arrow", on_click=start_analysis
                ).classes("w-full")
                run_progress = ui.linear_progress(show_value=False).props(
                    "indeterminate instant-feedback"
                )
                run_progress.set_visibility(False)

        # ------------------------- Colonne workflow -------------------------
        with ui.column().classes("col-grow"):
            ui.label("Workflow").classes("text-h6")
            for step in WORKFLOW_STEPS:
                with ui.card().classes("w-full"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(step["name"]).classes("text-subtitle1")
                        badge = ui.badge("En attente", color="grey")
                    with ui.scroll_area().classes("w-full").style(
                        "height: 220px; border-left: 3px solid #ddd; padding-left: 8px"
                    ):
                        prod_md = ui.markdown("*Production non generee*")
                    ui.button(
                        "Relecture / Validation",
                        icon="rate_review",
                        on_click=lambda s=step["id"]: _open_checkpoint_dialog(s),
                    ).props("flat dense")
                    step_cards[step["id"]] = {
                        "name": step["name"],
                        "badge": badge,
                        "prod_md": prod_md,
                        "production_text": "",
                        "review_text": "",
                    }

            log = ui.log(max_lines=500).classes("w-full").style("height: 200px")
            log.push("Astuce : cliquez sur le bouton refresh du modele local pour lister")
            log.push("les modeles charges sur le serveur LM Studio.")

    # Popup de relecture / decision des checkpoints
    with ui.dialog() as checkpoint_dialog:
        with ui.card().style("width: 1100px; max-width: 96vw"):
            dlg_title = ui.label("Point de controle").classes("text-h6")
            with ui.row().classes("w-full items-stretch"):
                with ui.column().classes("col grow"):
                    ui.label("Production").classes("text-subtitle2 text-grey")
                    with ui.scroll_area().style(
                        "height: 45vh; border-left: 3px solid #1976d2; padding-left: 8px"
                    ):
                        dlg_prod_md = ui.markdown("")
                with ui.column().classes("col grow"):
                    ui.label("Relecture").classes("text-subtitle2 text-grey")
                    with ui.scroll_area().style(
                        "height: 45vh; border-left: 3px solid #f9a825; padding-left: 8px"
                    ):
                        dlg_review_md = ui.markdown("")
            dlg_status = ui.label("").classes("text-caption text-grey")
            dlg_feedback = ui.input("Feedback a integrer (optionnel)").classes("w-full")
            with ui.row():
                btn_continue = ui.button(
                    "CONTINUER", on_click=lambda: decide("CONTINUER")
                ).props("color=positive")
                btn_quit = ui.button(
                    "QUITTER", on_click=lambda: decide("QUITTER")
                ).props("color=negative outline")
                btn_feedback = ui.button(
                    "Envoyer le feedback", on_click=send_feedback
                ).props("color=warning")

    ui.label(
        "Outil local mono-utilisateur. Les livrables sont ecrits dans "
        f"{app_config.output_dir}"
    ).classes("text-caption text-grey")


build_page()


def parse_args():
    parser = argparse.ArgumentParser(description="GUI APR — Risk Analysis Copilot")
    parser.add_argument("--port", type=int, default=8080, help="Port du serveur web (8080)")
    parser.add_argument("--no-show", action="store_true", help="Ne pas ouvrir le navigateur")
    parser.add_argument("--reload", action="store_true", help="Mode dev NiceGUI")
    return parser.parse_args()


if __name__ in {"__main__", "__mp_main__"}:
    args = parse_args()
    ui.run(
        title="Risk Analysis Copilot — APR",
        port=args.port,
        show=not args.no_show,
        reload=args.reload,
    )
