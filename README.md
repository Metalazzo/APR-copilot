# APR Copilot — Analyse Préliminaire de Risque multi-agents

Système multi-agents d'assistance à l'**Analyse Préliminaire de Risque (APR)** en
contexte **SDF / RAMS** (Sûreté de Fonctionnement, Safety), tournant **en local**
(LM Studio) ou sur un cloud au choix — avec un point de contrôle humain à chaque
étape clé, un RAG hybride français, des sessions reprenables et un livrable
traçable.

> **Philosophie** : l'outil ne remplace pas l'expert métier. Il produit de façon
> fiable, traçable et structurée les ~95 % du travail répétitif (cadrage,
> filtrage, scénarios, barrières, mise en forme), pour laisser à l'humain les
> arbitrages, validations et enrichissements à forte valeur ajoutée.

---

## Ce que ce n'est pas

- ❌ Une simulation physique ou un calcul de fiabilité avancé (FMEA quantifiée, arbres de défaillance…)
- ❌ Un substitut à un expert SDF qualifié : le livrable est un **support de décision**, jamais une analyse validée
- ❌ Une source de vérité normative : les référentiels officiels (AFNOR/IEC/CLUSIF) restent vos documents de référence, à fournir dans le RAG

## Comment ça marche

5 agents (AutoGen) enchaînés, **avec un point de contrôle humain entre chaque étape** :

| Agent | Rôle |
|-------|------|
| **Orchestrateur** | Coordination, distribution des tâches, checkpoints humains, sessions |
| **Ingénieur Technique SDF** | Le cœur de l'analyse : cadrage → filtrage agressions/menaces → scénarios → barrières |
| **Animateur Qualité** | Contrôle de cohérence, traçabilité, conformité template (relit **chaque étape**) |
| **Représentant Client** | Regard usage/intégration : réalisme des barrières, angles morts |
| **Secrétaire** | Mise en forme du livrable final en 5 blocs (Markdown/JSON) |

```
Cadrage → Filtrage → Scénarios → Barrières → Livraison
   ↓ CP1      ↓ CP2       ↓ CP3         ↓ CP4
 CONTINUER / QUITTER / feedback structuré (À corriger · Sans objet · Déjà traité)
```

Un **feedback humain déclenche une re-génération + une nouvelle relecture** sur la
même étape — vous itérez autant de fois que nécessaire. Le livrable final est
assemblé en 5 blocs (résumé exécutif + RACI, filtrage, tableaux APR, plan de
traitement, points ouverts) avec la **traçabilité complète des décisions**.

## Fonctionnalités

- 🔀 **Hybride local / cloud** : chaque étape peut tourner sur un modèle local
  (LM Studio) ou une API cloud compatible OpenAI — et **on peut changer de modèle
  entre les étapes** via la reprise de session
- 📚 **RAG hybride français** : embeddings `multilingual-e5-large` + **BM25
  normalisé** (accents pliés, stop-words FR) fusionnés par RRF — ChromaDB 100 %
  local, jamais pollué par le web
- ✅ **Checkpoints ancrés** : chaque point de relecture porte un ID `[P#]`, sa
  **localisation précise** et un **extrait verbatim** de la production ; vos
  qualifications (À corriger / Sans objet / Déjà traité) sont **tracées dans le
  livrable final** (Bloc 5) et **jamais re-soumis** aux itérations suivantes
- 💾 **Sessions reprenables** : l'état complet est sauvegardé après chaque
  production/relecture/décision ; QUITTER = pause reprenable ; changement de
  modèle entre étapes à la reprise (`--force-delivery` pour re-générer le
  livrable seul)
- 📊 **Statistiques de génération** : tokens, durées, tok/s par étape/agent,
  ETA des étapes restantes, coût estimé — exportées en `stats.md`/`stats.json`
- 🖥️ **GUI par onglets** (NiceGUI) : une étape = un onglet, qualification inline,
  production repliable/plein écran, tiroirs (Journal, Documents RAG, Sessions,
  Réglages) — ajout de documents **pendant l'analyse**
- 🔍 **Recherche web optionnelle** (DuckDuckGo / SearXNG auto-hébergé / Tavily),
  injectée comme « état de l'art NON VÉRIFIÉ » — jamais indexée dans le RAG
- ⚡ **Gestion auto du contexte** : détection du contexte chargé de LM Studio et
  répartition des injections par étape

## Démarrage rapide

**Prérequis** : Python 3.10+ · un serveur local **compatible OpenAI** ([LM Studio](https://lmstudio.ai/), llama.cpp, koboldcpp, Ollama, vLLM…) avec un modèle chargé, ou une clé API cloud · les documents de votre projet.

```bash
pip install -r requirements.txt

# Indexer vos documents de référence (RAG)
python main.py ingest ./sample_docs

# Lancer l'interface graphique
python gui.py
```

Dans la GUI (tiroir **Réglages**) : profil `local`/`cloud`/`hybrid`, modèles du
serveur local détectés en direct (endpoint OpenAI standard), lancement. Ou en
CLI :

```bash
python main.py analyze \
  -p "Mon_Projet" \
  -f ./test/exemple/description_exemple.txt

python main.py sessions          # lister les sessions reprenables
```

### Modèle local (LM Studio)

Charger le modèle, démarrer le serveur (*Developer → Start Server*), viser un
contexte chargé **largement au-delà de 16k tokens** (les prompts cumulent le RAG
et les travaux précédents) :

```bash
curl http://localhost:1234/v1/models   # identifiant exact du modèle chargé

export LOCAL_MODEL="<id_exact_lm_studio>"
export AGENT_PROFILE="local"
python main.py analyze -p "Mon_Projet" -f ./test/exemple/description_exemple.txt
```

> **WSL2** : si le script tourne dans WSL et LM Studio sous Windows, viser l'IP
> de l'hôte Windows : `LOCAL_BASE_URL="http://$(ip route show default | awk '{print $3}'):1234/v1"`
> (ou réseau WSL en mode `mirrored`).

## Moteurs locaux compatibles

Tout serveur **compatible OpenAI** fonctionne via `LOCAL_BASE_URL`. La
détection automatique du contexte chargé couvre **LM Studio / llama.cpp /
koboldcpp** ; pour les autres, déclarez `LOCAL_CONTEXT_TOKENS` :

| Moteur | Démarrage type | `LOCAL_BASE_URL` | Contexte |
|---|---|---|---|
| **LM Studio** | GUI (*Developer → Start Server*), port 1234 | `http://localhost:1234/v1` | auto-détecté |
| **llama.cpp** | `llama-server -hf <modèle> --port 8080 -c 131072 --flash-attn` | `http://localhost:8080/v1` | auto-détecté (`/props`) — ⚠️ port 8080 par défaut : conflit possible avec la GUI → GUI sur `--port 8090` |
| **koboldcpp** | `koboldcpp --model <fichier.gguf> --contextsize 131072 --port 5001` | `http://localhost:5001/v1` | auto-détecté (contexte de l'UI/CLI) |
| **Ollama / vLLM** | `ollama serve` / `vllm serve <modèle>` | `:11434/v1` / `:8000/v1` | déclarez `LOCAL_CONTEXT_TOKENS=131072` |
| n'importe lequel | — | — | `LOCAL_CONTEXT_TOKENS=<tokens chargés>` (prioritaire) |

Notes :
- **Clé API locale** : n'importe quelle valeur (« not-needed »)
- **Tool calling** : désactivé par défaut (`LLM_ENGINEER_TOOLS=false`) ;
  llama.cpp → `--jinja` si activé ; koboldcpp → `LLM_FUNCTION_CALLING=false`
- **`LLM_REASONING`** : les jetons `<|think_*>` sont interprétés par le
  **template côté serveur** (LM Studio + template custom) ; sur llama.cpp /
  koboldcpp ils restent inertes — désactivez le thinking dans le moteur si le
  modèle en a un

**Performances local** : désactivez le thinking (`LLM_REASONING=off`, gain de
plusieurs minutes/requête), activez Flash Attention + KV cache `q8_0` dans LM
Studio, calibrez `LLM_TIMEOUT` avec les stats réelles. Un MoE (ex. Qwen3.8-27B
GSQ-RCO quantizé) divise le temps de génération pour un contexte identique.

## Configuration

Variables d'environnement (ou champs de la GUI) — table complète dans
[`config.py`](config.py) :

<details><summary><strong>Modèles & affectation</strong></summary>

| Variable | Défaut | Description |
|----------|--------|-------------|
| `CLOUD_MODEL` / `CLOUD_BASE_URL` / `CLOUD_API_KEY` | DeepSeek | Profil cloud (Ingenieur/Qualité/Client en hybride). Toute API compatible OpenAI — voir « Confidentialité » |
| `LOCAL_MODEL` / `LOCAL_BASE_URL` / `LOCAL_API_KEY` | LM Studio | Modèle local (Secrétaire en hybride, tous agents en `local`) |
| `LOCAL_MAX_TOKENS` | `32768` | Plafond de génération par appel |
| `AGENT_PROFILE` | `hybrid` | `hybrid` (cloud = Ingénieur/Qualité/Client, local = Secrétaire) · `cloud` · `local` — surcharge par `--profile` |
| `DEFAULT_PROFILE` | `cloud` | Profil par défaut |
| `LLM_FUNCTION_CALLING` | `true` | `false` si le serveur ne supporte pas le tool calling |
| `LLM_ENGINEER_TOOLS` | `false` | Outils agentic de l'Ingénieur (le RAG est pré-injecté) |
| `LLM_REASONING` | `off` | Niveau de raisonnement : `off`/`low`/`medium`/`high`/`xhigh` |
| `LLM_TIMEOUT` / `LLM_MAX_RETRIES` | `1800` / `1` | Timeout par appel (streaming : s'applique entre chunks) et tentatives |

</details>

<details><summary><strong>RAG</strong></summary>

| Variable | Défaut | Description |
|----------|--------|-------------|
| `CHROMA_PERSIST_DIR` | `./chroma_db` | Répertoire du vector store |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-large` | Dépôt HuggingFace des embeddings |
| `EMBEDDING_LOCAL_DIR` | `models/embedding` | Copie locale du modèle : après le premier lancement, **plus aucun appel au Hub** |
| `EMBEDDING_OFFLINE` | `false` | Coupe tout contact avec le Hub |
| `HYBRID_ALPHA` | `0.5` | Poids RRF sémantique vs lexical (1.0 = sémantique seul) |
| `RAG_DEDUP_ADJACENT` | `true` | Écarter les chunks voisins du même fichier (diversité du contexte) |
| `CONTEXT_AUTO` | `true` | Détection du contexte chargé (LM Studio) → limite d'injection auto |
| `STEP_CONTEXT_LIMIT` | `40000` | Repli manuel (caractères par étape précédente) |

</details>

<details><summary><strong>Recherche web (optionnelle, déconseillée en confidentialité stricte)</strong></summary>

| Variable | Défaut | Description |
|----------|--------|-------------|
| `WEB_SEARCH_ENABLED` | `false` | Recherche web orchestree (les requêtes partent vers l'extérieur) |
| `WEB_SEARCH_BACKEND` | `ddg` | `ddg` (sans clé) / `searxng` (auto-hébergé, anonymisé) / `tavily` |
| `SEARXNG_URL` / `TAVILY_API_KEY` | — | Credentiels des backends |

Les résultats sont injectés comme contexte **NON VÉRIFIÉ** et ne sont **jamais
indexés** dans le RAG (l'index local reste référentiel de confiance). Pour
conserver une info web : copiez la page en local puis ingérez-la via la GUI.

</details>

<details><summary><strong>Sessions, stats & livrable</strong></summary>

| Variable / commande | Description |
|----------|-------------|
| `output/sessions/<id>/state.json` | État complet sauvegardé après chaque production, relecture et décision |
| `python main.py sessions` | Lister les sessions + commande de reprise prête à copier |
| `--resume <dossier>` | Reprendre : étapes validées conservées, suite sur le modèle actuel |
| `--force-delivery` | Re-générer la LIVRAISON seule (livrable tronqué) |
| `LIVRAISON_FORCE=true` | Idem en GUI (switch dans le tiroir Sessions) |
| `CLOUD_PRICE_INPUT/OUTPUT` | Prix €/M tokens (profil cloud) → coût estimé dans les stats |
| Si un bloc est coupé par le plafond de génération | **Continuation automatique** jusqu'à complétion + vérification de complétude du livrable |

</details>

## Confidentialité & souveraineté

- **100 % local possible** : LM Studio + embeddings en copie locale
  (`EMBEDDING_OFFLINE=true` coupe tout contact avec HuggingFace après le
  premier téléchargement) — zéro donnée sortante
- **Recherche web désactivée par défaut** ; si activée, préférez un
  **SearXNG auto-hébergé** (`docker run -p 8888:8080 searxng/searxng`, `json`
  ajouté à `search.formats`)
- **Si cloud** : le projet fonctionne avec toute API compatible OpenAI — pour
  une donnée industrielle, privilégiez un fournisseur **hébergé en Europe**
  (Mistral AI 🇫🇷, Scaleway, OVHcloud…) ; les passerelles grand public
  (OpenRouter, Zen…) sont hébergées aux USA (CLOUD Act)
- Le canal web n'est **jamais indexé** dans le RAG : votre index reste
  référentiel de confiance

## Avertissement

Outil d'**assistance** à l'analyse de risque : le livrable produit est un
support de décision destiné à être revu, complété et validé par un expert SDF
qualifié. Les référentiels complets (AFNOR, IEC, CLUSIF…) sont payants et
doivent être apportés par vos soins dans le RAG. Aucune garantie d'exhaustivité
ou d'exactitude des générations LLM.

## Roadmap

- Re-ranker local `BAAI/bge-reranker-v2-m3` (`RAG_RERANK`, ~2 Go, zéro réseau)
- Défauts cloud orientés UE (Mistral) au lieu de DeepSeek

## Licence

[MIT](LICENSE) — © 2026 Metalazzo.

**Crédits** : idée, cahier des charges et direction humaine — l'auteur ;
implémentation développée avec l'assistance d'agents LLM (GLM 5.3, DeepSeek
V4) sous pilotage humain.

L'historique complet des versions : [CHANGELOG.md](CHANGELOG.md) ·
l'ancienne interface (cartes + popup) reste disponible : `python gui_classic.py`
