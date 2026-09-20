"""Évaluation du RAG : recall@1/@3/@8 + MRR sur un jeu de requêtes métier.

Usage : python tests/eval_rag.py [label]
  label = 'baseline' (avant préfixes) | 'post' (après préfixes) — cosmétique,
  sert de titre au rapport.

Mesure le pipeline COMPLET (retrieve() : hybride e5+BM25 → RRF → top_k).
Vérité terrain : pour chaque requête, le(s) fichier(s) source(s) attendu(s)
(appariement par sous-chaîne du champ 'source' des métadonnées).

Sortie : tableau par requête + agrégats. Aucun appel LLM.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.retriever import retrieve

# Jeu de requetes-vérité : {query, expect=[sous-chaines de 'source']}.
# Conçu d'apres l'index reel : batterie (PDF EN, 216 chunks), CLUSIF (PDF FR,
# 310 chunks), sample_docs (agressions/menaces generiques, description systeme).
PAIRS = [
    {"query": "dégazage batterie menace thermique vers l'environnement",
     "expect": ["batterie"]},
    {"query": "battery thermal runaway propagation risk scenarios",
     "expect": ["batterie"]},
    {"query": "transport levage installation dépose phases de vie",
     "expect": ["batterie"]},
    {"query": "matrice de cotation gravité vraisemblance niveau de risque",
     "expect": ["clusif"]},
    {"query": "situation dangereuse événement redouté scénario de risque",
     "expect": ["clusif"]},
    {"query": "barrières de prévention détection protection récupération",
     "expect": ["clusif"]},
    {"query": "risque résiduel option de traitement acceptation",
     "expect": ["clusif"]},
    {"query": "analyse préliminaire de risque APR méthode étapes",
     "expect": ["clusif"]},
    {"query": "agressions environnementales température vibrations poussière",
     "expect": ["agressions_generiques"]},
    {"query": "foudre perturbations électromagnétiques CEM",
     "expect": ["agressions_generiques"]},
    {"query": "menace émise dégazage échauffement fuite explosion",
     "expect": ["menaces_generiques"]},
    {"query": "sous-station traction 25kV fonctions interfaces SCADA",
     "expect": ["description_systeme"]},
]


def _match(chunk, expect_list):
    src = str((chunk.metadata or {}).get("source", "")).lower()
    return any(e in src for e in expect_list)


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "mesure"
    from rag.retriever import retrieve

    rows = []
    r1 = r3 = r8 = mrr = 0.0
    for pair in PAIRS:
        chunks = retrieve(pair["query"], top_k=8)
        ranks = []
        for exp in pair["expect"]:
            rank = next(
                (i + 1 for i, c in enumerate(chunks)
                 if exp in str((c.metadata or {}).get("source", "")).lower()),
                None,
            )
            ranks.append(rank)
        best = min((r for r in ranks if r), default=None)
        hit1 = best is not None and best <= 1
        hit3 = best is not None and best <= 3
        hit8 = best is not None and best <= 8
        rr = (1.0 / best) if best else 0.0
        r1 += hit1
        r3 += hit3
        r8 += hit8
        mrr += rr
        rows.append((pair["query"], ranks, hit1, hit3, hit8, rr))

    n = len(PAIRS)
    print(f"\n=== ÉVALUATION RAG — {label} — {n} requêtes (top_k=8) ===\n")
    print(f"{'requête':<56} {'@1':>3} {'@3':>3} {'@8':>3} {'MRR':>6}")
    print("-" * 80)
    for q, ranks, h1, h3, h8, rr in rows:
        print(f"{q[:54]:<56} {'✓' if h1 else '·':>3} {'✓' if h3 else '·':>3} "
              f"{'✓' if h8 else '·':>3} {rr:.2f}")
    print("-" * 80)
    print(f"recall@1 = {r1 / n:.0%}   recall@3 = {r3 / n:.0%}   "
          f"recall@8 = {r8 / n:.0%}   MRR = {mrr / n:.3f}\n")


if __name__ == "__main__":
    main()
