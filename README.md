> **Unofficial sports adaptation of [MiroFish](https://github.com/666ghj/MiroFish).** This repository extends the existing MiroFish workflow for EPL, NPFL, and NBA forecasting and evaluation. The original project attribution and AGPL-3.0 license are retained. Agent forecast improvement has not yet been measured.

<div align="center">

<img src="./static/image/MiroFish_logo_compressed.jpeg" alt="MiroFish Logo" width="75%"/>

<a href="https://trendshift.io/repositories/16144" target="_blank"><img src="https://trendshift.io/api/badge/repositories/16144" alt="666ghj%2FMiroFish | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

简洁通用的群体智能引擎，预测万物
</br>
<em>A Simple and Universal Swarm Intelligence Engine, Predicting Anything</em>

<a href="https://www.shanda.com/" target="_blank"><img src="./static/image/shanda_logo.png" alt="666ghj%2FMiroFish | Shanda" height="40"/></a>

[![GitHub Stars](https://img.shields.io/github/stars/666ghj/MiroFish?style=flat-square&color=DAA520)](https://github.com/666ghj/MiroFish/stargazers)
[![GitHub Watchers](https://img.shields.io/github/watchers/666ghj/MiroFish?style=flat-square)](https://github.com/666ghj/MiroFish/watchers)
[![GitHub Forks](https://img.shields.io/github/forks/666ghj/MiroFish?style=flat-square)](https://github.com/666ghj/MiroFish/network)
[![Docker](https://img.shields.io/badge/Docker-Build-2496ED?style=flat-square&logo=docker&logoColor=white)](https://hub.docker.com/)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/666ghj/MiroFish)

[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?style=flat-square&logo=discord&logoColor=white)](http://discord.gg/ePf5aPaHnA)
[![X](https://img.shields.io/badge/X-Follow-000000?style=flat-square&logo=x&logoColor=white)](https://x.com/mirofish_ai)
[![Instagram](https://img.shields.io/badge/Instagram-Follow-E4405F?style=flat-square&logo=instagram&logoColor=white)](https://www.instagram.com/mirofish_ai/)

[English](./README.md) | [中文文档](./README-ZH.md)

</div>

## ⚡ Overview

**MiroFish** is a next-generation AI prediction engine powered by multi-agent technology. By extracting seed information from the real world (such as breaking news, policy drafts, or financial signals), it automatically constructs a high-fidelity parallel digital world. Within this space, thousands of intelligent agents with independent personalities, long-term memory, and behavioral logic freely interact and undergo social evolution. You can inject variables dynamically from a "God's-eye view" to precisely deduce future trajectories — **rehearse the future in a digital sandbox, and win decisions after countless simulations**.

> You only need to: Upload seed materials (data analysis reports or interesting novel stories) and describe your prediction requirements in natural language</br>
> MiroFish will return: A detailed prediction report and a deeply interactive high-fidelity digital world

### Our Vision

MiroFish is dedicated to creating a swarm intelligence mirror that maps reality. By capturing the collective emergence triggered by individual interactions, we break through the limitations of traditional prediction:

- **At the Macro Level**: We are a rehearsal laboratory for decision-makers, allowing policies and public relations to be tested at zero risk
- **At the Micro Level**: We are a creative sandbox for individual users — whether deducing novel endings or exploring imaginative scenarios, everything can be fun, playful, and accessible

From serious predictions to playful simulations, we let every "what if" see its outcome, making it possible to predict anything.

## 🌐 Live Demo

Welcome to visit our online demo environment and experience a prediction simulation on trending public opinion events we've prepared for you: [mirofish-live-demo](https://666ghj.github.io/mirofish-demo/)

## 📸 Screenshots

<div align="center">
<table>
<tr>
<td><img src="./static/image/Screenshot/运行截图1.png" alt="Screenshot 1" width="100%"/></td>
<td><img src="./static/image/Screenshot/运行截图2.png" alt="Screenshot 2" width="100%"/></td>
</tr>
<tr>
<td><img src="./static/image/Screenshot/运行截图3.png" alt="Screenshot 3" width="100%"/></td>
<td><img src="./static/image/Screenshot/运行截图4.png" alt="Screenshot 4" width="100%"/></td>
</tr>
<tr>
<td><img src="./static/image/Screenshot/运行截图5.png" alt="Screenshot 5" width="100%"/></td>
<td><img src="./static/image/Screenshot/运行截图6.png" alt="Screenshot 6" width="100%"/></td>
</tr>
</table>
</div>

## 🎬 Demo Videos

### 1. Wuhan University Public Opinion Simulation + MiroFish Project Introduction

<div align="center">
<a href="https://www.bilibili.com/video/BV1VYBsBHEMY/" target="_blank"><img src="./static/image/武大模拟演示封面.png" alt="MiroFish Demo Video" width="75%"/></a>

Click the image to watch the complete demo video for prediction using BettaFish-generated "Wuhan University Public Opinion Report"
</div>

### 2. Dream of the Red Chamber Lost Ending Simulation

<div align="center">
<a href="https://www.bilibili.com/video/BV1cPk3BBExq" target="_blank"><img src="./static/image/红楼梦模拟推演封面.jpg" alt="MiroFish Demo Video" width="75%"/></a>

Click the image to watch MiroFish's deep prediction of the lost ending based on hundreds of thousands of words from the first 80 chapters of "Dream of the Red Chamber"
</div>

> **Financial Prediction**, **Political News Prediction** and more examples coming soon...

## 🔄 Workflow

1. **Graph Building**: Seed extraction & Individual/collective memory injection & GraphRAG construction
2. **Environment Setup**: Entity relationship extraction & Persona generation & Agent configuration injection
3. **Simulation**: Dual-platform parallel simulation & Auto-parse prediction requirements & Dynamic temporal memory updates
4. **Report Generation**: ReportAgent with rich toolset for deep interaction with post-simulation environment
5. **Deep Interaction**: Chat with any agent in the simulated world & Interact with ReportAgent

## 🚀 Quick Start

### Option 1: Source Code Deployment (Recommended)

#### Prerequisites

| Tool | Version | Description | Check Installation |
|------|---------|-------------|-------------------|
| **Node.js** | 18+ | Frontend runtime, includes npm | `node -v` |
| **Python** | ≥3.11, ≤3.12 | Backend runtime | `python --version` |
| **uv** | Latest | Python package manager | `uv --version` |

#### 1. Configure Environment Variables

```powershell
Copy-Item .env.example .env
# Edit .env and set your own ZEP_API_KEY before a full graph/simulation run.
```

**Required Environment Variables:**

```env
LLM_PROVIDER=local
LLM_MODEL_NAME=phi4-mini
LLM_REPORT_MODEL_NAME=phi4-mini
LLM_MAX_OUTPUT_TOKENS=2048
MIROFISH_LOCAL_MODEL_BASE_URL=http://127.0.0.1:8080/v1
MIROFISH_LOCAL_REQUEST_TIMEOUT_SECONDS=480
MIROFISH_RUN_TOKEN_LIMIT=50000
MIROFISH_RUN_COST_LIMIT_USD=0
ZEP_API_KEY=your_zep_api_key
```

Run a local OpenAI-compatible server with the Phi-4 Mini GGUF before starting MiroFish. For the Windows CPU build of llama.cpp, one example is:

```powershell
$llamaServer = 'C:\path\to\llama-server.exe'
$gguf = 'C:\path\to\Phi-4-mini-instruct-Q4_K_M.gguf'
& $llamaServer -m $gguf -c 4096 -t 4 -np 1 --host 127.0.0.1 --port 8080 --alias phi4-mini --jinja
```

MiroFish creates an authenticated local gateway and persists one token budget per project or simulation run. Ontology, persona/config generation, OASIS agent turns, and ReportAgent use the same budget for their run. The OASIS listener closes when its subprocess finishes; ReportAgent opens a separate listener against the persisted budget. The model server alone does not replace Zep Cloud graph memory; a full MiroFish simulation still needs `ZEP_API_KEY`. This local Phi-4 Mini setup is the supported route for complete simulations; remote provider settings do not yet cover the OASIS subprocess.

Check the local model route without creating a Zep graph:

```powershell
Set-Location backend
& .\.venv\Scripts\python.exe -m scripts.smoke_local_model
```

This checks one budgeted model response and prints the run's token usage. It does not test sports forecasting or the full OASIS simulation.

From the repository root, a temporary synthetic sports project exercises the dossier API,
imported-history baseline, and local ontology generator without Zep or a
sports provider key, run:

```powershell
Set-Location backend
& .\.venv\Scripts\python.exe -m scripts.smoke_sports_project
```

### Sports forecasting extension (EPL, NPFL, NBA)

Sports forecasts use the existing MiroFish project, graph, persona, OASIS,
memory, and ReportAgent workflow. The football baseline combines Elo and a
Poisson model with a Dixon-Coles low-score correction; basketball uses Elo.
The baseline is the starting probability. Synthetic agent discussion may make
bounded adjustments, but it is never a quotation from a real coach, player,
or referee. No real betting, payments, or customer accounts are included.

Set `API_FOOTBALL_KEY` (or `API_SPORTS_KEY`) for EPL and NPFL fixture data,
`API_NBA_KEY` (or `API_SPORTS_KEY`) for NBA data, and optionally
`THE_ODDS_API_KEY` for bookmaker quotes in your local `.env`. Do not put keys
in the repository. `ZEP_API_KEY` is still required to build a graph and run
the agent simulation; the statistical baseline and offline evaluation do not
need it. Provider responses are stored as timestamped snapshots. A dossier
requires a provider fixture captured before its explicit cutoff of kickoff
minus `cutoff_hours`; after-the-fact facts cannot enter it.

Import historical finals explicitly before producing a live baseline. The
import stores immutable source-attributed snapshots and rejects conflicting
scores. These PowerShell examples run from `backend` with your own downloaded
source files:

```powershell
$importedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
& .\.venv\Scripts\python.exe -m scripts.import_sports_history `
  --football-file EPL 2025-26 'C:\data\1-premierleague.txt' `
  'https://raw.githubusercontent.com/openfootball/england/master/2025-26/1-premierleague.txt' `
  $importedAt
& .\.venv\Scripts\python.exe -m scripts.import_sports_history `
  --football-file NPFL 2025-26 'C:\data\2025-26_ng1.txt' `
  'https://raw.githubusercontent.com/openfootball/world/master/africa/nigeria/2025-26_ng1.txt' `
  $importedAt
& .\.venv\Scripts\python.exe -m scripts.import_sports_history `
  --nba-file 2026 'C:\data\nba_schedule_2026.csv' `
  'https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_schedules/nba_schedule_2026.csv' `
  $importedAt
```

Use the actual UTC fetch time for each file if you recorded it; `$importedAt`
is a conservative later time for a previously downloaded file. The sources are
[OpenFootball](https://github.com/openfootball/world) and
[SportsDataverse hoopR](https://github.com/sportsdataverse/hoopR-nba-data);
check their licenses before redistribution. Historical files fetched today
are useful for statistical walk-forward evaluation but are not evidence that
injuries, lineups, odds, or other mutable facts were available before an old
match. A live project baseline trains only on results captured by its cutoff.

Run an offline walk-forward check with an earlier training season and a held
out season. For example:

```powershell
$importedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
& .\.venv\Scripts\python.exe -m scripts.evaluate_sports_baselines `
  --football-file EPL 2024-25 'C:\data\epl-2024-25.txt' `
  'https://raw.githubusercontent.com/openfootball/england/master/2024-25/1-premierleague.txt' $importedAt `
  --football-file EPL 2025-26 'C:\data\epl-2025-26.txt' `
  'https://raw.githubusercontent.com/openfootball/england/master/2025-26/1-premierleague.txt' $importedAt `
  --cutoff-hours 24 --evaluate-from 2025-07-01 --output 'C:\data\epl-evaluation.json'
```

The report includes log loss, multiclass Brier score, calibration bins, and
per-match probabilities. Agent and bookmaker variants are marked unavailable
until pre-cutoff predictions and odds are actually captured. Agent backtests
also require anonymized historical fixtures or matches after the model's
training cutoff. Results are not evidence of betting profit.

An initial offline check on 14 October 2026 used 2024-25 finals to warm the
football models and scored 2025-26; NBA 2025 warmed the model and NBA 2026
was scored. All three used a 24-hour cutoff and the conservative date-only
result-availability rule above. Lower scores are better:

| Held-out league | Matches | Naive log loss / Brier | Primary baseline log loss / Brier |
| --- | ---: | ---: | ---: |
| EPL 2025-26 | 380 | 1.0839 / 0.6563 | Elo + Dixon-Coles: 1.0476 / 0.6306 |
| NPFL 2025-26 | 379 | 0.8874 / 0.5223 | Elo + Dixon-Coles: 0.9343 / 0.5510 |
| NBA 2026 | 1,230 | 0.6875 / 0.4944 | Elo: 0.6132 / 0.4232 |

The combined NPFL baseline was worse than the naive rate on this held-out
season. There are no scored agent or bookmaker comparisons yet, and these
scores do not measure betting returns. The historical source files provide
finals and dates, not archived kickoff, injury, lineup, or odds snapshots.

The hypothetical paper ledger is available through
`backend/app/sports/paper_ledger.py`. It records a timestamped manual or
provider quote, a forecast with a hypothetical NGN 100 or 200 pick, later
kickoff corrections, and a final-score settlement as append-only events.
Closing line value appears only when a quote was explicitly captured and
labeled closing before kickoff; that label is not independent proof of the
market's actual close. `THE_ODDS_API_KEY` enables current EPL and NBA prices;
NPFL uses manual entry. Historical odds access from that provider requires a
separate paid plan, so no bookmaker comparison was manufactured here.

Sports OASIS runs default to at most 16 synthetic personas and 3 discussion
rounds (`MIROFISH_SPORTS_AGENT_COUNT` and `MIROFISH_SPORTS_MAX_ROUNDS`).
The small-sample backtest coordinator caps new Zep graphs at 3 by default
(`MIROFISH_SPORTS_BACKTEST_MAX_GRAPHS`, range 1–10) and refuses historical
agent projects without an explicit anonymized dossier marker. This run has
no Zep key or archived historical dossiers, so agent predictive lift and
model tokens per fully simulated match remain unmeasured.

#### 2. Install Dependencies

```powershell
# One-click installation of all dependencies (root + frontend + backend)
npm run setup:all
```

Or install step by step:

```powershell
# Install Node dependencies (root + frontend)
npm run setup

# Install Python dependencies (backend, auto-creates virtual environment)
npm run setup:backend
```

#### 3. Start Services

```powershell
# Start both frontend and backend (run from project root)
npm run dev
```

**Service URLs:**
- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:5001`

**Start Individually:**

```powershell
npm run backend   # Start backend only
npm run frontend  # Start frontend only
```

### Option 2: Docker Deployment

```powershell
# 1. Configure environment variables (same as source deployment)
Copy-Item .env.example .env

# 2. Pull image and start
docker compose up -d
```

Reads `.env` from root directory by default, maps ports `3000 (frontend) / 5001 (backend)`

> Mirror address for faster pulling is provided as comments in `docker-compose.yml`, replace if needed.

## 📬 Join the Conversation

<div align="center">
<img src="./static/image/QQ群.png" alt="QQ Group" width="60%"/>
</div>

&nbsp;

The MiroFish team is recruiting full-time/internship positions. If you're interested in multi-agent simulation and LLM applications, feel free to send your resume to: **mirofish@shanda.com**

## 📄 Acknowledgments

**MiroFish has received strategic support and incubation from Shanda Group!**

MiroFish's simulation engine is powered by **[OASIS (Open Agent Social Interaction Simulations)](https://github.com/camel-ai/oasis)**, We sincerely thank the CAMEL-AI team for their open-source contributions!

## 📈 Project Statistics

<a href="https://github.com/666ghj/MiroFish">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="static/image/star-history-dark.svg" />
   <source media="(prefers-color-scheme: light)" srcset="static/image/star-history-light.svg" />
   <img alt="666ghj/MiroFish Star History Chart" src="static/image/star-history-light.svg" />
 </picture>
</a>
