"""Tests offline de l'export Excel (parseur markdown + classeur openpyxl).

python tests/test_export_xlsx.py   (~2 s)
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from export_utils import export_xlsx, markdown_tables, _sheet_name

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


MD_COMPLEX = """# Livrable

## Bloc 1 — Résumé exécutif

Texte d'introduction (pas un tableau).

| **Fonction** | **Rôle** | Décision |
| --- | --- | --- |
| Alimentation 25kV | A | OK |
| Maintenance | C | KO |

## Bloc 3 — Analyse préliminaire de risque

### Tableau principal

| ID | Gravité | Statut | Notes |
| :--- | :---: | ---: | --- |
| RISK-001 | Critique | KO | cellule avec<br>retour a la ligne |
| RISK-002 | moyen | OK | ligne suivante |

## Bloc 5 — Points ouverts

| Point | Décision humaine | Statut |
| --- | --- | --- |
| Phase de transport non couverte | **KO** — [P8] A CORRIGER (V2 Filtrage) | a traiter |
| ligne 74 coupee en pleine cellule | KO | incomplete |

| Pas un tableau | juste une ligne |
"""


def t_parse_simple():
    tables = markdown_tables("| A | B |\n| --- | --- |\n| 1 | 2 |")
    assert len(tables) == 1, tables
    title, cells = tables[0]
    assert cells[0] == ["A", "B"] and cells[1] == ["1", "2"], cells


def t_parse_complex():
    tables = markdown_tables(MD_COMPLEX)
    # 3 vrais tableaux (le bloc sans separateur est ignore)
    assert len(tables) == 3, [t[0] for t in tables]
    # titres = en-tete le plus proche
    assert any(t[0] == "Tableau principal" for t in tables), [t[0] for t in tables]
    # gras retire des en-tetes
    t3 = next(t for t in tables if "Gravité" in " ".join(t[1][0]))
    assert "**" not in t3[1][0][0] and t3[1][0][0] == "ID", t3[1][0]
    # <br> converti en retour a la ligne dans la cellule
    cell = t3[1][1][3]
    assert "retour a la ligne" in cell and "\n" in cell, cell
    # alignements '|:---|' acceptes
    assert t3[1][0][-1] == "Notes"


def t_parse_tableau_malforme():
    # sans ligne de separateur : pas un tableau -> ignore
    assert markdown_tables("| a | b |\n| pas un separateur |") == []


def t_sheet_name():
    used = set()
    n = _sheet_name("Bloc 3 — Analyse préliminaire de risque (18 colonnes) [*]", used)
    assert len(n) <= 31 and "/" not in n and "*" not in n, n
    n2 = _sheet_name(n, used)
    assert n2 != n, "doublon de nom d'onglet"


def t_export_xlsx_lifecycle():
    tmp = Path(tempfile.mkdtemp(prefix="xlsx_"))
    path = export_xlsx(
        [("Test · Tableau", [["ID", "Statut"], ["RISK-001", "KO"], ["RISK-002", "OK"]])],
        tmp / "out.xlsx",
    )
    assert path.exists()
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb["Test · Tableau"]
    assert ws["A1"].value == "ID" and ws["B2"].value == "KO"
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref.startswith("A1:")


def main():
    _run("parseur : table simple", t_parse_simple)
    _run("parseur : gras, <br>, alignements, multi-tableaux", t_parse_complex)
    _run("parseur : bloc sans separateur ignore", t_parse_tableau_malforme)
    _run("nom d'onglet : sanitisation + doublons", t_sheet_name)
    _run("cycle complet export/lire", t_export_xlsx_lifecycle)
    if not FAILURES:
        print("\nTESTS EXPORT EXCEL : TOUT PASSE")
    else:
        print(f"\n{len(FAILURES)} echec(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
