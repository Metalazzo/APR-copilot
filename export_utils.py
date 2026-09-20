"""Export Excel des tableaux d'analyse (v1.3.29 — 1 onglet PAR ETAPE).

Un classeur .xlsx avec UN onglet par etape (Cadrage, Filtrage, Scenarios,
Barrieres, Livraison) : les tableaux y sont empiles avec leur titre de
section, en-tete stylé, coloration des niveaux de risque/statut.

Le .md reste le document maitre (texte, justifications) — l'Excel est une vue
tabulaire produite en plus, sans modification du contenu.
"""

import re
from pathlib import Path

from config import config as app_config

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.styles import PatternFill
    from openpyxl.utils import get_column_letter
    OPENPYXL_OK = True
except Exception:  # pragma: no cover - openpyxl absent
    OPENPYXL_OK = False

_HEADER_FILL = "4472C4"
_TITLE_COLOR = "1F3864"
_SEV_FILL = {
    "critique": "C00000",
    "élevé": "ED7D31", "elevé": "ED7D31", "élevée": "ED7D31", "majeur": "ED7D31",
    "moyen": "FFC000", "modéré": "FFC000",
    "faible": "92D050", "mineur": "D9D9D9",
    "ko": "C00000", "ok": "92D050",
}

_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")


def _row_cells(line: str) -> list[str]:
    """Cellules d'une ligne de tableau markdown (tolere \\| echappe)."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    cells = re.split(r"(?<!\\)\|", s)
    out = []
    for c in cells:
        c = c.replace("\\|", "|")
        c = c.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        c = c.replace("**", "").replace("__", "")
        out.append(c.strip())
    return out


def _is_separator(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return bool(re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", s)) and "-" in s


def _sheet_name(title: str, used: set) -> str:
    name = re.sub(r"[\[\]:*?/\\]", " ", title)
    name = (re.sub(r"\s+", " ", name).strip() or "Feuille")[:31]
    base, k = name[:28], 2
    while name.lower() in {u.lower() for u in used}:
        name = f"{base}_{k}"[:31]
        k += 1
    used.add(name)
    return name


def markdown_tables(md: str) -> list[tuple[str, list[list[str]]]]:
    """Extrait les tableaux d'un markdown : [(titre, lignes avec en-tete)].

    Robustesse : en-tetes en gras, separateurs ':---', cellules <br>,
    lignes de continuation (repli du modele coupant une cellule longue)."""
    lines = (md or "").splitlines()
    tables: list[tuple[str, list[list[str]]]] = []
    current_title = ""
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        m = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if m:
            current_title = re.sub(r"[*_`]+", "", m.group(1)).strip()
        elif re.match(r"^\*\*.+\*\*:?\s*$", stripped):
            current_title = re.sub(r"[*_`]+", "", stripped).strip(": ")

        if stripped.startswith("|") and "|" in stripped[1:] and i + 1 < n \
                and _is_separator(lines[i + 1]):
            header = _row_cells(stripped)
            j = i + 2
            body: list[list[str]] = []
            while j < n:
                raw = lines[j]
                ls = raw.strip()
                if ls.startswith("|"):
                    body.append(_row_cells(ls))
                    j += 1
                    continue
                if ls == "":
                    # ligne vide : continuation si un tableau reprend apres
                    k = j
                    while k < n and not lines[k].strip():
                        k += 1
                    if k < n and lines[k].strip().startswith("|"):
                        j = k
                        continue
                    break
                if body:
                    # continuation de la derniere cellule (ligne sans pipe)
                    body[-1][-1] = f"{body[-1][-1]} {ls}".strip()
                    j += 1
                    continue
                break
            if header and body:
                tables.append((current_title or f"Tableau {len(tables) + 1}",
                               [header] + body))
            i = max(j, i + 1)
            current_title = ""
            continue
        i += 1
    return tables


def _cell(row, col, value, wrap=True, bold=False):
    c = row[col - 1] if col <= len(row) else row[0]
    c.value = str(value) if value is not None else ""
    c.alignment = Alignment(vertical="top", wrap_text=True)
    if bold:
        c.font = Font(bold=True, color="000000")
    return c


def _write_stacked_sheet(wb, name: str, blocks: list[tuple[str, list[list[str]]]],
                         used: set) -> None:
    """Un onglet par etape : les tableaux y sont EMPILES avec leur titre de
    section (ligne grise foncee), separes d'une ligne vide."""
    ws = wb.create_sheet(_sheet_name(name, used))
    row_idx = 1
    for t_title, cells in blocks:
        if not cells:
            continue
        # ligne de titre de section
        tcell = ws.cell(row=row_idx, column=1, value=t_title)
        tcell.font = Font(bold=True, size=12, color=_TITLE_COLOR)
        row_idx += 1
        # en-tete du tableau
        header = cells[0]
        for c_idx, val in enumerate(header, 1):
            cell = ws.cell(row=row_idx, column=c_idx, value=str(val))
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = _fill(_HEADER_FILL)
            cell.alignment = Alignment(horizontal="center", vertical="center",
                                       wrap_text=True)
        row_idx += 1
        for r in cells[1:]:
            for c_idx, val in enumerate(r, 1):
                cell = ws.cell(row=row_idx, column=c_idx, value=str(val) if val else "")
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                key = (val or "").strip().lower()
                if key in _SEV_FILL:
                    cell.fill = _fill(_SEV_FILL[key])
                    cell.font = Font(bold=True,
                                     color="FFFFFF" if key in ("critique", "ko")
                                     else "000000")
            row_idx += 1
        row_idx += 1  # ligne vide entre tableaux

    # largeurs de colonnes (premiere ligne de texte, bornees)
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        width = 12
        for r in range(1, min(ws.max_row, 300) + 1):
            v = ws.cell(row=r, column=col).value
            if v:
                first_line = str(v).split("\n")[0]
                width = max(width, min(len(first_line) + 2, 60))
        ws.column_dimensions[letter].width = width


def _fill(color: str):
    from openpyxl.styles import PatternFill
    return PatternFill("solid", fgColor=color)


def export_xlsx(sheets: list[tuple[str, list[tuple[str, list[list[str]]]]]],
                path: Path) -> Path:
    """Ecrit un classeur : [(nom d'onglet, [(titre de tableau, lignes)])].

    Un onglet par etape, tableaux empiles a l'interieur. Retourne le chemin."""
    if not OPENPYXL_OK:
        raise RuntimeError("openpyxl indisponible (pip install openpyxl)")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    used: set = set()
    for name, blocks in sheets:
        if not blocks:
            continue
        _write_stacked_sheet(wb, name, blocks, used)
    if not wb.sheetnames:
        ws = wb.create_sheet("Sans tableau")
        ws.append(["Aucun tableau detecte — le texte complet reste dans le .md"])
    wb.save(path)
    return path


def save_xlsx_from_outputs(outputs: dict, project: str, output_dir) -> Path:
    """Classeur d'analyse : UN onglet par etape (Cadrage -> Livraison).
    Appelé apres save_analysis_outputs (CLI et GUI)."""
    from agents.orchestrator import WORKFLOW_STEPS

    sheets: list[tuple[str, list[tuple[str, list[list[str]]]]]] = []
    for step in WORKFLOW_STEPS:
        text = (outputs or {}).get(step["id"], "")
        if not text:
            continue
        blocks = markdown_tables(text)
        if blocks:
            sheets.append((step["name"], blocks))
    path = Path(output_dir) / f"{project}_analyse.xlsx"
    return export_xlsx(sheets, path)
