"""Sauvegarde des livrables d'analyse (partage CLI / GUI)."""

import json
from pathlib import Path


def save_analysis_outputs(
    outputs: dict,
    project: str,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Ecrit le livrable Markdown et les metadonnees JSON de l'analyse.

    Si l'etape 'livraison' existe, le Markdown correspond au document final ;
    sinon, toutes les productions sont concatenees.

    Retourne (chemin_markdown, chemin_json).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{project}_analyse.md"
    livraison = outputs.get("livraison", "")
    if livraison:
        output_path.write_text(livraison, encoding="utf-8")
    else:
        # Analyse interrompue AVANT la livraison : bandeau explicite — on ne
        # presente plus un partiel comme le rapport final.
        done = ", ".join(outputs.keys()) or "aucune"
        banner = (
            "# ⚠️ ANALYSE INCOMPLÈTE (interrompue avant la livraison)\n\n"
            f"Étapes produites : {done}\n\n"
            "Reprendre la session : GUI (tiroir Sessions) ou "
            "`python main.py analyze -p <projet> --resume output/sessions/<id>`.\n\n"
            "---\n\n"
        )
        combined = banner + "\n\n".join(
            f"# {k}\n\n{v}" for k, v in outputs.items()
        )
        output_path.write_text(combined, encoding="utf-8")

    json_path = output_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {
                "project": project,
                # Livrable finalise ou non (livraison absente = analyse
                # interrompue : voir le bandeau dans le .md)
                "complete": bool(livraison),
                # Contenu integral : sert d'audit (verifier qu'aucun point n'a
                # saute entre les etapes et le livrable final).
                "steps": {k: str(v) for k, v in outputs.items()},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return output_path, json_path
