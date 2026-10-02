# Current Architecture Audit

## Scope

This audit covers the current working tree, the FastAPI and React entry points, local model and RAG integration, the science-exploration implementation, and representative theory documents currently present in the local knowledge database. No separate theory Markdown corpus is checked into the repository. The local `backend/data/rag.sqlite` currently contains 216 file records across 173 distinct filenames, including files from multiple accounts.

Representative theory material inspected includes `Core Theoretical Framework.md`, `Structural Vital Force Theory (SVFT).md`, `structural_generative_force_theory.md`, `theory_overview_zh.md`, `CoreFormulas.md`, `313 Structural Vital Force Unified Formula (v5.0).md`, `ExperimentalPlans.md`, `ExperimentalPlans_Full.md`, `Three-Scale Validation Scheme of Structural Vital Force Theory.md`, the ACLU modeling document, and the SVF Transformer design document. These documents describe a speculative framework with concepts including structure, vitality, boundary, structural angle, spiral coupling, and cross-scale emergence. They contain proposed formulae and experiments, but the documents themselves do not establish empirical validation. Several are drafts or conversational material, so future agents must preserve document provenance and epistemic status.

## Repository Layout

| Area | Current files | Responsibility |
|---|---|---|
| Backend API, chat, auth, RAG | `backend/app/main.py` | FastAPI routes, cookie authentication, chat history, Ollama embeddings, SQLite RAG, model-backed chat and streaming |
| Science exploration | `backend/app/research.py` | Agent definitions, Crossref/arXiv search, model call, task loop, checkpointing, SQLite output storage, router construction |
| Agent profiles and routing | `backend/app/agents/` | Typed `AgentProfile`, JSON-backed `AgentRegistry`, seven workflow modes, deterministic question router, and bounded depth budgets |
| Provider and retrieval contracts | `backend/app/providers/`, `backend/app/retrieval/` | OpenAI-compatible chat adapter and owner-scoped adapter over the current `RAGStore` |
| Backend tests | `backend/tests/` | Registry, routing, model-adapter, ownership-scoping, and checkpoint regression tests |
| Local runtime | `start.ps1`, `setup-local-model.ps1`, `backend/Modelfile` | Ollama setup/model selection, Python and Vite startup, model and embedding preflight |
| Frontend | `frontend/src/App.tsx`, `frontend/src/style.css` | Authentication, chat, knowledge library, research task creation and result previews, themes/locales/responsive styling |
| Frontend API proxy | `frontend/vite.config.ts` | Forwards `/api` to the configured backend URL |
| Local data | `backend/data/*.sqlite` | Accounts, chat history, knowledge files/chunks/vectors, research runs and outputs |

The backend tests use Python's built-in `unittest` runner, avoiding a new dependency. The frontend has typecheck/build scripts.

## Current Research Roles

The JSON profiles under `backend/app/agents/profiles/` define six selectable expert roles and three internal roles: synthesis, adversarial review, and researcher response. `backend/app/research.py` loads these through `AgentRegistry` and keeps an ID-to-profile mapping for the execution loop.

| ID | Current role | Current responsibility |
|---|---|---|
| `physics` | Fundamental Physics | Fields, spacetime, symmetry, and fundamental interactions |
| `math` | Mathematical Structures | Formal structures, axioms, symmetry, and provability |
| `complexity` | Complex Systems | Nonlinear dynamics, networks, phase transitions, and emergence |
| `cosmology` | Cosmology | Early universe, observations, and standard cosmological models |
| `foundations` | Foundations of Science | Theory unification, concepts, and cross-disciplinary comparison |
| `critic` | Critical Reviewer | Counterexamples, evidence gaps, falsifiability, and alternatives |
| `synthesis` | Synthesis Researcher | Summarizes the successful expert outputs into a round summary |
| `debate_critic` | Adversarial Review | Challenges evidence and assumptions before synthesis in theory attack, peer review, and experiment design |
| `debate_response` | Researcher Rebuttal | Responds to the review, qualifies claims, and records unresolved objections |

These are prompt roles rather than independent model instances. The frontend can route roles automatically by question and workflow, or allow manual selection. Fast, Normal, and Deep budgets cap selections at 3, 7, and 15 respectively; only six selectable profiles exist today, so the current practical maximum remains six.

The current router uses transparent keyword/domain rules and workflow requirements. It is not an LLM planner. Three workflows now support a bounded critic → researcher response → synthesis-as-judge cycle; task, claim/evidence, and owner-scoped lexical memory records persist in SQLite. It does not yet build sub-question task graphs or provide multi-round rebuttal.

## Current Execution Flow

1. The frontend posts a title, question, selected expert IDs, and either a finite round count or continuous-run setting to `POST /api/research/runs`.
2. The backend stores a run row and starts a daemon thread for that run.
3. Each round retrieves up to four chunks from the current user's local RAG and searches Crossref and arXiv for the research question.
4. The same sources and a rolling excerpt of recent outputs are placed in each selected expert's prompt. Experts run through one shared local model endpoint; the working tree now supports a separate `RESEARCH_MODEL` setting.
5. The backend persists retrieval, expert, debate, and synthesis task states. Explicitly tagged findings, hypotheses, predictions, counterevidence, and limitations are saved as claims. `[W#]`/`[K#]` references link claims to normalized evidence and successful agent outputs. Theory attack, peer review, and experiment design add a bounded critic → researcher response → synthesis-as-judge cycle.
6. The run's round and summary are checkpointed. Pause, stop, resume, and process-restart recovery are represented in SQLite. Existing completed outputs for the current round are skipped on resume; failed outputs are retried when that round is still current.
7. The frontend polls task detail and run lists every four seconds. It can preview every saved output and list the output's attached source links.

The API is mounted under `/api/research`: agent and workflow listing, route preview, run create/list/detail, bounded per-run graph, and run pause/resume/stop. Authentication is enforced at the router boundary and both the research store and RAG query are scoped to the current owner.

## Model, Knowledge, and Evidence Interfaces

- Chat uses LangChain `ChatOpenAI` against an OpenAI-compatible local endpoint. Research uses a separate provider-neutral `ChatModel` contract with an OpenAI-compatible adapter.
- The working tree now supports `RESEARCH_MODEL` independently from `LOCAL_MODEL`; `start.ps1` selects the Qwen3 4B Instruct 2507 quantized model for research, while ordinary chat can keep using `chatai-local`.
- Markdown files, 800-character chunks with 120-character overlap, and JSON-encoded Qwen3-Embedding vectors are stored in SQLite. Research calls the RAG store through its `search()` method; no research code depends directly on vector storage internals.
- `RAGStore.search()` computes cosine similarity over all chunks owned by the current user. FTS5 is also created, but the current retrieval path does not use lexical ranking or a hybrid ranker.
- Web sources and local passages are passed as prompt context. Explicitly tagged claims link to cited normalized source records. Current provenance distinguishes internal theory, Crossref bibliographic records, preprints, and general web references; it does not independently verify peer-review status or store calibrated confidence.
- Online search is currently a single broad search derived from the original research question each round; it is not specialized by agent or sub-question.

## Existing Strengths to Preserve

- Local-first deployment and a local embedding model.
- Authentication-aware ownership for RAG and research tasks.
- Durable SQLite checkpoints and per-agent output previews.
- Crossref and arXiv source links, plus local Markdown retrieval.
- Clear separation between ordinary chat and science exploration in the UI.
- A useful initial role set spanning physics, mathematics, complexity, cosmology, foundations, and criticism.
- Explicit uncertainty language in parts of the prompts and UI disclosure that exploration results are not established scientific conclusions.

## Main Constraints to Address

1. **Registry is now separated, execution is still fused.** Agent metadata has moved to JSON profiles, but prompt construction, scheduling, persistence, and model invocation remain in `research.py`.
2. **No dynamic router or planner exists.** The user manually picks experts, and all chosen experts are run in the same pattern each round.
3. **Collaboration is bounded but incomplete.** Theory attack, peer review, and experiment design have one critic → researcher response → synthesis-as-judge cycle. Other workflows still rely on parallel experts and synthesis; there is no decomposed sub-question graph or repeated debate loop.
4. **Claim/evidence links are early.** Explicitly tagged claims link to `[W#]`/`[K#]` source records with support or contradict relations, but local chunks are represented by source filename rather than stable chunk IDs, and confidence is not calibrated.
5. **Research memory is first-pass.** Each successful round stores a synthesis and tagged claims, retrieves up to three lexically relevant memories for the same owner, and records those links. It does not yet maintain contradictions, theory versions, research decisions, or embedding-ranked memory.
6. **The research model and chat model clients are coupled to one wire protocol.** A provider switch currently requires changes in more than one code path.
7. **The UI assumes exactly six experts.** Counts, selection limits, English focus copy, and workflows must become registry-driven.
8. **Run recovery is per-round, not dependency-aware.** A partial round may advance when one expert succeeded; an earlier failed expert is then not naturally retried when later rounds proceed.
9. **Theory files are user data, not source-controlled docs.** The checked-out source tree does not contain the Markdown corpus; local `rag.sqlite` is user-specific and must not become a code/config dependency.
10. **Regression coverage is growing.** Tests now cover registry, routing, adapter behavior, ownership, checkpoint resume, legacy database migration, claim/evidence links, debate orchestration, and memory isolation. More end-to-end API and live-model evaluations are still needed.

## What to Keep and What to Refactor

Keep the existing FastAPI application, account ownership boundaries, SQLite persistence, local RAG interface, Crossref/arXiv adapters, current UI workspace, checkpoint behavior, and the six current selectable expert identities. Refactor the monolithic research engine into registry, routing, orchestration/workflow, model-provider, evidence/memory, and persistence modules. Keep API payloads compatible during migration; add optional workflow/depth fields and retain explicit expert selection as an override.

Do not migrate the theory corpus into source files or bake its content into agent profiles. Profiles should describe expertise and retrieval policy; evidence should come from the current account's knowledge base and cited public sources at runtime.

## Recommended Staged Direction

Build the runtime around approximately **48 configured role profiles**, not 48 simultaneously running models. Begin implementation with a tested core of about 12 profiles and expand by configuration into the requested final catalogue. A typical run should use 3–10 roles; Fast, Normal, and Deep Research should cap cost and parallelism explicitly. Detailed target layers and workflows are in [MULTI_AGENT_ARCHITECTURE.md](MULTI_AGENT_ARCHITECTURE.md).
