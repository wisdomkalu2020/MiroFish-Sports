# Live rerun — 28 October 2026

Reused graph mirofish_a0ac8025c9904afd and saved personas in sim_7fdee4a13c55. Persona SHA-256 remained b829ee23c23cc77089799fe06cf6ed7189001f8fd4002c7de0639b10860467fd. No preparation or persona-generation call was made.

Fixed preparation detection to require profile files only for enabled platforms. Fixed sports-only single-platform launch to use the existing instrumented OASIS runner with --reddit-only or --twitter-only; standalone scripts did not supply the action log/completion events needed by memory and voting. Generic launch behavior is unchanged.

Validation: 18 targeted tests passed; complete backend suite: 278 passed. git diff --check passed.

Live outcome: FAILED safely. Both scheduled rounds were reached, but model requests were denied by the original budget. Charged usage stayed 38,215 / 50,000, with no pending reservations. The conservative request reservation could not fit the remaining allowance. No new model usage was charged. The run budget is marked exhausted; it was not reset or increased.

The two recorded actions are seed-post records, not genuine generated discussion. The initial post appears again in the round-one database scan; do not use that duplicate as agent evidence. Zep accepted two activity items; updater drained with zero failed items. This verifies that logging and memory writes are connected, not that the discussion or forecasting pipeline passed. No report, vote collection or accuracy evaluation was run.

A further live attempt needs an explicitly approved fresh budget or budget increase, and should address single-model concurrency and duplicate seed-post accounting before treating any output as a discussion result.
