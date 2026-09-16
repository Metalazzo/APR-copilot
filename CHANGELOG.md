# Changelog

## [1.3.21] - 2026-09-16

### Corrige — livrable final tronque en pleine ligne de tableau (fin à « 74 »)

Constat sur `Test_Projet_analyse.md` (session du 15/09) : le livrable (272 583
caractères) se coupait en PLEINE ligne de tableau (`| 74` puis rien) — le Bloc
5 « Points ouverts » est sans limite (tous les points à valider d'une analyse
de ~200k caractères de productions) et sa génération a buté sur le plafond
`max_tokens` de LM Studio, sans aucun contrôle de complétude avant
sauvegarde.

- **Détection de troncature par bloc** : dernière ligne de tableau ouverte
  (`|`, non fermée) = génération coupée → **appel de CONTINUATION**
  (max 2) : réécrire en entier la ligne coupée puis poursuivre le bloc — le
  livrable est complet même au-delà du plafond. Stats taggées
  `(continuation n)`.
- **Bloc 5 COMPACT** (format imposé) : tableau serré `Point | Ref (ancre) |
  Décision humaine | Statut` — une ligne courte par point, doublons
  strictement identiques regroupés, lignes toujours terminées par `|`.
- **Vérification de complétude** : les 5 en-têtes `## Bloc` doivent être
  présents et la fin ne doit pas être tronquée ; sinon log + événement
  `livrable_incomplet`.
- **Sauvegarde honnête des partiels** (`output_utils`) : si l'analyse est
  interrompue avant la livraison, le `.md` porte un bandeau
  `⚠️ ANALYSE INCOMPLÈTE (interrompue avant la livraison)` au lieu d'être
  présenté comme le rapport final ; le JSON gagne un flag `complete`.
- **Tokens estimés** : si LM Studio ne remonte pas l'usage (streaming), les
  stats estiment tokens = caractères / 3.5, marquées `usage_estime: true` —
  tok/s et coûts visibles même sans usage rapporté.
- **`--force-delivery`** (CLI) / `LIVRAISON_FORCE=true` (env) : à la reprise,
  re-générer la LIVRAISON seule (les étapes validées restent conservées) —
  corrige un livrable tronqué sans refaire les 10 h de génération.

### Rollback
- Etat pre-modification : commit `6647478`

## [1.3.20] - 2026-09-16

### Corrige — points de relecture « qui ne renvoient a rien » (ancrage de bout en bout)

Cote productions (l'ancrage devient possible) :
- **Filtrage** : chaque item porte sa **Ref** — le numero de l'item generique
  evalue (1 a 42) ; une ligne sans Ref n'est plus livrable
- **Barrieres** : chaque barriere porte un **ID** `B-RISK-XXX-nn` rattache a
  son scenario
- Scenarios (RISK-xxx) et cadrage (sections numerotees) : deja ancrees

Cote relecteurs :
- `quality.md`/`client.md` : Localisation = **ID de ligne precis obligatoire**
  (jamais « le tableau ») ; Extrait = copie exacte ; pour une **ABSENCE**
  (barriere manquante…), citer la ligne ou elle devrait figurer avec
  `ABSENCE :` ; **un point non ancrable n'est pas un point exploitable**

Cote GUI (verification objective de l'ancrage) :
- **⚠ non ancré** : point sans Localisation ET sans Extrait — le rate est du
  relecteur, pas du lecteur
- **⚠ extrait introuvable** : l'extrait cite ne figure pas (en normalise) dans
  la production affichee — le relecteur a reformule/invente : juger avec
  prudence
- **Le feedback repart ANCRE** : `- [A CORRIGER] [P3] Titre — Localisation :
  filtrage, item 33 — Extrait : « … » — detail : …` — la re-generation de
  l'Ingenieur est auto-suffisante (elle ne depend plus de retrouver P3)

### Ameliore — RAG : normalisation francaise + HYBRID_ALPHA + dedup

- **BM25 normalise** : accents plies + stop-words francais (index ET requete,
  meme tokenisation) — « dégazage » matche desormais « degazage » ; les chunks
  BM25 gardent leurs **vraies metadonnees** (bug corrige : la source reelle du
  fichier etait remplacee par un faux `source=bm25`, perdant la provenance)
- **`HYBRID_ALPHA` reellement branche** sur les poids RRF (0.5/0.5 etait en dur)
- **Dedup des voisins adjacents** du meme fichier (±1 chunk) apres la fusion —
  diversifie le contexte injecte (`RAG_DEDUP_ADJACENT=true`, defaut actif)
- Chunks de score nul ecartes du top_k

### En file (roadmap, option B)
- Re-ranker local `BAAI/bge-reranker-v2-m3` (`RAG_RERANK=true`, defaut off) —
  cf. README « En file ».

### Rollback
- Etat pre-modification : commit `56dbed3`

## [1.3.19] - 2026-09-11

### Corrige — doublons dans la relecture (nouvelle interface)

Cause racine : le relecteur etait instruit pour lister les points en DEUX
sections (« Points » + « Points a faire valider par l'humain ») et repetait le
meme point ; les 4 consignes d'etape imposaient en plus un format libre a
puces contradictoire avec le format canonique — le parseur combiné v1.3.16.1
rendait ces repetitions visibles comme doublons.

- **Prompts relecteurs** (`quality.md`, `client.md`) : UNE SEULE section
  « Points » — chaque point n'apparait qu'UNE FOIS, pas de puce libre hors
  blocs, champ optionnel « Pour l'humain : oui | non »
- **4 consignes d'etape** (`reviewer_task`) reecrites : alignement sur le
  format canonique [P#] (fin des listes libres « Reponds avec : ... »)
- **Deduplication robuste dans la GUI** (filet de secours) : normalisation
  (minuscules/sans accents) + match par prefixe ; un bloc ecrase la puce
  equivalente, un bloc dont l'ID est deja vu est ignore, puces dedoublonnees ;
  prefixe d'ID (« P1 Titre... ») retire des puces avant comparaison
- **Dedup entre iterations** : un point « nouveau » dont le texte normalise
  correspond a un point deja qualifie (renumerotation P1 -> P7) est traité
  comme re-signale — non re-soumis a l'humain
- **Garde-fous** : le rendu consultation n'ecrase plus une qualification en
  attente ; la checklist est videe a chaque rendu interactif

### Modifie — deja qualifies replies (gain de place verticale)

- La section « Deja qualifies (iterations precedentes) » est desormais une
  **zone repliée** : cliquer pour developper — elle n'occupe plus de place
  par defaut.

### Ajoute — regroupement des scenarios (l'Ingenieur « malin »)

- Tache de l'etape 3 : **REGROUPEMENT AVANT LIVRAISON** — une ligne par
  triplet (fonction x agression/menace x evenement redoute), causes multiples
  regroupees dans la colonne Causes, fusion des scenarios tres similaires en
  gardant la description la plus complete, fusions tracees (« RISK-005 =
  fusion de RISK-003 + RISK-008 »), zero doublon fonctionnel avant livraison.
  Aucune fusion d'etapes differentes par phase de vie/gravite (granularite
  preservee).
- `prompts/engineer.md` etape 4 : meme principe en dur.
- Relecture scénarios : detection des doublons FONCTIONNELS (meme triplet
  fonction + evenement redoute = doublon quelles que soient les formulations,
  fusion proposee en citant les deux ID).

### Rollback
- Etat pre-modification : commit `249e77b`

## [1.3.18] - 2026-09-11

### Ajoute — GUI par onglets (maquette A), documents a chaud, ouverture sur l'IP LAN

**Interface classique archivee** : `gui_classic.py` (copie figee et autonome de
v1.3.17, memes capacites backend) — rollback permanent, lancement :
`python gui_classic.py`.

**GUI par onglets (maquette A)** dans `gui.py` :
- 1 onglet par etape (Cadrage / Filtrage / Scenarios / Barrieres / Livraison),
  auto-selectionne a chaque step_start
- Production repliable + plein ecran (overlay lecture 97vw)
- Relecture affichee en CARTES de points dans l'onglet : badge de verdict
  (BLOQUANT/IMPORTANT/MINEUR), extrait verbatim en citation, localisation,
  justification, correction proposee, toggles de qualification + detail
- Qualification inline : **plus de popup de checkpoint** — production au-dessus,
  points en dessous, decision dans le meme onglet ; les points deja qualifies
  (iterations precedentes) restent en lecture seule, non re-soumis
- Barre de decision masquee hors checkpoint ; bouton « Voir la relecture » pour
  la consultation d'une etape passee

**Tiroirs bas** : Journal · Documents RAG · Sessions sauvegardees · Reglages &
lancement (tous repliables).

**Documents a chaud pendant l'analyse** :
- le tiroir Documents RAG reste utilisable pendant un checkpoint : ingestion
  thread separe, embeddings locaux, invalidation BM25
- reset de l'index **interdit pendant l'analyse** (garde-fou)
- chaque ajout est trace dans la session (`state.added_docs`) : l'encart de
  qualification rappelle « citez le passage utile dans votre feedback », le
  feedback envoye mentionne les documents ajoutes, et le Bloc 5 du livrable
  trace leur provenance

**Ouverture navigateur sur l'IP LAN** : detection automatique (socket route +
repli hostname) — le popup 'chat' du serveur llama.cpp sur localhost:8080 est
desormais evite ; surcharge via `GUI_OPEN_HOST`, desactivation via `--no-show`
(l'URL effective est affichee en console).

## [1.3.17] - 2026-09-11

### Ajoute — sessions sauvegardables, reprise et statistiques

**Sessions** : chaque analyse cree `output/sessions/<horodatage>/state.json`,
ecrit apres chaque production, relecture et decision de checkpoint. Quitter =
mise en pause reprenable ; un crash ne perd que l'etape en cours.

- **Reprise** : panneau « Sessions » dans la GUI (Reprendre / Supprimer) ou
  `python main.py analyze -p <projet> --resume <dossier>` — les etapes validees
  sont conservees en l'etat, la reanalyse demarre a la premiere etape non
  validee (l'etat d'une etape interrompue en cours de generation est perdu :
  elle repart de zero, les etapes etant auto-contenues).
- **Changement de modele entre etapes** : a la reprise, les clients sont
  reconstruits avec les reglages actuels — la suite de l'analyse s'execute sur
  le modele choisi au relancement, les etapes validees gardent leur modele
  producteur (traçabilite `models_used` par etape).
- **Traçabilite v1.3.16 preservee** : qualifications humaines par point
  (point_decisions) restaurees a la reprise — la table du Bloc 5 du livrable
  reste complete.

**Statistiques de generation** (collecte au tunnel unique `_ask_agent`, usage
OpenAI standard — LM Studio comme API cloud) :
- par appel : tokens prompt/completion, duree reelle, vitesse tok/s, modele
  producteur (etiquette `cloud:<model>` / `local:<model>`), etape/bloc/agent/
  iteration
- par phase : temps de generation de l'etape (production + relectures + toutes
  iterations), appels, tokens, modeles
- temps total de generation cumule ; duree ecoulee hors pauses (les attentes
  aux checkpoints sont soustraites) ; horodatages debut/fin de session
- ETA des etapes restantes (moyenne glissante) affichee des l'etape 2
- cout estime en EUR (optionnel, `CLOUD_PRICE_INPUT`/`CLOUD_PRICE_OUTPUT`,
  applique aux appels du profil cloud uniquement)
- affichage : evenement `step_stats` → carte d'etape + journal ; encart
  « Performance de l'etape » dans la popup de checkpoint ; recapitulatif CLI ;
  export `stats.md` + `stats.json` dans la session
- cumul a la reprise : les stats des etapes conservees persistent →
  comparaison inter-modeles au sein d'une meme analyse (ex. Qwen 20 tok/s vs
  Mistral 80 tok/s, phase par phase)

## [1.3.16.1] - 2026-09-10

### Corrige — points de relecture manquants (18 listes, 15 qualifiables)

- **Parseur combiné** : les blocs canoniques `### [P#]` ET les puces/numerations
  hors-blocs sont desormais collectes ensemble, en ordre du document — v1.3.16
  ignorait les puces des que des blocs canoniques existaient (relecteur melant
  les formats : 3 points sur 18 absents de la qualification).
- **Plafond 12 -> 100 points** : l'ancien plafond tronquait silencieusement
  les relectures a plus de 12 points.
- **Tolerances de format** : `###`/`####`, `[P#]`/`P#`/« P 1 », champs en gras
  (`**Extrait** :`), extrait renvoye a la ligne suivante, « Correction
  proposee : » reconnue comme champ de bloc.
- **Visibilite** : journal « N point(s) detecte(s) (X canonique(s), Y en
  repli) » + compteur dans la popup — plus de perte silencieuse.
- Hauteur de la zone de qualification augmentee (28vh -> 34vh).

## [1.3.16] - 2026-09-09

### Ameliore — relecture ancree et lisible (points de controle humains)

Le probleme : les points de la relecture etaient des fragments de phrases
decontextualises (lignes a puces melangees, tronquees a 200 caracteres), sans
lien avec la production, et le feedback reparti vers l'Ingenieur perdait toute
ancre.

- **Format canonique des points** (`prompts/quality.md`, `prompts/client.md`) :
  chaque point est un bloc structure obligatoire avec identifiant [P#],
  Localisation (section + ID de ligne), Extrait VERBATIM du passage concerne,
  Verdict (BLOQUANT/IMPORTANT/MINEUR), Justification, Correction proposee.
- **Rappel du format** dans les 4 consignes de relecture (orchestrator), avec
  regle « tous les points sont ancres et cites verbatim — jamais de paraphrase
  hors contexte ».
- **GUI enrichie** : parseur des blocs structurees (repli sur l'ancien
  heuristique si le modele ne suit pas le format) ; chaque point qualifiable
  affiche badge de verdict, titre complet, extrait en citation et localisation ;
  le feedback compose conserve les identifiants [P#].
- **Traçabilité inter-iterations** : les qualifications humaines par point sont
  enregistrees dans l'etat (persistant entre les iterations d'une etape) ;
  les relectures suivantes recovent les points deja decides (IDs stables,
  mention PERSISTE) ; un point deja qualifie n'est **jamais re-soumis** a
  l'humain (affiche en lecture seule dans la popup, repris tel quel).
- **Trace dans le rapport final** : Bloc 5 inclut la table de traçabilite des
  points de controle (etape | point | decision | detail | version) pour
  remonter au « pourquoi » de chaque conclusion.

### Rollback
- Etat pre-modification : commit `8d30538`

## [1.3.15] - 2026-09-06

### Corrige — livraison amputee (Bloc 4/5 absents du livrable)
- **Cause** : la livraison etait un appel unique demande d'assembler ~218 000
  caracteres de productions — impossible dans le plafond de sortie, la
  generation s'arretait apres le Bloc 3 (Bloc 4 « Plan de traitement » et
  Bloc 5 disparus), et le modele « compensait » en resumant.
- **Livraison PAR BLOCS** : 5 appels focalises (Bloc 1 resume/RACI, Bloc 2
  filtrage, Bloc 3 tableaux APR, Bloc 4 plan de traitement + decisions
  d'acceptation, Bloc 5 points ouverts), chacun avec ses sections sources
  dediees et la consigne « reprends TOUTES les lignes, ne resume pas » ;
  sorties concatenees dans le livrable. Evenement GUI « Livraison bloc N/5 ».

### Corrige — badge etape fige sur « Checkpoint en attente »
- La validation par qualification (SANS CORRECTION) ne mettait pas a jour le
  badge (seuls CONTINUER/QUITTER etaient geres). Nouveau badge « Validee
  (decisions tracees) ».

### Ameliore — axe menaces EMISES (le systeme source de danger)
- `menaces_generiques.txt` : nouvelle section « Agressions EMISES par le
  systeme vers son environnement » (items 33-42 : degazage/emanations
  chaudes, echauffement emis, fuite, explosion/ejection, danger electrique
  par contact, CEM emis, incendie propage, bruit, rayonnement, rejets) avec
  l'exemple batterie -> degazement -> menace thermique vers l'environnement.
- Tache filtrage + engineer.md : analyse BIDIRECTIONNELLE (agressions recues
  ET menaces emises) ; vocabulaire « agression reçue » / « menace emise ».

### Modifie
- `LOCAL_MAX_TOKENS` 24576 -> **32768** (~105k caracteres de sortie ; verifie
  dans le budget 153 856 tokens charges).

### Rollback
- Etat pre-modification : commit `d6a06d5`

## [1.3.14] - 2026-09-05

### Ajout — gestion automatique du contexte (modele local)
- Detection du contexte **reellement charge** du modele local au demarrage de
  chaque analyse (API LM Studio `/api/v0/models`, une requete de quelques Ko,
  mise en cache par analyse).
- **Calcul automatique de la limite d'injection par etape** :
  `(ctx - LOCAL_MAX_TOKENS - overhead 15k) x securite 0,9` tokens, convertis en
  caracteres (~3,5 car./token) et divises par le nombre d'etapes precedentes.
  Ex. avec 153 856 tokens charges : ~90 000 caracteres par etape.
- Repli automatique sur `STEP_CONTEXT_LIMIT` (40 000) si la detection echoue
  (serveur non-LM Studio, hors ligne, cloud pur) ; switch GUI « Contexte
  automatique » (defaut ON) et `CONTEXT_AUTO` pour forcer le manuel.
- Log/GUI : « [contexte] auto : 153856 tokens charges -> limite 90000
  caracteres par etape precedente ».

## [1.3.13] - 2026-09-05

### Ajout — embeddings locaux (zero reseau au demarrage)
- Le modele d'embeddings est **copie localement** dans
  `<projet>/models/embedding/` au premier lancement (sha de la version
  enregistre dans `_meta.json`).
- Lancements suivants : chargement **sans aucun appel au Hub** + verification
  legere du sha distant (API publique, quelques Ko via urllib — plus de
  warning « unauthenticated requests »). Si le depot a change : mise a jour
  automatique de la copie locale.
- `EMBEDDING_OFFLINE=true` coupe tout contact (copie locale requise) ;
  `EMBEDDING_CHECK_UPDATES=false` desactive la verification.
- `models/` ajoute au .gitignore.

### Modifie
- `LOCAL_MAX_TOKENS` 16384 -> **24576** : une production d'Ingenieur de
  51033 caracteres correspondait exactement a l'ancien plafond de 16384
  tokens (~52k caracteres) — generation probablement coupee avant sa fin.
  Verifie : 4 x 60000 car. d'injection + 24576 tokens de sortie restent
  sous les 102912 tokens de contexte charges.

### Rollback
- Etat pre-modification : commit `81d1d3e`

## [1.3.12] - 2026-09-05

### Ameliore — semantique explicite de la check-list de checkpoint
- Les toggles OK/KO ambigus sont remplaces par une qualification a 3 etats
  explicites par point de la relecture : **« A corriger »** (le probleme est
  reel, declenche une re-generation), **« Sans objet »** et **« Deja traite »**
  (pas de correction, decisions tracees dans le livrable).
- **Lien versions V1…Vn** : le titre de la popup affiche la version validee
  (« version V1 », V2 apres re-generation…), et le feedback compose est
  pre-fixe « (version Vn) » pour tracer quelle production est jugee.
- Nouvelle branche orchestrateur : un feedback commencant par
  « SANS CORRECTION » valide l'etape et trace les decisions **sans
  re-generation** (cas ou l'humain qualifie tous les points de la relecture
  en Sans objet / Deja traite).
- Composition du feedback : [A CORRIGER] / [SANS OBJET] / [DEJA TRAITE] +
  detail + commentaire global, avec version.

## [1.3.11] - 2026-09-05

### Ameliore — GUI : popup de checkpoint plus pratique
- **Popup grande et redimensionnable** : 1650x90vh par defaut avec poignee de
  redimensionnement (CSS resize), bouton **plein ecran/restaurer**, zones
  production/relecture elargies.
- **Check-list OK/KO par point** : les points listes dans la relecture (lignes a
  puces/numerotees) deviennent des lignes cochables avec champ detail. L'envoi
  compose un feedback structure « POINTS DE CONTROLE HUMAIN : - [KO] point —
  detail / - [OK] point / Commentaire global » injecte comme prioritaire a la
  re-generation. Sans KO ni commentaire -> conseil d'utiliser CONTINUER (evite
  une re-generation inutile).
- **Feedback global en zone multiligne** (textarea) au lieu d'une ligne.
- **Decisions tracees dans le livrable** (aligne Clusif §4.6) : les feedbacks
  cumules de chaque etape sont enregistres et transmis au Secretaire avec
  consigne d'ajouter dans Bloc 4/Bloc 5 une colonne « Decision humaine »
  (OK/KO + detail) par point a valider.

## [1.3.10] - 2026-09-02

### Ajout — alignement referentiel Clusif / ISO 27005
- Etape barrieres : les 4 options de traitement ISO 27005 (REDUCTION /
  MAINTIEN / REFUS / PARTAGE) + risque RESIDUEL estime apres barrieres.
- Livraison : 5 sections — Bloc 1 avec tableau RACI de la demarche (A =
  proprietaire des risques/validateur humain, R = Ingenieur, C = Qualite +
  Client, I = Secretaire) ; nouveau Bloc 4 « Plan de traitement et decisions
  d'acceptation » ; Bloc 5 = points ouverts.
- secretary.md / agents.py : 5 sections standard ; engineer.md : Etape 6 =
  barrieres + traitement + risque residuel ; quality.md : check-list
  conformite Clusif/ISO 27005.
- RAG : guide Clusif « Analyse de risques en pratique » indexe
  (test/references/, 310 chunks).

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
