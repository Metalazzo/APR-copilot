import argparse
import asyncio
import json
from pathlib import Path

from agents.agents import EngineerAgent, QualityAgent, ClientAgent, SecretaryAgent
from agents.orchestrator import RiskAnalysisOrchestrator, create_model_client
from rag.vector_store import ingest_documents
from rag.retriever import invalidate_bm25_cache
from config import config as app_config


def print_banner():
    print("""
    ================================================================
       RISK ANALYSIS COPILOT - Analyse Preliminaire de Risque
       Multi-Agent System (Microsoft Agent Framework + Mistral)
    ================================================================
    """)


async def ingest_command(args):
    directory = Path(args.directory)
    if not directory.exists():
        print(f"ERREUR: Le repertoire '{directory}' n'existe pas.")
        return

    print(f"Ingestion des documents depuis : {directory}")
    print(f"Vector store : {app_config.rag.persist_directory}")

    count = ingest_documents(directory, reset=args.reset)
    invalidate_bm25_cache()

    print(f"Ingestion terminee : {count} chunks indexes.")
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
    if not args.context and not args.context_file:
        print("ERREUR: Fournissez un contexte via --context ou --context-file.")
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
    print(f"Modele : {app_config.llm.model}")
    print(f"Endpoint : {app_config.llm.base_url}")
    print(f"Projet : {args.project}")
    print()

    model_client = create_model_client()

    engineer_wrapper = EngineerAgent(model_client)
    quality_wrapper = QualityAgent(model_client)
    client_wrapper = ClientAgent(model_client)
    secretary_wrapper = SecretaryAgent(model_client)

    async def interactive_checkpoint(step_id: str, production: str, review: str, all_outputs: dict) -> str:
        print(f"\n--- Production ({step_id}) ---")
        print(production[:2000])
        if review:
            print(f"\n--- Relecture ---")
            print(review[:1000])
        print()
        response = input(f">>> [Point de controle] CONTINUER / QUITTER / feedback : ")
        return response

    orchestrator = RiskAnalysisOrchestrator(
        model_client=model_client,
        engineer=engineer_wrapper.agent,
        quality=quality_wrapper.agent,
        client=client_wrapper.agent,
        secretary=secretary_wrapper.agent,
        human_callback=interactive_checkpoint,
    )

    orchestrator.state.analysis_state.project_name = args.project

    print(f"\nDemarrage de l'analyse pour le projet : {args.project}")
    print(f"Contexte fourni : {context[:500]}...\n")

    outputs = await orchestrator.run_full_analysis(initial_context=context)

    output_path = app_config.output_dir / f"{args.project}_analyse.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    livraison = outputs.get("livraison", "")
    if livraison:
        output_path.write_text(livraison, encoding="utf-8")
        print(f"\nDocument final sauvegarde : {output_path}")
    else:
        combined = "\n\n".join(f"# {k}\n\n{v}" for k, v in outputs.items())
        output_path.write_text(combined, encoding="utf-8")
        print(f"\nResultats sauvegardes : {output_path}")

    json_path = output_path.with_suffix(".json")
    json_path.write_text(
        json.dumps({"project": args.project, "steps": {k: str(v)[:5000] for k, v in outputs.items()}}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main():
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
