# Spine north-star vignette — end-to-end runbook

Run the whole loop on one entity: **a drift signal → a scoped, ontology-grounded,
human-gated remediation branch, with provenance.**

```
infodrift drift report  ──►  plan scoped remediation  ──►  governed codegen run
(entity_key)                 (code↔ontology mapping)       (branch + diff to review)
```

There are two tracks:

- **Fast path** — a tiny self-contained fraud repo + a hand-written drift report.
  Runs today; the only external requirement is an LLM key (for the codegen step).
- **Full path** — real OSS code + dataset + ontomesh/infodrift services. Same commands,
  realer inputs. Notes inline.

> **What's real vs. gated:** the remediation *planning + run* are real. Ontomesh
> grounding (Seam 1) and infodrift registration (Seam 2) are **gated** — they no-op
> unless you set their env vars, so the vignette works without them and gets richer
> when you add them.

---

## 0. Prerequisites

- Python 3.12, `uv`, `git`, and (for the codegen step) an LLM key.
- The orchestrator installed from this repo:

```bash
cd /path/to/agent-orachestrator
uv sync --extra sdlc            # add --extra otel for live tracing
```

- LLM config (the remediation run generates code):

```bash
export SDLC_CODEGEN=llm
export ORCHESTRATOR_INTAKE_MODEL=gpt-4o      # or your model
export OPENAI_API_KEY=sk-...                 # or ANTHROPIC_API_KEY
# optional: export SDLC_AGENTIC_CODEGEN=1    # tool-use loop instead of single-shot
```

---

## 1. Get a target repo (the code to remediate)

### Fast path — a minimal fraud repo (self-contained)

```bash
mkdir -p /tmp/fraud-svc && cd /tmp/fraud-svc
git init -q
mkdir -p src/fraud_svc tests
cat > src/fraud_svc/fraud_detector.py <<'PY'
"""A toy fraud scorer (the component the vignette remediates)."""


class FraudDetector:
    """Scores a card transaction's fraud risk in [0, 1]."""

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold

    def score(self, amount: float, is_foreign: bool) -> float:
        risk = min(1.0, amount / 10_000.0)
        if is_foreign:
            risk = min(1.0, risk + 0.2)
        return risk

    def is_fraud(self, amount: float, is_foreign: bool) -> bool:
        return self.score(amount, is_foreign) >= self.threshold
PY
cat > tests/test_fraud_detector.py <<'PY'
from fraud_svc.fraud_detector import FraudDetector


def test_high_amount_flagged() -> None:
    assert FraudDetector().is_fraud(9_000, True) is True


def test_low_amount_not_flagged() -> None:
    assert FraudDetector().is_fraud(10, False) is False
PY
cat > pyproject.toml <<'TOML'
[project]
name = "fraud-svc"
version = "0.1.0"
requires-python = ">=3.12"
TOML
git add -A && git commit -q -m "fraud service: FraudDetector"
echo "target repo ready at /tmp/fraud-svc"
```

### Full path — a real OSS fraud pipeline

```bash
git clone https://github.com/Fraud-Detection-Handbook/fraud-detection-handbook /tmp/fraud-svc
# Python pipeline + a transaction simulator (customers/terminals/time/geography)
# and concept-drift chapters — maps cleanly to Customer/Transaction/FraudDetector.
```

Set the repo the run branches from:

```bash
export SDLC_REPO_URL=/tmp/fraud-svc        # a local git repo path or a GitHub URL
```

---

## 2. (Optional, Seam 1) Stand up ontomesh for domain grounding

Skip this and the run still works (grounding degrades to none). To add **cited
domain knowledge** to the remediation:

```bash
docker run -d --name ontomesh -p 5051:5051 ghcr.io/synaptixs/ontomesh:latest
# Model a fraud ontology in the wizard at http://localhost:5051 (entities:
# Customer, Transaction, FraudDetector) — or seed a FIBO subset (edmcouncil/fibo).
export SPINE_ONTOMESH_URL=http://localhost:5051
export SPINE_ONTOMESH_FLAVOR=fraud         # your ontology flavor name
```

---

## 3. Build + confirm the code↔ontology mapping (Phase 0)

This is the **earned join** — propose mappings, confirm the good ones, persist them.
Save as `build_mappings.py` and run with `uv run python build_mappings.py`:

```python
from pathlib import Path

from orchestrator.pkg import FactStore, RepoCodeExtractor
from orchestrator.spine import CodeOntologyMapper, MappingLedger, MappingStore, OntologyClass

root = Path("/tmp/fraud-svc")
store = FactStore(RepoCodeExtractor().extract(root))

# The domain entities (ontomesh-minted in production; inline here). Labels must
# match the entity_key Component after spaces are stripped: "Fraud Detector" → FraudDetector.
classes = [
    OntologyClass("https://example.org/fraud#FraudDetector", "Fraud Detector",
                  aliases=("fraud model", "scorer")),
    OntologyClass("https://example.org/fraud#Transaction", "Transaction"),
    OntologyClass("https://example.org/fraud#Customer", "Customer"),
]

candidates = CodeOntologyMapper(classes).propose(store)
ledger = MappingLedger()
for c in candidates:
    print(f"{c.confidence:.2f}  {c.node_name:30} -> {c.label:16} ({c.rationale})")
    if c.confidence >= 0.6:          # the human-confirm step (review before confirming!)
        ledger.confirm(c, by="me")

MappingStore("spine-mappings.json").save(ledger.resolved())
print(f"\nconfirmed {len(ledger.resolved())} mapping(s) -> spine-mappings.json")
```

You should see `FraudDetector → Fraud Detector` confirmed at high confidence.

---

## 4. Get a drift report (infodrift's signal)

### Fast path — hand-write one (matches `DriftReport.from_infodrift`)

Save as `drift.json`:

```json
{
  "entities": {
    "FraudDetector_v5::APAC::CardTransactions": {
      "model_version": "5",
      "report": {
        "window_id": "2026-06-25",
        "alerts": [
          {
            "severity": "critical",
            "metric_type": "ece",
            "entity_key": "FraudDetector_v5::APAC::CardTransactions",
            "observed": 0.21,
            "threshold": 0.07,
            "message": "calibration eroded (ECE 3x baseline)",
            "recommendation": "recalibrate scores and add a calibration monitor"
          }
        ]
      }
    }
  }
}
```

### Full path — generate from infodrift + a real dataset

Use **Bank Account Fraud** (`feedzai/bank-account-fraud`, controlled month-over-month
shift) or the handbook's simulator: register the entity with month-0 as the baseline,
score a later (shifted) window, then dump the report:

```python
# sketch — see infodrift's README for the exact API
from drift_monitor.monitoring.orchestrator import DriftOrchestrator
from drift_monitor.reporting.report import HealthReporter

orch = DriftOrchestrator()
orch.register_entity("FraudDetector_v5::APAC::CardTransactions",
                     train_features_df=baseline_df, model_version="5", baseline_id="m0")
# ... run a shifted window through the entity's monitors ...
open("drift.json", "w").write(HealthReporter(orch).full_report(as_json=True))
```

---

## 5. Run the remediation (Seam 3 — the headline)

```bash
uv run orchestrator sdlc remediate \
  --report drift.json \
  --mappings spine-mappings.json \
  --repo /tmp/fraud-svc \
  --min-severity warning \
  --safe
```

What happens:

1. parse the drift report → material findings per `entity_key`;
2. derive scope: `entity_key` → ontology IRI (from your mappings) → the code nodes;
3. plan one **governed** `RemediationTask` (drift report = spec, ontology/SHACL =
   guardrails, scoped to `FraudDetector`);
4. run the codegen pipeline with that spec (intake skipped), **grounded by ontomesh**
   if configured;
5. `--safe` ⇒ a local branch + commit + diff, **no PR** (human-gated). Use `--live`
   to open a PR.

Expected tail:

```
REMEDIATION: 1 task(s)
  [OK] FraudDetector_v5::APAC::CardTransactions: ran -> feat/<id>/<KEY>
```

Review the change:

```bash
git -C /tmp/fraud-svc log --oneline -1
git -C /tmp/fraud-svc show --stat HEAD
```

---

## 6. Inspect the lineage / provenance (Phase 4)

Save as `show_lineage.py`, run with `uv run python show_lineage.py`:

```python
import json
from pathlib import Path

from orchestrator.spine import (
    DriftReport, LineageIndex, MappingStore,
    correlation_handles, infer_entity_iris, plan_remediations,
)

report = DriftReport.from_infodrift(json.loads(Path("drift.json").read_text()))
resolved = MappingStore("spine-mappings.json").load()
entity_iris = infer_entity_iris(report, resolved)
code_for_iri = {iri: nodes for iri, nodes in MappingStore("spine-mappings.json").code_for_iri().items()}

idx = LineageIndex()
for node_id, ref in resolved.items():
    idx.add_mapping(node_id, ref)
for f in report.findings:
    idx.add_drift(f)
for task in plan_remediations(report, entity_iris=entity_iris, code_for_iri=code_for_iri):
    idx.add_remediation(task)

ek = "FraudDetector_v5::APAC::CardTransactions"
rec = idx.for_entity(ek)
print(json.dumps(rec.as_dict(), indent=2))
print("stages present:", sorted(rec.stages_present))
print("correlation:", correlation_handles(rec))   # OTel trace_id + entity_key keys
# query from any entry point — they all resolve to the same entity:
print("from code node:", [r.entity_key for r in idx.for_node(next(iter(resolved)))])
```

You'll see the chain reconstructed: **domain → code → drift → remediation**, queryable
from the code node, the IRI, or the entity.

---

## 7. (Optional, Seam 2) Register shipped units with infodrift

So future runs are monitored from birth. Needs an infodrift register endpoint:

```bash
export SPINE_INFODRIFT_URL=http://localhost:8080
export SPINE_DEPLOY_TOPOLOGY='{"FraudDetector": [["APAC","CardTransactions"],["EU","CardTransactions"]]}'
export SPINE_SHIP_VERSION=6
# Registration then fires automatically on the post-merge step of a Temporal SDLC run.
```

---

## 8. (Optional) Live tracing

```bash
docker compose -f docker-compose.dev.yml up -d jaeger
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
# re-run step 5; open http://localhost:16686 to see the llm.complete / agent.step spans.
```

---

## Cleanup

```bash
docker rm -f ontomesh 2>/dev/null
rm -rf /tmp/fraud-svc spine-mappings.json drift.json
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No material drift findings` | severity below `--min-severity`, or empty `alerts` in the report. |
| remediation task is **unscoped** | the entity's component didn't map to a confirmed ontology IRI — re-check step 3 (label must match the `entity_key` component with spaces stripped). |
| no domain grounding in the diff | ontomesh not configured (`SPINE_ONTOMESH_URL`/`_FLAVOR`) — fine, it's best-effort. |
| codegen does nothing / errors on keys | `SDLC_CODEGEN=llm` + `ORCHESTRATOR_INTAKE_MODEL` + an API key must be set. |
| Seam 2 prints `skipped` | `SPINE_INFODRIFT_URL` + `SPINE_DEPLOY_TOPOLOGY` not set (gated off by default). |

## What this proves (and doesn't)

- **Proves:** the spine join is real and the loop runs end to end — drift localizes to
  an entity, scopes to the mapped code, produces a grounded/guardrailed remediation with
  provenance you can query from any point.
- **Doesn't yet:** a real-domain mapping-precision number (needs a real ontology +
  gold mappings) and a live cross-system run with production-scale data. Those are the
  operational next steps, not code.
