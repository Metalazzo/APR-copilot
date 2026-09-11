import argparse
import asyncio
import os
import sys
from pathlib import Path

from agents.agents import EngineerAgent, QualityAgent, ClientAgent, SecretaryAgent
from agents.orchestrator import RiskAnalysisOrchestrator, create_model_client, create_model_clients
from output_utils import save_analysis_outputs
from rag.vector_store import ingest_documents
from rag.retriever import invalidate_bm25_cache
from config import config as app_config


def print_banner():
    print("""
    ================================================================
       RISK ANALYSIS COPILOT - Analyse Preliminaire de Risque
       Multi-Agent System (AutoGen - modeles locaux LM Studio ou cloud)
    ================================================================
    """)


async def ingest_command(args):
    directory = Path(args.directory)
    if not directory.exists():
        print(f"ERREUR: Le repertoire '{directory}' n'existe pas.")
        return

    print(f"Ingestion des documents depuis : {directory}")
    print(f"Vector store : {app_config.rag.persist_directory}")

    report = ingest_documents(directory, reset=args.reset)
    invalidate_bm25_cache()

    print(report.summary())
    print(f"Collection : {app_config.rag.collection_name}")


async def search_command(args):
    from rag.retriever import retrieve, format_retrieved_context

    if not Path(app_config.rag.persist_directory).exists():
        print("ERREUR: Aucun index trouve. Lancez d'abord 'python main.py ingest <repertoire>'.")
        return

    chunks = retrieve(args.query, top_k=args.top_k)
    if not chunks:
        print("Aucun resultat trouve.")
        return

    print(f"\n{len(chunks)} resultats pour : '{args.query}'\n")
    print(format_retrieved_context(chunks))


async def analyze_command(args):
    mode = args.profile or app_config.profiles.agent_profile
    valid_modes = ("hybrid", "cloud", "local")
    if mode not in valid_modes:
        print(f"ERREUR: Profil d'affectation invalide : {mode!r} (attendus : {', '.join(valid_modes)}).")
        return

    # Reprise d'une session sauvegardee : le contexte initial devient optionnel
    resume_data = None
    resume_dir = getattr(args, "resume", None)
    if resume_dir:
        from session_store import load_session
        try:
            resume_data = load_session(resume_dir)
        except Exception as exc:
            print(f"ERREUR: Session illisible : {exc}")
            return
        print(f"Session en reprise : {resume_dir}")
    elif not args.context and not args.context_file:
        print("ERREUR: Fournissez un contexte via --context ou --context-file (ou --resume).")
        return

    context = args.context or ""
    if args.context_file:
        p = Path(args.context_file)
        if p.exists():
            context = p.read_text(encoding="utf-8")
        else:
            print(f"ERREUR: Fichier '{args.context_file}' introuvable.")
            return

    print_banner()
    print(f"Affectation des modeles : {mode}")
    print(f"Modele cloud : {app_config.profiles.cloud.model}")
    print(f"Modele local : {app_config.profiles.local.model}")
    print(f"Projet : {args.project}")
    print()

    clients = create_model_clients(mode)
    if mode == "cloud":
        cloud_client = local_client = clients["cloud"]
    elif mode == "local":
        cloud_client = local_client = clients["local"]
    else:  # hybrid : cloud pour les agents complexes, local pour le Secretaire
        cloud_client = clients["cloud"]
        local_client = clients["local"]

    # Etiquettes de modele par agent (traçabilite + statistiques/couts)
    if mode == "cloud":
        eng = sec = f"cloud:{app_config.profiles.cloud.model}"
    elif mode == "local":
        eng = sec = f"local:{app_config.profiles.local.model}"
    else:
        eng = f"cloud:{app_config.profiles.cloud.model}"
        sec = f"local:{app_config.profiles.local.model}"
    model_labels = {"engineer": eng, "quality": eng, "client": eng, "secretary": sec}

    engineer_wrapper = EngineerAgent(cloud_client)
    quality_wrapper = QualityAgent(cloud_client)
    client_wrapper = ClientAgent(cloud_client)
    secretary_wrapper = SecretaryAgent(local_client)

    async def interactive_checkpoint(
        step_id: str, production: str, review: str, all_outputs: dict,
        decided_points: dict | None = None,
    ) -> str:
        print(f"\n--- Production ({step_id}) ---")
        print(production[:2000])
        if review:
            print(f"\n--- Relecture ---")
            print(review[:1000])
        if decided_points:
            print(f"\n--- Points deja qualifies (non re-soumis) ---")
            for pid, d in decided_points.items():
                print(f"  [{pid}] {d.get('decision', '?')} — "
                      f"{(d.get('text') or '')[:100]} (V{int(d.get('iteration', 0)) + 1})")
        print()
        response = input(f">>> [Point de controle] CONTINUER / QUITTER / feedback : ")
        return response

    orchestrator = RiskAnalysisOrchestrator(
        engineer=engineer_wrapper.agent,
        quality=quality_wrapper.agent,
        client=client_wrapper.agent,
        secretary=secretary_wrapper.agent,
        human_callback=interactive_checkpoint,
        session_dir=resume_dir,
        model_labels=model_labels,
    )

    orchestrator.state.analysis_state.project_name = args.project

    print(f"\nDemarrage de l'analyse pour le projet : {args.project}")
    print(f"Contexte fourni : {context[:500]}...\n")

    outputs = await orchestrator.run_full_analysis(
        initial_context=context, resume_state=resume_data
    )

    output_path, json_path = save_analysis_outputs(
        outputs, args.project, app_config.output_dir
    )
    if outputs.get("livraison"):
        print(f"\nDocument final sauvegarde : {output_path}")
    else:
        print(f"\nResultats sauvegardes : {output_path}")


def main():
    # Console/pipe Windows : force UTF-8 afin que les sorties LLM (fleches,
    # accents, emojis...) ne fassent pas planter l'analyse avec un
    # UnicodeEncodeError cp1252 quand la sortie est redirigee.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Risk Analysis Copilot - Analyse Preliminaire de Risque (APR)"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commandes disponibles")

    ingest_parser = subparsers.add_parser("ingest", help="Indexer des documents dans le RAG")
    ingest_parser.add_argument("directory", help="Repertoire contenant les documents (.txt, .md, .pdf, .docx)")
    ingest_parser.add_argument("--reset", action="store_true", help="Reinitialiser l'index avant ingestion")

    search_parser = subparsers.add_parser("search", help="Rechercher dans les documents indexes")
    search_parser.add_argument("query", help="Requete de recherche")
    search_parser.add_argument("--top-k", type=int, default=5, help="Nombre de resultats")

    analyze_parser = subparsers.add_parser("analyze", help="Lancer une analyse de risque")
    analyze_parser.add_argument("--project", "-p", required=True, help="Nom du projet")
    analyze_parser.add_argument("--context", "-c", help="Description/contexte du systeme (texte)")
    analyze_parser.add_argument("--context-file", "-f", help="Fichier contenant la description du systeme")
    analyze_parser.add_argument("--output-dir", "-o", default=None, help="Repertoire de sortie")
    analyze_parser.add_argument(
        "--profile",
        choices=["hybrid", "cloud", "local"],
        default=None,
        help="Affectation des modeles aux agents (defaut : AGENT_PROFILE, sinon hybrid)",
    )
    analyze_parser.add_argument(
        "--resume",
        default=None,
        help="Reprendre une session sauvegardee (dossier output/sessions/<id>)",
    )

    args = parser.parse_args()

    if args.command == "ingest":
        asyncio.run(ingest_command(args))
    elif args.command == "search":
        asyncio.run(search_command(args))
    elif args.command == "analyze":
        asyncio.run(analyze_command(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
