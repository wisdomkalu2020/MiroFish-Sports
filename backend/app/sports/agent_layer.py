"""Bounded, dossier-grounded votes collected after an ordinary OASIS run.

The graph, persona generator, OASIS turns, Zep ingestion, and ReportAgent remain
the MiroFish pipeline.  This module only interprets the synthetic discussion
after it finishes.  The statistical forecast is supplied separately and is
never calculated from an LLM response.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..utils.llm_client import LLMClient, LLMResponseError
from ..utils.openai_chat_compat import is_budget_exceeded_error
from .forecast_context import load_forecast_context as load_project_forecast_context


MAX_ADJUSTMENT = 0.10
_DOSSIER_HEADER = "# Pre-match dossier:"
_FACT_LINE = re.compile(r"^- (.+?): (.+?) \[source: (.+?); captured: (.+?)\]$")


class SportsAgentError(ValueError):
    """A sports-only forecast cannot safely be produced."""


def is_sports_dossier(text: str | None) -> bool:
    return bool(
        isinstance(text, str)
        and text.startswith(_DOSSIER_HEADER)
        and "- Sport: " in text
        and "- Evidence cutoff (UTC): " in text
    )


def sports_agent_limit() -> int:
    raw = os.environ.get("MIROFISH_SPORTS_AGENT_COUNT", "16")
    try:
        value = int(raw)
    except ValueError as error:
        raise SportsAgentError("MIROFISH_SPORTS_AGENT_COUNT must be an integer from 10 to 30") from error
    if not 10 <= value <= 30:
        raise SportsAgentError("MIROFISH_SPORTS_AGENT_COUNT must be an integer from 10 to 30")
    return value


def sports_round_limit() -> int:
    raw = os.environ.get("MIROFISH_SPORTS_MAX_ROUNDS", "3")
    try:
        value = int(raw)
    except ValueError as error:
        raise SportsAgentError("MIROFISH_SPORTS_MAX_ROUNDS must be an integer from 1 to 12") from error
    if not 1 <= value <= 12:
        raise SportsAgentError("MIROFISH_SPORTS_MAX_ROUNDS must be an integer from 1 to 12")
    return value


def select_sports_entities(entities: Sequence[Any]) -> list[Any]:
    """Cap OASIS personas while retaining the main dossier perspectives."""

    priorities = ("coach", "player", "team", "squad", "referee", "analyst")

    def rank(entity: Any) -> tuple[int, str]:
        kind = str(entity.get_entity_type() or "").lower()
        return (next((i for i, word in enumerate(priorities) if word in kind), 99),
                str(getattr(entity, "name", "")))

    return sorted(entities, key=rank)[:sports_agent_limit()]


def mark_synthetic_profile(profile: Any, index: int) -> None:
    """Label one generated profile before it can be logged or saved."""

    if str(getattr(profile, "user_name", "")).startswith("synthetic_sports_"):
        return
    kind = str(getattr(profile, "source_entity_type", None) or "analyst").strip()
    kind = re.sub(r"[^A-Za-z ]", "", kind)[:30].strip() or "analyst"
    profile.name = f"Synthetic {kind} perspective {index}"
    profile.user_name = f"synthetic_sports_{index}"
    profile.bio = f"Fictional pre-match {kind.lower()} perspective; not an actual person or statement."
    profile.persona = (
        "You are a fictional analytical perspective, not the real coach, player, "
        "referee, or team. Discuss only the provided pre-cutoff dossier. "
        "Never claim first-hand knowledge or quote a real person. "
        + str(profile.persona)
    )


def mark_synthetic_profiles(profiles: Sequence[Any]) -> None:
    """Keep generated personas, but never display them as real people speaking."""

    for index, profile in enumerate(profiles, start=1):
        mark_synthetic_profile(profile, index)


def _utc_timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise SportsAgentError(f"{field} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise SportsAgentError(f"{field} must be a UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SportsAgentError(f"{field} must be a UTC timestamp")
    return parsed.astimezone(timezone.utc)


def _outcomes(sport: str) -> tuple[str, ...]:
    if sport == "football":
        return ("home", "draw", "away")
    if sport == "basketball":
        return ("home", "away")
    raise SportsAgentError("sports forecast context must name football or basketball")


def _probabilities(raw: Any, outcomes: tuple[str, ...]) -> dict[str, float]:
    if not isinstance(raw, dict) or set(raw) != set(outcomes):
        raise SportsAgentError("baseline probabilities must contain the sport's exact outcomes")
    if any(isinstance(raw[k], bool) or not isinstance(raw[k], (int, float)) for k in outcomes):
        raise SportsAgentError("baseline probabilities must be numeric")
    values = {k: float(raw[k]) for k in outcomes}
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in values.values()):
        raise SportsAgentError("baseline probabilities must be finite values in [0, 1]")
    if abs(sum(values.values()) - 1.0) > 1e-8:
        raise SportsAgentError("baseline probabilities must sum to one")
    return values


def validate_forecast_context(
    context: Mapping[str, Any],
    *,
    project_id: str,
    simulation_id: str,
    dossier_text: str,
) -> dict[str, Any]:
    """Check the loaded statistical artifact against this exact simulation."""

    if not isinstance(context, dict) or context.get("project_id") != project_id:
        raise SportsAgentError("Sports baseline context belongs to another project")
    if context.get("simulation_id") not in (None, simulation_id):
        raise SportsAgentError("Sports baseline context belongs to another simulation")
    expected_hash = hashlib.sha256(dossier_text.encode("utf-8")).hexdigest()
    if context.get("dossier_sha256") != expected_hash:
        raise SportsAgentError("Sports baseline context does not match the pre-cutoff dossier")
    sport = context.get("sport")
    outcomes = _outcomes(sport)
    if f"- Sport: {sport}" not in dossier_text:
        raise SportsAgentError("Sports baseline context sport disagrees with the dossier")
    cutoff = _utc_timestamp(context.get("cutoff_at"), "cutoff_at")
    cutoff_line = next(
        (line for line in dossier_text.splitlines() if line.startswith("- Evidence cutoff (UTC): ")),
        None,
    )
    cutoff_text = (
        cutoff_line.removeprefix("- Evidence cutoff (UTC): ").split(" (", 1)[0]
        if cutoff_line else None
    )
    if cutoff_text is None or _utc_timestamp(cutoff_text, "dossier cutoff") != cutoff.replace(microsecond=0):
        raise SportsAgentError("Sports baseline context cutoff disagrees with the dossier")
    baseline = _probabilities(context.get("baseline_probabilities"), outcomes)
    latest_fetch = context.get("training_latest_fetch_at")
    if latest_fetch is not None and _utc_timestamp(latest_fetch, "training_latest_fetch_at") > cutoff:
        raise SportsAgentError("Sports baseline uses a result fetched after the cutoff")
    count = context.get("training_matches")
    ids = context.get("training_match_ids")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise SportsAgentError("Sports baseline training_matches is invalid")
    if not isinstance(ids, list) or len(ids) != count or any(not isinstance(item, str) for item in ids):
        raise SportsAgentError("Sports baseline training_match_ids is invalid")
    validated = dict(context)
    validated["baseline_probabilities"] = baseline
    return validated


def _facts(dossier_text: str) -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}
    section = ""
    for line in dossier_text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        match = _FACT_LINE.fullmatch(line)
        if not match:
            continue
        fact_id = f"F{len(catalog) + 1:03d}"
        catalog[fact_id] = {
            "category": section,
            "subject": match.group(1),
            "statement": match.group(2),
            "source": match.group(3),
            "captured_at": match.group(4),
        }
    return catalog


def _warning_lines(dossier_text: str) -> list[str]:
    return [
        line.removeprefix("- ")
        for line in dossier_text.splitlines()
        if line.startswith(("- Kickoff warning:", "- Data warning:", "- Data-freshness warning:"))
    ]


def _discussion_by_agent(actions: Sequence[Mapping[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    snippets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for action in actions:
        agent_id = action.get("agent_id")
        if isinstance(agent_id, bool) or not isinstance(agent_id, int):
            continue
        if action.get("success") is False or action.get("action_type") not in {
            "CREATE_POST", "CREATE_COMMENT", "QUOTE_POST"
        }:
            continue
        args = action.get("action_args") or {}
        content = args.get("content") if isinstance(args, dict) else None
        if not isinstance(content, str) or not content.strip():
            continue
        round_num = action.get("round")
        snippets[agent_id].append({
            "round": round_num if isinstance(round_num, int) else None,
            "text": " ".join(content.split())[:300],
        })
    return {agent_id: items[-3:] for agent_id, items in snippets.items()}


def _vote_from_json(
    data: Any, *, agent_id: int, label: str, outcomes: tuple[str, ...],
    facts: Mapping[str, Any], rounds_seen: list[int],
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SportsAgentError("agent vote is not a JSON object")
    raw = data.get("adjustments")
    if not isinstance(raw, dict) or set(raw) != set(outcomes):
        raise SportsAgentError("agent vote has wrong outcome keys")
    if any(isinstance(raw[k], bool) or not isinstance(raw[k], (int, float)) for k in outcomes):
        raise SportsAgentError("agent vote adjustments must be numeric")
    deltas = {k: float(raw[k]) for k in outcomes}
    if any(not math.isfinite(v) or abs(v) > MAX_ADJUSTMENT + 1e-9 for v in deltas.values()):
        raise SportsAgentError("agent vote exceeds the ten-point bound")
    if abs(sum(deltas.values())) > 1e-6:
        raise SportsAgentError("agent vote adjustments must sum to zero")
    fact_ids = data.get("fact_ids")
    if not isinstance(fact_ids, list) or not fact_ids or any(fid not in facts for fid in fact_ids):
        raise SportsAgentError("agent vote must cite dossier fact IDs")
    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip() or "\n" in rationale or len(rationale) > 240:
        raise SportsAgentError("agent vote needs a one-line rationale")
    return {
        "agent_id": agent_id,
        "synthetic_label": label,
        "synthetic": True,
        "adjustments": deltas,
        "fact_ids": list(dict.fromkeys(fact_ids))[:3],
        "rationale": rationale.strip(),
        "discussion_rounds_seen": sorted(rounds_seen),
    }


def _bounded_final(baseline: Mapping[str, float], mean_delta: Mapping[str, float]) -> dict[str, float]:
    """Project onto the probability simplex with a ±10-point box per outcome."""

    keys = tuple(baseline)
    lower = {k: max(0.0, baseline[k] - MAX_ADJUSTMENT) for k in keys}
    upper = {k: min(1.0, baseline[k] + MAX_ADJUSTMENT) for k in keys}
    raw = {k: baseline[k] + mean_delta[k] for k in keys}
    lo, hi = -2.0, 2.0
    for _ in range(100):
        shift = (lo + hi) / 2
        total = sum(min(upper[k], max(lower[k], raw[k] + shift)) for k in keys)
        if total < 1:
            lo = shift
        else:
            hi = shift
    result = {k: min(upper[k], max(lower[k], raw[k] + (lo + hi) / 2)) for k in keys}
    if abs(sum(result.values()) - 1) > 1e-10:
        raise SportsAgentError("bounded probability projection did not sum to one")
    return result


def collect_sports_votes(
    *,
    context: Mapping[str, Any],
    dossier_text: str,
    profiles: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    llm: LLMClient,
    simulation_id: str,
) -> dict[str, Any]:
    """Turn completed synthetic OASIS discussion into bounded JSON votes."""

    sport = str(context["sport"])
    outcomes = _outcomes(sport)
    baseline = _probabilities(context["baseline_probabilities"], outcomes)
    fact_catalog = _facts(dossier_text)
    warnings = _warning_lines(dossier_text)
    warnings.extend(str(warning) for warning in context.get("warnings", []) if isinstance(warning, str))
    selected = list(profiles)[:sports_agent_limit()]
    if len(profiles) > len(selected):
        warnings.append(f"Only {len(selected)} synthetic OASIS profiles were sampled for cost control")
    if len(selected) < 10:
        warnings.append(f"Only {len(selected)} graph-grounded synthetic personas were available")
    if not selected:
        raise SportsAgentError("No generated OASIS profiles are available for sports voting")
    if not fact_catalog:
        raise SportsAgentError("The dossier has no verified pre-cutoff facts to cite")
    discussion = _discussion_by_agent(actions)
    if not discussion:
        raise SportsAgentError(
            "OASIS recorded no synthetic discussion text; sports votes cannot "
            "adjust the baseline without a completed discussion"
        )
    participating_profiles = []
    for profile in selected:
        try:
            agent_id = int(profile.get("user_id"))
        except (TypeError, ValueError):
            continue
        if agent_id in discussion:
            participating_profiles.append(profile)
    if len(participating_profiles) < len(selected):
        warnings.append(
            f"{len(selected) - len(participating_profiles)} synthetic profiles had "
            "no recorded discussion text and did not vote"
        )
    selected = participating_profiles
    if not selected:
        raise SportsAgentError("No selected OASIS profile participated in the synthetic discussion")

    # The prompt is bounded even when the source dossier is large. A vote can
    # cite only the facts actually included here, never an invented reference.
    prompt_facts = dict(list(fact_catalog.items())[:60])
    fact_lines = [
        f"{fid}: {f['category']} / {f['subject']}: {f['statement'][:260]} "
        f"[source: {f['source']}; captured: {f['captured_at']}]"
        for fid, f in prompt_facts.items()
    ]
    votes: list[dict[str, Any]] = []
    rejected = 0
    seen_ids: set[int] = set()
    for index, profile in enumerate(selected, start=1):
        raw_id = profile.get("user_id")
        try:
            agent_id = int(raw_id)
        except (TypeError, ValueError):
            rejected += 1
            continue
        if agent_id in seen_ids:
            rejected += 1
            continue
        seen_ids.add(agent_id)
        role = str(profile.get("profession") or "analyst").strip()[:40]
        label = f"Synthetic {role} perspective {index}"
        snippets = discussion.get(agent_id, [])
        rounds_seen = [item["round"] for item in snippets if item["round"] is not None]
        system = (
            "You are one fictional analytical perspective in a sports simulation. "
            "You are not a real coach, player, referee, or team. The OASIS posts "
            "are synthetic discussion, NOT evidence. Only the numbered dossier "
            "facts are evidence. Do not claim a known final result. Return a JSON "
            "object with adjustments, fact_ids, rationale. Adjustments must use "
            f"exact keys {list(outcomes)}, each between -0.10 and +0.10, and sum "
            "to zero. Cite 1–3 listed fact IDs. Rationale must be one line."
        )
        user = json.dumps({
            "synthetic_perspective": label,
            "baseline_probabilities": baseline,
            "cutoff_at": context["cutoff_at"],
            "verified_pre_cutoff_facts": fact_lines,
            "synthetic_oasis_discussion_not_evidence": snippets,
            "example_shape": {
                "adjustments": {key: 0.0 for key in outcomes},
                "fact_ids": [next(iter(prompt_facts))],
                "rationale": "A short evidence-based explanation.",
            },
        }, ensure_ascii=False)
        try:
            response = llm.chat_json(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.2,
                max_tokens=256,
            )
            votes.append(_vote_from_json(
                response, agent_id=agent_id, label=label, outcomes=outcomes,
                facts=prompt_facts, rounds_seen=rounds_seen,
            ))
        except Exception as error:
            if is_budget_exceeded_error(error):
                raise
            if not isinstance(error, (SportsAgentError, LLMResponseError)):
                raise
            rejected += 1

    required = max(2, math.ceil(len(selected) / 2))
    if len(votes) < required:
        warnings.append(
            f"Only {len(votes)} of {len(selected)} synthetic votes validated; "
            "agent adjustment withheld"
        )
        mean_delta = {key: 0.0 for key in outcomes}
        consensus_facts: list[str] = []
        consensus_rationale = "Insufficient validated dossier-grounded votes; baseline unchanged."
    else:
        mean_delta = {key: sum(v["adjustments"][key] for v in votes) / len(votes) for key in outcomes}
        counts = Counter(fid for vote in votes for fid in vote["fact_ids"])
        consensus_facts = [fid for fid, _ in counts.most_common(3)]
        consensus_rationale = (
            f"Mean of {len(votes)} synthetic votes, chiefly citing "
            + ", ".join(consensus_facts) + "."
        )
    final = _bounded_final(baseline, mean_delta)
    adjustment = {key: final[key] - baseline[key] for key in outcomes}
    return {
        "schema_version": 1,
        "simulation_id": simulation_id,
        "project_id": context["project_id"],
        "sport": sport,
        "dossier_sha256": context["dossier_sha256"],
        "baseline_method": context["baseline_method"],
        "training_matches": context["training_matches"],
        "cutoff_at": context["cutoff_at"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "synthetic_discussion": True,
        "baseline_probabilities": baseline,
        "votes": votes,
        "rejected_vote_count": rejected,
        "consensus": {
            "adjustments": adjustment,
            "fact_ids": consensus_facts,
            "rationale": consensus_rationale,
        },
        "final_probabilities": final,
        "confidence": "Unvalidated; no calibration or predictive lift established",
        "data_freshness_warnings": warnings,
        "fact_catalog": fact_catalog,
    }


def read_oasis_actions(simulation_dir: Path) -> list[dict[str, Any]]:
    """Read only finished action logs; no Zep or simulation path is bypassed."""

    actions: list[dict[str, Any]] = []
    for platform in ("twitter", "reddit"):
        path = simulation_dir / platform / "actions.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and "event_type" not in item:
                actions.append(item)
    return actions


def save_forecast_once(simulation_dir: Path, forecast: Mapping[str, Any]) -> Path:
    """An initial forecast for a simulation is write-once, including its votes."""

    path = simulation_dir / "sports_agent_forecast.json"
    simulation_dir.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump(dict(forecast), file, ensure_ascii=False, indent=2)
    return path


def render_sports_forecast_section(forecast: Mapping[str, Any]) -> str:
    """Deterministic numbers appended to the existing ReportAgent narrative."""

    sport = str(forecast["sport"])
    outcomes = _outcomes(sport)
    baseline = _probabilities(forecast["baseline_probabilities"], outcomes)
    final = _probabilities(forecast["final_probabilities"], outcomes)
    adjustments = forecast["consensus"]["adjustments"]
    label = {"home": "Home win", "draw": "Draw", "away": "Away win"}
    rows = [
        "## Structured pre-match forecast",
        "",
        "OASIS discussion and votes are synthetic analytical perspectives, not "
        "statements by real coaches, players, referees, or teams. "
        "The statistical baseline is the primary probability source. "
        "No accuracy or profit has been established.",
        "",
        f"Evidence cutoff (UTC): {forecast['cutoff_at']}",
        "",
        "| Outcome | Statistical baseline | Agent adjustment | Final probability |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key in outcomes:
        rows.append(
            f"| {label[key]} | {baseline[key]:.2%} | "
            f"{float(adjustments[key]):+.2%} | {final[key]:.2%} |"
        )
    rows.extend((
        "",
        f"Synthetic consensus: {forecast['consensus']['rationale']}",
        f"Validated votes: {len(forecast['votes'])}; rejected votes: "
        f"{forecast['rejected_vote_count']}.",
        f"Confidence: {forecast['confidence']}.",
    ))
    if isinstance(forecast.get("agent_vote_tokens"), int):
        rows.append(
            f"Agent-vote model tokens for this match: {forecast['agent_vote_tokens']}."
        )
    if isinstance(forecast.get("total_run_tokens_at_report"), int):
        rows.append(
            "Total metered local-model tokens through this report: "
            f"{forecast['total_run_tokens_at_report']}."
        )
    catalog = forecast.get("fact_catalog", {})
    cited = forecast["consensus"].get("fact_ids", [])
    if cited:
        rows.extend(("", "Cited pre-cutoff dossier facts:"))
        for fact_id in cited:
            fact = catalog[fact_id]
            rows.append(
                f"- {fact_id}: {fact['subject']}: {fact['statement']} "
                f"(source: {fact['source']}; captured: {fact['captured_at']})"
            )
    if forecast.get("data_freshness_warnings"):
        rows.extend(("", "Data-freshness and validation warnings:"))
        rows.extend(f"- {warning}" for warning in forecast["data_freshness_warnings"])
    return "\n".join(rows).rstrip() + "\n"


def label_synthetic_report(content: str) -> str:
    """Put the fictional-agent notice at the top of a sports report."""

    notice = (
        "> **Synthetic pre-match simulation.** All OASIS agent posts, "
        "interviews, and votes are fictional analytical perspectives. "
        "They are not statements by real coaches, players, referees, "
        "or teams. Only the pre-cutoff dossier supplies factual evidence."
    )
    title_end = content.find("\n")
    if title_end >= 0:
        return content[:title_end] + "\n\n" + notice + content[title_end:]
    return notice + "\n\n" + content


def get_or_collect_sports_forecast(
    *, project_id: str, simulation_id: str, llm: LLMClient | None = None,
) -> dict[str, Any] | None:
    """Report-stage hook: after OASIS and Zep finish, collect once if sports."""

    from ..config import Config
    from ..models.project import ProjectManager
    from ..services.simulation_manager import SimulationManager
    from ..utils.local_llm_runtime import local_model_run

    dossier_text = ProjectManager.get_extracted_text(project_id)
    if not is_sports_dossier(dossier_text):
        return None
    simulation_dir = Path(SimulationManager.SIMULATION_DATA_DIR) / simulation_id
    context = validate_forecast_context(
        load_project_forecast_context(project_id),
        project_id=project_id, simulation_id=simulation_id, dossier_text=dossier_text,
    )
    existing = simulation_dir / "sports_agent_forecast.json"
    if existing.is_file():
        forecast = json.loads(existing.read_text(encoding="utf-8"))
        if (
            forecast.get("simulation_id") != simulation_id
            or forecast.get("project_id") != project_id
            or forecast.get("dossier_sha256") != context["dossier_sha256"]
        ):
            raise SportsAgentError("Existing sports forecast belongs to another run")
        return forecast
    manager = SimulationManager()
    profiles = manager.get_profiles(simulation_id)
    actions = read_oasis_actions(simulation_dir)
    if llm is not None:
        forecast = collect_sports_votes(
            context=context, dossier_text=dossier_text, profiles=profiles,
            actions=actions, llm=llm, simulation_id=simulation_id,
        )
    elif Config.LLM_PROVIDER == "local":
        with local_model_run(simulation_id, report=True) as gateway:
            before_tokens = gateway.budget.snapshot()["spent_tokens"]
            forecast = collect_sports_votes(
                context=context, dossier_text=dossier_text, profiles=profiles,
                actions=actions, llm=LLMClient(), simulation_id=simulation_id,
            )
            forecast["agent_vote_tokens"] = (
                gateway.budget.snapshot()["spent_tokens"] - before_tokens
            )
    else:
        forecast = collect_sports_votes(
            context=context, dossier_text=dossier_text, profiles=profiles,
            actions=actions, llm=LLMClient(model=Config.LLM_REPORT_MODEL_NAME),
            simulation_id=simulation_id,
        )
    try:
        save_forecast_once(simulation_dir, forecast)
    except FileExistsError:
        # Another report request won the immutable write. Use its complete
        # result rather than overwriting votes or comparing generated text.
        forecast = json.loads(existing.read_text(encoding="utf-8"))
    return forecast
