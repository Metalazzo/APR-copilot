"""Test offline : le champ 'Pour l'humain' des points canoniques n'est plus un
point fantome (bug vu sur l'analyse deepseek-flash du 28/09).

python tests/test_pour_humain.py   (~2 s)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gui import _parse_review_points

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
Conforme avec réserves.

### [P2] Modèle de cotation non appliqué
- Localisation : Bloc 3, scénario RISK-004
- Extrait : « la gravité est estimée sans la matrice fournie »
- Verdict : IMPORTANT
- Pour l'humain : oui
- Justification : la matrice fournie n'a pas été utilisée
- Correction proposée : appliquer la matrice du projet

### [P3] Hypothèse de maintenance à confirmer
- Localisation : Bloc 1, section 3
- Extrait : « maintenance préventive annuelle »
- Verdict : MINEUR
- Pour l'humain : non
- Justification : la périodicité doit être confirmée
- Correction proposée : citer la périodicité exacte

## Hors blocs
- Pour l'humain : oui (ligne isolée hors bloc)
"""


def t_point_canonique_avec_pour_humain():
    pts = _parse_review_points(REVIEW)
    # 2 points canoniques — les lignes "Pour l'humain" ne sont PAS des points
    assert len(pts) == 2, [(p.get("id"), p["titre"]) for p in pts]
    p2 = next(p for p in pts if p["id"] == "P2")
    assert p2["pour_humain"] == "oui", p2
    p3 = next(p for p in pts if p["id"] == "P3")
    assert p3["pour_humain"] == "non", p3
    # aucun point fantome "Pour l'humain"
    assert not any("pour l" in p["titre"].lower() for p in pts), [p["titre"] for p in pts]


def t_ligne_isolee_hors_bloc_exclue():
    pts = _parse_review_points(REVIEW)
    assert not any("ligne isolée" in p["titre"] for p in pts), [p["titre"] for p in pts]


def main():
    _run("point canonique avec champ 'Pour l'humain' : pas de point fantome", t_point_canonique_avec_pour_humain)
    _run("ligne 'Pour l'humain' isolee hors bloc : exclue", t_ligne_isolee_hors_bloc_exclue)
    if not FAILURES:
        print("\nTEST POUR L'HUMAIN : TOUT PASSE")
    else:
        print(f"\n{len(FAILURES)} echec(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
