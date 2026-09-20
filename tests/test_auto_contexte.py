"""Tests offline de la detection multi-moteurs (LOCAL_CONTEXT_TOKENS + sondes).

python tests/test_auto_contexte.py   (~2 s)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.orchestrator import (
    _AUTO_CONTEXT_CACHE,
    _injection_limit,
    _parse_kobold,
    _parse_llama_cpp,
    _parse_lm_studio,
)

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


def t_sonde_lm_studio():
    raw = '''{"object": "list", "data": [
        {"id": "m1", "state": "notloaded", "loaded_context_length": 4096},
        {"id": "qwen3.8-27b", "state": "loaded", "loaded_context_length": 153856}
    ]}'''
    assert _parse_lm_studio(raw) == 153856
    assert _parse_lm_studio('{"data": []}') == 0


def t_sonde_llama_cpp():
    # builds recents : n_ctx dans default_generation_settings
    assert _parse_llama_cpp('{"default_generation_settings": {"n_ctx": 131072}}') == 131072
    # builds plus anciens : n_ctx a la racine
    assert _parse_llama_cpp('{"n_ctx": 65536, "total_slots": 1}') == 65536
    assert _parse_llama_cpp("{}") == 0


def t_sonde_kobold():
    assert _parse_kobold("131072") == 131072
    assert _parse_kobold("  32768\n") == 32768


def t_limite_injection():
    lim = _injection_limit(153856)   # contexte type utilisateur (IQ3_S MTP)
    assert 80000 <= lim <= 90000, lim
    assert _injection_limit(0) == 1000       # clamp minimal
    assert _injection_limit(4096) == 1000    # contexte trop petit -> clamp


def t_declaration_manuelle_prioritaire():
    """LOCAL_CONTEXT_TOKENS fourni => pas de reseau, limite calibree dessus."""
    os.environ["LOCAL_CONTEXT_TOKENS"] = "131072"
    os.environ["CONTEXT_AUTO"] = "true"
    try:
        from agents.orchestrator import _auto_context_limit
        assert _auto_context_limit() == _injection_limit(131072)
        assert _AUTO_CONTEXT_CACHE["engine"] == "declaration manuelle"
    finally:
        os.environ.pop("LOCAL_CONTEXT_TOKENS")


def main():
    os.environ.pop("LIVRAISON_FORCE", None)
    os.environ["CONTEXT_AUTO"] = "false"
    _run("sonde LM Studio", t_sonde_lm_studio)
    _run("sonde llama.cpp /props", t_sonde_llama_cpp)
    _run("sonde koboldcpp", t_sonde_kobold)
    _run("limite d'injection", t_limite_injection)
    _run("LOCAL_CONTEXT_TOKENS prioritaire",
         t_local_context_tokens_prioritaire)

    if not FAILURES:
        print("\nTESTS AUTO-CONTEXTE : TOUT PASSE")
    else:
        print(f"\n{len(FAILURES)} echec(s)")
        sys.exit(1)


def t_local_context_tokens_prioritaire():
    os.environ["LOCAL_CONTEXT_TOKENS"] = "131072"
    os.environ["CONTEXT_AUTO"] = "true"
    try:
        from agents.orchestrator import _auto_context_limit
        assert _auto_context_limit() == _injection_limit(131072)
        assert _AUTO_CONTEXT_CACHE["engine"] == "declaration manuelle"
    finally:
        os.environ.pop("LOCAL_CONTEXT_TOKENS")
        os.environ["CONTEXT_AUTO"] = "false"


if __name__ == "__main__":
    main()
