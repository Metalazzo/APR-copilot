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
| `STEP_CONTEXT_LIMIT` | `20000` | Caracteres max par etape precedente reinjectee dans une tache (avant : 6000, causait des pertes de points dans le livrable) |
| `WEB_SEARCH_ENABLED` | `false` | Recherche web orchestree (etat de l'art) injectee dans les taches de l'Ingenieur. Confidentialite : les requetes partent vers l'exterieur |
| `WEB_SEARCH_BACKEND` | `ddg` | `ddg` (sans cle) / `searxng` (auto-heberge, anonymise) / `tavily` (cle cloud) |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Nombre de resultats web injectes par etape |
| `SEARXNG_URL` | — | URL de l'instance SearXNG (`search.formats` avec `json` active) |
| `TAVILY_API_KEY` | — | Cle API Tavily (si backend tavily) |
| `LLM_TIMEOUT` | `600` | Timeout (secondes) par appel LLM — valeur large conseillee en local |
| `LLM_MAX_RETRIES` | `3` | Nombre de tentatives par appel LLM |

**RAG :**

| Variable | Defaut | Description |
|----------|--------|-------------|
| `CHROMA_PERSIST_DIR` | `./chroma_db` | Repertoire du vector store |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-large` | Modele d'embeddings |

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

Ouvre un navigateur sur `http://localhost:8080` : choix du profil (hybride/cloud/local),
liste des modeles LM Studio en direct, parametres (temperature, max_tokens, tool calling),
gestion du RAG (ingestion, statut), lancement d'analyse avec affichage en direct des etapes
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
