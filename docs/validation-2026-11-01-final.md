# Sports integration validation — 1 November 2026

This extends the original MiroFish application. Graph construction, personas,
OASIS, Zep memory and ReportAgent remain in the workflow. Sports discussion is
synthetic; it is never attributed to real coaches or players.

## Software checks

- 309 backend tests passed in 29.54 seconds, including five new context-excerpt
  regression cases (UTF-8 bounds, cloud/short-result preservation, invalid config).
- Production frontend build passed (699 modules). Existing bundle-size and
  mixed static/dynamic import warnings remain.
- Browser checks covered the original home workflow, sports historical
  evaluation, calibration charts and the embedded odds/paper-ledger forms.
- EPL and NBA scores shown by the UI matched the saved historical evaluation.
- Changed files passed `git diff --check` and the credential-pattern scan.

The current source includes manual/provider odds capture, immutable hypothetical
NGN 100/200 predictions bound to saved forecasts, corrected kickoff handling,
and settlement from imported final scores. API tests exercise these operations
with isolated temporary stores; no real bets or payments were made.

## Local-model integration check

Phi-4 Mini ran through the existing OASIS environment using constrained JSON
tool transport. Run `sim_d8513eb9b69d` reused the saved graph and byte-identical
personas. One round finished with nine recorded actions: a seeded dossier post,
five generated comments, one generated post and two abstentions. Some attempted
agent responses did not produce valid actions; ten profiles do not mean ten
successful contributions.

Two Zep memory writes failed during a DNS/network interruption. The run correctly
failed closed before ReportAgent. After connectivity returned, the graph's
episode inventory was checked: neither batch had been stored. Recovery uploaded
the seven meaningful saved actions once; abstentions are not memory facts.
Cloud processing subsequently completed: seven activities, two batches, zero
failed batches and zero pending episodes. ReportAgent then resumed against the
same graph. Report `report_d4b1c8e3db5e` produced its two-section outline and
completed an InsightForge research call, then failed at 27%: its next prompt
contained 8,819 tokens, exceeding the server's 8,192-token context window.
There is no completed narrative report from this run.

Local ReportAgent now limits each observation excerpt to 1,000 UTF-8 bytes by
default (`MIROFISH_LOCAL_REPORT_OBSERVATION_BYTES`, range 256–16,000), preserves
the full tool output in its log and tells the model not to infer omitted facts.
Cloud observations and the existing research loop are unchanged. This mitigation
has regression coverage but has **not** passed a fresh live end-to-end test;
it is not a tokenizer-based guarantee that every accumulated prompt will fit.

Six votes passed the structural, bounded-adjustment, zero-sum and fact-ID checks;
none were rejected. Four profiles without discussion text did not vote. Voting
used 5,276 tokens. The synthetic fixture's home/draw/away baseline was
42.4411% / 26.4715% / 31.0874%; consensus changed it to
44.9411% / 23.9715% / 31.0874% (sum 100%).

**Semantic quality remains weak.** For example, one rationale calls its nonzero
adjustment neutral, another describes favoring the home team while reducing its
probability, and others infer that teams are new from missing history. Existing
validators cannot prove natural-language rationale correctness. These are
integration results, not validated evidence for the adjustment or a reason to
trust this small model's analysis.

The discussion used 21,191 tokens. The final run ledger charged 70,264 tokens,
including the conservative reservation charge for the failed request; this is
not a claim that all those tokens were generated. Its budget is locked with no
pending reservations. An earlier stopped attempt was conservatively
charged 20,086 tokens, including an uncertain request. The current run retains
its 79,914-token cap, so the combined new allowance cannot exceed 100,000. No
budget ledger was reset. Combined charged usage was 90,350 of the authorized
100,000 tokens. No further live retry was started. Local inference has no billed API token charge, but
hardware, electricity and Zep costs are not measured here.

## Measured forecasting results and remaining acceptance work

See [the historical evaluation](evaluation-2026-10-28/README.md) for the full
method and calibration bins. On 380 held-out EPL matches, the combined baseline
scored log loss 1.047601 and Brier 0.630637 versus naive 1.083933 / 0.656277.
On 1,230 NBA matches, Elo scored 0.613177 / 0.423244 versus naive
0.687538 / 0.494400. Lower is better. These retrospective score-file checks are
not archived pre-match dossier replays.

No leakage-safe historical agent sample or bookmaker comparison has yet been
measured. The synthetic integration fixture cannot establish accuracy, agent
lift, calibration or profit. Real fixture/enrichment and odds-provider keys are
not configured on this installation. Archived pre-cutoff data or a prospective
sample is needed to evaluate agents honestly. The original task's acceptance
conditions therefore remain open even though software checks pass.

Run instructions are in [the sports workflow guide](sports-workflow.md).
