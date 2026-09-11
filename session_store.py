"""Persistance des sessions d'analyse (reprise apres interruption).

Chaque session est un dossier output/sessions/<horodatage>/ contenant
state.json : sorties, relectures, validations et qualifications humaines
par point, statistiques d'appels, modele producteur par etape. L'etat est
ecrit apres chaque evenement cle (production, relecture, decision de
checkpoint) : quitter = mise en pause reprenable, crash = perte max d'une
etape en cours.

Module sans dependance vers l'orchestrateur (pas d'import circulaire) :
la serialisation vit dans OrchestratorState.to_dict()/from_dict().
"""

import json
import shutil
import time
from pathlib import Path

from config import config as app_config

SESSIONS_ROOT = Path(app_config.output_dir) / "sessions"
STATE_FILE = "state.json"
SESSION_VERSION = 1


def new_session_dir() -> Path:
    """Cree (et retourne) un dossier de session horodate."""
    SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    d = SESSIONS_ROOT / stamp
    i = 0
    while d.exists():
        i += 1
        d = SESSIONS_ROOT / f"{stamp}-{i}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def save_session(session_dir: str | Path, state_dict: dict) -> Path:
    """Ecrit l'etat (ecriture atomique : tmp puis remplacement)."""
    p = Path(session_dir) / STATE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state_dict, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    tmp.replace(p)
    return p


def load_session(session_dir: str | Path) -> dict:
    p = Path(session_dir) / STATE_FILE
    if not p.exists():
        raise ValueError(f"Session introuvable : {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def delete_session(session_dir: str | Path) -> None:
    shutil.rmtree(session_dir, ignore_errors=False)


def list_sessions(limit: int = 30) -> list[dict]:
    """Sessions disponibles (plus recente d'abord) avec resume d'etat."""
    if not SESSIONS_ROOT.exists():
        return []
    out = []
    for d in sorted(SESSIONS_ROOT.iterdir(), reverse=True):
        p = d / STATE_FILE
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        validations = data.get("human_validations", {}) or {}
        n_valid = sum(1 for v in validations.values() if v and v != "quit")
        stats = data.get("call_stats", []) or []
        gen = sum((s or {}).get("elapsed_s", 0) or 0 for s in stats)
        tout = sum((s or {}).get("completion_tokens", 0) or 0 for s in stats)
        models = sorted({str(v) for v in (data.get("models_used") or {}).values()})
        out.append({
            "dir": str(d),
            "name": d.name,
            "steps_validated": n_valid,
            "models": models,
            "gen_time_s": round(gen, 1),
            "tokens_out": tout,
            "last_ts": max((s.get("ts", "") for s in stats if s), default=""),
        })
        if len(out) >= limit:
            break
    return out
