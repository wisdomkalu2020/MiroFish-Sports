"""Leakage-conscious, baseline-only walk-forward sports evaluation.

Historical scores are used for training only after their conservative
``result_available_at`` timestamp.  All fixtures on a calendar date are
forecast together before any result from that date can update the models.
The date-only sources do not verify kickoff time, so their forecast cutoff is
00:00 UTC on the listed match date minus ``cutoff_hours``.  A verified kickoff
earlier than that date boundary makes the cutoff earlier still.

The result availability timestamps supplied by the historical loaders are
conservative assumptions, not archived proof of what a bookmaker or an agent
could see at that time.  This module makes no network, LLM, or Zep calls.
"""

from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import isclose, isfinite, log
from typing import Iterable, Mapping

from .baseline import BasketballBaseline, FootballBaseline
from .history import HistoricalMatch

_LOG_EPSILON = 1e-15
_AGENT_METHODS = frozenset({"anonymized", "post_training_cutoff"})


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _probabilities(values: Mapping[str, float], outcomes: tuple[str, ...]) -> dict[str, float]:
    if set(values) != set(outcomes):
        raise ValueError(f"probabilities must contain exactly {', '.join(outcomes)}")
    probabilities = {key: float(values[key]) for key in outcomes}
    if any(not isfinite(value) or value < 0 or value > 1 for value in probabilities.values()):
        raise ValueError("probabilities must be finite values between 0 and 1")
    if not isclose(sum(probabilities.values()), 1.0, abs_tol=1e-8, rel_tol=0):
        raise ValueError("probabilities must sum to one")
    return probabilities


@dataclass(frozen=True, slots=True)
class AgentPrediction:
    """Final baseline-plus-agents probability, captured before the cutoff.

    ``leakage_method`` records the submitter's attestation.  It cannot prove
    that historical outcome knowledge was absent from an LLM's pretraining.
    """

    match_id: str
    recorded_at: datetime
    probabilities: Mapping[str, float]
    leakage_method: str
    model_training_cutoff_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.match_id, str) or not self.match_id.strip():
            raise ValueError("agent match_id is required")
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at, "recorded_at"))
        if self.leakage_method not in _AGENT_METHODS:
            raise ValueError("agent leakage_method must be anonymized or post_training_cutoff")
        if self.leakage_method == "post_training_cutoff":
            if self.model_training_cutoff_at is None:
                raise ValueError("post_training_cutoff requires model_training_cutoff_at")
            object.__setattr__(
                self,
                "model_training_cutoff_at",
                _utc(self.model_training_cutoff_at, "model_training_cutoff_at"),
            )
        elif self.model_training_cutoff_at is not None:
            raise ValueError("anonymized agent rows must not set model_training_cutoff_at")


@dataclass(frozen=True, slots=True)
class MarketOdds:
    """Decimal odds captured at a particular time; never assumed closing."""

    match_id: str
    recorded_at: datetime
    decimal_odds: Mapping[str, float]
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.match_id, str) or not self.match_id.strip():
            raise ValueError("market match_id is required")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("market source is required")
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at, "recorded_at"))


def devig_decimal_odds(odds: Mapping[str, float], outcomes: tuple[str, ...]) -> dict[str, float]:
    """Proportionally remove the overround from positive decimal odds."""

    if set(odds) != set(outcomes):
        raise ValueError(f"odds must contain exactly {', '.join(outcomes)}")
    numeric = {key: float(odds[key]) for key in outcomes}
    if any(not isfinite(value) or value <= 1.0 for value in numeric.values()):
        raise ValueError("decimal odds must be finite and greater than one")
    inverse = {key: 1.0 / value for key, value in numeric.items()}
    total = sum(inverse.values())
    return {key: value / total for key, value in inverse.items()}


def _cutoff(match: HistoricalMatch, hours: float) -> datetime:
    midnight = datetime.combine(match.match_date, time.min, timezone.utc)
    anchor = min(midnight, match.kickoff_at) if match.kickoff_at is not None else midnight
    return anchor - timedelta(hours=hours)


def _outcome(match: HistoricalMatch) -> str:
    if match.home_score > match.away_score:
        return "home"
    if match.home_score < match.away_score:
        return "away"
    return "draw"


def _latest_pre_cutoff(rows, cutoff: datetime):
    eligible = [row for row in rows if row.recorded_at <= cutoff]
    return max(eligible, key=lambda row: row.recorded_at, default=None)


def _metrics(samples: list[tuple[Mapping[str, float], str]], outcomes: tuple[str, ...], bins: int) -> dict:
    if not samples:
        return {"status": "unavailable", "count": 0, "reason": "No eligible pre-cutoff predictions"}
    log_loss = 0.0
    brier = 0.0
    calibration: dict[str, list[dict]] = {
        outcome: [
            {"lower": index / bins, "upper": (index + 1) / bins, "count": 0,
             "predicted_sum": 0.0, "observed_sum": 0}
            for index in range(bins)
        ]
        for outcome in outcomes
    }
    for probabilities, actual in samples:
        log_loss -= log(max(probabilities[actual], _LOG_EPSILON))
        brier += sum((probabilities[outcome] - float(actual == outcome)) ** 2 for outcome in outcomes)
        for outcome in outcomes:
            probability = probabilities[outcome]
            bucket = calibration[outcome][min(int(probability * bins), bins - 1)]
            bucket["count"] += 1
            bucket["predicted_sum"] += probability
            bucket["observed_sum"] += actual == outcome
    for buckets in calibration.values():
        for bucket in buckets:
            count = bucket.pop("count")
            predicted_sum = bucket.pop("predicted_sum")
            observed_sum = bucket.pop("observed_sum")
            bucket.update({
                "count": count,
                "mean_predicted": predicted_sum / count if count else None,
                "observed_frequency": observed_sum / count if count else None,
            })
    return {
        "status": "scored",
        "count": len(samples),
        "log_loss": log_loss / len(samples),
        "brier_score": brier / len(samples),
        "calibration": calibration,
    }


def _paired_comparison(
    pairs: list[tuple[Mapping[str, float], Mapping[str, float], str]],
    *, outcomes: tuple[str, ...], bins: int, baseline_name: str,
) -> dict:
    if not pairs:
        return {"status": "unavailable", "count": 0, "reason": "No eligible paired predictions"}
    baseline = _metrics([(before, actual) for before, _, actual in pairs], outcomes, bins)
    alternative = _metrics([(after, actual) for _, after, actual in pairs], outcomes, bins)
    return {
        "status": "scored",
        "count": len(pairs),
        "baseline_variant": baseline_name,
        "baseline_log_loss": baseline["log_loss"],
        "alternative_log_loss": alternative["log_loss"],
        "delta_log_loss": alternative["log_loss"] - baseline["log_loss"],
        "baseline_brier_score": baseline["brier_score"],
        "alternative_brier_score": alternative["brier_score"],
        "delta_brier_score": alternative["brier_score"] - baseline["brier_score"],
    }


def evaluate_walk_forward(
    matches: Iterable[HistoricalMatch],
    *,
    cutoff_hours: float,
    evaluation_start: date | None = None,
    agent_predictions: Iterable[AgentPrediction] = (),
    market_odds: Iterable[MarketOdds] = (),
    calibration_bins: int = 10,
) -> dict:
    """Score one league's full final-score history without agents or Zep.

    By default every match is evaluated, including cold starts.  With
    ``evaluation_start``, earlier matches warm the models only as their
    results become available at held-out fixture cutoffs.  Naive probabilities
    are Laplace-smoothed observed outcome rates.
    Football exposes Elo, Dixon-Coles and their equal-weight combination;
    basketball exposes Elo.  Optional external agent and market rows must have
    been recorded by the match cutoff.  Absent variants are reported as such.
    """

    if isinstance(cutoff_hours, bool) or not isinstance(cutoff_hours, (int, float)):
        raise ValueError("cutoff_hours must be a non-negative finite number")
    if not isfinite(cutoff_hours) or cutoff_hours < 0:
        raise ValueError("cutoff_hours must be a non-negative finite number")
    if type(calibration_bins) is not int or calibration_bins < 2 or calibration_bins > 100:
        raise ValueError("calibration_bins must be an integer from 2 to 100")
    if evaluation_start is not None and type(evaluation_start) is not date:
        raise ValueError("evaluation_start must be a calendar date")
    history = tuple(matches)
    if not history:
        raise ValueError("at least one completed match is required")
    if not all(isinstance(match, HistoricalMatch) for match in history):
        raise TypeError("matches must be HistoricalMatch records")
    sport, league = history[0].sport, history[0].league
    if any((match.sport, match.league) != (sport, league) for match in history):
        raise ValueError("evaluate one sport and league at a time")
    if len({match.match_id for match in history}) != len(history):
        raise ValueError("duplicate match IDs in history")
    if sport not in {"football", "basketball"}:
        raise ValueError("unsupported sport")
    if evaluation_start is not None and not any(match.match_date >= evaluation_start for match in history):
        raise ValueError("no matches on or after evaluation_start")
    outcomes = ("home", "draw", "away") if sport == "football" else ("home", "away")
    model = FootballBaseline(league) if sport == "football" else BasketballBaseline(league)
    by_day: dict = defaultdict(list)
    for match in history:
        by_day[match.match_date].append(match)
    match_ids = {match.match_id for match in history}
    agents_by_id: dict[str, list[AgentPrediction]] = defaultdict(list)
    markets_by_id: dict[str, list[MarketOdds]] = defaultdict(list)
    for prediction in agent_predictions:
        if not isinstance(prediction, AgentPrediction):
            raise TypeError("agent_predictions must contain AgentPrediction rows")
        if prediction.match_id not in match_ids:
            raise ValueError("agent prediction has unknown match ID")
        _probabilities(prediction.probabilities, outcomes)
        agents_by_id[prediction.match_id].append(prediction)
    for quote in market_odds:
        if not isinstance(quote, MarketOdds):
            raise TypeError("market_odds must contain MarketOdds rows")
        if quote.match_id not in match_ids:
            raise ValueError("market odds have unknown match ID")
        devig_decimal_odds(quote.decimal_odds, outcomes)
        markets_by_id[quote.match_id].append(quote)

    variants = ["naive", "elo"]
    if sport == "football":
        variants += ["dixon_coles", "combined"]
    variants += ["baseline_plus_agents", "bookmaker"]
    samples: dict[str, list[tuple[Mapping[str, float], str]]] = {variant: [] for variant in variants}
    primary_baseline = "combined" if sport == "football" else "elo"
    agent_pairs: list[tuple[Mapping[str, float], Mapping[str, float], str]] = []
    market_pairs: list[tuple[Mapping[str, float], Mapping[str, float], str]] = []
    counts = {outcome: 0 for outcome in outcomes}
    queued: list[tuple[datetime, str, HistoricalMatch]] = []
    predictions: list[dict] = []
    for day in sorted(by_day):
        day_matches = sorted(by_day[day], key=lambda match: match.match_id)
        if evaluation_start is not None and day < evaluation_start:
            for match in day_matches:
                heapq.heappush(queued, (match.result_available_at, match.match_id, match))
            continue
        day_cutoff = min(_cutoff(match, cutoff_hours) for match in day_matches)
        while queued and queued[0][0] <= day_cutoff:
            _, _, known_match = heapq.heappop(queued)
            model.observe(known_match, as_of=day_cutoff)
            counts[_outcome(known_match)] += 1
        training_matches = model.training_matches
        naive = {outcome: (counts[outcome] + 1) / (training_matches + len(outcomes)) for outcome in outcomes}
        for match in day_matches:
            forecast = model.predict(match.home_team, match.away_team)
            row_probs: dict[str, dict[str, float]] = {"naive": naive, "elo": dict(forecast.elo)}
            if sport == "football":
                row_probs["dixon_coles"] = dict(forecast.dixon_coles)
                row_probs["combined"] = dict(forecast.combined)
            agent = _latest_pre_cutoff(
                (
                    row for row in agents_by_id[match.match_id]
                    if row.leakage_method == "anonymized"
                    or day_cutoff > row.model_training_cutoff_at
                ),
                day_cutoff,
            )
            if agent is not None:
                row_probs["baseline_plus_agents"] = _probabilities(agent.probabilities, outcomes)
            quote = _latest_pre_cutoff(markets_by_id[match.match_id], day_cutoff)
            if quote is not None:
                row_probs["bookmaker"] = devig_decimal_odds(quote.decimal_odds, outcomes)
            actual = _outcome(match)
            for variant, probabilities in row_probs.items():
                samples[variant].append((probabilities, actual))
            if "baseline_plus_agents" in row_probs:
                agent_pairs.append((row_probs[primary_baseline], row_probs["baseline_plus_agents"], actual))
            if "bookmaker" in row_probs:
                market_pairs.append((row_probs[primary_baseline], row_probs["bookmaker"], actual))
            predictions.append({
                "match_id": match.match_id,
                "match_date": match.match_date.isoformat(),
                "cutoff_at": day_cutoff.isoformat(),
                "kickoff_unverified": match.kickoff_unverified,
                "training_matches": training_matches,
                "actual_outcome": actual,
                "probabilities": row_probs,
                "agent_leakage_method": agent.leakage_method if "baseline_plus_agents" in row_probs else None,
                "agent_recorded_at": agent.recorded_at.isoformat() if "baseline_plus_agents" in row_probs else None,
                "bookmaker_source": quote.source if quote is not None else None,
                "bookmaker_recorded_at": quote.recorded_at.isoformat() if quote is not None else None,
            })
        # These results can only affect a *later* date, even if an upstream
        # source claims a same-day final was available very early.
        for match in day_matches:
            heapq.heappush(queued, (match.result_available_at, match.match_id, match))
    scored = {variant: _metrics(samples[variant], outcomes, calibration_bins) for variant in variants}
    if not agents_by_id:
        scored["baseline_plus_agents"]["reason"] = "No agent predictions supplied"
    if not markets_by_id:
        scored["bookmaker"]["reason"] = "No bookmaker odds supplied"
    return {
        "sport": sport,
        "league": league,
        "match_count": len(predictions),
        "history_match_count": len(history),
        "evaluation_start": evaluation_start.isoformat() if evaluation_start is not None else None,
        "cutoff_hours": float(cutoff_hours),
        "cutoff_rule": "All same-date matches use the earliest of their date's 00:00 UTC or verified kickoff, minus cutoff_hours; same-date results are excluded",
        "result_availability_assumption": "Historical final scores enter training only at their loader-provided conservative result_available_at; this is not an archived pre-match snapshot",
        "score_definition": "Multiclass Brier is the sum of squared outcome errors (0 to 2); log loss clips probabilities at 1e-15",
        "variants": scored,
        "paired_comparisons": {
            "agents_vs_baseline": _paired_comparison(
                agent_pairs, outcomes=outcomes, bins=calibration_bins, baseline_name=primary_baseline,
            ),
            "bookmaker_vs_baseline": _paired_comparison(
                market_pairs, outcomes=outcomes, bins=calibration_bins, baseline_name=primary_baseline,
            ),
        },
        "predictions": predictions,
    }
