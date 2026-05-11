# Note technique : briques fonctionnelles d'une plateforme d'agents IA

**Date** : 2026-05-11
**Audience** : architectes, tech leads, contributeurs LCNC A2A
**Objet** : passer en revue, brique par brique, ce qu'on attend en 2026 d'une plateforme d'agents IA. Sert de référence indépendante du projet pour cadrer le backlog et les choix d'architecture.

Pour chaque brique : définition, attentes (état de l'art mai 2026), variantes courantes, pièges récurrents, technos notables (3 à 5 références retenues, liste non exhaustive).

---

## 1. Couche LLM (provider abstraction)

**Définition** : couche qui isole l'agent du modèle sous-jacent.

**Attentes** :
* Multi-provider (OpenAI, Anthropic, Google, Mistral, Cohere, vLLM, Ollama, OpenRouter…) derrière une interface unique.
* Streaming token par token, avec heartbeat pour endpoints lents.
* Retour structuré : `content`, `reasoning`, `tool_calls`, `tokens_in/out`, `cost_usd`, `finish_reason`, `request_id`.
* Routing dynamique : choix du modèle par coût, latence, fiabilité, capacité (tool use, long context, vision).
* Fallback automatique en cas de 5xx ou rate-limit, idéalement vers un modèle dégradé du même provider, puis cross-provider.
* Headers customs (auth IBM watsonx, GCP project, Azure deployment).

**Pièges** :
* Tools "OpenAI format" pas universels : Anthropic et Google ont leurs variantes.
* Coût par token mal traçable sur les endpoints custom (vLLM, Ollama).
* Reasoning interne (Claude thinking, o1/o3) à exposer comme événement séparé, jamais comme contenu final.

**Technos notables** : LiteLLM (gateway open source, ajout enterprise SSO/RBAC/budgets team-level en 2026), OpenRouter (SaaS), Portkey (gateway + cache + budget guards), Helicone (gateway proxy + observability), LangChain ChatModels (abstraction code-side).

---

## 2. Tool use et function calling

**Définition** : capacité de l'agent à appeler des fonctions externes (APIs, scripts, MCP servers).

**Attentes** :
* **MCP** (Model Context Protocol) comme standard de fait pour exposer des outils à un LLM, indépendamment du provider.
* Schéma JSON Schema des arguments, validation côté agent avant appel.
* Retries avec backoff exponentiel, idempotency keys quand applicable.
* Hints d'annotation (`destructiveHint`, `idempotentHint`, `readOnlyHint`) qui drivent l'auto-confirmation ou la pause HITL.
* Timeouts configurables par tool, kill du process zombie pour stdio.
* Découverte dynamique des tools (capabilities négociées au handshake).
* Sandboxing : sub-process pour stdio, network policies pour HTTP.

**Transport MCP : SSE est déprécié, viser streamable HTTP** :
* La spec MCP **2025-03-26** a déprécié SSE au profit de **streamable HTTP** : un seul endpoint HTTP qui accepte POST et qui peut renvoyer du JSON simple OU upgrader en stream dans la même réponse. Bidirectionnel sans connexion persistante.
* SDK officiels alignés depuis avril 2025 (TypeScript SDK 1.10.0 a été le premier à embarquer streamable HTTP).
* Backward compat assurée pendant la migration. Les principaux fournisseurs ont annoncé des dates de coupe SSE concrètes : Keboola 1er avril 2026, Atlassian 30 juin 2026.
* Pour un nouveau serveur MCP : implémenter directement streamable HTTP. Pour un client : prévoir compat des deux, négocier streamable en préférence.
* Bonus déploiement : un seul endpoint simplifie reverse proxy et load balancer (pas de sticky sessions à gérer).

**Pièges** :
* MCP stdio fuite des env vars si on ne scrub pas avant `Popen`.
* Tools non-déterministes (LLM-as-tool) qui cassent les retries.
* Le LLM hallucine des noms de tools : valider que `tool_calls[i].function.name` est bien dans la liste exposée.

**Technos notables** : MCP SDK officiel (Python, TypeScript, plusieurs autres en 2026), OpenAI tool spec, Anthropic tool use, LangChain Tools, Pydantic AI Tools.

---

## 3. Patterns de raisonnement

**Définition** : la boucle de contrôle entre LLM, tools et état.

**Variantes** :
| Pattern | Usage typique | Force | Faiblesse |
|---|---|---|---|
| **Direct** (1-shot) | QA simple, summarisation | Latence min, coût min | Pas d'outil, pas de raisonnement |
| **Simple** (LLM + tools en boucle) | Tâches procédurales, agent assistant | Simple, prévisible | Pas de planning, peut tourner en rond |
| **ReAct** (Thought, Action, Observation) | Recherche, exploration | Raisonnement intercalé visible | Boucles infinies, drift sémantique |
| **Plan and Execute** | Tâches multi-étapes parallélisables | Stages parallèles, replan possible | Surcoût planner, plan rigide |
| **Tree of Thoughts** | Problèmes math / logique | Explore plusieurs branches | Coût exponentiel |
| **Graph / state machine** (LangGraph style) | Workflows complexes, conditions, cycles contrôlés | Explicite, debuggable, time-travel | Demande modélisation amont |
| **Reflexion** (auto-critique) | Code, écriture | Qualité finale meilleure | +1 round par itération |

**Attentes 2026** :
* Stop conditions explicites : max steps, max tokens, similarité d'observations, coût plafonné.
* **Détection de boucle multi-niveaux** :
  * **Identité brute** : signature `(tool_name, arguments_json)` ; si même call dans la fenêtre des N derniers steps, on stoppe ou on injecte un message de redirection.
  * **Similarité sémantique** (la vraie méthode 2026) : embedder les n derniers messages ou tool calls, calculer la cosine similarity, et déclencher si elle dépasse un seuil (~0.92) sur 2 ou 3 tours consécutifs. Détecte les boucles qui changent de surface mais répètent le fond.
  * **RAG sur l'historique du run** : indexer en mémoire (HNSW in-process) les observations et thoughts du run en cours, et avant chaque nouvel appel LLM, faire un retrieval. Si on retrouve un état très proche d'il y a quelques tours, on injecte explicitement "tu as déjà tenté X, change d'angle". Beaucoup plus efficace qu'un simple stop sur seuil.
  * **Meta-agent observer** (option avancée) : un agent superviseur qui regarde le trace et peut interrompre, reprioriser ou re-prompter.
* Capacité à mixer plusieurs patterns dans un même agent (planner Plan&Execute, executor ReAct).

**Technos notables** : **LangGraph** (graph state, time-travel, leader GitHub stars 2026), **Microsoft Agent Framework 1.0** (Python + .NET, gouvernance enterprise mature, GA avril 2026), **CrewAI** (rôles + tasks), **Pydantic AI** (typage strict, Capabilities + AgentSpec YAML/JSON depuis v1.71), **AutoGen / AG2** (event-driven, GroupChat), **Google ADK** (A2A natif, hiérarchique).

---

## 4. Mémoire et état conversationnel

**Trois scopes désormais standards** (Mem0 v1.0, état de l'art) :
* **Épisodique** : ce qui s'est passé (interactions passées, événements).
* **Sémantique** : ce qui est connu (faits, préférences, profils utilisateur).
* **Procédurale** : comment faire (règles, comportements appris).

**Deux dimensions orthogonales à séparer** :
* **Mémoire utilisateur** (`user knowledge`) : faits sur un end-user spécifique (préférences, historique, profil), partitionnée par `user_id`, scope client. Lecture cross-session, écriture sur événements explicites ou observation.
* **Mémoire agent** (`agent self-learning`) : faits appris par l'agent sur lui-même et sur le monde (succès/échecs de tools, patterns récurrents, règles métier inférées), scope global ou par version d'agent. Sert au self-improvement.
* Confusion fréquente : stocker des faits user dans la mémoire agent pollue les autres tenants ; stocker des règles agent dans la mémoire user empêche l'apprentissage cross-utilisateur.

**Attentes** :
* **Window buffer** (N derniers tours) comme baseline.
* **Summary memory** (sliding) : résumé glissant via LLM quand on approche du cap context, avec **prévalence des informations récentes** (fenêtre récente détaillée, ancien résumé compressé, et compression récursive du résumé lui-même quand il grossit).
* **Vector memory** : embeddings des tours passés, retrieval par similarité, retourne top-k pertinent. Choix typique pour la **mémoire longue de mots-clés et similarité simple**.
* **Knowledge graph memory temporelle** (Zep, Mem0g) : graphe d'entités et relations daté, **algorithme de prévalence sur les informations récentes au stockage et au retrieval** (recency boosting). Choix typique pour la **mémoire longue structurée temporelle**.
* **GraphRAG memory** : si la mémoire dépasse l'épisodique simple et nécessite raisonnement multi-hop sur des relations.
* **Partitionnement multi-tenant** : mémoire scoped par end-user, par session, ou par contexte métier (`{"context": "healthcare"}`).
* **Paging explicite** (Letta / MemGPT) : l'agent décide quoi swap in/out comme un OS.
* TTL et droit à l'oubli RGPD.

**Choix d'algorithme par cas** :
| Besoin | Algorithme |
|---|---|
| Continuité de conversation court terme | Window buffer + sliding summary |
| Préférences user durables, lookup par mot-clé | Vector memory (RAG simple) |
| Faits structurés avec relations multi-hop | GraphRAG memory |
| Évolution temporelle ("X savait Y avant l'événement Z") | Knowledge graph memory temporelle (Zep TKG) |
| Apprentissage de règles par l'agent | Procedural memory + summary |

**Pièges** :
* Cap dur sur le nombre de messages : passer à un résumé glissant plutôt qu'un truncation brut.
* Mélange de scope (mémoire utilisateur vs mémoire agent) : à séparer logiquement, sinon pollution croisée entre tenants.
* Coût d'embedding à chaque append : batcher.

**Technos notables** : **Mem0** (Apache 2.0, 55k+ stars, multi-LLM, variante graph Mem0g), **Zep** (temporal KG, sub-second), **Letta** (ex-MemGPT, OS-style paging), **Cognee** (graph + vector), **LangChain Memory** (interfaces de base).

---

## 5. RAG (Retrieval Augmented Generation)

**Pipeline standard** :
1. **Ingestion** : loader (PDF, DOCX, HTML, code), parsing structuré, métadonnées.
2. **Chunking** : voir stratégies ci-dessous.
3. **Embedding** : modèle dédié (voir tableau ci-dessous), batch, dimension 768 / 1024 / 1536 / 3072.
4. **Indexation** : vector store + métadonnées filtrables.
5. **Retrieval** : top-k, optionnellement avec reranker.
6. **Hybrid search** : vector + BM25 fusion via Reciprocal Rank Fusion (RRF).
7. **Augmentation** : injection dans le prompt avec citations.

### Modèles d'embedding 2026 par cas d'usage

Source : MTEB leaderboard mars 2026 + benchmarks indépendants.

| Cas d'usage | Modèle recommandé | Score MTEB | Open weights | Note |
|---|---|---|---|---|
| **Anglais général**, top performer | Gemini Embedding 001 (Google) | 68.32 | Non (API) | Tête du leaderboard MTEB |
| **Anglais général**, in-context learning | BGE-en-ICL (BAAI) | 71.24 (avec ICL) | Oui | Gain significatif avec exemples |
| **Anglais général**, équilibre coût | OpenAI text-embedding-3-large | 64.60 | Non (API) | $0.13 / 1M tokens, 8 192 tokens |
| **Multilingue 250+ langues** | Qwen3-Embedding-8B | 70.58 (multi) | Oui | 8B params, 32K context, #1 multilingual leaderboard |
| **Multilingue alternative** | NVIDIA Llama-Embed-Nemotron-8B | tête multi-MTEB | Oui | Apache 2.0 |
| **Code search** | Qwen3-Embedding-8B | 80.68 (MTEB Code) | Oui | Aussi meilleur sur code |
| **Multimodal** (texte+image+vidéo+audio+PDF) | Gemini Embedding 2 (mars 2026) | 1er cross-lingual 0.997 | Non | 5 modalités, premier all-modality |
| **Domain-specific** (médical, juridique, finance) | Modèle fine-tuné sur corpus du domaine | variable | variable | **Bat systématiquement** les généralistes sur leur domaine |

Règle : ne pas se fier aveuglément au MTEB. Lancer une eval sur **votre** corpus avant de figer le choix (un modèle top public peut être moyen sur du ticketing interne).

### Stratégies de chunking par cas d'usage

| Stratégie | Quand l'utiliser | Tuning recommandé | Performance |
|---|---|---|---|
| **Recursive character** | Baseline universel | 400 à 512 tokens, overlap 10 à 20% | ~69% accuracy (bench Feb 2026 sur 50 papers) |
| **Page-level** | Docs techniques structurés (rapports, slides) | 1 chunk = 1 page logique | Bon quand structure forte |
| **Sentence-based** | FAQ, support, contenu court | Phrase entière, regroupement min/max | Très bon pour Q&A directe |
| **Semantic** | Articles longs avec changements de thème | Détection de rupture par embedding similarity | +9% recall sur certains cas, mais peut sur-fragmenter (~43 tokens / chunk → 54% accuracy si non maîtrisé) |
| **Hierarchical parent-child** | Documents structurés où on veut retrieval fin + contexte large | Petits chunks pour search, parent restitué pour contexte | +18 à 25% sur retrieval quality |
| **Late chunking** | Chunks ambigus sans contexte (pronoms, headers, cross-refs) | Embedding du doc entier, puis chunking après | Préserve le contexte global |
| **Adaptive aligné sur frontières logiques** | Contenu réglementaire / clinique avec sections claires | Topic boundary detection | 87% vs 13% pour fixed-size (étude clinique) |
| **LLM-based** | Petit volume, qualité maximale | LLM choisit les coupures | Cher, à réserver aux datasets critiques |

Règle : commencer recursive 512 / overlap 64, mesurer, et ne basculer vers semantic/hierarchical/late que si les métriques (recall, NDCG, MRR) le justifient.

### Patterns avancés à connaître (2026)

* **Hybrid + RRF baseline minimum** : pure dense ne tient plus la route. BM25 + dense fusionnés par **Reciprocal Rank Fusion** est le minimum viable de toute déploiement RAG sérieux.
* **Two-stage pipeline (dominant)** : hybrid RRF pour récupérer 50 à 100 candidats (rappel large), puis **cross-encoder reranker** (Cohere Rerank 3, BGE Reranker, Jina Reranker) pour scorer top-k. Précision et latence acceptables.
* **Contextual Retrieval (Anthropic, fin 2024, validé 2026)** : avant indexation, chaque chunk est augmenté par un court contexte généré par un LLM (le rôle du chunk dans le document complet). Résultat publié : **-49% de retrievals ratés**, **-67% combiné avec un reranker**. Confirmé par benchmarks Feb 2026 (contextual hybrid > vanilla hybrid RRF).
* **Retrieve-as-tool** : pas de retrieval automatique, le LLM décide quand interroger via un MCP-like tool. Évite le bourrage de contexte inutile et permet plusieurs retrievals dans un même run.
* **Citations** obligatoires : chaque assertion référence un chunk-source. Sert à la fois pour la confiance utilisateur et pour la faithfulness check à l'eval.
* **Multi-modal extraction** : VLM pour PDF complexes (tableaux, formules, schémas). C'est le terrain de Docling.

### Extraction : pourquoi Docling (IBM) est l'état de l'art

* **Docling** (open source, projet IBM donné à la Linux Foundation Agentic AI Foundation en 2026) convertit PDF, DOCX, PPTX, HTML, images vers markdown en préservant layout, tableaux, formules, code.
* **Granite-Docling-258M** (janvier 2026, Apache 2.0, sur Hugging Face) : VLM ultra-compact (258M params) spécialisé conversion document. Performances qui rivalisent avec des VLM plusieurs fois plus gros, à coût d'inférence très faible.
* Format de sortie **DocTags** : markup universel qui décrit chaque élément de page (tables, charts, formules, footnotes, captions) ET leurs relations contextuelles ET leur position. Pivot très propre pour pipelines RAG sérieux et pour GraphRAG.
* Pour un pipeline RAG mûr en 2026 : Docling en entrée, embeddings 2026, hybrid RRF + cross-encoder, contextual retrieval optionnel.

### Stores vector

| Store | Force | Quand |
|---|---|---|
| **pgvector** | Postgres natif, transactions, joins | Apps déjà sur Postgres, volume modéré |
| **Qdrant** | Performance, filtres riches, hybrid natif | Volume élevé, déploiement dédié |
| **Pinecone** | SaaS, zéro ops | POC rapide, équipe sans data infra |
| **Weaviate** | Hybrid search natif, multi-modal | Recherche complexe |
| **Chroma** | Embedded, dev local | Prototypage |

### Pièges

* Chunk trop petit (perd le contexte) ou trop grand (recall faible).
* Embedding model qui change : tout réindexer, prévoir le coût et la fenêtre de cutover.
* Ne pas filtrer par tenant : fuite de données entre clients.
* Skip du reranker "pour gagner de la latence" : généralement faux économie, le reranker reste sous 100 ms en local et améliore franchement la qualité.
* Pas de citations : impossible d'auditer ni de mesurer la faithfulness.

**Technos notables (frameworks RAG)** : LlamaIndex (le plus complet), LangChain Retrievers, Haystack (deepset), DSPy (optimisation prompt+retrieval), RAGFlow (UI + pipelines), **Docling + Granite-Docling** (extraction state of the art).

---

## 6. GraphRAG

**Définition** : RAG augmenté d'un knowledge graph (entités, relations) construit à l'ingestion. Permet le raisonnement multi-hop ("qui a travaillé avec X sur le projet Y au Q3 ?") qu'un vector store seul ne fait pas.

**Pipeline GraphRAG** :
1. **Définition du schéma cible** (voir best practice ci-dessous).
2. Extraction d'entités et relations par LLM ou VLM, **contrainte par le schéma**.
3. Résolution d'entités (mêmes entités sous différents libellés).
4. Construction du graphe (Neo4j, Memgraph, NetworkX in-memory).
5. **Détection de communautés** (Leiden, Louvain) : clustering hiérarchique.
6. **Résumé par communauté** via LLM.
7. Au query time : retrieval mixte (chunks + sous-graphe + résumés de communautés).

### Best practice : définir le schéma en amont, jamais laisser le LLM inventer

C'est **la** différence entre un GraphRAG qui marche et un graphe bricolé qui dérive à chaque run.

* **Schéma cible défini en amont** : liste explicite des types d'entités attendus (`Person`, `Product`, `Project`, `Contract`, `Department`…) et des types de relations (`works_for`, `owns`, `references`, `signed_on`), avec leurs propriétés typées.
* **LLM assisté pour la conception du schéma**, c'est OK : on peut demander à un LLM une suggestion de schéma à partir d'un échantillon de documents. Mais **le schéma est validé et figé par l'humain** avant le run d'extraction.
* **Schema-enforced extraction** : à l'extraction, le LLM est contraint (function calling, JSON schema, structured output) à ne produire **que** des entités et relations conformes au schéma. Tout ce qui sort du schéma est rejeté ou normalisé.
* **Sans schéma** : entités inconsistantes (`Jean Dupont`, `J. Dupont`, `M. Dupont` traités comme 3 entités), relations inventées à chaque run, graphe incohérent multi-runs, impossible à requêter avec Cypher proprement.

### IBM docling-graph : implémentation référence du pattern schema-first

* **docling-graph** (IBM, open source, Apache 2.0) transforme des documents non structurés en knowledge graphs **validés**.
* Combine deux pipelines d'extraction au choix :
  * **Local via Docling VLM** (Granite-Docling) : extraction structurée à partir du document.
  * **LLM via LiteLLM** : routage vers un runtime local (vLLM, Ollama) ou API (Mistral, OpenAI, Gemini, IBM watsonx).
* **Schema enforcement activé par défaut** sur les sorties LLM (à désactiver explicitement via CLI ou API si on veut faire de la découverte libre).
* Output : graphes typés `Pydantic` → `NetworkX` directed graph, IDs stables, métadonnées d'arêtes.
* Export `CSV`, `Cypher` (chargement direct dans Neo4j), formats KG-friendly.

### État de l'art mai 2026

* **Microsoft GraphRAG** a lancé la vague (2024) mais $33K d'indexation pour un dataset moyen le rend impraticable hors enterprise.
* **LightRAG** (open source) : merge de subgraphs voisins, +30% latence vs RAG (~80ms), accuracy supérieure.
* **nano-graphrag** : implémentation pédagogique, base de beaucoup de forks.
* **Fast GraphRAG** : optimisé coût d'indexation.
* **Mem0g** : variante graph de Mem0, KG construit en streaming pendant l'extraction.
* **Zep Temporal Knowledge Graph** : graphe avec dimension temporelle native, raisonnement "qui savait quoi quand".
* **IBM docling-graph** : extraction document → graphe validé avec schema enforcement, le pattern le plus "production-ready" pour pipelines maîtrisés.

### Quand GraphRAG vs RAG

| Type de question | Choix |
|---|---|
| Single-hop, fact lookup | RAG vector seul |
| Multi-hop, relations | GraphRAG |
| Temporel ("avant que X arrive") | Temporal KG (Zep) |
| Synthèse globale ("résume tout ce qu'on sait sur Y") | GraphRAG (community summaries) |
| Mix | Hybride: agent décide via deux tools (un RAG, un GraphRAG) |

### Pièges

* **Laisser le LLM inventer le schéma au runtime** : tueur n°1, conduit à un graphe inutilisable.
* Coût d'extraction LLM à l'ingestion (parfois plus que tout le RAG vector).
* Qualité du graphe = qualité du LLM extracteur. Modèles plus petits dégradent fortement.
* Re-extraction à chaque update document = rebuild partiel du graphe nécessaire.

**Technos notables** : Microsoft GraphRAG, LightRAG, Fast GraphRAG, **IBM docling-graph** (schema-first), Neo4j (store graphe le plus mûr), LlamaIndex PropertyGraphIndex.

---

## 7. Multi-agent orchestration

**Patterns** :
* **Supervisor / router** : un LLM dispatche vers l'agent spécialisé.
* **Orchestrator-workers** : décompose la tâche, délègue à des sous-agents, agrège.
* **Sequential pipeline** : agent A puis agent B puis agent C.
* **Parallel agents** : tâches indépendantes via `asyncio.gather` ou équivalent.
* **Hand-off explicite** : agent A passe la main à agent B avec son contexte (le pattern A2A).
* **Debate / consensus** : plusieurs agents argumentent, un juge tranche.

**Attentes** :
* Protocole d'échange standard entre agents : voir §8.
* Hand-off avec transfert de contexte minimal et signé.
* Trace distribuée (un trace_id propagé entre agents).
* Cost accounting par agent dans une chaîne.

**Paysage frameworks (mai 2026)** :
| Framework | Statut | Force |
|---|---|---|
| **LangGraph** | Leader GitHub stars depuis early 2026, v1.1.3 avec deep agent templates et runtime distribué | Graph state, checkpointing, time-travel |
| **Microsoft Agent Framework** | GA 1.0 avril 2026, .NET + Python, gouvernance enterprise mature | Multi-agent + observabilité + governance |
| **CrewAI** | Très adopté en prototypage rapide, migration fréquente vers LangGraph en prod | Rôles + tasks, modèle simple |
| **AG2 (ex-AutoGen)** | Rearchitecturé event-driven, GroupChat | Recherche, multi-agent debate |
| **Pydantic AI** | v1.71 avec Capabilities + AgentSpec YAML/JSON + Thinking cross-provider | Type safety, DX FastAPI-like |
| **Google ADK** | A2A natif, hiérarchique | Multi-vendor via A2A |
| **OpenAI Agents SDK** | Adopté durable execution comme feature first-class en 2026 | Si stack OpenAI-first |

---

## 8. Protocoles d'interface agent (A2A, OpenAI Responses, MCP)

**Définition** : la spécification du contrat entre un agent et ses callers (autres agents, applications, utilisateurs). Trois familles distinctes en 2026, **non interchangeables**.

| Protocole | Rôle | Promoteur | Adoption | Statut mai 2026 |
|---|---|---|---|---|
| **A2A** (Agent2Agent) | Agent ↔ Agent et Agent ↔ App | Google + IBM + Microsoft + 150+ orgs | Croissance forte 2025-2026 | v0.3.0 release, **v1.0 en draft** (pas de "v2") |
| **OpenAI Responses API** | App ↔ Agent (et tool runtime intégré) | OpenAI | Énorme sur écosystème OpenAI | Remplace l'Assistants API (sunset 26 août 2026) |
| **MCP** (Model Context Protocol) | LLM ↔ Tool / Resource | Anthropic + écosystème | Standard de fait pour les tools | Spec 2025-03-26, streamable HTTP standard |

**Choisir A2A quand** :
* Agent multi-vendor (clients agents écrits avec différents frameworks doivent appeler le tien).
* Besoin d'un Agent Card public et discoverable (`.well-known/agent-card.json`).
* Hand-off entre agents portables.
* Stratégie open standard + indépendance vendor.
* Streaming SSE / streamable HTTP avec sémantique de task (input_required, working, completed, failed, canceled).

**Choisir OpenAI Responses quand** :
* App déjà OpenAI-first, équipe ne veut pas gérer l'orchestration.
* Besoin des hosted tools OpenAI (web search, file search, computer use, deep research).
* Acceptable de coupler la stack à OpenAI.
* Bonus 2026 : Responses + Conversations API affichent 40 à 80% de gain coût vs Chat Completions grâce au cache utilisation.
* Si vous étiez sur Assistants API : la migration vers Responses est **obligatoire** avant le 26 août 2026.

**Choisir MCP quand** :
* On expose des outils ou ressources à consommer par un LLM (jamais une "interface agent" complète).
* Pas un substitut à A2A ou Responses : c'est la couche en dessous.

**Combinaisons typiques** :
* Agent A2A qui consomme des MCP servers en interne : **recommandé**.
* Agent OpenAI Responses qui appelle un MCP server : **supporté nativement** depuis 2025.
* Agent A2A qui appelle un autre agent A2A : c'est précisément le pattern hand-off A2A.

**Convergence multi-vendor** :
* OpenAI a soutenu MCP dès 2024 et reconnait A2A en 2025, signe de convergence côté tool layer.
* Google ADK embarque A2A nativement, ce qui permet à un agent ADK d'invoquer un agent LangGraph ou CrewAI via task interface standardisée.
* La compétition se déplace côté capabilities (deep research, computer use, hosted tools), pas côté protocole de surface.

---

## 9. Triggers et inputs

**Surfaces d'entrée attendues** :
* **HTTP REST / JSON-RPC** synchrone (Agent Card A2A).
* **Streaming** (SSE A2A, streamable HTTP, WebSocket).
* **Webhook** générique entrant (avec validation HMAC).
* **Cron / Schedule** (interval, cron expression, durable scheduler).
* **Chat widget** hébergé (URL embed-ready).
* **Email** (poll IMAP, ou push via Postmark / SendGrid inbound).
* **Event bus** : Kafka, NATS, RabbitMQ, GCP PubSub, AWS SQS.
* **DB change** (Postgres LISTEN/NOTIFY, Debezium CDC).
* **File drop** (S3 / GCS object created).
* **Slack / Teams / WhatsApp** mentions.

**Pièges** :
* Idempotency : un même message Kafka peut être livré 2 fois.
* Backpressure : si le LLM est lent, le consumer Kafka doit lag proprement, pas accumuler en RAM.
* Auth par trigger différente de l'auth interne.

**Technos notables (orchestrateurs durables)** : Temporal (1.0 janvier 2026, $300M raise février 2026 à $5B valuation), Inngest (workflows Temporal-compatibles depuis février 2026, `step.ai.infer` pour LLM long-running), Trigger.dev (jobs durables), Apache Airflow (data pipelines), Restate (durable execution, commercial mars 2026).

---

## 10. Outputs et streaming

**Attentes** :
* **SSE** pour le stream synchrone côté client (compatible browsers).
* **Streamable HTTP** pour MCP (et de plus en plus pour A2A).
* **Push notifications** (webhook callback) pour les long-running tasks (A2A v1 spec).
* **Artifacts typés** : texte, JSON, image, fichier binaire.
* Heartbeats / keep-alive pour traverser les proxies (timeout 60s par défaut côté nginx, ALB…).
* Cancellation propre côté client (close stream → annule le run).

**Technos notables** : SDKs A2A officiels (Python, JavaScript), FastAPI EventSourceResponse, httpx-sse (client), MCP SDK (gère streamable HTTP), OpenAI Responses streaming.

---

## 11. Human-in-the-loop (HITL)

**Patterns** :
* **Confirm-before-destructive** : pause sur tool `destructiveHint`.
* **Approval avec timeout** : auto-approve / auto-deny après X minutes.
* **Suggested actions** : agent propose, humain valide.
* **Time-travel debugging** (LangGraph) : checkpoint à chaque step, rollback, replay avec décision alternative.
* **Niveaux d'autonomie** par tool ou par catégorie de coût.
* **Notification** out-of-band (email, Slack) du valideur.
* **UI dédiée** d'approbation pour utilisateurs non-techniques.

**Pièges** :
* Bloquer un run pendant des heures consomme une connexion HTTP. Préférer un pattern pause-and-resume avec polling ou push (signals Temporal, interrupt LangGraph).
* Race condition : approval reçue alors que le run a déjà timeout.

**Technos notables** : LangGraph `interrupt()` + Checkpointer, CrewAI human input tools, Inngest pause/resume, Temporal signals, A2A `INPUT_REQUIRED` status.

---

## 12. State persistence et checkpointing (durable execution)

**Tendance forte 2026** : **durable execution** est devenu une feature first-class chez **LangGraph, Pydantic AI, OpenAI Agents SDK** (et CrewAI Enterprise). Ce n'est plus une option : c'est la baseline pour des agents en prod.

**Attentes** :
* Chaque step persisté (input, output, tool_calls, cost, latence).
* Possibilité de **resume** un run interrompu (crash worker, restart pod).
* **Checkpoint** explicite (LangGraph Checkpointer, Temporal journal replay) : sauvegarde de l'état complet à chaque transition de noeud.
* **Idempotency** des tool calls (clés idempotency key) : ré-exécuter un step déjà fait ne re-déclenche pas les side-effects.
* **Time-travel** : revenir à un checkpoint antérieur, modifier une décision, replay.

**Deux mécanismes dominants** :
* **Journal-based replay** (Temporal, Inngest) : on enregistre chaque step terminé, on rejoue depuis le journal après crash.
* **Database checkpointing** (LangGraph Checkpointer) : on persiste l'état complet à chaque transition.

**Schéma typique côté DB** :
* `agent_runs` (run lifecycle : status, tokens, cost, stop_reason).
* `agent_run_steps` (chronologie des steps, role, tool_calls, payload).
* `agent_contexts` + `agent_messages` (historique conversationnel).
* `agent_checkpoints` (snapshots d'état, optionnel pour graph mode).

**Technos notables** : LangGraph Checkpointer (Postgres, SQLite, Redis), Temporal (durable execution complet, GA 1.0 janvier 2026), Inngest (event sourcing + steps), Restate (RPC + state durables, commercial mars 2026), DBOS (Postgres-natif).

---

## 13. Observabilité et télémétrie

**Trois piliers** : **traces** (OTel spans), **métriques** (compteurs, histogrammes, gauges), **logs** structurés.

### Principes

1. Toute métrique doit pouvoir se corréler à une trace via `trace_id` et `span_id` (exemplars OTel).
2. Toute métrique de comptage porte une dimension `status` (`ok`, `error`, `timeout`, `denied`) pour calculer les taux.
3. Les dimensions caractérisant un acteur transverse (`tenant`, `app_id`, `task_name`, `agent_id`) se propagent via W3C Baggage.
4. Les dimensions caractérisant un service déployé (`environment`, `cluster`) sont des OTel resource attributes.
5. Les dimensions caractérisant une opération locale (`model`, `tool`, `collection`, `memory_type`) sont des span attributes locaux.
6. Pas de label `user_id` direct (cardinalité explosive et fuite PII) : passer par un `tenant` agrégé ou un hash.

### Traçabilité bout en bout

* Tout composant instrumenté démarre un span (root ou child) avant d'émettre une métrique.
* Le header `traceparent` (W3C Trace Context) est propagé sur tout appel sortant HTTP, gRPC, MCP (via `params.metadata`), A2A (via `metadata` de task) et message queue (headers de message).
* Le `trace_id` est exposé à l'UI utilisateur pour lier les feedbacks UX à la trace.
* Exemplars activés sur tous les histogrammes pour cliquer d'un point p99 vers la trace.

### Schéma de propagation des dimensions

| Dimension | Origine | Mécanisme | Destinations |
|---|---|---|---|
| `tenant` | client (query string) | W3C Baggage | broker, router, backend, agent, MCP |
| `app_id` | client (header) | W3C Baggage | tous les étages |
| `task_name` | router LLM | Baggage ajouté par le router | backend, retour via trace |
| `agent_id` | runtime agent | Baggage | LLM, MCP, RAG, memory |
| `agent_version` | runtime agent | Baggage | idem |
| `mcp_server`, `tool` | serveur MCP | span attribute local | pas propagé en aval |
| `rag_collection`, `embedding_model` | service RAG | span attribute local | pas propagé en aval |
| `memory_type`, `memory_store` | service Memory | span attribute local | pas propagé en aval |
| `environment`, `cluster` | déploiement | OTel resource attribute | jamais via baggage |
| `model_alias`, `model_physical` | router, backend | span attribute, éventuellement metadata de réponse | remonté côté client si exposé |

Règle : un appel sortant sans `app_id` ni `tenant` est rejeté en production (warning en dev). Sans ces deux dimensions, les métriques ne sont pas exploitables pour le pilotage.

### Métriques par famille

#### LLM

Dimensions : `model_alias`, `model_physical`, `app_id`, `tenant`, `task_name`, `environment`, `cluster`, `streaming`, `status`.

| Métrique | Type |
|---|---|
| `llm_invocations_total` | Counter |
| `llm_time_to_first_token_seconds` | Histogram |
| `llm_completion_duration_seconds` | Histogram |
| `llm_tokens_per_second` | Histogram |
| `llm_tokens_input_total` | Counter |
| `llm_tokens_output_total` | Counter |

#### MCP

Dimensions : `app_id`, `agent_id`, `tenant`, `mcp_server`, `tool`, `tool_version`, `environment`, `cluster`, `status`.

| Métrique | Type |
|---|---|
| `mcp_invocations_total` | Counter |
| `mcp_tool_duration_seconds` | Histogram |
| `mcp_tool_request_bytes` | Histogram |
| `mcp_tool_response_bytes` | Histogram |

#### Agent

Dimensions : `agent_id`, `agent_version`, `app_id`, `tenant`, `environment`, `cluster`, `status` (`ok`, `failed`, `aborted`, `human_approval_required`).

| Métrique | Type |
|---|---|
| `agent_invocations_total` | Counter |
| `agent_time_to_first_stream_seconds` | Histogram |
| `agent_wall_clock_duration_seconds` | Histogram |
| `agent_tokens_input_total` | Counter |
| `agent_tokens_output_total` | Counter |
| `agent_llm_calls_total` | Counter |
| `agent_mcp_calls_total` | Counter |

#### RAG

Dimensions : `rag_collection`, `embedding_model`, `reranker_model`, `app_id`, `agent_id`, `tenant`, `environment`, `cluster`, `status`.

| Métrique | Type |
|---|---|
| `rag_retrievals_total` | Counter |
| `rag_retrieval_duration_seconds` | Histogram |
| `rag_embedding_duration_seconds` | Histogram |
| `rag_vector_search_duration_seconds` | Histogram |
| `rag_rerank_duration_seconds` | Histogram |
| `rag_documents_retrieved` | Histogram |
| `rag_chunks_tokens_total` | Counter |
| `rag_embedding_tokens_total` | Counter |
| `rag_top_score` | Histogram |
| `rag_zero_results_total` | Counter |

#### Memory

Dimensions : `memory_type` (`short_term`, `long_term`, `episodic`, `semantic`, `procedural`), `memory_store` (`redis`, `postgres`, `vector`, `graph`), `app_id`, `agent_id`, `tenant`, `environment`, `operation` (`read`, `write`, `delete`, `evict`), `status`.

| Métrique | Type |
|---|---|
| `memory_operations_total` | Counter |
| `memory_operation_duration_seconds` | Histogram |
| `memory_tokens_loaded_total` | Counter |
| `memory_tokens_written_total` | Counter |
| `memory_size_bytes` | Gauge |
| `memory_recall_hits_total` | Counter |
| `memory_recall_misses_total` | Counter |
| `memory_evictions_total` | Counter |
| `memory_session_turns` | Histogram |

### Sécurité (Zero Trust for AI)

Dimensions transverses : `app_id`, `agent_id`, `tenant`, `environment`, `cluster`.

| Métrique | Type | Dimensions spécifiques |
|---|---|---|
| `guardrail_rejections_total` | Counter | `guardrail_name`, `phase` (`input`, `output`), `rejection_reason` (`toxicity`, `pii`, `off_topic`, `jailbreak`, `fact_error`) |
| `prompt_injection_detections_total` | Counter | `detector` (`regex`, `classifier`, `llm_judge`), `severity`, `injection_pattern` |
| `pii_redaction_events_total` | Counter | `source` (`prompt`, `completion`, `tool_response`, `rag_chunk`), `pii_type` (`email`, `phone`, `iban`, `nom`, `nir`) |
| `tool_access_denied_total` | Counter | `mcp_server`, `tool`, `reason` (`scope_missing`, `role_denied`, `user_unauthorized`, `rate_limited`) |
| `output_filter_blocks_total` | Counter | `filter` (`secret_leak`, `pii_leak`, `system_prompt_leak`) |
| `policy_violations_total` | Counter | `policy` (`data_residency`, `model_authorized`, `max_tokens`), `enforcement` (`warn`, `block`) |
| `auth_token_validation_total` | Counter | `result` (`ok`, `expired`, `invalid`, `revoked`), `token_type` (`bearer`, `api_key`, `session`) |

Lecture transverse :
* `prompt_injection_detections_total` compte les tentatives détectées ; `guardrail_rejections_total` compte les rejets effectifs.
* Une rejection guardrail en phase `input` empêche l'invocation LLM en aval : `llm_invocations_total` n'incrémente pas.
* Une rejection guardrail en phase `output` survient après une invocation LLM ok : `llm_invocations_total{status="ok"}` a déjà incrémenté.

### UX et qualité

Dimensions : `app_id`, `agent_id`, `agent_version`, `task_name`, `tenant`, `environment`.

#### Signaux UX explicites

| Métrique | Type | Dimensions spécifiques |
|---|---|---|
| `user_feedback_total` | Counter | `sentiment` (`positive`, `negative`, `neutral`), `feedback_type` (`thumbs`, `star`, `report`) |
| `response_regeneration_total` | Counter | (signal négatif implicite) |
| `response_edit_total` | Counter | |
| `response_copy_total` | Counter | (signal positif implicite) |
| `response_dismissed_total` | Counter | |

#### Signaux UX implicites

| Métrique | Type | Dimensions spécifiques |
|---|---|---|
| `session_completed_total` | Counter | `outcome` (`success`, `abandoned`, `error`) |
| `session_duration_seconds` | Histogram | |
| `conversation_turns` | Histogram | |
| `time_to_user_satisfaction_seconds` | Histogram | |

#### Évaluation offline et dérive

| Métrique | Type | Dimensions spécifiques |
|---|---|---|
| `eval_score` | Gauge | `eval_suite` (`rag_recall`, `answer_correctness`, `faithfulness`, `hallucination_rate`, `tool_selection_accuracy`) |
| `eval_runs_total` | Counter | `eval_suite`, `result` (`pass`, `fail`) |
| `drift_score` | Gauge | `target` (`input_prompt`, `embedding`, `output_distribution`) |

#### Coût en tokens par session

| Métrique | Type |
|---|---|
| `tokens_per_session` | Histogram |
| `llm_calls_per_session` | Histogram |
| `mcp_calls_per_session` | Histogram |

Règle de liaison feedback ↔ trace : chaque event de feedback porte le `trace_id` de l'interaction concernée. L'UI affichant la réponse expose ce `trace_id` et le réutilise dans les payloads d'events feedback.

### Buckets histogrammes

Calibrage pour usage agentique on-premise (familles de modèles 7B à 70B sur GPU mid-range, mix dense + MoE).

#### Temps (secondes)

| Métrique | Buckets |
|---|---|
| `llm_time_to_first_token_seconds` | 0.1, 0.25, 0.5, 1, 2, 5, 10, 30 |
| `llm_completion_duration_seconds` | 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600 |
| `agent_time_to_first_stream_seconds` | 0.25, 0.5, 1, 2, 5, 10, 30 |
| `agent_wall_clock_duration_seconds` | 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800 |
| `mcp_tool_duration_seconds` | 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30 |
| `rag_retrieval_duration_seconds` | 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10 |
| `rag_embedding_duration_seconds` | 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5 |
| `rag_vector_search_duration_seconds` | 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2 |
| `memory_operation_duration_seconds` | 0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1 |

#### Débit, tailles, scores

| Métrique | Buckets |
|---|---|
| `llm_tokens_per_second` | 1, 5, 10, 25, 50, 100, 200, 500 |
| `mcp_tool_request_bytes` | 256, 1024, 4096, 16384, 65536, 262144, 1048576 |
| `mcp_tool_response_bytes` | 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304 |
| `rag_documents_retrieved` | 1, 3, 5, 10, 20, 50 |
| `rag_top_score` | 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99 |
| `memory_session_turns` | 1, 3, 5, 10, 20, 50, 100 |

Règle : bornes basses sur les valeurs idéales (cache RAG, mémoire Redis), bornes hautes sur les seuils d'investigation (TTFT supérieur à 10s ou complétion supérieure à 5 minutes), densité plus forte dans la zone normale.

### Mapping OpenTelemetry GenAI semconv

Adoption de la convention sémantique OTel GenAI 2025 comme socle, complétée par un namespace propre à l'organisation pour les dimensions spécifiques.

#### Métriques LLM

| Nom local | OTel semconv |
|---|---|
| `llm_invocations_total` | `gen_ai.client.invocations` |
| `llm_completion_duration_seconds` | `gen_ai.client.operation.duration` |
| `llm_tokens_input_total` | `gen_ai.client.token.usage` avec `gen_ai.token.type=input` |
| `llm_tokens_output_total` | `gen_ai.client.token.usage` avec `gen_ai.token.type=output` |
| `llm_time_to_first_token_seconds` | `gen_ai.server.time_to_first_token` |
| `llm_tokens_per_second` | dérivé de `gen_ai.server.time_per_output_token` |

#### Attributs canoniques

| Attribut OTel | Valeur exemple |
|---|---|
| `gen_ai.system` | `<org>.iagen_proxy` ou `<org>.llm_router` |
| `gen_ai.request.model` | `model_alias` (alias logique configurable, ex: `chat-large`, `code-large`) |
| `gen_ai.response.model` | `model_physical` (modèle réel servi, ex: `Mistral-Small-3.2-24B`) |
| `gen_ai.operation.name` | `chat`, `embeddings`, `completion` |

Les couches Agent, RAG, Memory et MCP restent sur le nommage local tant que la spec OTel GenAI ne stabilise pas ces périmètres.

### Technos notables

Six plateformes ancrent 2026 :
* **Langfuse** : leader open source, self-hostable, **acquis par ClickHouse en janvier 2026** (code OSS toujours actif, community maintenue).
* **LangSmith** : intégration la plus profonde avec LangChain + LangGraph, SaaS, vendor lock-in fort.
* **Arize Phoenix** : héritage ML observability, primitives d'eval plus profondes.
* **Helicone** : proxy drop-in, installation la plus simple (changer base URL = traces), free tier généreux.
* **Datadog LLM Observability** : choix par défaut pour les shops Datadog.
* **Honeycomb LLM Observability** : tracing événementiel profond.

Backend OTel générique : Grafana stack (Tempo + Mimir + Loki), Jaeger.

---

## 14. Évaluation

**Niveaux** :
* **Unit eval** : un prompt isolé, dataset de cas, scoring.
* **Trajectory eval** : la séquence complète de steps, qualité du raisonnement.
* **End-to-end eval** : task success rate, coût, latence.

**Méthodes de scoring** :
* **Exact match / regex** pour outputs structurés.
* **LLM-as-judge** avec rubrique : un LLM tiers note l'output sur des critères (faithfulness, relevance, conciseness).
* **Métriques RAG dédiées** : faithfulness, answer relevance, context precision/recall.
* **Pairwise comparison** : modèle A vs B sur même input, juge tranche.
* **Human review** échantillonné pour calibrer le LLM-judge.

**CI / regression** :
* Eval lancée à chaque modif de prompt ou de modèle.
* Promotion de version bloquée si régression > seuil.
* A/B test en prod avec split routing.

**Technos notables** :
* **RAGAS** : standard académique pour RAG (papier EACL 2024), métriques faithfulness, answer relevancy, context precision, agent goal accuracy, tool call accuracy. Fastest path zero → scored RAG.
* **DeepEval** : couvre 50+ métriques (RAG, agents, multi-turn, MCP, safety), la plus large bibliothèque. Bon choix quand la stack a dépassé l'exploratoire.
* **TruLens** : acquis par Snowflake en mai 2024, toujours OSS et self-hostable. Fort sur multi-hop traces agentiques.
* **Promptfoo** : CLI eval déclaratif (YAML), idéal pour CI et regression rapide sur prompts.
* **MLflow, LangSmith Evals, Phoenix, Vellum, Open Evals, EvalScope** : alternatives selon stack.

Tendances 2026 : génération synthétique de datasets d'éval, sélection automatique des métriques pertinentes, eval multi-modal.

---

## 15. Sécurité

**Surfaces** :
* **Prompt injection** : entrées utilisateur ou docs RAG qui hijack l'agent. Mitigations : delimiter ferme, sanitization, modèles fine-tunés, guardrails (cf. ci-dessous).
* **Tool injection** : MCP server malveillant qui retourne du contenu poisonné. Sandbox + validation schema strict.
* **Data exfiltration** : agent qui envoie des secrets dans une requête web tool. Egress allowlist.
* **Code execution** : si tool `python_repl`, sandbox (Docker, Pyodide, gVisor, e2b.dev).
* **Secrets at rest** : Fernet / AES-GCM, KMS / Vault. Jamais en clair en DB.
* **Secrets in transit** : TLS partout, mTLS inter-agents pour A2A enterprise.
* **Logs scrubbing** : redaction PII, API keys, tokens avant exporter.
* **Rate limiting** par end-user et par agent.

### Guardrails : ce qu'on protège, où, et avec quoi

Les guardrails sont des contrôles automatisés appliqués en deux phases au minimum (entrée et sortie de l'agent), parfois aussi sur les tool I/O. Modèle defense-in-depth : pas de silver bullet, on empile.

**Catégories de protection (génériques)** :
| Catégorie | Phase | Exemple de violation | Détection typique |
|---|---|---|---|
| **PII / données régulées** | input + output + tool I/O | numéro de carte, NIR, IBAN, nom dans un log | NER + regex spécialisés (Presidio, Comprehend) |
| **Prompt injection / jailbreak** | input | "ignore previous instructions" en clair ou obfusqué | classifier dédié (Lakera Guard, LlamaGuard, Rebuff) |
| **Toxicity / hate / harassment** | input + output | propos haineux, insultes | classifier (LlamaGuard, Perspective API, OpenAI Moderation) |
| **Off-topic / brand safety** | output | agent banque qui parle politique | LLM-judge avec rubrique métier |
| **Hallucination / faithfulness** | output | affirmation non sourcée par le RAG | RAG citations check + LLM-judge |
| **Secret leak** | output | API key, internal URL, system prompt | regex haute confiance + classifier |
| **Compliance sectorielle** | output | conseil financier, médical, juridique non autorisé | LLM-judge + règles métier |
| **Intent detection** | input | requête hors scope déclaré | classifier rapide |

**Catégories souvent entreprise-spécifiques (à ne pas oublier)** :
| Catégorie | Exemple |
|---|---|
| **Mention de compétiteurs** | l'agent ne doit pas comparer / recommander les produits concurrents |
| **Engagements contractuels** | pas de promesse commerciale, prix, délai non validé |
| **Propriété intellectuelle interne** | pas de divulgation de roadmap, codes projet, noms internes confidentiels |
| **Politiques internes** | formulations interdites, ton imposé, disclaimers obligatoires |
| **Naming convention métier** | terminologie réglementée (banque, assurance, santé) à respecter |
| **Disclaimers obligatoires** | mention "ceci ne constitue pas un conseil X" en bas de réponse |
| **Géofencing data residency** | refus si la donnée doit rester EU et que le modèle est US |
| **Plages horaires** | refus en dehors des horaires d'ouverture pour certains tools |

**Pattern d'implémentation typique** :
1. **Entrée** : classifier rapide (latence < 50ms, ex prompt injection + toxicity).
2. **Pendant** : check tools (allowlist, rate limit par tool, scope vérification).
3. **Sortie** : second pass (PII redaction, secret leak scanning, hallucination check si réponse RAG).
4. **Asynchrone** : LLM-judge plus lent en sample (1% du trafic) pour calibrer les classifiers et détecter les nouvelles attaques.
5. **Audit** : tout rejet incrémente une métrique (cf. §13) avec le `rejection_reason` pour diagnostic.

**Technos notables** :
| Catégorie | Outils |
|---|---|
| Prompt injection | **Lakera Guard** (SaaS, leader, sub-50ms, 98%+ détection, 100+ langues), **Rebuff** (OSS), **Microsoft Prompt Shields**, **PromptArmor** |
| PII | **Microsoft Presidio** (OSS, NER + regex configurable, 50 à 200ms), **AWS Comprehend PII**, **Google DLP** |
| Content safety / toxicity | **LlamaGuard** (Meta, OSS, classifier 7B), **OpenAI Moderation API**, **Perspective API**, **Azure AI Content Safety** |
| Frameworks programmables | **NeMo Guardrails** (Nvidia, Apache 2.0, langage Colang, 5 rail types : input, dialog, retrieval, execution, output, sub-100ms sur GPU), **Guardrails AI** (OSS, validators), **LLM Guard** (MIT, 15 input scanners + 20 output scanners), **Pangea AI Guard**, **Robust Intelligence** |
| Secrets / leaks | **TruffleHog** patterns réutilisables au niveau output |

---

## 16. Identité et authentification

**Côté builder (utilisateurs internes)** :
* SSO d'entreprise : SAML, OIDC. Providers : Azure AD / Entra, Google Workspace, Okta, Keycloak, IBMid.
* RBAC : owner, editor, viewer par agent ou par folder.
* SCIM pour provisioning automatique.
* Audit log des actions sensibles.

**Côté end-user (callers de l'agent A2A)** :
* Bearer API key (le standard A2A actuel).
* OAuth2 sur l'agent : end-user s'auth via OIDC, le token est propagé aux tools downstream (delegation).
* Identité propagée dans la trace + dans la mémoire (partition par tenant ou par identifiant haché, cf. §13 sur la cardinalité).
* Quotas par end-user.

### User identity forwarding : trou dans les protocoles

**Constat (mai 2026)** : ni MCP ni A2A ne définissent à date de mécanisme natif pour propager l'identité d'un end-user à travers la chaîne `app → agent → MCP tool → service backend`.

* **MCP** : l'auth MCP est une couche server↔client (clé d'API du serveur), pas de spec pour transporter l'identité de l'utilisateur final qui a déclenché l'appel.
* **A2A** : versions actuelles **v0.3.0 release + v1.0 draft** (pas de "v2"). La spec délègue explicitement l'authentification au niveau HTTP / transport : les payloads JSON-RPC **ne portent pas l'identité** du client ou de l'utilisateur. L'Agent Card déclare les `securitySchemes` alignés sur OpenAPI (Bearer, OAuth2, OpenID Connect), mais le binding standard pour l'identité end-user déléguée n'existe pas.
* **OAuth2 Token Exchange (RFC 8693)** résout le problème en théorie (échange d'un token user contre un token scopé tool), mais aucun des deux protocoles ne le requiert ni ne le standardise dans son binding.

**Conséquence** : les implémentations doivent **inventer** la convention. Patterns observés :
* **Header HTTP custom** (`X-On-Behalf-Of: <jwt>`) propagé hors-spec côté A2A et MCP streamable HTTP.
* **Champ `metadata`** des tasks A2A et `params.metadata` des appels MCP, avec un sous-objet auth conventionnel (`{"actor": {"sub": "...", "iss": "..."}}`).
* **Re-authentification** côté tool : l'agent demande à l'end-user d'OAuth-er chaque tool individuellement (pattern "token vault" : Composio, Pica, Arcade).
* **Mode dégradé déclaratif** : pour de la lecture non sensible, l'end-user est juste déclaré (par claim signé), pas re-authentifié.

**Recommandations** :
* Définir une convention interne le plus tôt possible et la documenter (`metadata.actor` typé, signé JWT court, scope explicite).
* Suivre l'évolution A2A v1.0 (en draft) et les drafts MCP qui adressent cette lacune (en discussion auprès des working groups Anthropic et Google).
* Pour les appels critiques : OAuth2 Token Exchange (RFC 8693) avec un STS interne, et invalidation rapide côté token vault.

**Technos notables** :
| Catégorie | Outils |
|---|---|
| IdP / SSO | Auth0, Keycloak (OSS), Azure AD / Entra, Okta, IBMid, Casdoor (OSS), Authentik (OSS) |
| OAuth2 token vault pour agents | Composio, Pica, Arcade.dev, Nango |
| STS / Token Exchange | Keycloak (RFC 8693 supporté), Auth0, services maison |

---

## 17. Versioning, lifecycle, déploiement

**Attentes** :
* **Draft / Published** par agent.
* **Snapshot** de la config à chaque publication.
* **Promotion** entre environnements (dev / staging / prod).
* **A/B testing** : split routing X% v1, Y% v2 sur le même endpoint. Décision persistée par run pour permettre la comparaison.
* **Canary release** : 1% / 10% / 50% / 100% progressif, avec auto-rollback si métriques dégradent.
* **Rollback** instantané vers une version précédente.
* **Shadow execution** (émergent 2026) : nouvelle version exécutée en parallèle avec l'ancienne sur la même entrée, output ignoré, on mesure le `divergence rate` (plans ou tool calls différents).
* Pour AI agents : monitorer non pas seulement la latence / les erreurs, mais aussi le comportement de raisonnement, la conformité aux policies, le drift de modèle.

**Modèle** : `agents` (logique) + `agent_versions` (snapshots immutables) + `agent_routes` (règles de split).

**Technos notables** :
* **Microsoft Agent Framework 1.0** (avril 2026) : gouvernance enterprise la plus mature, semantic versioning, automated rollback, hot-reload natifs.
* **Telnyx AI Agents** : canary deployments natifs avec divergence monitoring, exemple récent.
* **Feature flag backends à brancher** : LaunchDarkly, GrowthBook (OSS), Unleash (OSS), Statsig.
* Côté agent-spécifique le tooling reste jeune ; le pattern dominant en 2026 est de brancher un feature-flag backend sur le routing A2A entrant.

---

## 18. Cost et quota management

**Attentes** :
* Cost per run, per day, per agent, per end-user.
* Budget avec hard cap (l'agent refuse de tourner au-delà).
* Soft cap avec alerting (Slack, email).
* Rate limit par end-user (req/min).
* Cost preview au design time : estimation basée sur le modèle et la profondeur de boucle.

**Technos notables** :
* **Helicone** : gateway proxy avec cost tracking, free tier 10K req/mois, alerting sur seuil.
* **Langfuse** : cost per trace, agrégations multi-dimensions.
* **Portkey** : gateway + budget guards + multi-provider routing.
* **LiteLLM enterprise** : team-level budgets + SSO + RBAC en plus du gateway OSS.
* **AI Cost Board** : cost tracking pur, low cost.
* **OpenMeter** : usage metering générique, branché sur Stripe pour facturation downstream.

Pattern dominant : gateway pour cost tracking + alerting (Helicone ou Portkey), couplé avec un outil d'eval pour la qualité (Phoenix, TruLens, RAGAS).

---

## 19. Portabilité

**Attentes** :
* **Export config** en YAML / JSON (workflow + prompts + MCP refs, sans secrets).
* **Import** symétrique pour clone et restore.
* **Code generation** : extraction de l'agent en projet autonome (LangGraph + Dockerfile + README) pour graduate hors du builder.
* **Versioning Git-friendly** : le format export doit être diffable.
* Standards émergents : pas encore de format universel, mais A2A Agent Card + MCP server config est la base la plus stable.

**Technos notables / inspirations** :
* **n8n workflow JSON** : standard de fait côté workflow visuel.
* **CrewAI YAML** : rôles + tasks déclaratifs.
* **Pydantic AI AgentSpec** (v1.71, février 2026) : YAML / JSON pour charger un agent, format encore peu adopté hors framework.
* **LangGraph code Python** : pas de format déclaratif, le code est la spec.
* **OpenAPI 3.x** : pour décrire les tools quand ils sont exposés en HTTP.

---

## Synthèse : matrice de maturité par brique

| Brique | Maturité 2026 | Standardisation | Choix critique |
|---|---|---|---|
| LLM provider | Mûre | OpenAI tool format de fait | Multi-provider obligatoire |
| MCP / tools | Mûre, en consolidation | MCP standard, transport streamable HTTP (SSE déprécié, deadlines 2026) | Adopter MCP, viser streamable HTTP |
| Patterns raisonnement | Mûre | Pas de standard | Mixer plusieurs patterns, anti-loop multi-niveaux |
| Mémoire window | Mûre | Aucun | Baseline obligatoire |
| Mémoire vector | Mûre | Aucun | Sortir du window pur |
| Mémoire graph (Mem0g, Zep) | Émergente | Aucun | À surveiller |
| Mémoire agent vs user | Émergente conceptuellement | Aucun | Séparer dès le design |
| RAG vector | Mûre | Aucun | Hybrid RRF + cross-encoder reranker = baseline 2026 |
| RAG extraction documentaire | Mûre | DocTags (Docling) émergent | Docling / Granite-Docling = état de l'art |
| GraphRAG | Émergente, coûteuse | Aucun | **Schéma défini en amont**, jamais laissé au LLM |
| Multi-agent | Émergente, traction forte | A2A v0.3.0, v1.0 draft, OpenAI Responses | A2A pour multi-vendor, Responses si OpenAI-first |
| Protocoles d'interface | En consolidation | A2A et OpenAI Responses convergent vers MCP côté tool | Choisir tôt, A2A si ouverture |
| Triggers | Mûre | Aucun | Couvrir HTTP, cron, webhook, event bus |
| Streaming | Mûre | A2A SSE, MCP streamable HTTP | Heartbeats obligatoires |
| HITL | Mûre côté pause, jeune côté time-travel | Aucun | LangGraph est la référence |
| Durable execution | Mûre 2026 | Aucun | Adopter Temporal / LangGraph / Inngest |
| Observabilité (OTel) | Mûre | OTel GenAI semconv 2025 | Adopter semconv + W3C Baggage |
| Évaluation | Émergente, en pleine effervescence | Aucun | RAGAS / DeepEval / TruLens + LLM-as-judge + datasets versionnés |
| Sécurité (guardrails) | Jeune, problème ouvert | Aucun | Defense in depth, deux phases input + output |
| Identité forwarding (MCP/A2A) | Manquant dans les protocoles | Aucun | Convention interne nécessaire |
| Versioning agent | Jeune mais accélère | Aucun | Snapshots immutables, canary + shadow execution |
| Cost management | Jeune | Aucun | Gateway (Helicone / Portkey) + quotas tôt |
| Portabilité (export) | Jeune | Aucun | YAML lisible + code gen complémentaires |

---

## Sources

### Embeddings et MTEB
* [Best Embedding Models for RAG 2026 (PremAI)](https://blog.premai.io/best-embedding-models-for-rag-2026-ranked-by-mteb-score-cost-and-self-hosting/)
* [Best Embedding Models 2026 (Mixpeek)](https://mixpeek.com/curated-lists/best-embedding-models)
* [MTEB Leaderboard March 2026](https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-march-2026/)
* [MTEB Leaderboard (Hugging Face)](https://huggingface.co/spaces/mteb/leaderboard)

### RAG et chunking
* [Best Chunking Strategies for RAG 2026 (Firecrawl)](https://www.firecrawl.dev/blog/best-chunking-strategies-rag)
* [Contextual Retrieval (Anthropic)](https://www.anthropic.com/news/contextual-retrieval)
* [RAG-Fusion: multi-query + RRF](https://github.com/Raudaschl/rag-fusion)
* [Advanced RAG: Understanding RRF in Hybrid Search (Feb 2026)](https://glaforge.dev/posts/2026/02/10/advanced-rag-understanding-reciprocal-rank-fusion-in-hybrid-search/)

### Docling et GraphRAG
* [IBM Granite-Docling announcement](https://www.ibm.com/new/announcements/granite-docling-end-to-end-document-conversion)
* [docling-project/docling (GitHub)](https://github.com/docling-project/docling)
* [docling-project/docling-graph (GitHub)](https://github.com/docling-project/docling-graph)
* [Docling Graph documentation (IBM)](https://ibm.github.io/docling-graph/fundamentals/)
* [RAG vs GraphRAG: Systematic Evaluation (arXiv 2502.11371)](https://arxiv.org/abs/2502.11371)
* [Graph RAG in 2026: A Practitioner's Guide](https://medium.com/graph-praxis/graph-rag-in-2026-a-practitioners-guide-to-what-actually-works-dca4962e7517)

### Protocoles
* [MCP Specification 2025-03-26: Transports](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)
* [Why MCP Deprecated SSE for Streamable HTTP (Brightdata)](https://brightdata.com/blog/ai/sse-vs-streamable-http)
* [Agent2Agent Protocol Specification](https://a2a-protocol.org/latest/specification/)
* [A2A v0.3.0 spec](https://a2a-protocol.org/v0.3.0/specification/)
* [OpenAI Assistants → Responses Migration Guide](https://developers.openai.com/api/docs/assistants/migration)
* [OAuth 2.0 Token Exchange (RFC 8693)](https://datatracker.ietf.org/doc/html/rfc8693)

### Mémoire
* [State of AI Agent Memory 2026 (Mem0)](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
* [AI agent memory comparison 2026 (n1n.ai)](https://explore.n1n.ai/blog/ai-agent-memory-comparison-2026-mem0-zep-letta-cognee-2026-04-23)
* [Best AI Agent Memory Frameworks 2026 (Atlan)](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/)

### Frameworks multi-agent
* [Definitive Guide to Agentic Frameworks 2026](https://softmaxdata.com/blog/definitive-guide-to-agentic-frameworks-in-2026-langgraph-crewai-ag2-openai-and-more/)
* [The 2026 AI Agent Framework Decision Guide](https://dev.to/linou518/the-2026-ai-agent-framework-decision-guide-langgraph-vs-crewai-vs-pydantic-ai-b2h)
* [Microsoft Agent Framework 1.0 GA (April 2026)](https://visualstudiomagazine.com/articles/2026/04/06/microsoft-ships-production-ready-agent-framework-1-0-for-net-and-python.aspx)

### Durable execution
* [Durable Execution Patterns for AI Agents (Zylos)](https://zylos.ai/research/2026-02-17-durable-execution-ai-agents)
* [Temporal for AI](https://temporal.io/solutions/ai)
* [Durable Execution: Temporal, Restate, DBOS (2026)](https://devstarsj.github.io/2026/04/03/durable-execution-temporal-restate-dbos-distributed-workflows-2026/)

### Observabilité et eval
* [Agent Observability: LangSmith, Langfuse, Arize 2026](https://www.digitalapplied.com/blog/agent-observability-platforms-langsmith-langfuse-arize-2026)
* [LLMOps Observability comparison May 2026](https://medium.com/@kanerika/llmops-observability-langsmith-vs-arize-vs-langfuse-vs-w-b-f1baeabd1bbf)
* [RAGAS, TruLens, DeepEval comparison (Atlan)](https://atlan.com/know/llm-evaluation-frameworks-compared/)
* [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)

### Sécurité et guardrails
* [Production LLM Guardrails: NeMo, Guardrails AI, Llama Guard (PremAI)](https://blog.premai.io/production-llm-guardrails-nemo-guardrails-ai-llama-guard-compared/)
* [5 Best AI Guardrails Platforms 2026 (Galileo)](https://galileo.ai/blog/best-ai-guardrails-platforms)
* [OWASP Top 10 for LLM Applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/)

### Anti-loop, agentic patterns, generic
* [Stop the Loop: Preventing Infinite Conversations (DEV)](https://dev.to/alessandro_pignati/stop-the-loop-how-to-prevent-infinite-conversations-in-your-ai-agents-ekj)
* [AI Agent Engineering 2026](https://blog.whoisjsonapi.com/ai-agent-engineering-in-2026-architectures-patterns-and-real-world-systems/)
* [AI Agent Orchestration Patterns (Microsoft Learn)](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)

### Versioning et cost
* [Telnyx AI Agents canary deployments](https://telnyx.com/release-notes/versioning-canary-deployments)
* [Best LLM Cost Tracking Tools 2026 (AI Cost Board)](https://aicostboard.com/guides/best-llm-cost-tracking-tools-2026)
