"""Tests offline de la verification d'ancrage des points de relecture.

python tests/test_ancrage.py   (~2 s)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui import _anchor_flags

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


PROD = ("Le filtrage couvre les agressions environnementales (temperature, "
        "vibrations, poussiere), les agressions electriques et CEM, les "
        "menaces emises par le systeme (degazage, echauffement, fuite) et "
        "les barrières de prevention associees.")


def t_extrait_verbatim_conforme():
    pt = {"localisation": "filtrage, item 12", "extrait": "menaces emises par le systeme (degazage, echauffement)"}
    no_anchor, missing = _anchor_flags(pt, PROD)
    assert (no_anchor, missing) == (False, False), (no_anchor, missing)


def t_extrait_reformule_conforme():
    """Une simple variation de formulation (mots differents, ordre inverse)
    ne doit PAS declencher le drapeau (>= 85 % des mots presents)."""
    pt = {"localisation": "filtrage, item 12",
          "extrait": "echauffement et degazage figurent parmi les menaces emises du systeme"}
    _, missing = _anchor_flags(pt, PROD)
    assert missing is False, missing


def t_extrait_invente_non_conforme():
    pt = {"localisation": "filtrage", "extrait": "l'agent propose une refonte complete de la matrice SIL"}
    _, missing = _anchor_flags(pt, PROD)
    assert missing is True, missing


def t_sans_ancrage():
    pt = {"localisation": "", "extrait": ""}
    no_anchor, missing = _anchor_flags(pt, PROD)
    assert no_anchor and not missing


def t_mot_a_mot_tolerance():
    """~15 % des mots changes : pas de drapeau ; ~50 % changes : drapeau."""
    base = "menaces emises par le systeme degazage echauffement fuite prevention"
    pt = {"localisation": "x", "extrait": base}
    _, missing = _anchor_flags(pt, PROD)
    assert missing is False
    pt2 = {"localisation": "x", "extrait": "refonte complete du mode de calcule sil asil dal registre"}
    _, missing2 = _anchor_flags(pt2, PROD)
    assert missing2 is True


def main():
    _run("extrait verbatim → conforme", t_extrait_verbatim_conforme)
    _run("extrait reformulé (mots présents) → conforme", t_extrait_reformule_conforme)
    _run("extrait inventé (mots absents) → non conforme", t_extrait_invente_non_conforme)
    _run("sans ancrage → drapeau", t_sans_ancrage)
    _run("tolérance mot à mot", t_mot_a_mot_tolerance)
    if not FAILURES:
        print("\nTEST ANCRAGE : TOUT PASSE")
    else:
        print(f"\n{len(FAILURES)} echec(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
