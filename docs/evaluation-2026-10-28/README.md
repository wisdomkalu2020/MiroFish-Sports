# Historical baseline evaluation — 28 October 2026

This evaluates statistical models only. No agent improvement or betting profit has been established.

| Dataset | Matches scored | Model | Log loss (lower is better) | Brier score (lower is better) |
|---|---:|---|---:|---:|
| EPL | 380 | naive | 1.083933 | 0.656277 |
| EPL | 380 | elo | 1.051450 | 0.632846 |
| EPL | 380 | dixon_coles | 1.054335 | 0.635219 |
| EPL | 380 | combined | 1.047601 | 0.630637 |
| NBA | 1230 | naive | 0.687538 | 0.494400 |
| NBA | 1230 | elo | 0.613177 | 0.423244 |

## Method and limitations

Both runs score the season starting after 1 July 2025, with earlier imported finals used to warm up the models. The cutoff is 24 hours before the conservative match-day boundary. Same-day results are excluded from training.

These source files were imported retrospectively. Availability of old final scores is assumed using the loader-provided conservative result time; this is not a replay of archived pre-match snapshots. Unverified kickoff times remain flagged. No injury, lineup or mutable pre-match evidence is inferred.

Calibration bins (predicted probability versus observed frequency and sample count) are saved alongside this document. Football uses multiclass Brier summed across home/draw/away; NBA uses home/away, so raw Brier scores should not be compared across sports.

Agents and bookmaker comparisons are unavailable: no eligible pre-cutoff agent forecasts or timestamped odds were supplied. The baseline results cannot establish that the agent layer helps, beats bookmakers, or is profitable.
