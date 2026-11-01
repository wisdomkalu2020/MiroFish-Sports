# Report retry and remaining acceptance work — 2 November 2026

The user resumed live testing after the earlier stop. The report-only retry
uses ledger `sim_report_retry_20261102_1`, capped at 100,000 local-model tokens.
Old ledgers are unchanged. ReportAgent's explicit `llm_run_id` parameter allows
an operator-authorized retry to retain the source simulation ID while routing
the report and all its research LLM calls through a separate shared ledger.
Ordinary API calls still use their original simulation budget.

## Saved workflow reused

- Source simulation: `sim_d8513eb9b69d`.
- Existing graph: `mirofish_a0ac8025c9904afd`.
- Report: `report_effbef5168ce`.
- Zep readback confirmed both discussion batches processed before reporting.
- No graph, persona, discussion or vote generation was repeated.
- Phi-4 Mini, 8,192-token server context, 512-token output cap, and 1,000-byte
  report observation excerpts.

Execution completed at 100%, using 67,160 of 100,000 tokens with zero pending
reservations and no exhausted budget. SHA-256 comparisons confirmed that the
saved personas, actions and forecast/votes remained unchanged. Both narrative
sections and the structured forecast table were assembled. The report therefore
passes the execution check but **fails narrative quality review**. Its local
display copy carries an explicit review-failed notice; the original is preserved
as `full_report.before_review.md`, with findings in `quality_review.json`.

The retry passed the previously failing graph-research step without a context
overflow. Both sections requested follow-up interviews against the stopped
environment. An old `env_status.json` incorrectly said alive, causing two
180-second timeouts. No interview answers were returned. The repair now checks
terminal run status and returns an explicit unavailable result before spending
tokens on interview preparation. That repair was tested separately because the
live process had already imported its code.

## Quality assessment

This synthetic fixture is an integration test, not a prediction accuracy test.
The generated prose is not reliable: the first section confuses an approximately
zero adjustment with the away-win probability, adds unsupported tactical claims,
and is written in Chinese despite an English report request. The outline also
repeats a section title. Structural completion must not be equated with an
accepted report or successful forecasting. The sports-report notice now states
that narrative reasoning is unverified and distinguishes it from the computed
forecast table. This disclosure does not repair the reasoning.

## Checks

- Full backend suite: 316 passed in 27.34 seconds.
- Final targeted reporting tests: 16 passed. An earlier restricted run had
  two temporary-directory setup errors; the unrestricted rerun passed.
- Regression coverage includes default/shared versus explicit retry routing,
  UTF-8 excerpt bounds, stale alive files after terminal runs, and no model calls
  for closed-environment interviews.
- Frontend code was unchanged; the preceding production build passed.

## Outstanding data and evaluation

Only synthetic dossier evidence and retrospective final-score snapshots are
available here. Sports-data and odds-provider keys are not configured. The
provider name has been requested. A key alone would not establish historical
pre-cutoff availability: archived dossiers/odds or a prospective captured sample
are still needed. No agent-lift or bookmaker comparison is measured. The
[baseline evaluation](evaluation-2026-10-28/README.md) remains the measured
historical result; no accuracy or profit claim is added by this test.

For PowerShell startup, import and paper-ledger instructions, see the
[sports workflow guide](sports-workflow.md).
