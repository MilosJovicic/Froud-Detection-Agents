# How to Run


### Terminal 1 — Temporal dev server

```powershell
temporal server start-dev
```

### Terminal 2 — the worker

```powershell
py -3.13 src/worker.py
```


### Terminal 3 — submit a hypothesis

Pick any of these. Each takes ~60–120 s.

**Ring (shared device)**
```powershell
py -3.13 src/client.py --hypothesis "Urgent review of a collusion ring where customers C1_1 and C1_2 appear to share the same device."
```

**Chargeback cluster**
```powershell
py -3.13 src/client.py --hypothesis "Investigate a chargeback cluster of merchants sharing charged-back customers across M1 and M2."
```

**Decline routing anomaly**
```powershell
py -3.13 src/client.py --hypothesis "Investigate payment decline routing anomalies across acquirers and suspicious routing behavior."
```

**JSON output instead of pretty-printed**
```powershell
py -3.13 src/client.py --hypothesis "..." --json
```

While the client waits, open <http://localhost:8233> to watch the workflow execute activity-by-activity.

---

## 3. Seed the graph (if needed)

The eval harness (`make eval` / `run_eval.py`) wipes and re-seeds the graph every run. If you haven't run it recently, the client may have nothing to query.

### Check current node count

```powershell
py -3.13 -c "import asyncio,os; from dotenv import load_dotenv; load_dotenv(); from neo4j import AsyncGraphDatabase
async def m():
    d=AsyncGraphDatabase.driver(os.getenv('NEO4J_URI'),auth=(os.getenv('NEO4J_USER'),os.getenv('NEO4J_PASSWORD')))
    async with d.session() as s:
        r=await s.run('MATCH (n) RETURN count(n) AS c'); print('nodes:', (await r.single())['c'])
    await d.close()
asyncio.run(m())"
```

If `nodes: 0`, reseed by running the eval once (`make eval`), or paste the contents of [tests/fixtures/graph_seed.cypher](tests/fixtures/graph_seed.cypher) into the Neo4j Desktop query pane.

---

## 4. Run the tests

```powershell
# Everything
py -3.13 -m pytest tests/ -v

# Just the milestones
py -3.13 -m pytest tests/test_projections.py tests/test_algorithms.py -v   # M0-M1
py -3.13 -m pytest tests/test_verifiers.py -v                               # M2
py -3.13 -m pytest tests/test_workflow_branch.py -v                         # M3
py -3.13 -m pytest tests/test_branch_agents.py -v                           # M4
py -3.13 -m pytest tests/test_agents.py -v                                  # M5
py -3.13 -m pytest tests/test_workflow_investigation.py -v                  # M6
py -3.13 -m pytest tests/test_composer.py tests/test_workflow_custom.py -v  # M7
py -3.13 -m pytest tests/test_eval_harness.py -v                            # M8
```

Most tests hit Neo4j, so make sure the DBMS is running.

---

## 5. Run ablation studies (for the paper)

### Single ablation
```powershell
py -3.13 evals/ablations.py baseline
py -3.13 evals/ablations.py no_gds_verifier
py -3.13 evals/ablations.py single_branch
```

### Compare baseline against an ablation
```powershell
py -3.13 evals/ablations.py baseline no_gds_verifier
```

### Run all predefined ablations
```powershell
py -3.13 evals/ablations.py --all
```

Available ablations: `baseline`, `no_gds_verifier`, `no_cypher_verifier`, `single_branch`, `router_rule_based`, `planner_rule_based`, `minimal_algo_set`, `composer_always`.

### Temperature sweep
```powershell
py -3.13 evals/ablations.py baseline --temp 0.0 0.3 0.5 0.7
```

### Custom environment variable
```powershell
py -3.13 evals/ablations.py --custom MAX_BRANCHES=2
```

Results land in `evals/ablation_results/` with `comparison.json` and `index.json`.

---

## 6. Troubleshooting

### `ModuleNotFoundError: No module named 'activities'`
You ran `py -3.13 -m src.worker`. That's wrong. Use:
```powershell
py -3.13 src/worker.py
```

### `ImportError: cannot import name 'WorkflowFailureError' from 'temporalio.exceptions'`
Already patched — `WorkflowFailureError` now imports from `temporalio.client` in [src/client.py](src/client.py).

### Worker connects but the client times out
Temporal server isn't running. Open Terminal 1 and run `temporal server start-dev`.

### Client returns but scores are all zeros
Graph is empty. Run `make eval` once to seed, or paste [tests/fixtures/graph_seed.cypher](tests/fixtures/graph_seed.cypher) into Neo4j Desktop.

### Ollama calls are very slow
First call after model load takes 10+ s (model warm-up). Subsequent calls are fast. For concurrency, `OLLAMA_NUM_PARALLEL=4` in `.env` lets 4 branches call the interpreter in parallel.

### Neo4j auth error
Check `.env` — `NEO4J_PASSWORD=milos1234` must match the password on your Neo4j Desktop DBMS.

---

## 7. Everyday recipes

### "I want to see it work, right now"
```powershell
make eval
```

### "I want to submit my own hypothesis"
Open 3 terminals, run in order:
```powershell
# Terminal 1
temporal server start-dev

# Terminal 2
py -3.13 src/worker.py

# Terminal 3
py -3.13 src/client.py --hypothesis "your hypothesis here"
```

### "I want to compare with/without the GDS verifier for the paper"
```powershell
py -3.13 evals/ablations.py baseline no_gds_verifier
```
Then open `evals/ablation_results/comparison.json`.
