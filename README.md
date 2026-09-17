# VeriAgent

**Verifiable agent runtimes for evidence-grounded reasoning, recovery, and auditable data analysis.**

VeriAgent is a research-oriented Python project exploring a stricter form of agent reliability:

> **An agent should not only produce an answer. It should expose enough evidence, verification, control decisions, and execution traces for its behavior to be inspected and, when possible, recovered.**

The repository currently contains two related but intentionally separate systems:

- **ProofWriter VeriAgent** — a verifiable retrieval-and-recovery runtime for multi-hop logical reasoning under an Open World Assumption (OWA).
- **Olist Data Agent** — a constrained data-analysis pipeline combining LLM generation with deterministic metric contracts, read-only SQL execution, optional cross-checking, and structured traces.

They share the same engineering philosophy — **model generation should be bounded by deterministic checks and observable traces** — but they are **not yet a single integrated runtime**.

---

## Why VeriAgent?

Many agent systems are optimized around one question: *did the model produce a useful answer?*

VeriAgent focuses on a stricter set of questions:

- What evidence supports the answer?
- Can the proof or execution result be checked mechanically?
- Does the system know when evidence is insufficient?
- Can it distinguish an unsupported request from an execution failure?
- What happens when retrieval or tool execution fails?
- Can the decision path be reconstructed from traces?

The project therefore separates **generation**, **verification**, **control**, and **observability** instead of treating the LLM response itself as the final source of truth.

---

## 1. ProofWriter VeriAgent

The original VeriAgent runtime targets ProofWriter-style logical reasoning tasks.

At a high level:

```text
Question
   ↓
Planner
   ↓
Evidence Retrieval
   ↓
Answerer
   ↓
Auditor / Proof Verification
   ↓
Controller
   ├── STOP
   ├── RETRIEVE
   ├── REPLAN
   ├── ROLLBACK
   └── CONTINUE
   ↓
Verified Result + Trace
```

### Core ideas

- **Explicit planning** for the target proposition and its complement.
- **Local evidence retrieval** instead of blindly exposing the full world state.
- **Forward reasoning** to construct candidate answers and proofs.
- **Deterministic proof verification** before accepting TRUE/FALSE conclusions.
- **Coverage checks for UNKNOWN**, rather than treating uncertainty as a free-form model judgment.
- **Controller-driven recovery** with re-retrieval, replanning, checkpoints, and rollback.
- **Fault injection** for studying recovery behavior.
- **Structured event traces** for evaluation and downstream controller-SFT export.

The main runtime entry point is:

```text
runner/agent_loop.py::VeriAgentRunner
```

Key components:

| Component | Responsibility |
|---|---|
| `planner/` | Build and revise retrieval plans |
| `retrieval/` | Retrieve relevant local evidence |
| `answerer/` | Produce candidate logical answers |
| `auditor/` | Check reasoning state and verification conditions |
| `controller/` | Select `STOP / RETRIEVE / REPLAN / ROLLBACK / CONTINUE` |
| `tools/proof.py` | Forward inference and proof verification |
| `state/` | Runtime state, checkpoints, and trace persistence |
| `faults/` | Controlled failure injection |
| `baselines/` | Symbolic and LLM baselines |
| `evaluation/` | Run-level evaluation utilities |
| `sft/` | Controller trace export for supervised training data |

---

## 2. Olist Data Agent

The second subsystem applies the same verification-first philosophy to structured data analysis.

Instead of allowing an LLM to directly translate a question into SQL and return a number, the runtime inserts explicit contracts and deterministic execution boundaries:

```text
User Question
      ↓
LLM Analysis / Metric Selection
      ↓
Deterministic Metric Contract
      ↓
Schema Inspection
      ↓
LLM SQL Generation
      ↓
SQL Safety Validation
      ↓
Read-only DuckDB Execution
      ↓
Optional Independent Cross-check
      ↓
LLM Final Answer
      ↓
Trace + Run Summary
```

The main entry point is:

```text
data_agent/runner/minimal_runner.py::DataAgentRunner
```

### What the deterministic layer checks

The current Data Agent can enforce or expose:

- supported metric definitions;
- required tables;
- metric grain and time-field contracts;
- read-only SQL constraints;
- schema existence;
- bounded query results;
- optional independent SQL cross-checks;
- structured execution traces;
- explicit `STOP_WITH_DATA_GAP` behavior for known unsupported requests.

A key design principle is that **a SQL query successfully executing is not sufficient evidence that the business answer is correct**.

For example, the runtime distinguishes:

```text
FAILED
```

from

```text
STOP_WITH_DATA_GAP
```

The latter means the system intentionally refused to manufacture an answer when the available data or metric contract did not support the request.

---

## Repository Architecture

```text
veriagent/
├── answerer/              # Deterministic candidate answer generation
├── auditor/               # Verification and audit logic
├── baselines/             # Symbolic and LLM baselines
├── controller/            # Runtime control policy
├── data_agent/            # Olist constrained data-analysis subsystem
│   ├── llm/               # OpenAI-compatible client + structured prompts
│   ├── runner/            # Minimal linear Data Agent runtime
│   ├── tools/             # Metric, schema, SQL, join, cross-check tools
│   └── trace/             # Data Agent event tracing
├── docs/owner_guide/      # Detailed architecture and maintenance guides
├── evaluation/            # Evaluation utilities
├── faults/                # Fault injection
├── knowledge/             # Human-readable metric contracts
├── llm/                   # LLM client used by ProofWriter baselines
├── planner/               # Deterministic planning and replanning
├── retrieval/             # Local retrieval
├── runner/                # ProofWriter VeriAgent runtime loop
├── scripts/data_agent/    # Data preparation, smoke, OpenPAI, scoring scripts
├── sft/                   # Controller trace export
├── state/                 # Agent state, checkpoints, JSONL trace store
├── tools/                 # Retrieval and proof-verification tools
├── cli.py                 # ProofWriter CLI entry point
└── schemas.py             # Shared ProofWriter runtime schemas
```

---

## Runtime Design Principles

### 1. Verification is not model confidence

An answer is not considered verified merely because the model expresses high confidence. Verification should be tied to inspectable evidence or deterministic checks whenever possible.

### 2. Tool capability and tool policy are different

A tool being implemented or registered does not mean the current runtime automatically invokes it. This distinction is especially important in the Data Agent, where some validation capabilities are present but not yet wired into every request path.

### 3. Failure should be explicit

The runtime attempts to distinguish:

- insufficient evidence;
- unsupported metrics or data gaps;
- malformed model output;
- unsafe SQL;
- execution errors;
- failed verification;
- exhausted recovery paths.

### 4. Traces are first-class artifacts

Both research tracks preserve structured execution evidence so that a run can be inspected after the fact instead of being reduced to a final label or answer string.

---

## ProofWriter CLI

The ProofWriter side exposes a lightweight CLI through `cli.py`.

Because this repository is currently a source snapshot rather than a fully packaged release, run it with the repository available as the `veriagent` Python package — for example, from the parent directory of the cloned repository.

### Validate ProofWriter tasks

```bash
python -m veriagent.cli validate \
  --input /path/to/meta-test.jsonl
```

### Compare baselines and VeriAgent

```bash
python -m veriagent.cli run \
  --input /path/to/meta-test.jsonl \
  --methods direct-full,standard-rag,react,fixed,veriagent \
  --output results.json \
  --trace-output trace.jsonl
```

Available offline methods include:

```text
direct-full
standard-rag
react
fixed
veriagent
veriagent-auditor
veriagent-recovery
```

LLM-backed baselines are also implemented and use an OpenAI-compatible endpoint through environment variables such as:

```bash
export LLM_API_KEY=...
export LLM_MODEL=...
export LLM_BASE_URL=...
```

### Export controller traces

```bash
python -m veriagent.cli export-sft \
  --trace-input trace.jsonl \
  --output controller_sft.jsonl
```

---

## Data Agent Usage

The Data Agent source and experiment scripts are included in this repository, but the public snapshot does **not** currently bundle all datasets, generated DuckDB assets, tests, or experiment-result directories referenced by the internal owner documentation.

Relevant scripts include:

```text
scripts/data_agent/build_olist_duckdb.py
scripts/data_agent/profile_olist.py
scripts/data_agent/smoke_data_tools.py
scripts/data_agent/smoke_minimal_agent.py
scripts/data_agent/run_openpai_qwen_smoke.py
scripts/data_agent/run_openpai_qwen_smoke_job.sh
scripts/data_agent/score_qwen_smoke.py
scripts/data_agent/validate_v0_gold.py
```

The Data Agent additionally depends on DuckDB and an OpenAI-compatible model endpoint for real-model runs.

For implementation details, start with:

- [`docs/owner_guide/01_project_owner_guide.md`](docs/owner_guide/01_project_owner_guide.md)
- [`docs/owner_guide/02_runtime_flow.md`](docs/owner_guide/02_runtime_flow.md)
- [`docs/owner_guide/03_module_learning_guide.md`](docs/owner_guide/03_module_learning_guide.md)
- [`docs/owner_guide/04_design_reasoning.md`](docs/owner_guide/04_design_reasoning.md)

---

## What Is Implemented Today?

### ProofWriter VeriAgent

Implemented:

- deterministic planning and replanning;
- local evidence retrieval;
- forward logical inference;
- proof verification;
- UNKNOWN coverage auditing;
- controller actions;
- checkpoints and rollback;
- fault injection;
- symbolic and LLM baselines;
- JSONL traces;
- controller-SFT export.

### Olist Data Agent

Implemented:

- constrained metric selection;
- deterministic metric contracts;
- schema inspection;
- structured LLM protocols;
- read-only SQL safety checks;
- DuckDB execution;
- optional SQL cross-checking;
- explicit data-gap stopping;
- event traces and run summaries;
- local/mock and OpenPAI-oriented experiment scripts.

---

## Current Limitations

The repository intentionally documents incomplete pieces instead of presenting them as finished capabilities.

Most importantly:

- the Olist Data Agent is **not yet connected** to the ProofWriter Planner/Auditor/Controller/Checkpoint recovery loop;
- SQL safety validation does **not** prove SQL business semantics;
- the Data Agent does not yet deterministically verify that the final natural-language `key_values` exactly match the SQL result;
- join-cardinality checking exists as a tool but is not automatically enforced on every runtime path;
- independent cross-check SQL is not automatically generated for every task;
- the current public repository snapshot does not include all datasets/tests/result artifacts used in the original research workspace;
- the repository is not yet packaged as a polished end-user service or library release.

These boundaries are part of the research problem rather than hidden implementation details.

---

## Documentation

The `docs/owner_guide/` directory contains a deeper engineering walkthrough:

| Document | Purpose |
|---|---|
| [`01_project_owner_guide.md`](docs/owner_guide/01_project_owner_guide.md) | System-level mental model and implementation boundaries |
| [`02_runtime_flow.md`](docs/owner_guide/02_runtime_flow.md) | End-to-end Data Agent request flow |
| [`03_module_learning_guide.md`](docs/owner_guide/03_module_learning_guide.md) | Module responsibilities and code-reading guide |
| [`04_design_reasoning.md`](docs/owner_guide/04_design_reasoning.md) | Design decisions and trade-offs |
| [`05_debugging_and_maintenance.md`](docs/owner_guide/05_debugging_and_maintenance.md) | Debugging and operational workflow |
| [`06_interview_defense.md`](docs/owner_guide/06_interview_defense.md) | Evidence boundaries and technical Q&A |
| [`07_reading_order.md`](docs/owner_guide/07_reading_order.md) | Seven-day owner learning path |

---

## Research Direction

The longer-term direction is to move from **answer-generating agents** toward **evidence-producing, contract-aware, recoverable agents**.

Near-term engineering priorities include:

1. stronger deterministic SQL semantic validation;
2. final-answer/result consistency checking;
3. risk-triggered join-cardinality validation;
4. clearer cross-check independence guarantees;
5. stricter trace lifecycle/schema validation;
6. eventual integration of Data Agent auditing with controller-based repair and rollback.

---

## Project Status

VeriAgent is an active research and engineering project. The codebase is best viewed as a collection of **working research runtimes and verification components**, not as a production-ready autonomous agent platform.

Current package version:

```text
0.1.0
```

---

## Citation / License

A formal citation and license have not yet been added to this repository. Please check the repository history for the latest project status before reusing the code in downstream work.
