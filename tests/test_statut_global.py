"""Test offline : l'avis global du relecteur ('Statut global…') n'est pas un
point qualifiable — il s'affiche comme contexte (constat v1.3.40).

python tests/test_statut_global.py   (~2 s)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui import _extract_statut, _parse_review_points

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


REVIEW = """## Statut global
À corriger — le cadrage reste trop générique sur les phases de vie et ne cite
pas les interfaces SCADA.

### [P2] Phases de vie incomplètes
- Localisation : Etape 1, section 3
- Extrait : « phases de vie : exploitation, maintenance »
- Verdict : IMPORTANT
- Justification : la phase de dépose est absente
- Correction proposée : ajouter la dépose

### [P5] Statut global à revoir ??? un point piege
- Localisation : nulle part
- Verdict : MINEUR

- Statut global : à corriger — blablabla (le relecteur l'écrit en puce aussi)
"""


def t_statut_pas_un_point():
    pts = _parse_review_points(REVIEW)
    # P2 reste un point ; le pseudo-point piege (titre prefixe 'Statut global')
    # et la puce de verdict sont ECARTES
    assert len(pts) == 1, [(p.get("id"), p["titre"]) for p in pts]
    assert pts[0]["id"] == "P2", pts[0]
    assert pts[0]["titre"] == "Phases de vie incomplètes", pts[0]["titre"]


def t_avis_extrait():
    avis = _extract_statut(REVIEW)
    assert avis.lower().startswith("à corriger — le cadrage"), avis
    assert "phases de vie" in avis, avis


def t_statut_numerote():
    """Structure numerotee : '1. **Statut global**' + contenu sur la ligne
    suivante (constat v1.3.42 — l'avis s'arretait a '1. Statut global')."""
    review = """1. **Statut global**
À corriger — le cadrage ne cite pas les phases de vie.
2. **Points**
### [P1] Phases de vie incomplètes
- Verdict : IMPORTANT"""
    avis = _extract_statut(review)
    assert avis.lower().startswith("à corriger"), avis
    assert "phases de vie" in avis, avis
    assert "statut global" not in avis.lower(), avis
    # le marqueur '2. Points' ne doit pas etre ramasse
    assert "points" not in avis.lower(), avis


def t_pas_de_statut():
    assert _extract_statut("### [P1] Un point\n- Verdict : MINEUR") == ""


def main():
    _run("verdict global : pas un point qualifiable", t_statut_pas_un_point)
    _run("avis du relecteur extrait comme contexte", t_avis_extrait)
    _run("absence d'avis -> chaine vide", t_pas_de_statut)
    _run("statut numerote (1. **Statut global**) -> contenu ramasse", t_statut_numerote)
    if not FAILURES:
        print("\nTEST STATUT GLOBAL : TOUT PASSE")
    else:
        print(f"\n{len(FAILURES)} echec(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
