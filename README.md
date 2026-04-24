# Fraud Detection Agent with Graph Algorithms and LLM Reasoning

A research artifact demonstrating fraud detection through the combination of graph algorithms, semantic verification, and lightweight LLM reasoning. Built for ICTAI submission with emphasis on correctness and reproducibility.

## Overview

This system investigates fraud hypotheses by:

1. **Routing**: Classifying the hypothesis into one of 6 fraud patterns (ring, chargeback, routing anomaly, entity similarity, money flow, custom)
2. **Planning**: Selecting 1-3 appropriate graph projections and algorithms to explore
3. **Execution**: Running the algorithms in parallel on Neo4j + GDS, interpreting results
4. **Verification**: Double-checking claims with deterministic Cypher probes (CoVe-style)
5. **Synthesis**: Aggregating evidence and producing an analyst-facing verdict

**Key Design**: The algorithm reasons, the LLM routes. Graph algorithms are primary; Qwen 3.5 4B handles classification and template-shaped summarization, never freeform Cypher composition.

## Architecture

```
Hypothesis (analyst input)
    ↓
RouterAgent (classify into claim type)
    ↓
BranchPlannerAgent (select 1-3 investigation paths)
    ↓
[ReasoningBranchWorkflow] ×N (parallel)
    ├─ ProjectGraph (Cypher projection)
    ├─ RunAlgorithm (WCC/Louvain/PageRank/etc)
    ├─ InterpreterAgent (→ StructuralClaim)
    ├─ VerifyCypher (deterministic probe)
    └─ DropProjection (cleanup)
    ↓
AggregationActivity (consensus + conflicts)
    ↓
SynthesizerAgent (analyst-facing verdict)
    ↓
FraudVerdict
```

See [CLAUDE.md](CLAUDE.md) for detailed design principles, data contracts, and build order.

## Setup

### Prerequisites

- Python 3.12+
- Neo4j 5+ with GDS 2026.03
- Temporal Server
- Ollama (with `qwen3:4b` model pulled)

### Installation

```bash
# Clone and activate venv
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .
pip install -e '.[dev]'
```

### Configuration

Copy the provided `.env` file and adjust for your environment:

```bash
# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=changeme

# Temporal
TEMPORAL_HOST=localhost:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=fraud-agent

# Ollama
OLLAMA_URL=http://localhost:11434/v1
OLLAMA_MODEL=qwen3:4b
OLLAMA_NUM_PARALLEL=4
```

### Local Services

```bash
# Terminal 1: Temporal Server
temporal server start-dev

# Terminal 2: Neo4j (Docker)
docker run --rm -d \
  -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/changeme \
  -e NEO4J_PLUGINS='["graph-data-science"]' \
  neo4j:5-enterprise

# Terminal 3: Ollama
ollama serve

# Terminal 4: Seed the graph (optional, for testing)
neo4j-cli cypher -f tests/fixtures/graph_seed.cypher
```

## Running

### Start the Worker

```bash
python -m src.worker
```

This starts a Temporal worker that listens on the configured task queue and executes workflows.

### Submit a Hypothesis

```bash
python -m src.client --hypothesis "Collusion ring: customers C1 and C2 share device D5"
```

Or via Python API:

```python
from src.client import submit_investigation

verdict = submit_investigation(
    hypothesis="Unusual routing pattern across ACQ_1 and ACQ_2",
    trace_id="demo-001"
)
print(verdict)
```

### Run Tests

```bash
# All tests
pytest tests/

# Specific test suite
pytest tests/test_projections.py -v
pytest tests/test_workflow_investigation.py -v

# With coverage
pytest tests/ --cov=src --cov-report=term-missing
```

## Project Structure

```
src/
├── contracts/           # Data models (Pydantic)
│   ├── hypothesis.py   # Input: claim type, scope, urgency
│   ├── branch.py       # Branch specs, structural claims
│   └── verdict.py      # Output: final fraud verdict
├── gds/                 # Neo4j + GDS integration
│   ├── driver.py       # Neo4j session management
│   ├── projections.py  # 7 pre-registered graph projections
│   └── algorithms.py   # 6 graph algorithms (WCC, Louvain, PageRank, etc)
├── agents/              # LLM agents (PydanticAI + Ollama)
│   ├── router.py       # Classify hypothesis
│   ├── planner.py      # Plan investigation branches
│   ├── interpreter.py  # Interpret algorithm output
│   ├── verifier.py     # Select verification template
│   ├── synthesizer.py  # Analyst-facing summary
│   ├── composer.py     # Novel projection proposals (CUSTOM claims)
│   └── stubs.py        # Fallback logic when LLM unavailable
├── verifiers/           # Deterministic verification
│   └── templates.py    # Parameterized Cypher verification templates
├── activities/          # Temporal activities (deterministic units of work)
│   ├── parse.py        # Parse raw hypothesis text
│   ├── plan.py         # Branch planning
│   ├── project.py      # Create graph projections
│   ├── run_algo.py     # Execute GDS algorithms
│   ├── interpret.py    # Interpret results with agent
│   ├── verify.py       # Verify with Cypher
│   ├── aggregate.py    # Consensus logic
│   ├── synthesize.py   # Final verdict
│   └── compose.py      # Compose custom projections
├── workflows/           # Temporal workflows
│   ├── investigation.py # Top-level orchestration
│   └── branch.py       # Parallel investigation branch
├── worker.py           # Temporal worker entrypoint
├── client.py           # CLI / API client
└── metrics.py          # Activity instrumentation

tests/
├── fixtures/
│   └── graph_seed.cypher   # Synthetic fraud dataset
├── test_projections.py     # Projection correctness
├── test_algorithms.py      # Algorithm results
├── test_verifiers.py       # Verification templates
├── test_agents.py          # Agent routing
├── test_workflow_*.py      # Workflow orchestration
└── test_eval_harness.py    # Evaluation framework

evals/
├── scenarios.yaml      # 3 demo scenarios (shared_device_ring, merchant_cochargeback, decline_routing)
├── run_eval.py        # Evaluation harness
└── ablations.py       # Ablation testing for ICTAI paper
```

## Key Abstractions

### Projections Library (§4, CLAUDE.md)

Pre-registered graph subsets. The router picks by ID; no freeform Cypher from the LLM.

| ID | Signal |
|---|---|
| `shared_device_ring` | Customers sharing devices (collusion rings) |
| `shared_card_ring` | Customers sharing cards (account takeover) |
| `ip_cohort` | Customers from same IP (bot farms) |
| `merchant_cochargeback` | Merchants sharing charged-back customers |
| `money_flow` | Settlement paths (acquirer → issuer) |
| `payout_cluster` | Merchant → payout account → country |
| `decline_routing` | Acquirer decline patterns |

### Algorithm Library (§5, CLAUDE.md)

Graph algorithms applied to projections. Each projection has a pre-approved set of compatible algorithms.

| Algorithm | Use |
|---|---|
| `wcc` | Hard connected components (rings) |
| `louvain` | Soft community detection |
| `pagerank` | Entity centrality / importance |
| `node_similarity` | Embedding-based lookalike detection |
| `betweenness` | Bottleneck / bridge entities |
| `fastrp_knn` | Fast random projection + KNN |

### Verification Templates (§2, CLAUDE.md)

Deterministic Cypher probes that verify structural claims. Each template is parameterized and returns AGREE | DISAGREE | NOT_APPLICABLE.

Examples:
- `confirm_component`: verify nodes are in the same WCC/Louvain component
- `confirm_money_flow_path`: verify a settlement path exists
- `confirm_decline_routing`: verify an acquirer's decline anomaly

## Development

### Adding a New Projection

1. Add a function to `src/gds/projections.py` with signature `def project_X(session, **params) -> ProjectionHandle`
2. Register it in the projection library dict
3. Add unit test in `tests/test_projections.py` with known output on synthetic data
4. Update the catalog in §5 of CLAUDE.md

### Adding a New Algorithm

1. Implement in `src/gds/algorithms.py` with signature `def run_X(handle, **params) -> list[dict]`
2. Add to the algorithm library dict
3. Update the projection-to-algorithm mapping in §5
4. Add unit test

### Adding a New Verification Template

1. Create a Cypher probe in `src/verifiers/templates.py`
2. Test against known true claims on the synthetic dataset
3. Update the verifier agent to map `StructuralClaim` shapes to template IDs

### Running Evaluations

```bash
# Run all 3 demo scenarios
python evals/run_eval.py

# Run with ablations
python evals/ablations.py --ablate-gds-verifier
python evals/ablations.py --ablate-cypher-verifier
python evals/ablations.py --max-branches=1
```

See [CLAUDE.md §11](CLAUDE.md#11-ablation-hooks-for-the-ictai-paper) for all available ablation flags.

## Testing Strategy

The build follows a strict milestone-based order ([CLAUDE.md §10](CLAUDE.md#10-build-order-strict)):

- **M0**: Data contracts + projection/algorithm libraries (deterministic)
- **M1**: Full GDS library with unit tests
- **M2**: Verifier templates with CoVe validation
- **M3**: Single-branch workflow (no LLM)
- **M4**: Router + planner agents
- **M5**: Interpreter + verifier agents
- **M6**: Top-level orchestration + synthesizer
- **M7**: Composer agent (CUSTOM claims)
- **M8**: Evaluation harness

Run tests at each milestone to ensure stability before proceeding.

```bash
# Green tests are required to proceed
pytest tests/test_projections.py tests/test_algorithms.py        # M0
pytest tests/test_catalog.py                                     # M1
pytest tests/test_verifiers.py                                   # M2
pytest tests/test_workflow_branch.py                             # M3
pytest tests/test_branch_agents.py                               # M4
pytest tests/test_agents.py                                      # M5
pytest tests/test_workflow_investigation.py                      # M6
pytest tests/test_composer.py                                    # M7
pytest evals/run_eval.py                                         # M8
```

## Design Principles

1. **Algorithm-first reasoning**: GDS algorithms are the primary inference primitives
2. **Dual verification**: Semantic (Cypher) + structural (GDS) both verify claims
3. **Pre-registered libraries**: No LLM composition of graph code; use fixed projections and templates
4. **Explicit projection lifecycle**: All projections have try/finally guards to prevent OOM
5. **Type-safe payloads**: All cross-activity data is Pydantic-validated
6. **Ablation-ready**: Every reasoning component has an off-switch for research evaluation

## Non-Goals

- General-purpose Cypher writing by the LLM (use ProjectionComposerAgent for review-only proposals)
- Training data collection from production runs without opt-in
- UI (analyst interface is separate)
- Multi-tenant isolation (single-tenant with run_id scoping)
- Runtime model swapping (fixed to Qwen 3.5 4B for this submission)

## References

- Design spec: [CLAUDE.md](CLAUDE.md)
- Papers / context: ICTAI submission (fraud detection via graph reasoning)
- GDS docs: https://neo4j.com/docs/graph-data-science/current/
- Temporal docs: https://docs.temporal.io/
- PydanticAI docs: https://ai.pydantic.dev/

## License

Research artifact for ICTAI submission.
