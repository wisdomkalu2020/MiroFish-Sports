# Resume validation — 1 November 2026

## Verified outcome

Resumed the existing MiroFish fork, retaining graph `mirofish_a0ac8025c9904afd` and simulation `sim_8c2588e1bc53`. The saved Reddit persona SHA-256 remains `b829ee23c23cc77089799fe06cf6ed7189001f8fd4002c7de0639b10860467fd`. No graph or persona generation was repeated.

The saved one-round run contained one manual seed, two genuine synthetic comments and one DO_NOTHING action. Several other model responses failed tool validation. Eight of ten profiles had no discussion text. This is partial integration evidence, not a passed full-agent simulation.

Resumed the existing report API. Both attempted votes failed validation; zero votes were accepted. The consensus correctly withheld adjustments and retained the baseline. Vote collection used 2,012 tokens. ReportAgent retrieved the existing Zep graph and generated a five-section outline, then failed before the first section because its conservative reservation could not fit the remaining budget.

Final persisted ledger: 42,302 charged / 50,000 token limit; zero reservations and pending requests; exhausted=true. Local API charge was USD 0 (hardware/electricity costs excluded). This ledger includes earlier troubleshooting and conservative uncertain-usage charges, so it is not a clean per-match performance benchmark. No budget was reset or increased. Report ID: `report_df827fe2f934`; status failed. No completed report or agent accuracy claim is justified.

## Changes and checks

- Added HTTP gateway regression coverage proving JSON tool translation preserves the output cap and token accounting, including rejected actions.
- Added explicit jsonschema dependency to both installation manifests and regenerated uv.lock (no dependency versions changed).
- Added validation reasons to future rejected-vote records; retained the original write-once forecast.
- Documented JSON tool transport and the 8,192-token server/CAMEL context; corrected the README's completion wording.
- Full backend suite: 288 passed. After the diagnostics change, all 12 focused agent-layer tests passed.
- Initial sandbox test attempt failed creating pytest temporary directories; the normal-permission rerun above passed.

PowerShell, from the repository root:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests -q
```

Use `.env.example` for configuration names, retaining local secrets in `.env`. The model server must use `-c 8192`; enable `MIROFISH_LOCAL_TOOL_MODE=json`. Do not reuse an exhausted run for new calls or erase its ledger.

## Remaining acceptance work

A fresh explicitly budgeted integration attempt must improve local-model action/vote reliability and complete ReportAgent. Keep the existing graph/personas and preserve all prior accounting. A future clean benchmark should separate preparation, discussion, votes and report costs.

The statistical historical evaluation is in [the evaluation record](evaluation-2026-10-28/README.md). It does not measure agent lift. Archived pre-cutoff dossiers (with anonymization or a verified model-cutoff strategy) and timestamped odds are still needed for the four-way historical comparison. Browser-level acceptance of the complete sports flow remains outstanding. No real betting, payments or customer accounts have been added.
