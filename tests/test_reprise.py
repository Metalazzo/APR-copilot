"""Harnais de test du state-machine de reprise (scenarios critiques).

Execution : python tests/test_reprise.py   (~5 s, aucun appel LLM)
Chaque scenario imprime OK/KO ; le script sort avec le code 1 au moindre
echec. A re-executer apres TOUTE modification de l'orchestrateur.

Scenarios couverts (issues de la revue de code v1.3.25) :
  S1  CONTINUER etape 1 -> crash -> reprise : etape 1 sautee, suite a l'etape 2
  S2  "A corriger" etape 1 -> crash pendant la re-generation -> reprise :
      l'etape est RE-GENEREES avec le feedback (regression du bug C1)
  S3  QUITTER etape 2 -> reprise : validées conservées, etape 2 refaite
  S4  reprise d'une session complete sans force : rien ne re-tourne, livrable
      conservé (regression du bug I2)
  S5  LIVRAISON_FORCE=true : blocs deja generes reutilises, manquants only
  S6  LIVRAISON_FORCE=all : les 5 blocs re-generes
  S7  compat ancien format (derivation state.validated)
  S8  livraison partielle : crash apres le bloc 2 -> reprise FORCE=true :
      blocs 1-2 reutilises, 3-5 re-generés
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.orchestrator as orch_mod
from agents.orchestrator import OrchestratorState, RiskAnalysisOrchestrator, WORKFLOW_STEPS

orch_mod.retrieve = lambda q, top_k=8: []
orch_mod.format_retrieved_context = lambda c: ""
os.environ["CONTEXT_AUTO"] = "false"
os.environ.pop("LIVRAISON_FORCE", None)


class FakeTeam:
    """Secrétaire : renvoie un texte de bloc de 120 car. ; autres agents : court."""

    def __init__(self, participants, max_turns):
        self.p = participants

    async def run(self, task=None, **kw):
        a = self.p[0]
        await asyncio.sleep(0.002)
        content = (
            f"BLOC-{a.name[:3]}-" + "x" * 120 if a.name == "Secretaire" else f"PROD-{a.name}"
        )
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        return SimpleNamespace(messages=[
            SimpleNamespace(content="task-echo", models_usage=None),
            SimpleNamespace(content=content, models_usage=usage),
        ])


orch_mod.RoundRobinGroupChat = FakeTeam


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


def _scripted(o, answers):
    it = iter(answers)

    async def cb(*a, **k):
        return next(it)
    o.human_callback = cb
    return o


async def _run(o, answers, crash_engineer_after=None, crash_secretary_after=None):
    """Crash simulation : crash_engineer_after = crash au n-ieme appel
    ingenieur ; crash_secretary_after = crash au n-ieme APPEL SECRETAIRE
    (chaque bloc de livraison = un appel)."""
    calls = {"engineer": 0, "secretary": 0}
    orig_eng = o._run_engineer_step

    async def eng(step, previous_production="", human_feedback="", iteration=0):
        calls["engineer"] += 1
        if crash_engineer_after and calls["engineer"] > crash_engineer_after:
            raise RuntimeError("CRASH SIMULE (engineer call %d)" % calls["engineer"])
        return await orig_eng(step, previous_production=previous_production,
                              human_feedback=human_feedback, iteration=iteration)

    if crash_secretary_after:
        orig_ask = o._ask_agent

        async def ask(agent, task, *, stat_step="", stat_agent="", stat_bloc="",
                      stat_iteration=0):
            if getattr(agent, "name", "") == "Secretaire":
                calls["secretary"] += 1
                if calls["secretary"] > crash_secretary_after:
                    raise RuntimeError(
                        "CRASH SIMULE (secretary call %d)" % calls["secretary"]
                    )
            return await orig_ask(agent, task, stat_step=stat_step,
                                  stat_agent=stat_agent, stat_bloc=stat_bloc,
                                  stat_iteration=stat_iteration)
        o._ask_agent = ask

    o._run_engineer_step = eng
    _scripted(o, answers)
    try:
        outs = await o.run_full_analysis(initial_context="desc")
        return outs, calls, None
    except RuntimeError as exc:
        return {}, calls, str(exc)


def _resume(o, answers, **kw):
    return asyncio.run(_run(o, answers, **kw))


SCENARIOS = []


def scenario(name):
    def deco(fn):
        SCENARIOS.append((name, fn))
        return fn
    return deco


@scenario("S1 CONTINUER etape 1 -> crash -> reprise : etape 1 sautee")
def s1(tmp):
    o1 = _make()
    outs, calls, err = asyncio.run(_run(o1, ["CONTINUER"], crash_engineer_after=1))
    assert err is not None and "CRASH SIMULE" in err, "le crash simule aurait du se produire"
    assert o1.state.validated.get("cadrage") is True
    data = o1.state.to_dict()
    o2 = _resume(_make(), data)
    assert "cadrage" not in o2["_eng_steps"], "l'etape validee doit etre sautee"
    assert set(o2["_outs"]) == {s["id"] for s in WORKFLOW_STEPS}


@scenario("S2 'A corriger' -> crash pendant la re-generation -> l'etape est RE-GENEREES (C1)")
def s2(tmp):
    o1 = _make()
    outs, calls, err = asyncio.run(_run(o1, [
        "POINTS DE CONTROLE HUMAIN (version V1) :\n"
        "- [A CORRIGER] [P1] Modele trop generique — ajouter les phases de vie",
    ], crash_engineer_after=1))
    assert err is not None and "CRASH SIMULE" in err
    assert o1.state.validated.get("cadrage") is False, "feedback en attente = NON valide"
    data = o1.state.to_dict()
    o2 = _resume(_make(), data)
    assert "cadrage" in o2["_eng_steps"], "l'etape avec feedback non integre doit etre RE-GENEREES"


@scenario("S3 QUITTER etape 2 -> reprise : etape 2 re-generees")
def s3(tmp):
    o1 = _scripted(_make(), ["CONTINUER", "QUITTER"])
    asyncio.run(o1.run_full_analysis(initial_context="desc"))
    assert o1.state.validated.get("cadrage") is True
    assert o1.state.validated.get("filtrage") is False
    data = o1.state.to_dict()
    o2 = _resume(_make(), data)
    assert "cadrage" not in o2["_eng_steps"], "l'etape validee doit etre sautee"
    assert "filtrage" in o2["_eng_steps"], "l'etape quittee doit etre re-generee"


@scenario("S4 reprise session complete sans force : rien ne re-tourne, livrable conserve")
def s4(tmp):
    o1 = _scripted(_make(), ["CONTINUER"] * 4)
    outs1 = asyncio.run(o1.run_full_analysis(initial_context="desc"))
    data = o1.state.to_dict()
    o2 = _resume(_make(), data)
    assert not o2["_eng_steps"] and not o2["_sec_calls"], "rien ne doit re-tourner"
    assert o2["_outs"].get("livraison"), "le livrable de la session doit rester dans all_outputs"


@scenario("S5 LIVRAISON_FORCE=true : blocs stockes reutilises, manquants generes")
def s5(tmp):
    o1 = _scripted(_make(), ["CONTINUER"] * 4)
    asyncio.run(o1.run_full_analysis(initial_context="desc"))
    data = o1.state.to_dict()
    data["outputs"]["livraison"] = ""
    data["delivery_blocs"] = {"bloc1": "BLOC-1-STOCKE", "bloc2": "BLOC-2-STOCKE"}
    os.environ["LIVRAISON_FORCE"] = "true"
    try:
        o2 = _resume(_make(), data)
        # blocs 1-2 reutilises (stockes) -> seuls 3-5 sont generes (3 appels)
        assert o2["_sec_asks"] == 3, o2["_sec_asks"]
        liv = o2["_outs"]["livraison"]
        assert "BLOC-1-STOCKE" in liv and "BLOC-2-STOCKE" in liv
        assert set(o2["_blocs_generated"]) >= {"bloc3", "bloc4", "bloc5"}
    finally:
        os.environ.pop("LIVRAISON_FORCE")


@scenario("S6 LIVRAISON_FORCE=all : les 5 blocs re-generés")
def s6(tmp):
    o1 = _scripted(_make(), ["CONTINUER"] * 4)
    asyncio.run(o1.run_full_analysis(initial_context="desc"))
    data = o1.state.to_dict()
    os.environ["LIVRAISON_FORCE"] = "all"
    try:
        o2 = _resume(_make(), data)
        assert o2["_sec_asks"] == 5, o2["_sec_asks"]   # les 5 blocs re-generés
    finally:
        os.environ.pop("LIVRAISON_FORCE")


@scenario("S7 compat ancien format : derivation state.validated")
def s7(tmp):
    st = OrchestratorState.from_dict({"version": 1, "human_validations": {
        "cadrage": "validated",
        "filtrage": "SANS CORRECTION (version V2) — points qualifies",
        "scenarios": "## FEEDBACK HUMAIN A INTEGRER (par ordre chronologique)\n1. x",
        "barrieres": "POINTS DE CONTROLE HUMAIN (version V1) :\n- [A CORRIGER] x",
    }})
    assert st.validated == {"cadrage": True, "filtrage": True,
                            "scenarios": True, "barrieres": False}


@scenario("S8 livraison : crash apres le bloc 2 -> reprise FORCE reutilise 1-2")
def s8(tmp):
    o1 = _scripted(_make(), ["CONTINUER"] * 4)
    outs, _, err = asyncio.run(_run(o1, ["CONTINUER"] * 4, crash_secretary_after=2))
    assert err is not None and "CRASH SIMULE" in err
    assert set(o1.state.delivery_blocs) == {"bloc1", "bloc2"}, o1.state.delivery_blocs
    data = o1.state.to_dict()
    os.environ["LIVRAISON_FORCE"] = "true"
    try:
        o2 = _resume(_make(), data)
        # blocs 1-2 stockes (reutilises) -> seuls 3-5 generes (3 appels)
        assert o2["_sec_asks"] == 3, o2["_sec_asks"]
        liv = o2["_outs"]["livraison"]
        assert "Bloc 1" in liv and "Bloc 5" in liv
    finally:
        os.environ.pop("LIVRAISON_FORCE")


def _resume(base_orch, data):
    """Reprend une session dans un nouveau process, en espionnant les appels."""
    os.environ["CONTEXT_AUTO"] = "false"
    o = _make()
    eng_steps, sec_calls, blocs_generated = [], [], []
    orig_eng, orig_sec = o._run_engineer_step, o._run_secretary_step
    orig_ask = o._ask_agent
    sec_asks = {"n": 0}

    async def eng(step, previous_production="", human_feedback="", iteration=0):
        eng_steps.append(step["id"])
        return await orig_eng(step, previous_production=previous_production,
                              human_feedback=human_feedback, iteration=iteration)

    async def sec(step, previous_production="", human_feedback="", iteration=0):
        sec_calls.append(step["id"])
        # capture AVANT la generation : les blocs re-genere = valeur changee
        # ou ajoutee — pas des reutilises
        before = dict(o.state.delivery_blocs)
        out = await orig_sec(step, previous_production=previous_production,
                             human_feedback=human_feedback, iteration=iteration)
        changed = sorted(
            k for k, v in o.state.delivery_blocs.items() if before.get(k) != v
        )
        blocs_generated.extend(changed)
        return out

    async def ask(agent, task, *, stat_step="", stat_agent="", stat_bloc="",
                  stat_iteration=0):
        if getattr(agent, "name", "") == "Secretaire":
            sec_asks["n"] += 1   # chaque bloc genere = 1 appel secretaire
        return await orig_ask(agent, task, stat_step=stat_step,
                              stat_agent=stat_agent, stat_bloc=stat_bloc,
                              stat_iteration=stat_iteration)

    o._run_engineer_step = eng
    o._run_secretary_step = sec
    o._ask_agent = ask

    async def cb(*a, **k):
        return "CONTINUER"
    o.human_callback = cb
    outs = asyncio.run(o.run_full_analysis(initial_context="", resume_state=data))
    return {"_outs": outs, "_eng_steps": eng_steps, "_sec_calls": sec_calls,
            "_blocs_generated": blocs_generated, "_sec_asks": sec_asks["n"]}


def main():
    tmp = tempfile.mkdtemp(prefix="reprise_")
    import session_store
    saved_root = session_store.SESSIONS_ROOT
    session_store.SESSIONS_ROOT = Path(tmp)

    failures = []
    for name, fn in SCENARIOS:
        try:
            fn(tmp)
            print(f"[OK]    {name}")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"[FAIL]  {name}")
            failures.append((name, exc))

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    session_store.SESSIONS_ROOT = saved_root
    if failures:
        print(f"\n{len(failures)} scenario(s) en echec")
        sys.exit(1)
    print("\nHARNAIS DE REPRISE : TOUT PASSE")


if __name__ == "__main__":
    main()
