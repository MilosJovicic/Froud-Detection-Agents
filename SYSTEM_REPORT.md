# Agentic System Report

This report explains the fraud-investigation system as it is implemented today. It is based on the current design note in [CLAUDE.md](CLAUDE.md), the implementation under [`src/`](src/), the checked-in evaluation artifact at [evals/reports/latest.json](evals/reports/latest.json), and the graph fixture and tests under [`tests/`](tests/).

Validation note: the repository is configured to use a local Neo4j endpoint at [`neo4j://127.0.0.1:7687`](.env), and graph-backed tests seed and query that database through [tests/conftest.py](tests/conftest.py). In this workspace, that endpoint is not currently reachable, so runtime claims below are grounded in source inspection plus the checked-in eval artifact rather than a fresh end-to-end rerun.

## System Overview

At a high level, this system investigates a fraud hypothesis by combining:

- deterministic graph reasoning in Neo4j GDS
- narrow LLM agents that classify, choose from fixed catalogs, interpret bounded result tables, select deterministic verifier templates, and write the final analyst-facing summary
- Temporal workflows that make the investigation durable, parallel, and recoverable

The core design really is "the algorithm reasons, the LLM routes." The important implementation consequence is that the model does not get to invent arbitrary Cypher during normal operation. Instead, it chooses among pre-registered graph projections in [src/gds/projections.py](src/gds/projections.py), pre-registered algorithms in [src/gds/algorithms.py](src/gds/algorithms.py), and pre-registered verifier templates in [src/verifiers/templates.py](src/verifiers/templates.py). The top-level orchestration lives in [src/workflows/investigation.py](src/workflows/investigation.py), and each reasoning branch lives in [src/workflows/branch.py](src/workflows/branch.py).

## Architecture

The two key control-flow diagrams below are copied from [CLAUDE.md](CLAUDE.md) so the intended architecture stays visible while reading the as-built explanation.

```text
+---------------------------------------------------------------------+
|                                                                     |
|   Analyst                                                           |
|     |                                                               |
|     |  Hypothesis (natural language)                                |
|     v                                                               |
|  +---------------------------------------------------------------+  |
|  |           FraudInvestigationWorkflow (Temporal)               |  |
|  |                                                               |  |
|  |    +-----------------+                                        |  |
|  |    | ParseHypothesis | --> RouterAgent (Qwen 3.5 4B)          |  |
|  |    +-----------------+     classify: ring | chargeback |      |  |
|  |             |                        routing | custom         |  |
|  |             v                                                 |  |
|  |    +-----------------+                                        |  |
|  |    |  BranchPlanner  | --> picks k in {1..3}                  |  |
|  |    |  (GoT fan-out)  |     (projection, algorithm, params)    |  |
|  |    +-----------------+                                        |  |
|  |             |                                                 |  |
|  |      +------+------+                                          |  |
|  |      v      v      v                                          |  |
|  |   +----+ +----+ +----+   Child Workflows (parallel)           |  |
|  |   | B1 | | B2 | | B3 |   ReasoningBranchWorkflow              |  |
|  |   +----+ +----+ +----+   one per (projection, algo)           |  |
|  |      |      |      |                                          |  |
|  |      +------+------+                                          |  |
|  |             v                                                 |  |
|  |    +-----------------+                                        |  |
|  |    |   Aggregator    | --> majority-vote + conflict detect    |  |
|  |    +-----------------+                                        |  |
|  |             |                                                 |  |
|  |             v                                                 |  |
|  |    +-----------------+                                        |  |
|  |    |    Synthesize   | --> SynthesizerAgent (Qwen 3.5 4B)     |  |
|  |    +-----------------+     analyst-facing verdict             |  |
|  |             |                                                 |  |
|  +-------------+-------------------------------------------------+  |
|                v                                                    |
|          FraudVerdict                                               |
|                                                                     |
+---------------------------------------------------------------------+
                  |                          |
                  v                          v
        +-----------------+        +-----------------+
        |     Ollama      |        |  Neo4j + GDS    |
        |  +-----------+  |        |  +-----------+  |
        |  | qwen3:4b  |  |        |  |   graph   |  |
        |  |           |  |        |  |   + GDS   |  |
        |  +-----------+  |        |  | projects  |  |
        |   :11434/v1     |        |  +-----------+  |
        +-----------------+        +-----------------+
```

```text
+------------------------------------------------------------------+
|             ReasoningBranchWorkflow (one GoT node)               |
|                                                                  |
|  Input: BranchSpec(projection_id, algorithm_id, params)          |
|                                                                  |
|    +------------------+                                          |
|    |  ProjectGraph    | --> gds.graph.project.cypher(...)        |
|    +------------------+                                          |
|           |                                                      |
|           v                                                      |
|    +------------------+                                          |
|    |  RunAlgorithm    | --> gds.<algo>.stream / write            |
|    +------------------+                                          |
|           |                                                      |
|           v                                                      |
|    +------------------+                                          |
|    |   Interpret      | --> InterpreterAgent (Qwen 3.5 4B)       |
|    |                  |     reads table -> StructuralClaim       |
|    +------------------+                                          |
|           |                                                      |
|           v                                                      |
|    +------------------+                                          |
|    |   VerifyCypher   | --> parameterized template probe         |
|    |    (CoVe)        |     returns AGREE | DISAGREE | N/A       |
|    +------------------+                                          |
|           |                                                      |
|           v                                                      |
|    +------------------+                                          |
|    |  DropProjection  |     (always runs, compensation)          |
|    +------------------+                                          |
|                                                                  |
|  Output: StructuralEvidence(claim, confidence, witnesses)        |
+------------------------------------------------------------------+
```

## End-to-End Orchestration

The parent workflow is [`FraudInvestigationWorkflow.run`](src/workflows/investigation.py). Its behavior is:

1. Parse raw text if needed. If the caller passes a string rather than a typed `Hypothesis`, the workflow calls [`parse_hypothesis_activity`](src/activities/parse.py), which combines regex extraction for customer IDs, merchant IDs, countries, amounts, and time windows with a routing call to [`classify_hypothesis`](src/agents/router.py). This is not free-form semantic parsing; it is structured extraction plus narrow classification.
2. Short-circuit custom claims. If the parsed claim type is `custom`, the workflow bypasses branch fan-out and instead queues a review-only projection proposal through [`compose_projection_activity`](src/activities/compose.py). The proposal is written to `.claude/composer_proposals/`, and the returned verdict explicitly tells the analyst not to auto-execute it. That behavior is covered by [tests/test_workflow_custom.py](tests/test_workflow_custom.py).
3. Plan branches. For non-custom hypotheses, the workflow calls [`plan_branches_activity`](src/activities/plan.py), which delegates to [`plan_branches`](src/agents/planner.py). The planner can return 1 to 3 `BranchSpec` objects and validates that every `(projection_id, algorithm_id)` pair exists in the fixed catalog from [src/gds/algorithms.py](src/gds/algorithms.py).
4. Fan out child workflows. The parent uses `asyncio.gather(...)` around `workflow.execute_child_workflow(...)` calls, so branches run in parallel as independent Temporal child workflows in [src/workflows/branch.py](src/workflows/branch.py).
5. Aggregate evidence. After all child workflows return `StructuralEvidence`, the parent calls [`aggregate_evidence_activity`](src/activities/aggregate.py), which computes supported/mixed/unsupported consensus, an aggregate confidence score, and a list of conflicts.
6. Synthesize the final verdict. The parent finishes with [`synthesize_verdict_activity`](src/activities/synthesize.py), which produces the analyst-facing `FraudVerdict`.

Temporal is doing more than just sequencing. Both workflows define retry policies and timeouts, so transient failures retry at the activity boundary rather than forcing the whole investigation to be rebuilt by hand. The parent workflow sets a 5-minute execution budget and 60-second activity timeouts in [src/workflows/investigation.py](src/workflows/investigation.py); the branch workflow applies the same 60-second activity timeout pattern in [src/workflows/branch.py](src/workflows/branch.py).

## Reasoning Inside One Branch

The reasoning branch is where most of the real signal extraction happens. [`ReasoningBranchWorkflow.run`](src/workflows/branch.py) executes this sequence:

1. Create a uniquely named projection. The branch suffixes the projection name with the Temporal `run_id` to avoid collisions across concurrent workflows, then calls [`project_graph_activity`](src/activities/project.py).
2. Run a graph algorithm. [`run_algorithm_activity`](src/activities/run_algo.py) looks up the chosen algorithm in the fixed library at [src/gds/algorithms.py](src/gds/algorithms.py) and executes it against the temporary projection.
3. Interpret the algorithm output. [`interpret_result_activity`](src/activities/interpret.py) extracts the relevant Neo4j node IDs from the algorithm rows, loads node details, and asks [`interpret_structural_claim`](src/agents/interpreter.py) to convert the top rows into a typed `StructuralClaim`.
4. Verify the claim with deterministic Cypher. [`verify_with_cypher_activity`](src/activities/verify.py) asks [`select_verifier_request`](src/agents/verifier.py) for a template ID and bindings, then executes that template through [src/verifiers/templates.py](src/verifiers/templates.py).
5. Drop the projection in a `finally` block. [`drop_projection_activity`](src/activities/drop.py) is always called even if interpretation or verification fails.
6. Assign branch confidence. The branch currently uses a very simple confidence rule in [src/workflows/branch.py](src/workflows/branch.py): `0.9` if the verifier says `AGREE`, otherwise `0.3`.

This is important for understanding the phrase "reasoning branch." The branch does not let the LLM freestyle over the whole graph. Instead:

- the projection is deterministic Cypher from a fixed library
- the algorithm is deterministic GDS from a fixed library
- the interpreter only sees a filtered table plus node metadata
- the verifier only selects from fixed Cypher templates

That keeps the model in a bounded slot-filling role while the actual graph reasoning stays inside Neo4j GDS and deterministic Cypher.

The branch cleanup path is explicitly tested: [tests/test_workflow_branch.py](tests/test_workflow_branch.py) checks successful evidence production, cleanup on injected post-algorithm failure, and 50 parallel branches without leaked projections.

## Projection Library

The projection library lives in [src/gds/projections.py](src/gds/projections.py). Each function builds a temporary GDS graph with `gds.graph.project.cypher(...)`.

| Projection ID | What it isolates | Main fraud signal |
| --- | --- | --- |
| `shared_device_ring` | Customers connected to devices used by at least `N` customers | collusion rings and shared-device clusters |
| `shared_card_ring` | Customers connected to cards used by at least `N` customers | account takeover, mule reuse, shared card infrastructure |
| `ip_cohort` | Customers connected to IPs used by at least `N` customers | bot farms and suspicious login or access cohorts |
| `merchant_cochargeback` | Merchant-to-merchant graph via charged-back customers | merchant collusion around chargeback-heavy customer overlap |
| `money_flow` | Transaction -> Acquirer -> Issuer paths above an amount threshold | settlement-path reasoning and fund routing |
| `payout_cluster` | Merchant -> PayoutAccount -> Country subgraphs with reused payout accounts | shell-company and payout clustering patterns |
| `decline_routing` | Transactions routed to acquirers with high decline counts | routing inefficiency and suspicious decline concentration |

The repository also checks basic projection counts and catalog coverage against the synthetic seed graph in [tests/fixtures/graph_seed.cypher](tests/fixtures/graph_seed.cypher), [tests/test_projections.py](tests/test_projections.py), and [tests/test_catalog.py](tests/test_catalog.py).

## Algorithm Guide

The algorithm library and projection-to-algorithm catalog live in [src/gds/algorithms.py](src/gds/algorithms.py).

| Algorithm | Return shape | What it does | Fraud signal here |
| --- | --- | --- | --- |
| `wcc` | `(nodeId, componentId)` | Finds hard connected components in an unweighted graph | good for "who is definitively in the same ring?" |
| `louvain` | `(nodeId, communityId)` | Finds softer communities by maximizing modularity | good for looser merchant or entity clusters |
| `pagerank` | `(nodeId, score)` | Scores structurally central nodes | good for "which entity is the most important hub or mule?" |
| `node_similarity` | `(node1, node2, similarity)` | Compares neighborhood overlap between nodes | good for lookalike customers or entities |
| `betweenness` | `(nodeId, score)` | Scores how often a node sits on shortest paths between others | good for bottlenecks, bridges, or chokepoints in money flow |
| `fastrp_knn` | `(node1, node2, similarity)` after embedding mutate step | Builds embeddings with FastRP, then runs KNN similarity | good for embedding-based lookalike detection beyond exact shared neighbors |

Two implementation details matter here:

- The planner only sees the compatibility catalog, not the entire GDS surface area. That catalog is encoded as `PROJECTION_ALGORITHM_CATALOG` in [src/gds/algorithms.py](src/gds/algorithms.py).
- The interpreter logic adapts to the output shape. Cluster-style outputs are grouped by `componentId` or `communityId`; score-style outputs select the highest-scoring node; pairwise outputs select the highest-similarity pair. That logic lives partly in [src/agents/interpreter.py](src/agents/interpreter.py) and partly in the deterministic fallback at [src/agents/stubs.py](src/agents/stubs.py).

### Projection to Algorithm Catalog

| Projection ID | Allowed algorithms |
| --- | --- |
| `shared_device_ring` | `wcc`, `louvain`, `pagerank` |
| `shared_card_ring` | `wcc`, `louvain` |
| `ip_cohort` | `wcc`, `louvain`, `pagerank` |
| `merchant_cochargeback` | `louvain`, `pagerank`, `betweenness` |
| `money_flow` | `betweenness`, `pagerank` |
| `payout_cluster` | `wcc`, `louvain` |
| `decline_routing` | `pagerank` |

That catalog is enforced by planner validation in [src/agents/planner.py](src/agents/planner.py), and the full set of catalog pairs is smoke-tested in [tests/test_catalog.py](tests/test_catalog.py).

## LLM Agents

All LLM-backed agents use the same local Ollama-backed model factory in [src/agents/model.py](src/agents/model.py), but each agent has a narrow role and a deterministic fallback path.

| Agent | Narrow role | Main implementation area | Fallback behavior |
| --- | --- | --- | --- |
| `RouterAgent` | classify the hypothesis into `ClaimType` | [src/agents/router.py](src/agents/router.py) | falls back to `_rule_based_claim_type(...)` |
| `BranchPlannerAgent` | choose 1-3 `BranchSpec` objects from the fixed catalog | [src/agents/planner.py](src/agents/planner.py) | retries once on invalid output, then falls back to `DEFAULT_BRANCH_SPEC` |
| `InterpreterAgent` | summarize a bounded result table into `StructuralClaim` | [src/agents/interpreter.py](src/agents/interpreter.py) | retries once, then falls back to `build_structural_claim(...)` |
| `VerifierAgent` | choose one verifier template and valid bindings | [src/agents/verifier.py](src/agents/verifier.py) | retries once, then falls back to `choose_verifier_request(...)` |
| `SynthesizerAgent` | write the final analyst-facing assessment and actions | [src/agents/synthesizer.py](src/agents/synthesizer.py) | retries once, then falls back to `_rule_based_verdict(...)` |
| `ProjectionComposerAgent` | draft review-only projection Cypher for custom claims | [src/agents/composer.py](src/agents/composer.py) | retries once, then falls back to `_rule_based_projection_draft(...)` |

This is one of the strongest orchestration choices in the repository: every agent either emits a typed object or yields to deterministic fallback logic. No agent can silently return arbitrary free text across a workflow boundary because Temporal payloads are typed with Pydantic contracts under [src/contracts/](src/contracts/).

## How Reasoning Is Orchestrated

### 1. Deterministic substrate first

The actual fraud signal comes from graph topology and deterministic probes:

- projection builders in [src/gds/projections.py](src/gds/projections.py)
- GDS algorithm runners in [src/gds/algorithms.py](src/gds/algorithms.py)
- verifier templates in [src/verifiers/templates.py](src/verifiers/templates.py)

The LLM is orchestrated around that substrate rather than replacing it.

### 2. Catalog-constrained choice, not free-form tool invention

The planner can only choose among pre-approved `(projection_id, algorithm_id)` combinations, and the verifier can only choose from template schemas exposed by [src/verifiers/templates.py](src/verifiers/templates.py). Even parameter passing is filtered by signature through [`execution_params(...)`](src/activities/_utils.py), which drops unsupported keys before activities invoke projections or algorithms.

### 3. Reasoning is staged

The overall chain is:

1. classify the investigation
2. choose a few graph-analysis branches
3. run deterministic graph algorithms
4. summarize those graph outputs into typed claims
5. verify those claims with deterministic Cypher
6. aggregate and narrate the result

That staged structure matters because it makes the system easier to ablate, test, and debug. The instrumentation for activity timing and estimated token usage lives in [src/metrics.py](src/metrics.py), and the eval harness packages that into the JSON report in [evals/run_eval.py](evals/run_eval.py).

### 4. Retries and fallbacks are part of the reasoning design

This code treats LLM failure as expected, not exceptional:

- the router falls back to keyword-based classification
- the planner retries once with validation feedback, then defaults
- the interpreter retries once with validation feedback, then uses deterministic claim construction
- the verifier retries once with validation feedback, then uses deterministic template selection
- the synthesizer retries once with validation feedback, then emits a deterministic verdict

That makes the system resilient but also means successful end-to-end runs are not always "pure LLM" runs. The eval artifact proves this point clearly, as described below.

### 5. Temporal provides durability and compensation

Temporal is used as the durable control plane:

- parent workflow orchestrates the whole investigation
- child workflows isolate branch execution
- activities hold non-deterministic work such as LLM calls and database access
- projection cleanup is enforced with workflow-level `try/finally`

The important engineering benefit is not just retry. It is that projection lifecycle management becomes explicit, testable compensation logic instead of informal cleanup.

## As-Built Gaps and Findings

This section focuses on places where the current code differs from the looser design language in [CLAUDE.md](CLAUDE.md), or where hooks exist but are not yet wired into runtime behavior.

### 1. Aggregation policy is no longer an open question

[CLAUDE.md](CLAUDE.md) still lists aggregator consensus policy as an open design question, but the code has already chosen a concrete policy in [src/activities/aggregate.py](src/activities/aggregate.py):

- consensus is `supported` if there are agreeing branches and no disagreeing branches
- consensus is `mixed` if both agreeing and disagreeing branches exist
- otherwise consensus is `unsupported`
- aggregate confidence uses:
  - average agreeing confidence when any agree, otherwise average all branch confidence
  - multiplied by a consensus ratio of `(agreeing + 0.5 * not_applicable) / total`
  - minus a disagreement penalty of `0.15 * (disagreeing / total)`
  - clamped to `[0.0, 1.0]`

So this is implemented policy, not an unresolved design decision.

### 2. Several ablation environment variables are declared but not consumed

The ablation harness in [evals/ablations.py](evals/ablations.py) advertises switches such as:

- `ABLATE_GDS_VERIFIER`
- `ABLATE_CYPHER_VERIFIER`
- `MAX_BRANCHES`
- `ALGO_SET`
- `ALLOW_COMPOSER_ALWAYS`
- `OLLAMA_TEMPERATURE`

However, those variables are not read by the main runtime code under [`src/`](src/). By contrast, the mode switches that are actually wired today are things like `ROUTER_MODE`, `PLANNER_MODE`, `INTERPRETER_MODE`, `VERIFIER_MODE`, `SYNTHESIZER_MODE`, and `COMPOSER_MODE`, plus `ALLOW_COMPOSER_AUTOEXEC` in [src/activities/compose.py](src/activities/compose.py).

This means the ablation harness currently documents a broader experimental surface than the runtime actually honors.

### 3. The only explicit verifier stage in code is the Cypher/template verifier

The design language in [CLAUDE.md](CLAUDE.md) talks about both semantic and structural verification. In the current implementation, the explicit verifier activity is [`verify_with_cypher_activity`](src/activities/verify.py), which routes into deterministic Cypher templates in [src/verifiers/templates.py](src/verifiers/templates.py).

There is no separate standalone "GDS verifier" activity. Instead, the structural side of the reasoning is implicitly:

- the chosen graph projection
- the GDS algorithm output
- the interpreter's typed summary of that output

So the structural reasoning exists, but it is not implemented as a second explicit verifier stage parallel to the Cypher verifier.

### 4. The latest eval shows successful routing overall, but not pure RouterAgent success

The checked-in eval artifact at [evals/reports/latest.json](evals/reports/latest.json) reports:

- `scenario_accuracy: 1.0`
- `claim_type_accuracy: 1.0`
- `primary_branch_accuracy: 1.0`

But the same report also shows:

- `RouterAgent` total calls: 3
- `RouterAgent` success count: 0
- `RouterAgent` failure count: 3

That means all three router LLM calls failed during the recorded eval run, yet the overall scenarios still routed correctly because [`classify_hypothesis(...)`](src/agents/router.py) falls back to the deterministic rule-based classifier. The system therefore achieved correct top-level routing in that eval, but not because the router LLM itself succeeded.

### 5. The latest recorded latency is dominated by interpretation and Cypher verification

The most recent checked-in metrics in [evals/reports/latest.json](evals/reports/latest.json) show average activity latencies of roughly:

- `project_graph`: `164.782 ms`
- `run_algorithm`: `74.19 ms`
- `interpret_result`: `24398.619 ms`
- `verify_with_cypher`: `32660.128 ms`

So in the recorded run, graph projection and algorithm execution were relatively cheap. The expensive parts were:

- LLM interpretation of algorithm rows
- verifier-template selection plus Cypher execution

That is an important operational reality: the bottleneck is not the graph math itself.

### 6. Branch confidence is still intentionally coarse

Branch confidence is currently a binary post-verifier rule in [src/workflows/branch.py](src/workflows/branch.py): `0.9` for `AGREE`, `0.3` otherwise. There is no algorithm-specific calibration layer yet. The richer confidence handling only appears at aggregation time, not within each branch.

## Evidence Trail

If you want to trace the system quickly, these are the best starting points:

- Parent orchestration: [src/workflows/investigation.py](src/workflows/investigation.py)
- Branch orchestration: [src/workflows/branch.py](src/workflows/branch.py)
- Hypothesis parsing: [src/activities/parse.py](src/activities/parse.py)
- Projection library: [src/gds/projections.py](src/gds/projections.py)
- Algorithm library and compatibility catalog: [src/gds/algorithms.py](src/gds/algorithms.py)
- Verifier templates: [src/verifiers/templates.py](src/verifiers/templates.py)
- Deterministic fallbacks: [src/agents/stubs.py](src/agents/stubs.py)
- Metrics and token accounting: [src/metrics.py](src/metrics.py)
- Eval harness: [evals/run_eval.py](evals/run_eval.py)
- Latest recorded run: [evals/reports/latest.json](evals/reports/latest.json)

If you want to trace the test evidence behind the architecture:

- projection and algorithm catalog coverage: [tests/test_catalog.py](tests/test_catalog.py)
- verifier-template behavior: [tests/test_verifiers.py](tests/test_verifiers.py)
- branch cleanup and parallel branch durability: [tests/test_workflow_branch.py](tests/test_workflow_branch.py)
- top-level scenario execution: [tests/test_workflow_investigation.py](tests/test_workflow_investigation.py)
- custom-claim composer path: [tests/test_workflow_custom.py](tests/test_workflow_custom.py)

## Bottom Line

This repository is an algorithm-first agentic system, not a free-form "LLM does the investigation" system. Temporal provides the durable orchestration layer, Neo4j GDS provides the structural reasoning substrate, deterministic Cypher templates provide the explicit verification layer, and the LLM is tightly sandboxed into choosing, interpreting, and summarizing within fixed interfaces.

The current implementation is also more opinionated than the design note sometimes suggests: aggregation policy is already chosen, fallbacks are heavily relied upon, and several ablation flags are declared without corresponding runtime wiring. If you read it that way, the codebase becomes much easier to understand: it is a constrained graph-reasoning pipeline with LLM assistance, not an open-ended reasoning agent.
