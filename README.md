# Risk Analysis Copilot - APR Multi-Agent

Systeme multi-agent d'assistance a l'Analyse Preliminaire de Risque (APR) en contexte SDF/RAMS.

## Architecture

5 agents sur Microsoft Agent Framework (AutoGen 0.4+) :

| Agent | Role |
|-------|------|
| **Orchestrateur** | Coordination globale, distribution des taches, checkpoints humains |
| **Ingenieur Technique SDF** | Core analyse : cadrage, filtrage, scenarios, barrieres (acces RAG) |
| **Animateur Qualite** | Controle qualite interne, tracabilite, preparation validation humaine |
| **Representant Client** | Point de vue utilisateur final, pertinence operationnelle |
| **Secretaire** | Mise en forme, templates, generation livrables (Markdown/CSV/JSON) |

## Stack technique

- **Orchestration** : Microsoft Agent Framework (`autogen-agentchat` >= 0.4.0)
- **LLM** : tout modele expose via une API compatible OpenAI — LM Studio en local (ex. Qwen3 32B/35B), ou API cloud (DeepSeek...)
- **RAG** : ChromaDB + sentence-transformers + BM25 (retrieval hybride)
- **GUI** : NiceGUI (interface web locale, `python gui.py`)
- **Formats supportes** : `.txt`, `.md`, `.pdf`, `.docx`

## Pre-requis

```bash
pip install -r requirements.txt
```

### Configuration

Variables d'environnement (ou modifier `config.py`) :

**Modèle cloud (agents complexes) :**

| Variable | Defaut | Description |
|----------|--------|-------------|
| `CLOUD_MODEL` | `deepseek-v4-flash` | Modele cloud pour Ingenieur, Qualite, Client |
| `CLOUD_BASE_URL` | `https://api.deepseek.com/v1` | URL de l'API |
| `CLOUD_API_KEY` | (requis) | Cle API |

**Modèle local (formatage) :**

| Variable | Defaut | Description |
|----------|--------|-------------|
| `LOCAL_MODEL` | `gemma-4-12b-qat` | Modele local pour le Secretaire |
| `LOCAL_MAX_TOKENS` | `32768` | Plafond de generation par appel (≈ 105k caracteres de sortie) |
| `LOCAL_BASE_URL` | `http://localhost:1234/v1` | URL LM Studio |
| `LOCAL_API_KEY` | `not-needed` | Cle API |

**Affectation des modeles et robustesse :**

| Variable | Defaut | Description |
|----------|--------|-------------|
| `AGENT_PROFILE` | `hybrid` | Affectation des agents : `hybrid` (cloud = Ingenieur/Qualite/Client, local = Secretaire), `cloud` (tout sur cloud), `local` (tout en local). Surcharge par `--profile` |
| `DEFAULT_PROFILE` | `cloud` | Profil utilise quand aucun nom explicite n'est donne |
| `LLM_FUNCTION_CALLING` | `true` | Mettre `false` si le serveur ne supporte pas le tool calling |
| `LLM_ENGINEER_TOOLS` | `false` | Outils agentic (`search_rag`) de l'Ingenieur : desactives par defaut (le RAG est pre-injecte dans chaque tache). `true` pour re-activer la boucle d'outils |
| `LLM_REASONING` | `off` | Niveau de raisonnement injecte au message systeme : `off`/`low`/`medium`/`high`/`xhigh` (jetons interpretes par le template Jinja du modele) |
| `CONTEXT_AUTO` | `true` | Detection automatique du contexte charge du modele local (API LM Studio) et calcul de la limite d'injection par etape ; `false` pour utiliser `STEP_CONTEXT_LIMIT` |
| `STEP_CONTEXT_LIMIT` | `40000` | Repli manuel : caracteres max par etape precedente reinjectee (si `CONTEXT_AUTO=false` ou detection impossible). Toute troncature est signalee en log/GUI |
| `WEB_SEARCH_ENABLED` | `false` | Recherche web orchestree (etat de l'art) injectee dans les taches de l'Ingenieur. Confidentialite : les requetes partent vers l'exterieur |
| `WEB_SEARCH_BACKEND` | `ddg` | `ddg` (sans cle) / `searxng` (auto-heberge, anonymise) / `tavily` (cle cloud) |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Nombre de resultats web injectes par etape |
| `SEARXNG_URL` | — | URL de l'instance SearXNG (`search.formats` avec `json` active) |
| `TAVILY_API_KEY` | — | Cle API Tavily (si backend tavily) |
| `HYBRID_ALPHA` | `0.5` | Poids RRF semantique vs lexical (1.0 = semantique seul, 0.0 = lexical seul) |
| `RAG_DEDUP_ADJACENT` | `true` | Ecarter les chunks adjacents (±1) du meme fichier deja retenus — diversifie le contexte injecte |
| `CLOUD_PRICE_INPUT` | `0` | Prix d'1M tokens d'entree en EUR (profil cloud) — active le cout estime dans les statistiques |
| `CLOUD_PRICE_OUTPUT` | `0` | Prix d'1M tokens de sortie en EUR (profil cloud) |

### Le RAG en bref (v1.3.20)

Recherche hybride : embeddings `multilingual-e5-large` (ChromaDB) + **BM25
normalise francais** (accents plies, stop-words retires) fusionnes par
Reciprocal Rank Fusion (poids `HYBRID_ALPHA`) puis deduplication des voisins
adjacents. Les chunks BM25 gardent leurs vraies metadonnees (fichier source).

**Canal web separe du RAG** (si `WEB_SEARCH_ENABLED`) : les resultats sont
injected dans la tache comme « Etat de l'art — NON VERIFIE » et ne sont JAMAIS
indexes dans Chroma (l'index local reste referentiel de confiance). Pour
consommer une info web utile de facon tracée : copiez la page en local puis
ingerez-la via le tiroir « Documents RAG ».

### En file (roadmap)

- **Re-ranker local (option B)** : cross-encoder `BAAI/bge-reranker-v2-m3`
  (~2 Go, copie locale, zero reseau) pour re-scorder les ~20 candidats avant
  le top_k — activable via `RAG_RERANK=true` (defaut off), a tester en vitesse
  CPU/GPU avant activation.

### LIVRAISON_FORCE / --force-delivery (v1.3.21)

A la reprise d'une session dont le livrable etait tronque :
`python main.py analyze -p <projet> --resume output/sessions/<id> --force-delivery`
re-genere la LIVRAISON seule (les etapes validees restent conservees). Si un
bloc est coupe par le plafond de generation, une continuation complete
automatiquement la generation.

### Sessions sauvegardables et reprise (v1.3.17)

Chaque analyse cree `output/sessions/<horodatage>/` : l'etat complet (productions, relectures, validations et qualifications humaines par point, modele producteur par etape, statistiques) y est ecrit apres chaque production, relecture et decision de checkpoint.

- **QUITTER = mise en pause** : la session reste reprenable
- **Reprise** : bouton « Reprendre » du panneau Sessions (GUI) ou `python main.py analyze -p <projet> --resume output/sessions/<id>`
- **Changement de modele entre etapes** : a la reprise, la suite s'execute avec le modele configure au moment du relancement ; les etapes validees restent en l'etat (leur modele producteur est trace)
- **Statistiques** : par appel (tokens prompt/completion, duree, tok/s), par phase (temps de generation de l'etape, appels, modeles), temps total de generation, duree ecoulee hors pauses, ETA des etapes restantes, cout estime — visibles sur les cartes, dans la popup de checkpoint, dans le journal et exportees en `stats.md`/`stats.json` dans la session
| `LLM_TIMEOUT` | `600` | Timeout (secondes) par appel LLM — valeur large conseillee en local |
| `LLM_MAX_RETRIES` | `3` | Nombre de tentatives par appel LLM |

**RAG :**

| Variable | Defaut | Description |
|----------|--------|-------------|
| `CHROMA_PERSIST_DIR` | `./chroma_db` | Repertoire du vector store |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-large` | Depot HuggingFace du modele d'embeddings |
| `EMBEDDING_LOCAL_DIR` | `<projet>/models/embedding` | Copie locale du modele : apres le premier lancement, plus aucun appel au Hub |
| `EMBEDDING_CHECK_UPDATES` | `true` | Verification legere du sha du depot au demarrage (quelques Ko) ; mise a jour automatique de la copie locale si le depot a change |
| `EMBEDDING_OFFLINE` | `false` | Coupe tout contact avec le Hub (copie locale requise) |

### Exemple : 100% local avec LM Studio

1. Dans LM Studio : charger le modele voulu (ex. Qwen3 35B quantize), demarrer le
   serveur (onglet *Developer* > *Start Server*) et verifier la fenetre de contexte :
   16384 tokens minimum (le defaut de 4096 tronquerait les prompts enrichis par le RAG),
   davantage si votre build le permet (ex. 256k sur Qwen3 35B).
2. Recuperer l'identifiant exact du modele charge :

```bash
curl http://localhost:1234/v1/models
```

3. Configurer puis lancer :

```bash
export LOCAL_MODEL="<id_exact_affiche_par_lm_studio>"
export LOCAL_BASE_URL="http://localhost:1234/v1"
export DEFAULT_PROFILE="local"
export AGENT_PROFILE="local"

# Optionnel : avec un grand contexte (ex. 256k), autoriser des livrables plus longs
# export LOCAL_MAX_TOKENS="8192"

python main.py analyze -p "Test_Projet" -f ./test/sample_docs/description_systeme.txt
```

> **WSL2** : si le script tourne dans WSL alors que LM Studio tourne sous Windows,
> `localhost` n'atteint pas l'hote sauf si le reseau WSL est en mode `mirrored`.
> Sinon, viser l'IP de l'hote Windows :
>
> ```bash
> export LOCAL_BASE_URL="http://$(ip route show default | awk '{print $3}'):1234/v1"
> ```

**Recherche documentaire de l'Ingenieur** : le RAG est **pre-injecte dans chaque
tache** (profondeur reglable via `RAG_TOP_K`, defaut 8). Les outils agentic
(`search_rag`) sont **desactives par defaut** (`LLM_ENGINEER_TOOLS=false`) : la
boucle d'outils en streaming s'est avere fragile avec certains templates LM Studio
(appels re-emis en texte brut `<tool_call>` au lieu d'appels structures, production
finale = XML). Re-activables via `LLM_ENGINEER_TOOLS=true` (la reflexion
`reflect_on_tool_use=True` est alors appliquee automatiquement). La priorite des
sources de l'Ingenieur est : feedback humain > template/tableau projet (donnees
d'entree ou RAG) > donnees d'entree > structure par defaut > RAG generique.

### Performances en local (eviter les timeouts)

Les prompts cumulent du contexte au fil des etapes (RAG + travaux precedents) et les
generations peuvent etre longues sur un 27-35B local. Le client utilise desormais le
**streaming** (le timeout s'applique entre chunks, pas sur la generation entiere) avec
un timeout de 1800 s par requete et 1 retry.

- **Desactivez ou reduisez le Thinking/Reasoning** : defaut desormais `off` via
  `LLM_REASONING` (reglable aussi dans la GUI, « Niveau de raisonnement »). Le jeton
  correspondant (`<|think_off|>`, `<|think_low|>`, `<|think_medium|>`…) est ajoute au
  message systeme et interprete par le template Jinja du modele (Qwen3.8 : off, low,
  medium, high, xhigh). Gain de plusieurs minutes par requete.
- **Activez Flash Attention et quantifiez le KV cache (q8_0)** au chargement : divise
  la memoire du KV cache par ~2, evite l'offload CPU, accelere le prefill.
- **Calibrez le timeout** avec les stats reelles de LM Studio (tok/s, TTFT) :
  `LLM_TIMEOUT > (tokens du prompt / prefill tok/s) + (max_tokens / generation tok/s)`.
- **Grand contexte = gros KV cache** : ~100 Ko/token en FP16 pour un 27-35B, soit
  ~10 Go pour 100k tokens. Si la VRAM ne suffit pas, LM Studio offload sur CPU et
  tout ralentit.

## Utilisation

### Socle de référence (état de l'art) dans le RAG

Placez vos documents de référence (extraits publics de normes, guides, REX, articles)
dans un sous-dossier du répertoire indexé (ex. `test/references/`) puis ré-indexez :

```bash
python main.py ingest test --reset
```

Les normes complètes sont payantes (AFNOR/IEC) : le web ne donnera que des résumés —
vos documents propriétaires restent la meilleure source. Le nettoyage PDF
(headers/footers) et la fusion des micro-chunks s'appliquent automatiquement.

### Recherche web (état de l'art) — optionnelle

L'orchestrateur peut compléter le RAG d'une recherche web par étape de l'Ingénieur
(les résultats sont injectés comme contexte **NON VÉRIFIÉ**, avec bandeau de réserve).
Désactivé par défaut (les requêtes partent vers l'extérieur) :

- **GUI** : switch « Recherche web » + backend dans *Paramètres avancés*
- **CLI** : `WEB_SEARCH_ENABLED=true`, `WEB_SEARCH_BACKEND=ddg|searxng|tavily`

Pour un usage intensif, préférez un **SearXNG auto-hébergé** (anonymisation) :
`docker run -p 8888:8080 searxng/searxng` avec `json` ajouté à `search.formats`
dans `settings.yml`, puis `SEARXNG_URL=http://localhost:8888`.

### Lancement rapide (interface graphique)

```bash
python gui.py
```

Interface **par onglets** (v1.3.18) : 1 onglet par etape (production repliable,
relecture en cartes de points, qualification inline), tiroirs bas (Journal,
Documents RAG, Sessions, Reglages). Le navigateur s'ouvre sur l'**IP LAN** de la
machine — `localhost:8080` pouvant etre tenu par le serveur llama.cpp ; IP
imposable via `GUI_OPEN_HOST` (ex. `192.168.1.95`), ou `--no-show` pour ne rien
ouvrir.

- **Ajout de documents pendant l'analyse** : tiroir « Documents RAG » — ajoutez
  le document pendant un checkpoint, puis **citez le passage utile dans le
  feedback** ; l'etape suivante et les re-generations le verront via le RAG,
  la provenance est tracee dans le livrable (Bloc 5)
- **Interface precedente** (cartes + popup) : `python gui_classic.py`

Gestion : profil (hybride/cloud/local), liste des modeles LM Studio en direct,
parametres (temperature, max_tokens, tool calling), sessions sauvegardees
(reprise avec changement de modele), statistiques par etape.
et points de controle interactifs (CONTINUER / QUITTER / feedback).

Options : `--port 8090`, `--no-show`, `--reload` (dev).

### 1. Indexer les documents de reference (RAG)

```bash
python main.py ingest ./test/sample_docs
```

Cela indexe tous les fichiers `.txt`, `.md`, `.pdf`, `.docx` du repertoire dans ChromaDB.

Options :
- `--reset` : reinitialise l'index avant ingestion

### 2. Tester la recherche RAG

```bash
python main.py search "procedure de cotation des risques"
```

### 3. Lancer une analyse de risque

```bash
python main.py analyze \
  --project "Systeme_X" \
  --context "Systeme de freinage ferroviaire embarque, environnement exterieur..." \
  --context-file ./test/sample_docs/description_systeme.txt
```

Le workflow :
1. **Cadrage** → Ingenieur + Animateur → Point de controle 1
2. **Filtrage agressions/menaces** → Ingenieur + Client → Point de controle 2
3. **Scenarios de risque** → Ingenieur + Animateur → Point de controle 3
4. **Barrieres** → Ingenieur + Client → Point de controle 4
5. **Livraison** → Secretaire → Document final

A chaque point de controle, le systeme attend votre validation (CONTINUER / QUITTER / feedback).
Un **feedback declenche une re-generation, puis une nouvelle relecture et un nouveau
point de controle sur la meme etape** : vous pouvez iterer autant de fois que necessaire.
Chaque tour repart d'une memoire d'agent purgee, avec la production precedente et tous
vos feedbacks injectes en tete de tache (les relecteurs sont informes de vos demandes).

### 4. Format des sorties

- `output/<projet>_analyse.md` — Document complet (Markdown)
- `output/<projet>_analyse.json` — Metadonnees structurees

## Documents a fournir pour une analyse (dans sample_docs/)

Pour une analyse complete, preparer les documents suivants :

1. **Description du systeme** — texte ou PDF decrivant le systeme, son perimetre, ses fonctions
2. **Analyse fonctionnelle** — fonctions de service, fonctions contraintes, chaines fonctionnelles
3. **Listes d'agressions generiques** — si vous avez des listes specifiques a votre domaine
4. **Listes de menaces generiques** — idem
5. **Template d'analyse** — structure de tableau APR attendue (colonnes, rubriques)
6. **Referentiels / procedures** — procedure Chorus, guides internes SDF, regles de cotation
7. **Analyses anterieures** — exemples d'APR deja validees pour des systemes similaires

### Exemple de document description_systeme.txt

```
Systeme : Sous-station electrique de traction ferroviaire
Perimetre : Alimentation, transformation, distribution 25kV
Phases de vie : Exploitation, Maintenance, Arret d'urgence
Fonctions principales :
  - Alimenter la catenaire en 25kV AC
  - Proteger contre les surcharges et courts-circuits
  - Permettre l'isolement de sections pour maintenance
Interfaces :
  - Reseau electrique national (63kV ou 90kV)
  - Catenaires (25kV)
  - Systeme de teleconduite (SCADA)
  - Voie ferree et installations de quai
Utilisateurs : Agents de conduite, mainteneurs habilites electriques
Environnement : Exterieur, -20C a +45C, pluie, neige, poussiere
```

## Tests

Apres avoir place vos documents dans `test/sample_docs/` :

```bash
# Ingestion
python main.py ingest ./test/sample_docs --reset

# Verification
python main.py search "analyse fonctionnelle"

# Analyse
python main.py analyze -p "Test_Projet" -f ./test/sample_docs/description_systeme.txt
```
