# Changelog

## [1.3.1] - 2026-08-30

### Corrige
- **Boucle infinie dans `_chunk_text`** : si le dernier separateur (point/espace) avant
  `chunk_size` tombait exactement a l'indice `overlap - 1`, la decoupe ne progressait
  plus (MemoryError sur de vrais PDF, index vide apres `--reset`). Progression stricte
  garantie desormais.
- L'ingestion est **isolee par fichier** : un document en erreur (PDF chiff, illisible,
  scanne sans couche texte…) est ignore avec sa raison au lieu de tuer toute l'ingestion.
- `upsert` au lieu de `add` + hash du chemin dans les IDs de chunks : re-ingestion sans
  `--reset` sans erreur d'ID duplique, plus de collision entre fichiers homonymes.
- Retrait du parametre mort `glob_pattern` de `ingest_documents`.

### Ajout
- `IngestReport` : rapport d'ingestion (fichiers lus / ignores avec raison / chunks)
  affiche en CLI et dans le log de la GUI.

## [1.3.0] - 2026-08-30

### Ajout
- GUI NiceGUI (`gui.py`) : profil d'affectation (hybride/cloud/local), liste des modeles
  LM Studio en direct, parametres (temperature, max_tokens, tool calling, timeout),
  gestion RAG (ingestion, statut), suivi en direct des 5 etapes et points de controle
  interactifs (CONTINUER / QUITTER / feedback)
- `client_from_profile(profile, function_calling)` dans l'orchestrateur : client
  construisible depuis un profil explicite (utilise par la GUI)
- Evenements de progression structurels de l'orchestrateur (`progress_callback`) sans
  changer le comportement CLI
- `output_utils.save_analysis_outputs()` partage entre CLI et GUI

## [1.2.0] - 2026-08-25

### Ajout
- Flag CLI `--profile hybrid|cloud|local` et variable `AGENT_PROFILE` pour piloter
  l'affectation des modeles aux agents (defaut `hybrid` = comportement historique)
- Support complet d'un endpoint local LM Studio : `model_info` explicite dans
  `create_model_client()`, timeout/retries configurables (`LLM_TIMEOUT`,
  `LLM_MAX_RETRIES`) et switch tool calling `LLM_FUNCTION_CALLING`

### Changement
- `create_model_client()` leve une erreur sur un profil inconnu au lieu de retomber
  silencieusement sur le cloud
- Suppression de la classe morte `LLMConfig` (defauts « mistral-large » obsoletes)

### Documentation
- README : exemple 100% local avec LM Studio, nouvelles variables, note WSL2

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
