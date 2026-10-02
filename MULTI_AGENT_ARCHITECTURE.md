# SVF Multi-Agent Research Architecture

## Design Goals

Build a local-first AI research organization for Structural Generative Force / Structural Vital Force research. Expertise is represented by reusable role profiles, while one configured local model can serve many roles. A router selects only the roles needed for a question. Every workflow has explicit stage limits, evidence provenance, checkpoint behavior, and a review path that can challenge SVF claims.

The initial production target is **48 configured role profiles**. A typical task uses 3–10. Fast, Normal, and Deep Research bound the number of selected roles, debate rounds, prompt context, and parallel model calls. These values are configuration, not a requirement to start that many model instances.

## Ten-Layer Organization and 48 Profiles

| Layer | Role profiles | Count |
|---|---|---:|
| 1. Central coordination | Chief Scientist, Research Planner, Agent Router, Research Coordinator | 4 |
| 2. SVF foundation | SVF Core & Structure, Generative Force, Structure–Energy, Spiral Dynamics, Hierarchical Emergence, AICU Framework | 6 |
| 3. Mathematics and formalization | Mathematical Formalization, Minimal Model, Dynamical Systems, Information Theory, Network Science, Geometry/Topology & Scaling | 6 |
| 4. Natural sciences | Cosmology, Gravitation, Quantum Foundations, Statistical Physics/Thermodynamics, Condensed Matter, Astronomy/Observation | 6 |
| 5. Life and complex systems | Origin of Life, Evolution, Systems Biology, Complex Systems/Ecology, Neuroscience | 5 |
| 6. Information and AI | AI Architecture, Transformer Research, Artificial Life, Recursive Intelligence, Memory & Information Spiral | 5 |
| 7. Philosophy and cognition | Philosophy of Science/Epistemology, Ontology, Cognitive Science | 3 |
| 8. Validation and criticism | Skeptic/Counterexample, Falsification/Prediction, Logic/Prior-Theory Comparison, Experimental Design, Statistical Validation, Research Judge | 6 |
| 9. Knowledge and evidence | Knowledge Retrieval, Evidence/Citation Manager, Contradiction/Theory-Version Tracker | 3 |
| 10. Theory synthesis | Synthesis, Theory Architect, Research Report, Open Problems/Hypothesis Curator | 4 |
| **Total** |  | **48** |

The requested overlapping specialties are combined where a single stable prompt/tool policy is enough. Profiles remain independently selectable and can later be split if evaluation shows a meaningful difference. The existing six expert roles map forward as follows: physics → natural sciences, math → mathematics, complexity → life/complex systems, cosmology → natural sciences, foundations → philosophy/synthesis, and critic → validation.

## Profile and Registry Model

Use one typed profile schema and JSON profile files so adding a role does not require a Python class or a new frontend list entry. JSON avoids adding a YAML dependency to the current Python environment.

```json
{
  "id": "cosmology",
  "name": {"zh": "宇宙学研究员", "en": "Cosmology Researcher"},
  "layer": "natural_sciences",
  "role": "Evaluate cosmological claims against observations and standard models.",
  "system_prompt": "...",
  "domains": ["cosmology", "large_scale_structure"],
  "skills": ["observation_comparison", "model_comparison"],
  "tools": ["web_search", "knowledge_retrieval"],
  "allowed_agents": ["svf_core", "gravitation", "skeptic", "falsification"],
  "knowledge_scope": ["theory_internal", "peer_reviewed", "preprint"],
  "temperature": 0.25,
  "max_tokens": 1200,
  "priority": 70,
  "cost_level": "normal",
  "review_required": true
}
```

`AgentRegistry` validates and loads profiles at startup, rejects duplicate IDs and invalid tool/role references, and exposes `register_agent`, `get_agent`, `list_agents`, `find_agents_by_domain`, and `find_agents_by_capability`. A registry endpoint supplies the UI. Runtime overrides should be explicit and validated rather than allowing profiles to invoke arbitrary other profiles.

## Target Package Layout

```text
backend/app/
  agents/
    base.py                 # AgentProfile and AgentTask types
    registry.py             # profile validation and discovery
    router.py               # relevance scoring and role selection
    profiles/*.json         # one file per role
  research/
    api.py                  # backward-compatible FastAPI routes
    planner.py              # question decomposition and depth plan
    orchestrator.py         # bounded graph execution and checkpoints
    workflows.py            # named workflow stage definitions
    debate.py               # capped critique/rebuttal rounds
    evidence.py             # claims, citations, confidence, provenance
    memory.py               # research memory and open questions
    graph.py                # typed nodes/edges backed by SQLite
    repository.py           # run/output/claim persistence
  providers/
    models.py               # ChatModel protocol and provider factory
    ollama.py                # local Ollama adapter
    openai_compatible.py     # compatible local/cloud adapter
  retrieval/
    base.py                 # KnowledgeRetriever protocol
    rag_store.py            # adapter around the existing RAGStore
    web_search.py           # Crossref/arXiv source adapter
```

Migration should be incremental. Keep the existing `main.py` application and route prefix; initially have `/api/research` delegate to the new package. Move logic from `research.py` behind tests before deleting the old implementation.

## Provider and Retrieval Contracts

Define small interfaces so roles and orchestration do not know whether a provider is Ollama, another OpenAI-compatible endpoint, or a later cloud model.

- `ChatModel.generate(messages, model, temperature, max_tokens, timeout) -> ModelResponse`
- `KnowledgeRetriever.search(query, owner_id, scope, top_k) -> RetrievedChunk[]`
- `WebSearch.search(query, providers, limit) -> SourceDocument[]`
- `ResearchRepository` handles runs, task nodes, outputs, evidence, and checkpoints.

The first migration keeps the installed Qwen3 embedding model, SQLite storage, and `RAGStore.search()` behind the retrieval adapter. It does not require changing embedding models or moving user files out of SQLite. Hybrid lexical/vector retrieval and reranking can be added later behind the same contract.

## Routing and Resource Profiles

The router should use a transparent two-stage strategy:

1. Deterministic rules map explicit terms and task intent to candidate domains, required validation roles, tools, and workflow mode.
2. A bounded planner call may rank candidates and propose sub-questions. Validate its output against the registry, mode, user choices, and resource budget; unknown IDs are discarded.

Always add the minimum needed evidence and criticism roles. Do not require every task to invoke SVF Core: a pure methods or literature question can be answered without theory advocacy. User-selected roles act as required/optional overrides but still obey the run's maximum active-agent limit.

| Depth | Typical active roles | Debate rounds | Parallel LLM calls | Intended use |
|---|---:|---:|---:|---|
| Fast | 2–3 | 0–1 | 1–2 | Clarification, brief expert view |
| Normal | 4–7 | 1 | 2 | Cross-disciplinary analysis |
| Deep Research | 8–15 | 1–3 | 2 (configurable) | Longer investigations, intensive review |

Expose these as settings: `max_active_agents`, `max_parallel_agents`, `max_debate_rounds`, `max_context_tokens`, and `research_depth`. Keep parallelism conservative for an 8 GB local GPU. Depth controls role count and stages; it must not silently create more model instances.

## Composable Workflow Modes

All modes share planning, provenance-aware retrieval, bounded execution, persistence, and final reporting. They differ in role selection and stage graph.

| Mode | Bounded stage outline |
|---|---|
| Expert consultation | Router → one or two domain experts → Judge/Synthesis |
| Multidisciplinary research | Planner → 3–7 domain experts in parallel → critic → synthesis |
| Theory attack | SVF claim extraction → skeptic/counterexample → prior-theory comparison → logic review → falsification → judge |
| Mathematical modeling | Core concepts/variables → formalization → minimal model → dynamical/simulation review → critic |
| Experimental design | Hypothesis → prediction → experimental/statistical design → falsification criteria → review |
| Open exploration | Planner → 4–10 diverse experts → one capped debate → synthesis/open problems |
| Peer review | Evidence audit → methods/statistical review → logic/prior-art review → verdict and revisions |

Agent tasks should be nodes with declared dependencies. Independent experts can run in parallel; debate, judging, and synthesis wait on the nodes they consume. A default debate is one critique and one response; the hard limit is three rounds.

## Bounded Research Flow

```text
User question + mode/depth
        ↓
Planner: research objective, sub-questions, success criteria
        ↓
Router: validated role IDs + tools + max-agent budget
        ↓
Knowledge retrieval + web search (record each source once)
        ↓
Independent expert tasks (parallel within GPU budget)
        ↓
Critique/debate tasks (only when selected by workflow)
        ↓
Judge: compare evidence, disagreements, uncertainty, and missing tests
        ↓
Synthesis/report + hypotheses + open problems
        ↓
SQLite checkpoint and Research Graph update
```

Each stage writes its result before dependent work begins. Resume reuses completed nodes and retries failed/incomplete nodes at the same checkpoint. Run state should identify the active stage and node rather than only `current_round`.

## Evidence and Research Graph

Use ordinary SQLite tables and typed JSON for the first version. A graph database is unnecessary at this scale.

| Entity | Minimum fields |
|---|---|
| `research_runs` | owner, question, workflow, depth/budgets, status, active stage, summary, timestamps |
| `research_tasks` | run, parent task, stage, profile ID, sub-question, dependencies, status, attempt, output |
| `claims` | run/task, text, claim kind, confidence, uncertainty, theory version, status |
| `evidence` | source type, document ID/chunk ID or URL/DOI, excerpt, retrieved timestamp, metadata |
| `claim_evidence` | claim, evidence, relation (`supports`, `contradicts`, `context`), rationale |
| `claim_reviews` | claim, supporting/opposing profiles, counterargument, review outcome |
| `research_memory` | hypotheses, findings, unresolved questions, contradictions, decisions, links to claims/tasks |
| `research_edges` | source node, target node, relation (`decomposes_to`, `supports`, `contradicts`, `tests`, `supersedes`) |

Evidence/source types must distinguish at least `theory_internal`, `peer_reviewed`, `preprint`, `web_reference`, `model_inference`, `hypothesis`, and `experiment_result`. Internal Markdown proves only what the internal theory document states; it is not external validation. A model-generated claim without a source is explicitly recorded as an inference or hypothesis. Citation Agent validates source IDs and URLs rather than inventing references.

The final report should separate established external findings, what internal SVF documents propose, model-derived interpretation, open hypothesis, counter-evidence, and testable predictions. Confidence is a calibrated qualitative/structured estimate with reasons, not a vote count.

## Research Memory and Theory Evolution

At the end of each task node and run, save compact structured memory: objective, selected roles, key claims, evidence links, opposing arguments, unresolved questions, model/recipe version, and next action. Later tasks retrieve memory by owner, theory version, and semantic relevance; they do not blindly append all prior prose to the prompt.

Theory Evolution tracks document/version identifiers and explicit supersession links. It must never merge conflicting formulas or silently treat a newer uploaded file as scientifically better. Researchers can inspect both the earlier statement and the revision history.

## UI Evolution

Keep the existing Science Workspace, task list, output previews, and polling flow. Add progressively:

1. Workflow selector and Fast/Normal/Deep Research depth.
2. “Auto-select experts” as default plus an advanced expert override from the registry.
3. Current stage and active task cards with queued/running/reviewing/complete/failed states.
4. Debate panel showing claim, critique, response, and judge result.
5. Evidence panel per claim with internal/web/paper/hypothesis labels and support/counter-evidence.
6. Final report sections for conclusion, confidence, limitations, predictions, open problems, and sources.

Do not replace the chat app shell or redesign the full website for this work. All labels and role names come from the API registry and remain bilingual.

## Target Directory Layout

The concrete package layout is specified above under “Target Package Layout.” Profile files are the extension point. Runtime code stays small and typed; adding a profile should require no route, orchestrator, or frontend source edit.

## Migration Plan

1. **Compatibility and characterization:** add architecture docs and tests describing the six current roles, route contracts, owner isolation, saved outputs, and resume semantics. Keep existing API responses intact.
2. **Typed registry:** define profile schema, JSON loader, validation, registry methods, and six migrated profiles. Add a registry endpoint while retaining legacy `/api/research/agents` response fields.
3. **Provider/retrieval adapters:** wrap current Ollama calls and `RAGStore` behind protocols; add tests for response parsing, timeout/errors, and per-owner retrieval. **Completed (initial adapters):** `ChatModel` contract, configurable OpenAI-compatible chat-completions provider, and owner-scoped `KnowledgeRetriever` adapter around the existing `RAGStore`.
4. **Planner/router and budgets:** introduce workflow/depth parameters with defaults matching current behavior; add deterministic route tests for the requested sample questions before enabling model planning. **Completed (deterministic first pass):** seven selectable workflow modes, Fast/Normal/Deep budgets, keyword/domain role routing, explicit role overrides, route preview endpoint, and persisted route metadata.
5. **Task graph, memory, evidence:** add SQLite migrations and persist task nodes, claims, source chunks, provenance types, and memory while still writing legacy `research_outputs` for the existing UI. **Partial:** run-stage task/checkpoint rows, normalized evidence, explicitly tagged claims with support/counterevidence links, and per-round owner-scoped research memory are persisted. Later runs retrieve up to three lexically relevant prior memories and store links. Embedding-based memory search and memory editing/retention policies remain.
6. **Workflow and debate:** migrate one workflow at a time, start with consultation and multidisciplinary modes, then theory attack and experiment design. Cap every cycle. **Partial:** theory attack, peer review, and experiment design run one bounded critic → researcher response → synthesis-as-judge cycle per research round; configurable budgets cap depth but repeated debate rounds within one research round are not implemented yet.
7. **UI:** add depth/mode, auto-routed roles, progress stages, and claim/evidence previews using existing workspace components. **Partial:** workflow/depth selectors, automatic/manual role choice, route reason, persisted task-stage status, structured claim cards, and a provenance-labelled source panel are implemented; richer uncertainty/confidence and contradiction graph views remain.
8. **Catalogue expansion:** grow from the tested core toward 48 profiles. Require route/workflow fixture coverage before a profile is enabled by default.

### Current Progress

The registry, deterministic routing, provider/retrieval adapters, persistent research stages, and cross-run memory are implemented in the working tree. Six expert roles are selectable; synthesis, adversarial review, and researcher response stay internal. Seven workflows and three bounded depth budgets feed routing. Runs persist task-stage state; explicitly tagged findings, hypotheses, predictions, counterevidence, and limitations become structured claims. Explicit `[W#]`/`[K#]` citations link claims to normalized evidence and agent outputs. Evidence distinguishes theory-internal material, Crossref bibliographic records, preprints, and general web references. A Crossref record is not labeled as peer-reviewed because the importer does not verify review status. Selected workflows run one critic → researcher response → synthesis-as-judge cycle per research round. Per-round memory stores the question, synthesis, structured claims, evidence references, and open items; future tasks retrieve up to three lexically relevant memories belonging to the same owner and display those links. The UI displays stages, task statuses, structured claims, route reasoning, historical memory, and traceable sources while retaining legacy output cards. The 24 standard-library backend tests, production frontend build, typecheck, and app OpenAPI import pass.

## First Implementation Slice

The first code slice should remain small and safe:

- Completed: add backend tests and fixtures for the current roles, run persistence/resume, and model error handling.
- Completed: add `backend/app/agents/base.py`, `registry.py`, `profiles/*.json`, and migrate the existing six roles plus synthesis without changing workflow semantics.
- Completed: make `research.py` query the registry while retaining current API fields and existing SQLite run/output tables.
- Completed: make the frontend render agent choices from the registry rather than a hard-coded six-agent count.
- Completed: add deterministic route fixtures, workflow/depth fields, and budgets.
- Completed: separate model generation and local retrieval behind provider/retriever contracts while preserving the current API and database behavior.
- Partial: persist stages/evidence, parse explicitly tagged structured claims with support/counterevidence relations, run one adversarial review → response → judge cycle in selected workflows, retrieve owner-scoped lexical research memory, and expose a bounded run graph of tasks, outputs, claims, evidence, and memory links.
- Next: add calibrated uncertainty/confidence and theory-version tracking, then expand role profiles. Memory ranking can later move behind the retriever interface to use embeddings.

Do not add LLM-based routing, a full evidence graph, debate, or a 48-profile catalogue in the same patch. Routing is currently deterministic and fixture-tested; use provider/retrieval adapters and stage persistence as the next slices.

## Required Acceptance Scenarios

1. “结构生力是什么？” selects a small core-theory, evidence, and review set.
2. “信息螺旋是否可以用于 Transformer？” selects information/AI roles and at least one critical reviewer.
3. “结构生力能否解释宇宙膨胀？” selects cosmology/gravitation plus evidence and skeptical review.
4. “请攻击结构生力理论。” selects the attack workflow and its counterexample, falsification, prior-theory, and logic stages.
5. “请设计一个能够证伪信息螺旋的实验。” produces a hypothesis, a differentiating prediction, experimental/statistical design, and explicit falsification criteria.

Each scenario must assert selected roles and stage types using deterministic fixtures; it should not rely on live LLM output for router correctness.
