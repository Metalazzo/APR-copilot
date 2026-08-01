# Changelog

## [1.1.0] - 2026-08-01

### Ajout
- Support multi-modèles avec profils nommés (cloud / local)
- Associe un modèle cloud (DeepSeek V4 Flash) aux agents complexes (Ingénieur, Qualité, Client)
- Associe un modèle local (Gemma 4 12B QAT via LM Studio) au Secretary (formatage)
- Fonctions `create_model_client(profile_name)` et `create_model_clients()` dans l'orchestrateur

### Changement
- `config.py` : remplacement de `LLMConfig` unique par `ModelProfiles` avec deux profils configurables via env vars
- `agents/orchestrator.py` : `RiskAnalysisOrchestrator` ne prend plus de `model_client` en paramètre (chaque agent a son propre client)
- `main.py` : les agents sont créés avec le client adapté à leur profil de complexité
- Variables d'environnement renommées : `MISTRAL_*` → `CLOUD_*` / `LOCAL_*`

## [1.0.0] - 2026-08-01

### Ajout
- Architecture multi-agent avec 5 roles : Orchestrateur, Ingenieur Technique SDF, Animateur Qualite, Representant Client, Secretaire
- Systeme RAG avec ChromaDB et retrieval hybride (semantique + BM25 lexical)
- Workflow en 5 etapes avec 4 points de controle humain (HITL)
- CLI avec commandes `ingest`, `search`, `analyze`
- Prompts systeme dedies pour chaque agent (format Markdown)
- Documents de test (description systeme, agressions generiques, menaces generiques)
- Sorties en Markdown et JSON
- Support des formats d'entree : .txt, .md, .pdf, .docx
- Config via variables d'environnement (LLM, RAG, embeddings)
- Integration AutoGen 0.4+ (Microsoft Agent Framework)
