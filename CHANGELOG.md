# Changelog

## [1.3.9] - 2026-09-03

### Ameliore — marges de contexte et visibilite des troncatures
- `STEP_CONTEXT_LIMIT` 20000 -> **40000** caracteres par etape precedente
  (un modele verbeux peut produire 15-40k caracteres ; 105k de contexte
  absorbe largement 4 x 40000).
- **Toute troncature est desormais visible** : message console + evenement GUI
  « productions tronquees : X (-N car.) » avec conseil d'augmentation — avant,
  le marqueur n'existait que dans le prompt injecte, invisible pour l'utilisateur.
- GUI : champ « Limite de contexte par etape precedente » dans Parametres
  avances ; defaut retries aligne sur 1 (orchestrateur).
- `_previous_outputs_section` et `_secretary_task` passees en async (emission
  des alertes).

### Rollback
- Etat pre-modification : commit `74e869a`

## [1.3.8] - 2026-09-03

### Ajout — etat de l'art
- **Recherche web orchestree** (module `web_search.py`) : l'orchestrateur cherche
  AVANT de construire la tache et injecte les resultats comme contexte
  « NON VERIFIE » — le LLM n'appelle jamais d'outil (pattern fiable v1.3.7).
  Backends : DuckDuckGo (sans cle, via `ddgs`), SearXNG auto-heberge, Tavily.
  Desactive par defaut (`WEB_SEARCH_ENABLED=false`) — confidentialite.
  GUI : switch + backend + champs dans Parametres avances ; log par recherche.
- **Requetes par etape** : champ `query_hint` dans le workflow + extraction
  naive de mots-cles de la description systeme (sans appel LLM).
- README : socle de reference RAG (dossier `references/`) + doc recherche web.

### Corrige — pertes de points dans le livrable
- `STEP_CONTEXT_LIMIT` 6000 -> **20000** caracteres (reglable) : les productions
  des etapes precedentes etaient tronquees avant d'atteindre le Secretaire,
  d'ou des points sautes/tronques dans le livrable final. Avec un modele a
  grand contexte (100k+), l'injection integrale est desormais possible.
- Re-generations : injection de la production precedente passee de 4000 a la
  meme limite configurables.
- Export JSON : contenu **integral** des etapes (audit des pertes possible).

## [1.3.7] - 2026-09-02

### Corrige — Ingenieur produisant du vide / du brut aux etapes avec outils
- **Cause 1** : autogen 0.7.5 a change le defaut de `reflect_on_tool_use` a `False`
  — apres execution de `search_rag`, la « production » renvoyee au workflow etait le
  contenu brut de l'outil (chunks RAG) au lieu d'une analyse redigee.
- **Cause 2** : dans la phase de reflexion, le modele re-emettait ses appels
  d'outils en **texte brut `<tool_call>`** (non parses) — production finale = XML.
- **Correctif structurel** : outils agentic de l'Ingenieur **desactives par defaut**
  (`LLM_ENGINEER_TOOLS=false`) — le RAG est pre-injecte dans chaque tache
  (`RAG_TOP_K`, defaut 8). Mentions `search_rag` neutralisees dans les taches du
  workflow. Re-activables via `LLM_ENGINEER_TOOLS=true` (+ `reflect_on_tool_use=True`).
- **Priorite des templates verrouillee** : template/tableau/matrice propre au projet
  (donnees d'entree **ou** RAG) > structure par defaut de la tache > RAG generique.
- Valide : production d'etape 3 de 10 301 caracteres (analyse FR structuree)
  contre 440 caracteres de XML avant correctif.

### Rollback
- Etat pre-modification : commit `56fe5fe`

## [1.3.6] - 2026-09-02

### Ameliore — GUI : popup de relecture/decision
- Les checkpoints s'ouvrent desormais dans une **popup (dialog)** au lieu de
  developper la carte du workflow (qui cassait la mise en page) : production et
  relecture cote a cote dans des zones defilantes, avec les boutons de decision
  (CONTINUER / QUITTER / feedback) directement dans la popup.
- La popup se **ferme automatiquement** des que la decision est envoyee, puis le
  workflow reprend.
- Le bouton « Relecture / Validation » de chaque carte permet de rouvrir la popup
  (consultation sans decision si aucun checkpoint en attente sur cette etape).
- Badge de carte : « Checkpoint en attente » → « Validee » / « Interrompue ».
- Notifications rendues robustes aux appels depuis la tache de l'orchestrateur
  (fallback sur le journal).

### Rollback
- Etat pre-modification : commit `7ff8eea`

## [1.3.5] - 2026-09-02

### Corrige — cohérence des rôles
- `engineer.md` : la hiérarchie des sources était inversée — le RAG primait sur le
  template et les données d'entrée. Nouvel ordre : instructions système + feedback
  humain > **données d'entrée** (description, tableaux/templates, matrice) > RAG
  (support seulement) > connaissance générale.
- `engineer.md` : rôle explicite — conduire les analyses et **remplir exactement les
  tableaux d'analyse fournis dans les données d'entrée** ; ne jamais juger une analyse
  déjà faite (relecture = rôle des autres agents) ; exécuter uniquement l'étape de
  workflow demandée.
- `client.md` : mission recentrée — **gardien de la non-divergence**, analyse centrée
  sur l'usage/intégration réels du produit ; frontière « tu ne produis pas l'analyse ».
- Workflow : les 2 tâches de relecture client (filtrage, barrières) exigent désormais
  un verdict explicite de divergence.

### Rollback
- Etat pre-modification : commit `ffaa877`

## [1.3.4] - 2026-08-31

### Corrige
- **Boucle de raffinement par etape** : apres un feedback, la re-generation est
  suivie d'une nouvelle relecture et d'un nouveau point de controle sur la meme
  etape (avant : un seul feedback possible, resultat jamais re-presente).
- **Memoire des agents purgee a chaque tour** (`model_context.clear()`) : l'ancien
  comportement (`UnboundedChatCompletionContext`) accumulait tout l'historique,
  noyait les feedbacks humains dans le contexte et gonflait le prefill. La
  continuite est assuree par le chaînage explicite deja en place.
- **Re-generations auto-contenues** : la production precedente et tous les
  feedbacks humains sont injectes en tete de tache (le modele voit ce qu'il
  corrige et ce qui est demande, sans dependre de sa memoire).
- **Relecteurs informes** : sur iteration, Qualite/Client recoivent le contexte
  « le jury humain a demande… » et verifient l'integration.
- **RAG PDF nettoye** : suppression des en-tetes/pieds de page repetes (lignes
  communes a >= 50% des pages, numeros de page) et fusion des micro-chunks
  (< 300 car.) — les datasheets produisaient beaucoup de chunks-junk qui
  saturaient la recherche hybride.
- Flag `LLM_STREAM_ENGINEER` (defaut `true`) : desactive le streaming sur le seul
  agent a outils si le tool calling en streaming pose probleme.

### Rollback
- Etat pre-modification : commit `d48e58b`

## [1.3.3] - 2026-08-31

### Ajout
- **Pilotage du raisonnement par template** : `LLM_REASONING` (off/low/medium/high/xhigh)
  ajoute le jeton correspondant (`<|think_off|>`, `<|think_low|>`…) au message systeme
  de chaque agent — interprete par le template Jinja du modele cote LM Studio.
  Defaut `off` : supprime la phase de thinking, principal facteur de duree des
  requetes locales. Selecteur « Niveau de raisonnement » dans la GUI (Parametres
  avances).

### Rollback
- Etat pre-modification : commit `5c7c140`

## [1.3.2] - 2026-08-31

### Corrige
- **APITimeoutError sur re-generation** : le timeout de 600 s etait insuffisant pour
  les longues generations locales (prefill de prompts cumules + thinking + jusqu'a
  16k tokens de sortie). Le timeout passe a **1800 s** et les retries a **1**
  (retenter une generation de 30 min etait contre-productif).
- **Streaming active sur les 4 agents** (`model_client_stream=True`) : les headers
  arrivent immediatement et le timeout s'applique entre chunks — une generation
  de 30 min passe desormais sans timeout.

### Ajout
- GUI : message d'erreur explicite en cas de timeout avec pistes (timeout, max_tokens,
  Thinking off, Flash Attention + KV cache q8_0).
- README : section « Performances en local » (Thinking, Flash Attention, calibration
  du timeout, impact du KV cache).

### Rollback
- Commit de reference de l'etat pre-correction : `ee839b1`
  (`git revert` / `git checkout ee839b1 -- .` pour revenir en arriere).

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
