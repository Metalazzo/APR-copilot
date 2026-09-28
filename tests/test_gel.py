"""Tests offline : regle de correction ciblee + garde-fou iterations (v1.3.45).

python tests/test_gel.py   (~3 s)
"""

import asyncio
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.orchestrator as orch_mod
from agents.orchestrator import RiskAnalysisOrchestrator, WORKFLOW_STEPS

orch_mod.retrieve = lambda q, top_k=8: []
orch_mod.format_retrieved_context = lambda c: ""
os.environ["CONTEXT_AUTO"] = "false"
os.environ.pop("LIVRAISON_FORCE", None)


class FakeTeam:
    def __init__(self, participants, max_turns):
        self.p = participants

    async def run(self, task=None, **kw):
        a = self.p[0]
        await asyncio.sleep(0.002)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        return SimpleNamespace(messages=[
            SimpleNamespace(content="task-echo", models_usage=None),
            SimpleNamespace(content=f"PROD-{a.name}", models_usage=usage),
        ])


orch_mod.RoundRobinGroupChat = FakeTeam

FAILURES = []


def _run(name, fn):
    try:
        fn()
        print(f"[OK]   {name}")
    except Exception:
        import traceback
        traceback.print_exc()
        print(f"[FAIL] {name}")
        FAILURES.append(name)


class FA:
    def __init__(self, n):
        self.name = n


def _make():
    return RiskAnalysisOrchestrator(
        engineer=FA("Ingenieur_Technique_SDF"), quality=FA("Animateur_Qualite"),
        client=FA("Representant_Client"), secretary=FA("Secretaire"),
        model_labels={"engineer": "cloud:x", "quality": "cloud:x",
                      "client": "cloud:x", "secretary": "cloud:x"},
    )


def t_gel_dans_tache_regen():
    """La re-generation apres feedback porte la regle de correction ciblee,
    la production precedente ET le feedback."""
    o = _make()
    captured = {}
    orig = o._ask_agent

    async def spy(agent, task, **kw):
        captured["task"] = task
        return await orig(agent, task, **kw)
    o._ask_agent = spy

    feedback = ("POINTS DE CONTROLE HUMAIN (version V1) :\n"
                "- [A CORRIGER] [P1] Modele trop generique — ajouter les phases de vie")
    asyncio.run(o._run_engineer_step(
        {"id": "cadrage", "task": "Effectue le cadrage..."},
        previous_production="PRODUCTION-PRECEDENTE",
        human_feedback=feedback,
        iteration=1,
    ))
    task = captured["task"]
    assert "REGLE DE CORRECTION CIBLEE" in task, task[:300]
    assert "STRICTEMENT IDENTIQUE" in task, task[:300]
    assert "PRODUCTION-PRECEDENTE" in task, task[:300]   # version precedente fournie
    assert "A CORRIGER] [P1] Modele trop generique" in task, task[:300]  # feedback
    assert "iteration 1" in task, task[:300]
    print("[OK]   regle de gel dans la tache de re-generation")


def t_pas_de_gel_sans_feedback():
    """Production initiale (pas de feedback) : PAS de regle de gel."""
    o = _make()
    captured = {}
    orig = o._ask_agent

    async def spy(agent, task, **kw):
        captured["task"] = task
        return await orig(agent, task, **kw)
    o._ask_agent = spy
    asyncio.run(o._run_engineer_step({"id": "cadrage", "task": "TACHE"}))
    assert "REGLE DE CORRECTION CIBLEE" not in captured["task"]
    print("[OK]   premiere production : pas de regle de gel")


def t_iteration_limit_emise(tmp):
    """MAX_ITERATIONS_ETAPE=1 : apres le 1er feedback, le checkpoint suivant
    emet iteration_limit (iteration compte les tours de FEEDBACK)."""
    o = _make()
    events = []
    orig = o._emit

    async def spy(e):
        events.append(e)
        return await orig(e)
    o._emit = spy
    os.environ["MAX_ITERATIONS_ETAPE"] = "1"

    feedback = ("POINTS DE CONTROLE HUMAIN (version V1) :\n"
                "- [A CORRIGER] [P1] Modele trop generique — ajouter les phases de vie")
    seq = iter([feedback, feedback, feedback,
                "CONTINUER", "CONTINUER", "CONTINUER", "CONTINUER", "CONTINUER"])

    async def cb(*a, **k):
        return next(seq)
    o.human_callback = cb
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            asyncio.run(o.run_full_analysis(initial_context="desc"))
    finally:
        os.environ.pop("MAX_ITERATIONS_ETAPE")
    lims = [e for e in events if e["type"] == "iteration_limit"]
    assert lims, "aucun iteration_limit emis"
    assert lims[0]["iteration"] >= 1 and "limite" in lims[0]
    print("[OK]   iteration_limit emise au checkpoint (MAX_ITERATIONS_ETAPE)")


def main():
    _run("regle de gel dans la tache de re-generation", t_gel_dans_tache_regen)
    _run("premiere production sans gel", t_pas_de_gel_sans_feedback)
    _run("iteration_limit emise", lambda: t_iteration_limit_emise(None))
    if not FAILURES:
        print("\nTEST GEL + ITERATIONS : TOUT PASSE")
    else:
        print(f"\n{len(FAILURES)} echec(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
