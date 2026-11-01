# Using sports forecasting inside MiroFish

This extends the original MiroFish project, graph construction, persona generation, OASIS environment and runner, Zep memory, and ReportAgent. Football covers EPL and NPFL; basketball covers NBA. There is no separate Fibonacci dashboard, payment system, customer account system or real-betting integration.

## Start locally (PowerShell)

Keep credentials in the repository's ignored `.env`. Phi-4 Mini runs locally; it does not require OpenAI credits. Match these settings to your local server:

```dotenv
LLM_PROVIDER=local
LLM_MODEL_NAME=phi4-mini
LLM_REPORT_MODEL_NAME=phi4-mini
LLM_MAX_OUTPUT_TOKENS=1024
MIROFISH_LOCAL_MODEL_BASE_URL=http://127.0.0.1:8080/v1
MIROFISH_LOCAL_CONTEXT_TOKENS=8192
MIROFISH_LOCAL_TOOL_MODE=json
MIROFISH_LOCAL_TOOL_TEXT_LIMIT=400
MIROFISH_LOCAL_REQUEST_TIMEOUT_SECONDS=480
MIROFISH_RUN_TOKEN_LIMIT=100000
MIROFISH_RUN_COST_LIMIT_USD=0
```

The budget default applies only when creating a new run. Existing runs reopen their persisted limits, charges and exhaustion status. Changing `.env` never replenishes an old run. Use a deliberately budgeted new run for a retry and retain the old artifacts.

In a dedicated PowerShell window, start your downloaded llama.cpp server. Substitute your actual executable/model paths; keep its logs on disk:

```powershell
$llamaServer = 'C:\path\to\llama-server.exe'
$modelFile = 'C:\path\to\Phi-4-mini-instruct-Q4_K_M.gguf'
& $llamaServer -m $modelFile -c 8192 -t 4 -np 1 --host 127.0.0.1 --port 8080 --alias phi4-mini --jinja *> .\local-model.log
```

Do not start a second server on the same port. In another PowerShell window, from MiroFish's `backend` directory:

```powershell
.\.venv\Scripts\python.exe -c "from app import create_app; create_app().run(host='127.0.0.1',port=5001,debug=False,threaded=True)"
```

From `frontend` in a third window (with Node installed and dependencies installed):

```powershell
node node_modules/vite/bin/vite.js --host 127.0.0.1
```

Open `http://127.0.0.1:3000/sports/evaluation`. This is part of the existing MiroFish UI. The main workflow remains available through its navigation link.

## Match workflow

1. Configure `ZEP_API_KEY` for the existing memory service. For live fixture dossiers, configure `API_FOOTBALL_KEY` / `API_NBA_KEY` or their shared `API_SPORTS_KEY`.
2. Import past final scores through the sports page or the existing import CLI. Match team names exactly; a missing team is flagged and does not become invented form.
3. Choose the league, provider fixture ID, listed UTC kickoff and cutoff hours. MiroFish rejects evidence captured after that cutoff. Unknown injuries, players, lineups and other dossier sections remain unknown.
4. Create the match project and follow MiroFish's existing graph, preparation, simulation and report steps. Sports agents are synthetic analytical roles. Their messages are never statements by real coaches or players. Abstention and rejected votes are retained as limitations.
5. Inspect the baseline, bounded adjustments, normalized probabilities, source facts and freshness warnings. A successful software run does not establish forecast accuracy.

The sports report uses the existing ReportAgent planning, Zep research and writing loop with a two-section outline. The validated numerical forecast is appended separately. Invalid votes cannot alter the baseline. Local schema decoding helps formatting; the validator still checks bounds, outcome keys, fact IDs and the zero-sum adjustment rule.

For local inference, `MIROFISH_LOCAL_REPORT_OBSERVATION_BYTES` defaults to 1000.
It limits tool-result excerpts in the report prompt; full results remain in the
report log. Truncation is explicitly disclosed to the model. Long accumulated
conversations may still exceed the model context. The 2 November report retry
completed, but its narrative failed quality review. Passing the vote validator
does not prove that the model's prose correctly interprets facts. See the
[validation record](validation-2026-11-02.md) before using its output.

## Odds and paper records

On the same sports page, expand **Odds and paper ledger**:

- Capture a manual full market (home/draw/away for football; home/away for NBA), identifying its fixture, bookmaker, source and kickoff. Opening/current/closing labels describe observations, not independently verified market timestamps. The server stamps capture time now.
- Alternatively fetch current EPL/NBA prices with `THE_ODDS_API_KEY` configured locally. NPFL uses manual prices. No provider key is sent from the browser. Historical odds are not fabricated from current odds.
- Select the captured quote and an existing sports project. An optional simulation ID selects its saved agent forecast; leaving it blank uses the saved baseline. Probabilities are read from those artifacts, never typed into the pick form. Choose only a hypothetical NGN 100 or 200 amount.
- Add closing odds before kickoff for closing-line comparison. Append kickoff corrections without rewriting the earlier prediction.
- Import the final scores, then settle using the imported match ID. Fixture identity and date must match. Corrected kickoff times can void late predictions. Settlement is idempotent for the same final and rejects conflicting finals.

The API is `/api/sports/paper`, with POST routes `/quotes`, `/provider-quotes`, `/predictions`, `/kickoff`, and `/settle`. It only records hypothetical events; it cannot place a bet or transfer money.

## Evaluation and limits

Run the historical check on the same page, keeping a fixed cutoff and a held-out evaluation start date. It needs no LLM or Zep calls. The latest measured baseline results and calibration bins are in [the evaluation record](evaluation-2026-10-28/README.md). Score log loss, Brier and calibration, not a target win percentage.

Agent and bookmaker comparisons require eligible timestamped inputs. The current UI historical check evaluates statistical history; the evaluation engine's optional `AgentPrediction` and `MarketOdds` inputs must be supplied through a controlled offline evaluation. Paper records are not automatically interpreted as leakage-safe historical agent backtests. Anonymization must cover the actual dossier and prompts, or a documented model training cutoff must precede the evaluation matches. Do not backdate newly generated predictions.

The small-sample agent coordinator retains the configured graph cap. Archived pre-cutoff dossiers, real provider credentials and a prospective paper sample are still prerequisites for meaningful agent-lift measurement. A synthetic integration fixture is not an accuracy example. There is no established betting edge or profit.

## Checks

From `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

From `frontend`:

```powershell
node node_modules/vite/bin/vite.js build
```

Schema transport follows [llama.cpp's documented response format](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#post-v1chatcompletions-openai-compatible-chat-completions-api). The existing application validator remains authoritative when a provider does not support constrained decoding.
