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
import re
import urllib.request
from pathlib import Path

from nicegui import ui

from agents.agents import EngineerAgent, QualityAgent, ClientAgent, SecretaryAgent
from agents.orchestrator import (
    WORKFLOW_STEPS,
    OrchestratorState,
    RiskAnalysisOrchestrator,
    client_from_profile,
)
from config import ModelProfile, config as app_config
from output_utils import save_analysis_outputs
from rag.retriever import invalidate_bm25_cache
from rag.vector_store import get_collection, ingest_documents
from session_store import delete_session, list_sessions, load_session


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


def _local_profile() -> ModelProfile:
    """Profil local construit depuis les champs de la GUI."""
    return ModelProfile(
        model=local_model.value or "",
        base_url=local_base_url.value or "",
        api_key=local_api_key.value or "not-needed",
        temperature=float(local_temp.value or 0.3),
        max_tokens=int(local_max_tokens.value or 16384),
    )


def _cloud_profile() -> ModelProfile:
    """Profil cloud construit depuis les champs de la GUI."""
    return ModelProfile(
        model=cloud_model.value or "",
        base_url=cloud_base_url.value or "",
        api_key=cloud_api_key.value or "",
        temperature=float(cloud_temp.value or 0.3),
        max_tokens=int(cloud_max_tokens.value or 8192),
    )


def _fmt_phase_line(phase: dict, totals: dict | None = None) -> str:
    """Ligne de statistiques de phase (carte d'etape)."""
    if not phase or not phase.get("calls"):
        return ""
    parts = [
        f"⏱ {phase['gen_time_s']:.0f} s de génération",
        f"{phase['tokens_in']:,} tok in".replace(",", " "),
        f"{phase['tokens_out']:,} tok out".replace(",", " "),
    ]
    if phase.get("tok_s"):
        parts.append(f"{phase['tok_s']} tok/s")
    if phase.get("models"):
        parts.append(", ".join(phase["models"]))
    line = " · ".join(parts)
    if totals and totals.get("eta_remaining_s") is not None:
        line += f" · ETA restant ~{totals['eta_remaining_s'] / 60:.0f} min"
    return line


def _fmt_totals_line(totals: dict) -> str:
    """Ligne de statistiques globales (fin d'analyse)."""
    parts = [f"Temps de generation cumule : {totals['gen_time_s']:.0f} s"]
    if totals.get("elapsed_no_pause_s") is not None:
        parts.append(f"écoulé hors pauses : {totals['elapsed_no_pause_s']:.0f} s")
    parts.append(
        f"{totals['tokens_in']:,} in / {totals['tokens_out']:,} out".replace(",", " ")
    )
    if totals.get("tok_s"):
        parts.append(f"{totals['tok_s']} tok/s moyen")
    if totals.get("cost_eur") is not None:
        parts.append(f"coût ~{totals['cost_eur']:.3f} EUR")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Etat global (outil mono-utilisateur en local : une seule analyse a la fois)
# ---------------------------------------------------------------------------

pending_checkpoint: dict = {}          # {"future": Future, "step_id": str}
analysis_running: bool = False
step_cards: dict[str, dict] = {}       # step_id -> elements UI
log = None                             # ui.log, cree dans build_page()
checkpoint_dialog = None               # ui.dialog, creee dans build_page()
sessions_container = None              # ui.column du panneau Sessions


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


_CANON_BLOCK_RE = re.compile(
    r"^#{3,4}\s*\[?\[?(P\s?\d+|\d+)\]?\]?\s*[—–:-]?\s*(.+?)\s*$",
    re.MULTILINE,
)
_FIELD_LINE_RE = re.compile(
    r"^-\s*\*{0,2}(Localisation|Extrait|Verdict|Justification|Correction|"
    r"Recommandation|Statut)\b[^:]*:", re.IGNORECASE
)
_BULLET_RE = re.compile(r"^(?:[-*•]|\d+[.)])\s+(.+)$")


def _parse_review_points(review_text: str, max_items: int = 100) -> list[dict]:
    """Extrait TOUS les points de la relecture — canoniques ET en repli.

    1) Blocs canoniques : '### [P#] Titre' (### ou ####, P# avec ou sans
       crochets) suivis des champs Localisation/Extrait/Verdict/Justification/
       Correction (tolere le gras et l'extrait renvoye a la ligne suivante).
    2) Puces/numerations HORS des blocs canoniques : un relecteur peut
       melanger les formats — v1.3.16 les ignorait des que des blocs
       canoniques existaient (points manquants dans la popup, ex. 18 listes
       mais seulement 15 qualifiables).

    Points retournes dans l'ordre du document. Plafond large (100) : un
    point laisse de cote est un point que l'humain ne peut pas qualifier."""
    import re

    text = review_text or ""
    blocks = list(_CANON_BLOCK_RE.finditer(text))
    lines_info = []
    off = 0
    for line in text.splitlines(keepends=True):
        lines_info.append((off, line))
        off += len(line)

    def _in_block(s: str) -> bool:
        """Ligne appartenant au bloc canonique : vide, champ ('- Cle :') ou
        continuation d'extrait (« ... »). Une puce libre termine le bloc."""
        c = s.strip().replace("*", "")
        if c == "":
            return True
        if _FIELD_LINE_RE.match(s):
            return True
        return c.startswith("«")

    points: list[tuple[int, dict]] = []
    consumed: list[tuple[int, int]] = []

    for m in blocks:
        # Fin du bloc : premiere ligne non-vide/non-champ/non-continuation
        block_end = len(text)
        for lo, line in lines_info:
            if lo < m.end():
                continue
            if not _in_block(line):
                block_end = lo
                break
        consumed.append((m.start(), block_end))
        raw = m.group(1).replace(" ", "")
        pid = f"P{int(raw)}" if raw.isdigit() else f"P{raw[1:]}"
        p = {"id": pid, "titre": m.group(2).strip(),
             "localisation": "", "extrait": "", "verdict": "",
             "justification": "", "correction": ""}
        lines = text[m.end():block_end].splitlines()
        for li, line in enumerate(lines):
            clean = line.strip().replace("*", "")
            fm = re.match(r"^-\s*([^:]{2,30}):\s*(.*)$", clean)
            if not fm:
                # extrait renvoye a la ligne suivante (apres '- Extrait :' vide)
                if p["extrait"] == "" and clean.startswith("«") and li > 0:
                    prev = lines[li - 1].strip().replace("*", "").lower()
                    if prev.startswith("- extrait"):
                        p["extrait"] = clean.strip("«»").strip()
                continue
            key = fm.group(1).strip().lower()
            val = fm.group(2).strip().strip("«»\"").strip()
            if key.startswith("localisation"):
                p["localisation"] = val
            elif key.startswith(("extrait", "citation")):
                p["extrait"] = val or p["extrait"]
            elif key.startswith("verdict"):
                p["verdict"] = val[:40]
            elif key.startswith("justification"):
                p["justification"] = val
            elif key.startswith(("correction", "proposition")):
                p["correction"] = val
        p["text"] = p["titre"]
        points.append((m.start(), p))

    # Puces / numerations hors des blocs canoniques (repli combine)
    off = 0
    for line in text.splitlines(keepends=True):
        line_start = off
        off += len(line)
        if any(a <= line_start < b for (a, b) in consumed):
            continue
        s = line.strip()
        if _FIELD_LINE_RE.match(s):
            continue
        bm = _BULLET_RE.match(s)
        if not bm:
            continue
        item = re.sub(r"\*+", "", bm.group(1)).strip()
        if len(item) > 3 and not item.lower().startswith(("http", "source ")):
            txt = item[:240]
            points.append((line_start, {
                "id": None, "titre": txt, "text": txt,
                "localisation": "", "extrait": "", "verdict": "",
                "justification": "", "correction": ""}))
    points.sort(key=lambda t: t[0])
    return [p for _, p in points[:max_items]]


checklist_rows: list = []              # lignes OK/KO detail de la popup en cours
dialog_state: dict = {"maximized": False}
decided_points_by_step: dict = {}      # step_id -> {point_id: {decision, text, detail, iteration}}


def _split_decided_points(points: list[dict], decided: dict) -> tuple[list[dict], list[dict], set]:
    """Separe les points d'une relecture en : (a nouveaux points a qualifier),
    (b points deja qualifies aux iterations precedentes — non re-soumis) avec
    marquage 're_signale' lorsque le relecteur le re-emet, et (c) les IDs de
    la relecture courante. Un point deja decide n'est JAMAIS re-soumis."""
    current_ids = {p.get("id") for p in points if p.get("id")}
    new_points = [p for p in points if not (p.get("id") and p["id"] in decided)]
    already = []
    for pid, d in decided.items():
        d = dict(d)
        d.setdefault("id", pid)
        d["re_signale"] = pid in current_ids
        already.append(d)
    return new_points, already, current_ids


def _apply_dialog_size() -> None:
    border_p = "border-left: 3px solid #1976d2; padding-left: 8px; "
    border_r = "border-left: 3px solid #f9a825; padding-left: 8px; "
    if dialog_state["maximized"]:
        dlg_card.style("width: 97vw; height: 94vh; overflow: auto")
        dlg_prod_scroll.style(border_p + "height: 62vh")
        dlg_review_scroll.style(border_r + "height: 62vh")
    else:
        dlg_card.style(
            "width: 1650px; max-width: 96vw; height: 90vh; resize: both; overflow: auto"
        )
        dlg_prod_scroll.style(border_p + "height: 40vh")
        dlg_review_scroll.style(border_r + "height: 40vh")
    btn_maximize.props("icon=fullscreen_exit" if dialog_state["maximized"] else "icon=fullscreen")


def toggle_maximize() -> None:
    dialog_state["maximized"] = not dialog_state["maximized"]
    _apply_dialog_size()


async def gui_checkpoint(
    step_id: str, production: str, review: str, all_outputs: dict,
    decided_points: dict | None = None,
) -> str:
    """human_callback : ouvre la popup de relecture/decision et attend la decision.

    decided_points : qualifications humaines PAR POINT deja donnees aux
    iterations precedentes de cette etape — les points concernes ne sont PAS
    re-soumis a l'humain (ils sont affiches en lecture seule et traces dans le
    rapport final)."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    pending_checkpoint["future"] = fut
    pending_checkpoint["step_id"] = step_id
    decided_points_by_step[step_id] = decided_points or {}
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
    version = int(card.get("iteration", 0)) + 1  # V1 = premiere production, V2 = 1re re-generation...
    dlg_title.set_text(f"Point de contrôle — {card['name']} — version V{version}")
    dlg_prod_md.set_content(card.get("production_text") or "*Production non generee*")
    dlg_review_md.set_content(card.get("review_text") or "*Aucune relecture*")
    ph = card.get("phase_stats") or {}
    dlg_stats.set_text(
        f"Performance de l'étape : {ph.get('calls', 0)} appel(s) · "
        f"{ph.get('gen_time_s', 0):.0f} s de génération · "
        f"{ph.get('tokens_in', 0):,} tok in / {ph.get('tokens_out', 0):,} tok out"
        + (f" · {ph['tok_s']} tok/s" if ph.get("tok_s") else "")
        + (f" · {', '.join(ph['models'])}" if ph.get("models") else "")
    )
    dlg_stats.classes("text-caption text-grey")

    # Qualification des points souleves par la relecture (semantique explicite)
    dlg_points_container.clear()
    checklist_rows.clear()
    if pending_here:
        points = _parse_review_points(card.get("review_text") or "")
        n_canon = sum(1 for p in points if p.get("id"))
        log.push(f"[relecture] {len(points)} point(s) detecte(s) "
                 f"({n_canon} canonique(s), {len(points) - n_canon} en repli)")
        decided = decided_points_by_step.get(step_id, {})
        new_points, already_points, current_ids = _split_decided_points(points, decided)
        with dlg_points_container:
            # Points deja qualifies aux iterations precedentes : lecture seule,
            # repris tels quels, jamais re-soumis a l'humain.
            if already_points:
                ui.label(
                    "Points déjà qualifiés (itérations précédentes) — repris tels quels, "
                    "tracés dans le rapport final :"
                ).classes("text-subtitle2")
                with ui.scroll_area().style(
                    "height: 10vh; border-left: 3px solid #c8e6c9; padding-left: 8px"
                ):
                    for d in already_points:
                        v = int(d.get("iteration", 0)) + 1
                        dtxt = (d.get("text") or d.get("titre") or "")[:140]
                        ddet = (d.get("detail") or "").strip()
                        label = f"[{d.get('id')}] {d.get('decision', '?')} — {dtxt} — V{v}"
                        if d.get("re_signale"):
                            label += " (re-signalé par le relecteur mais conservé)"
                        with ui.row().classes("w-full items-center no-wrap"):
                            ui.icon("check_circle", color="grey").style("font-size: 14px")
                            ui.label(label).classes("grow text-caption")
                            if ddet:
                                ui.label(f"({ddet[:80]})").classes("text-caption text-grey")
            # Points a qualifier : enrichis (extra it verbatim, localisation, verdict).
            if new_points:
                ui.label(
                    f"Points soulevés par la relecture ({len(new_points)}) — "
                    "qualifiez chacun :"
                ).classes("text-subtitle2")
                ui.label(
                    "« À corriger » = le problème est réel, déclenche une re-génération. "
                    "« Sans objet » / « Déjà traité » = pas de correction ; c'est tracé "
                    "dans le livrable sans re-génération."
                ).classes("text-caption text-grey")
                with ui.scroll_area().style(
                    "height: 34vh; border-left: 3px solid #ddd; padding-left: 8px"
                ):
                    for pt in new_points:
                        title = pt.get("titre") or pt.get("text", "")
                        verdict = (pt.get("verdict") or "").upper()
                        vcolor = {"BLOQUANT": "red", "IMPORTANT": "orange",
                                  "MINEUR": "grey"}.get(verdict, "grey")
                        prefix = f"{pt['id']} " if pt.get("id") else ""
                        with ui.row().classes(
                            "w-full items-center no-wrap gap-2"
                        ).style("border-top: 1px solid #eee; padding-top: 6px"):
                            if verdict:
                                ui.badge(verdict, color=vcolor).props("dense")
                            tog = ui.toggle(
                                {
                                    "corriger": "À corriger",
                                    "sans_objet": "Sans objet",
                                    "deja_traite": "Déjà traité",
                                },
                                clearable=True,
                            ).props("dense")
                            ui.label(prefix + title).classes("grow text-body2")
                        if pt.get("extrait"):
                            ui.label("Extrait :").classes("text-caption text-grey")
                            ui.code(pt["extrait"].strip()).classes("w-full").style(
                                "white-space: pre-wrap; font-size: 12px; padding: 4px 8px"
                            )
                        with ui.row().classes("w-full items-center gap-2"):
                            if pt.get("localisation"):
                                ui.label(f"📍 {pt['localisation']}").classes(
                                    "text-caption text-grey"
                                )
                            det = ui.input(
                                placeholder="detail / justification"
                            ).props("dense outlined").style("width: 320px")
                        checklist_rows.append(
                            {"id": pt.get("id"), "text": title, "toggle": tog,
                             "detail": det}
                        )
            if not new_points and not decided:
                ui.label(
                    "Aucun point liste detecte dans la relecture — utilisez le "
                    "feedback global ci-dessous."
                ).classes("text-caption text-grey")

    for btn in (btn_continue, btn_quit, btn_feedback):
        btn.set_visibility(pending_here)
    dlg_feedback.set_visibility(pending_here)
    dlg_points_container.set_visibility(pending_here)
    dlg_status.set_text(
        f"Version validée : V{version}. Qualifiez les points ci-dessus, ou "
        "CONTINUER / QUITTER — la popup se ferme automatiquement après envoi."
        if pending_here
        else "Consultation. Aucune decision en attente sur cette etape."
    )
    if pending_here:
        pending_checkpoint["version"] = version
    checkpoint_dialog.open()


def decide(value: str) -> None:
    """Resout le checkpoint en attente et ferme automatiquement la popup."""
    fut = pending_checkpoint.get("future")
    if fut is None or fut.done():
        return
    step_id = pending_checkpoint.pop("step_id", None)
    pending_checkpoint.pop("future", None)
    pending_checkpoint.pop("version", None)
    checklist_rows.clear()
    checkpoint_dialog.close()
    fut.set_result(value)
    log.push(f">> Checkpoint {step_id} : {value[:80]}")


def send_feedback() -> None:
    """Compose un feedback structure qualifie : A CORRIGER / SANS OBJET /
    DEJA TRAITE, avec version de la production et commentaire global.

    - au moins un 'A corriger'  -> re-generation (feedback prioritaire)
    - uniquement Sans objet/Deja traite -> 'SANS CORRECTION' : etape validee
      et decisions tracees dans le livrable, sans re-generation
    """
    version = pending_checkpoint.get("version", 1)
    qualified = []
    corriger_count = 0
    for row in checklist_rows:
        choice = row["toggle"].value
        if choice not in ("corriger", "sans_objet", "deja_traite"):
            continue
        detail = (row["detail"].value or "").strip()
        label = {"corriger": "A CORRIGER", "sans_objet": "SANS OBJET",
                 "deja_traite": "DEJA TRAITE"}[choice]
        pid = row.get("id")
        line = f"- [{label}] " + (f"[{pid}] " if pid else "") + row["text"]
        if detail:
            line += f" — {detail}"
        qualified.append(line)
        if choice == "corriger":
            corriger_count += 1
    global_fb = (dlg_feedback.value or "").strip()

    if not qualified and not global_fb:
        _safe_notify(
            "Qualifiez au moins un point, saisissez un feedback global, ou "
            "utilisez CONTINUER / QUITTER.", type_="warning",
        )
        return

    if corriger_count == 0 and qualified:
        # Rien a corriger : decisions tracees, etape validee sans re-generation
        text = (
            f"SANS CORRECTION (version V{version}) — points de la relecture "
            f"qualifies par l'humain :\n" + "\n".join(qualified)
        )
        if global_fb:
            text += f"\nCommentaire global : {global_fb}"
        dlg_feedback.set_value("")
        decide(text)
        return

    lines = []
    if qualified:
        lines.extend(qualified)
    if global_fb:
        lines.append(f"Commentaire global : {global_fb}")
    dlg_feedback.set_value("")
    decide(f"POINTS DE CONTROLE HUMAIN (version V{version}) :\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Callback de progression (branche les evenements de l'orchestrateur sur l'UI)
# ---------------------------------------------------------------------------

async def on_progress(event: dict) -> None:
    etype = event.get("type")
    card = step_cards.get(event.get("step_id", ""))

    if etype == "step_start" and card:
        card["badge"].set_text("En cours…")
        card["badge"].props("color=amber")
        card["iteration"] = 0
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
        card["iteration"] = n
        card["badge"].set_text(f"Re-generation #{n}…")
        card["badge"].props("color=orange")
        log.push(f">> Feedback integre : re-generation #{n} de {event['step_id']}")
    elif etype == "context_purged":
        log.push(f"[memoire] contexte purge ({event.get('size_before', '?')} messages)")
    elif etype == "context_truncated":
        log.push(f"[contexte] TRONCATURE : {event.get('detail', '')}")
        log.push(
            f"[contexte] Des points risquent de sauter — augmentez la limite "
            f"(actuellement {event.get('limit', '?')} caracteres)."
        )
    elif etype == "context_auto":
        log.push(
            f"[contexte] auto : {event.get('ctx', '?')} tokens charges -> "
            f"{event.get('limit', '?')} caracteres par etape precedente"
        )
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
            elif answer.startswith("SANS CORRECTION"):
                card["badge"].set_text("Validee (decisions tracees)")
                card["badge"].props("color=green")
        log.push(f">> {event['step_id']} : {event['answer'][:80]}")
    elif etype == "delivery_bloc":
        card = step_cards.get(event.get("step_id", ""))
        if card:
            card["badge"].set_text(f"Livraison bloc {event.get('bloc', '')}…")
            card["badge"].props("color=teal")
        log.push(f"[livraison] {event.get('titre', '')} ({event.get('bloc', '')})")
    elif etype == "step_stats":
        ph = event.get("phase") or {}
        tt = event.get("totals") or {}
        if card:
            card["phase_stats"] = ph
            card["totals"] = tt
            if card.get("stats_md") is not None:
                card["stats_md"].set_content(_fmt_phase_line(ph, tt))
        log.push(_fmt_phase_line(ph, tt))
    elif etype == "step_skipped":
        if card:
            card["badge"].set_text("Validee (session)")
            card["badge"].props("color=green")
        log.push(f"[session] {event.get('name', event.get('step_id', ''))} : "
                 "deja validee en session, conservee en l'etat")
    elif etype == "resumed":
        log.push(f"[session] Reprise de '{event.get('session', '')}' — "
                 f"{event.get('n_steps', 0)} etape(s) validee(s) conservee(s)")
    elif etype == "done":
        log.push("=== Analyse terminee ===")
        totals = event.get("totals") or {}
        if totals:
            log.push(_fmt_totals_line(totals))


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
    _apply_env_settings()
    await _launch_analysis(mode, project, context)


def _apply_env_settings() -> None:
    """Parametres avances pushes en env (lus par client_from_profile / prompts)."""
    os.environ["LLM_TIMEOUT"] = str(int(timeout_input.value or 1800))
    os.environ["LLM_MAX_RETRIES"] = str(int(retries_input.value or 1))
    os.environ["LLM_REASONING"] = str(reasoning_select.value or "off")
    os.environ["WEB_SEARCH_ENABLED"] = "true" if web_search_switch.value else "false"
    os.environ["WEB_SEARCH_BACKEND"] = str(web_backend.value or "ddg")
    os.environ["SEARXNG_URL"] = str(searxng_url_input.value or "")
    os.environ["TAVILY_API_KEY"] = str(tavily_key_input.value or "")
    os.environ["STEP_CONTEXT_LIMIT"] = str(int(step_context_limit_input.value or 40000))
    os.environ["CONTEXT_AUTO"] = "true" if context_auto_switch.value else "false"


async def _launch_analysis(
    mode: str,
    project: str,
    context: str,
    resume_data: dict | None = None,
    session_dir: str | None = None,
) -> None:
    """Construit les clients/orchestrateur avec les reglages GUI actuels et
    lance (ou reprend) l'analyse.

    resume_data : etat de session charge — les etapes validees sont conservees
    en l'etat et la suite s'execute sur le modele choisi au moment de la
    reprise (changement de modele entre etapes)."""
    global analysis_running
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

    # Etiquettes de modele par agent (traçabilite + statistiques/couts)
    cp, lp = _cloud_profile(), _local_profile()
    if mode == "cloud":
        eng_label = sec_label = f"cloud:{cp.model}"
    elif mode == "local":
        eng_label = sec_label = f"local:{lp.model}"
    else:
        eng_label, sec_label = f"cloud:{cp.model}", f"local:{lp.model}"
    model_labels = {
        "engineer": eng_label, "quality": eng_label,
        "client": eng_label, "secretary": sec_label,
    }

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
        session_dir=session_dir,
        model_labels=model_labels,
    )
    orchestrator.state.analysis_state.project_name = project

    if resume_data:
        try:
            orchestrator.state = OrchestratorState.from_dict(resume_data)
        except ValueError as exc:
            ui.notify(f"Session incompatible : {exc}", type="negative")
            return
        # Repeupler les cartes des etapes deja connues de la session
        for step in WORKFLOW_STEPS:
            card = step_cards.get(step["id"])
            if not card:
                continue
            out = orchestrator.state.outputs.get(step["id"])
            if out:
                card["prod_md"].set_content(out)
                card["production_text"] = out
                card["review_text"] = orchestrator.state.reviews.get(step["id"]) or ""
                v = orchestrator.state.human_validations.get(step["id"])
                done = (v and v != "quit") or (step["id"] == "livraison")
                if out and done:
                    card["badge"].set_text("Validee (session)")
                    card["badge"].props("color=green")
    else:
        # Remise a zero visuelle des cartes
        for card in step_cards.values():
            card["badge"].set_text("En attente")
            card["badge"].props("color=grey")
            card["prod_md"].set_content("*Production non generee*")
            card["production_text"] = ""
            card["review_text"] = ""
            card["phase_stats"] = {}
            if card.get("stats_md"):
                card["stats_md"].set_content("")

    analysis_running = True
    run_btn.disable()
    run_progress.set_visibility(True)
    if resume_data:
        log.push(f"=== Reprise de session : {project} (mode {mode}) ===")
    else:
        log.push(f"=== Analyse demarree : {project} (mode {mode}) ===")

    try:
        outputs = await orchestrator.run_full_analysis(
            initial_context=context, resume_state=resume_data
        )
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
            log.push("  - ou reprendre la session plus tard (panneau Sessions)")
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
        _refresh_sessions()


async def resume_session(sdir: str) -> None:
    """Reprend une session sauvegardee : etapes validees conservees, suite
    sur le modele actuellement configure dans la GUI."""
    global analysis_running
    if analysis_running:
        ui.notify("Une analyse est deja en cours.", type="warning")
        return
    try:
        data = load_session(sdir)
    except Exception as exc:
        ui.notify(f"Session illisible : {exc}", type="negative")
        return
    mode = mode_radio.value
    project = (project_input.value or "").strip() or "Sans_Nom"
    context = (context_input.value or "").strip()
    _apply_env_settings()
    await _launch_analysis(mode, project, context, resume_data=data, session_dir=sdir)


def _delete_session(sdir: str) -> None:
    try:
        delete_session(sdir)
        log.push(f"[session] supprimee : {Path(sdir).name}")
        _refresh_sessions()
    except Exception as exc:
        ui.notify(f"Suppression impossible : {exc}", type="negative")


def _refresh_sessions(*_a) -> None:
    """Rafraichit le panneau des sessions sauvegardees."""
    if sessions_container is None:
        return
    sessions_container.clear()
    with sessions_container:
        try:
            rows = list_sessions()
        except Exception as exc:
            ui.label(f"Lecture des sessions impossible : {exc}").classes(
                "text-caption text-grey"
            )
            return
        if not rows:
            ui.label("Aucune session sauvegardee.").classes(
                "text-caption text-grey"
            )
            return
        for s in rows:
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                with ui.column().classes("gap-0 grow"):
                    ui.label(
                        f"{s['name']} — {s['steps_validated']} étape(s) validée(s)"
                    ).classes("text-body2")
                    models = ", ".join(s["models"]) or "—"
                    ui.label(
                        f"{models} · {s['gen_time_s']:.0f}s gen · "
                        f"{s['tokens_out']:,} tok out".replace(",", " ")
                    ).classes("text-caption text-grey")
                with ui.row():
                    ui.button(
                        "Reprendre", icon="play_arrow",
                        on_click=lambda sd=s["dir"]: resume_session(sd),
                    ).props("flat dense")
                    ui.button(
                        icon="delete",
                        on_click=lambda sd=s["dir"]: _delete_session(sd),
                    ).props("flat dense color=negative")


# ---------------------------------------------------------------------------
# Construction de la page
# ---------------------------------------------------------------------------

def build_page() -> None:
    global log, mode_radio, local_base_url, local_model, local_api_key, local_temp, local_max_tokens
    global cloud_model, cloud_base_url, cloud_api_key, cloud_temp, cloud_max_tokens
    global function_calling_switch, timeout_input, retries_input, reasoning_select
    global web_search_switch, web_backend, searxng_url_input, tavily_key_input
    global step_context_limit_input, context_auto_switch
    global checkpoint_dialog, dlg_title, dlg_prod_md, dlg_review_md, dlg_status, dlg_stats
    global dlg_feedback, btn_continue, btn_quit, btn_feedback
    global dlg_card, dlg_prod_scroll, dlg_review_scroll, btn_maximize, dlg_points_container
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
                        "Retries", value=float(os.getenv("LLM_MAX_RETRIES", "1")),
                        format="%.0f", min=0,
                    ).props("label-always")
                step_context_limit_input = ui.number(
                    "Limite de contexte par etape precedente (caracteres)",
                    value=float(os.getenv("STEP_CONTEXT_LIMIT", "40000")),
                    format="%.0f", min=1000,
                ).props("label-always").classes("w-full")
                context_auto_switch = ui.switch(
                    "Contexte automatique (detecte via LM Studio)", value=True
                )

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
            step_cards.clear()
            decided_points_by_step.clear()
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
                    stats_md = ui.markdown("").classes("text-caption")
                    step_cards[step["id"]] = {
                        "name": step["name"],
                        "badge": badge,
                        "prod_md": prod_md,
                        "production_text": "",
                        "review_text": "",
                        "stats_md": stats_md,
                        "phase_stats": {},
                        "totals": {},
                    }

            log = ui.log(max_lines=500).classes("w-full").style("height: 200px")
            log.push("Astuce : cliquez sur le bouton refresh du modele local pour lister")
            log.push("les modeles charges sur le serveur LM Studio.")

            # ------------------------- Panneau Sessions -------------------------
            with ui.row().classes("w-full items-center"):
                ui.label("Sessions sauvegardees").classes("text-subtitle1")
                ui.button(
                    "Rafraichir", icon="refresh", on_click=_refresh_sessions
                ).props("flat dense")
            sessions_container = ui.column().classes("w-full")
            _refresh_sessions()

    # Popup de relecture / decision des checkpoints
    with ui.dialog() as checkpoint_dialog:
        with ui.card() as dlg_card:
            with ui.row().classes("w-full items-center justify-between"):
                dlg_title = ui.label("Point de controle").classes("text-h6")
                btn_maximize = ui.button(
                    icon="fullscreen", on_click=toggle_maximize
                ).props("flat dense")
            with ui.row().classes("w-full items-stretch"):
                with ui.column().classes("col grow"):
                    ui.label("Production").classes("text-subtitle2 text-grey")
                    with ui.scroll_area() as dlg_prod_scroll:
                        dlg_prod_md = ui.markdown("")
                with ui.column().classes("col grow"):
                    ui.label("Relecture").classes("text-subtitle2 text-grey")
                    with ui.scroll_area() as dlg_review_scroll:
                        dlg_review_md = ui.markdown("")
            dlg_points_container = ui.column().classes("w-full")
            dlg_stats = ui.label("").classes("text-caption text-grey")
            dlg_status = ui.label("").classes("text-caption text-grey")
            dlg_feedback = ui.textarea(
                "Feedback global (libre, optionnel — structuré si besoin)"
            ).classes("w-full")
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
    _apply_dialog_size()

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
