# Validation — 19 October 2026

These checks use the existing MiroFish implementation. They do not measure
sports forecast accuracy, betting returns, or agent predictive improvement.

## Phi-4 Mini

- Local GGUF: `Phi-4-mini-instruct-Q4_K_M.gguf`, 2,491,874,272 bytes.
- SHA-256 matched the original download record:
  `88c00229914083cd112853aab84ed51b87bdf6b9ce42f532d8c85c7c63b1730a`.
- `scripts.smoke_local_model` returned `READY` through the shared budget gateway.
- Recorded usage: 18 of 50,000 tokens; estimated provider charge USD 0.
  Local hardware/electricity costs are not included.

## Zep Cloud

Authentication and graph-list access passed. The existing
`scripts.validate_zep_cloud_integration` then passed against a temporary
synthetic graph:

- Graph creation and ontology setup, including empty-attribute normalization.
- Ten records ingested through the Batch API.
- Initial graph: 8 nodes and 20 edges; pagination matched raw traversal.
- Four temporal updates processed.
- Five synthetic activities processed by MiroFish's memory updater;
  zero failed writes and zero pending episodes at shutdown.
- Final graph: 14 nodes and 38 edges; four invalidated relationships observed.
- Search calls completed; episode search returned zero results, so this is
  not evidence of retrieval quality for every query.
- Entity-reader context included incoming and outgoing edges.
- Temporary graph deleted after the check.

The validation script requires the key in its process environment before it
imports MiroFish. Load the local `.env` into that process first. Never put keys
in commands, logs, or this repository.

## Sports-project preparation

`scripts.smoke_sports_project` passed using synthetic fixture captures and the
real local model. It generated 10 ontology entity types, loaded 760 historical
training matches for the baseline, and returned probabilities summing to
1.000000. It reported two data warnings. This check does not run graph creation,
personas, OASIS, or ReportAgent and is not a historical accuracy test.

Run these existing smoke checks from `backend` in PowerShell with the local
model server running:

```powershell
& .\.venv\Scripts\python.exe -m scripts.smoke_local_model
& .\.venv\Scripts\python.exe -m scripts.smoke_sports_project
```

## Still required

- Complete a sports dossier → graph → personas → OASIS → ReportAgent run.
- Measure tokens per full sports simulation.
- Run a small leakage-controlled agent evaluation and compare with the
  statistical baseline and timestamped bookmaker odds.

The Windows sandbox initially denied access to Python-created temporary
directories during the sports-project smoke check. Retrying outside that
sandbox with a dedicated workspace temporary directory passed. No application
code change was needed. This is separate from Zep authentication and
cloud-memory validation.
