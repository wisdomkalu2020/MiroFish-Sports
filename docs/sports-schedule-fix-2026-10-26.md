# Sports discussion scheduling fix — 26 October 2026

The saved two-round simulation started at hours 0 and 1. No persona was active
at either hour. Independently, `int(uniform(5, 6) * 0.05)` selected zero agents.
The standalone runner silently skipped those rounds.

Sports configuration now gives every configured participant a turn each round:
all hours are active, activity probability is one, hourly minimum and maximum
equal the saved roster size, and period multipliers are one. Roster limits,
round caps and token/cost budgets remain in force. This guarantees selection,
not successful model calls, a discussion post or a valid forecast.

The existing generic MiroFish schedules are unchanged. The configuration
generator still runs; the sports-only schedule is applied before saving.

The saved configuration for `sim_7fdee4a13c55` was updated in place after backing
up its configuration, profiles, state, logs and database under
`work/sports-e2e-20261019/before-schedule-fix-20261026` in the workspace.
Its graph remains `mirofish_a0ac8025c9904afd`. No graph/persona regeneration ran.
The persona file SHA-256 before and after is:

`b829ee23c23cc77089799fe06cf6ed7189001f8fd4002c7de0639b10860467fd`

Regression tests execute the actual selection function from each OASIS runner
(Reddit, Twitter and parallel) without model calls. They reproduce the original
empty selection at hours 0 and 1, verify selection of all ten agents at every
hour with the fix, and check generic schedules and invalid rosters.
The targeted schedule and sports layer suite passed all 16 tests.

This is a scheduling regression check, not a completed live discussion or an
accuracy evaluation. The saved run has charged 38,215 of its 50,000 tokens;
its cap has not been reset or increased.
