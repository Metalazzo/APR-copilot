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

    output_path = output_dir / f"{project}_analyse.md"
    livraison = outputs.get("livraison", "")
    if livraison:
        output_path.write_text(livraison, encoding="utf-8")
    else:
        combined = "\n\n".join(f"# {k}\n\n{v}" for k, v in outputs.items())
        output_path.write_text(combined, encoding="utf-8")

    json_path = output_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {
                "project": project,
                "steps": {k: str(v)[:5000] for k, v in outputs.items()},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return output_path, json_path
