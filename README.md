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
- **LLM** : Mistral Large (deploiement local, API compatible OpenAI)
- **RAG** : ChromaDB + sentence-transformers + BM25 (retrieval hybride)
- **Formats supportes** : `.txt`, `.md`, `.pdf`, `.docx`

## Pre-requis

```bash
pip install -r requirements.txt
```

### Configuration

Variables d'environnement (ou modifier `config.py`) :

| Variable | Defaut | Description |
|----------|--------|-------------|
| `MISTRAL_MODEL` | `mistral-large` | Nom du modele |
| `MISTRAL_BASE_URL` | `http://localhost:8080/v1` | URL de l'API Mistral |
| `MISTRAL_API_KEY` | `not-needed` | Cle API (souvent inutile en local) |
| `CHROMA_PERSIST_DIR` | `./chroma_db` | Repertoire du vector store |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-large` | Modele d'embeddings |

## Utilisation

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
