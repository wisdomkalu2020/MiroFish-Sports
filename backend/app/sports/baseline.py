"""Time-ordered, non-LLM sports probability baselines.

Only ``observe`` changes model state.  It refuses a result until its recorded
availability time, so callers can run a walk-forward evaluation without Zep,
agents, or bookmaker data.  Parameters are fixed in code for now; they are
not fitted against the evaluation matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import exp, factorial, isfinite, log
from typing import Mapping, Protocol


class CompletedMatch(Protocol):
    match_id: str
    sport: str
    league: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    result_available_at: datetime


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must have a timezone")
    return value.astimezone(timezone.utc)


def _normalize(values: Mapping[str, float]) -> dict[str, float]:
    if not values or any(not isfinite(v) or v < 0 for v in values.values()):
        raise ValueError("probabilities must be finite and non-negative")
    total = sum(values.values())
    if total <= 0:
        raise ValueError("probabilities have zero mass")
    return {key: value / total for key, value in values.items()}


@dataclass(frozen=True, slots=True)
class FootballForecast:
    elo: Mapping[str, float]
    dixon_coles: Mapping[str, float]
    combined: Mapping[str, float]
    scorelines: Mapping[str, float]
    expected_home_goals: float
    expected_away_goals: float
    dixon_coles_rho: float
    training_matches: int


@dataclass(frozen=True, slots=True)
class BasketballForecast:
    elo: Mapping[str, float]
    training_matches: int


class _Elo:
    def __init__(self, *, home_advantage: float = 65.0, k: float = 20.0):
        self.ratings: dict[str, float] = {}
        self.home_advantage = home_advantage
        self.k = k

    def expected_home_score(self, home: str, away: str) -> float:
        home_rating = self.ratings.get(home, 1500.0) + self.home_advantage
        away_rating = self.ratings.get(away, 1500.0)
        return 1.0 / (1.0 + 10.0 ** ((away_rating - home_rating) / 400.0))

    def observe(self, home: str, away: str, home_score: int, away_score: int) -> None:
        expected = self.expected_home_score(home, away)
        actual = 1.0 if home_score > away_score else 0.0 if home_score < away_score else 0.5
        adjustment = self.k * (actual - expected)
        self.ratings[home] = self.ratings.get(home, 1500.0) + adjustment
        self.ratings[away] = self.ratings.get(away, 1500.0) - adjustment


class _TemporalModel:
    sport: str

    def __init__(self, league: str):
        if not isinstance(league, str) or not league.strip():
            raise ValueError("league is required")
        self.league = league.strip()
        self._seen: set[str] = set()
        self._last_result_available_at: datetime | None = None
        self.training_matches = 0
        self.elo = _Elo()

    def _validate(self, match: CompletedMatch, *, as_of: datetime) -> bool:
        cutoff = _utc(as_of)
        if match.sport != self.sport or match.league != self.league:
            raise ValueError("result belongs to a different sport or league")
        if not match.match_id or match.match_id in self._seen:
            raise ValueError("duplicate or missing match ID")
        if match.home_team == match.away_team or not match.home_team or not match.away_team:
            raise ValueError("match teams must be distinct")
        if (
            isinstance(match.home_score, bool)
            or isinstance(match.away_score, bool)
            or not isinstance(match.home_score, int)
            or not isinstance(match.away_score, int)
            or min(match.home_score, match.away_score) < 0
        ):
            raise ValueError("final scores must be non-negative integers")
        result_time = _utc(match.result_available_at)
        if result_time > cutoff:
            raise ValueError("result was not available by the forecast cutoff")
        if self._last_result_available_at is not None and result_time < self._last_result_available_at:
            raise ValueError("results must be observed in availability order")
        self._seen.add(match.match_id)
        self._last_result_available_at = result_time
        self.training_matches += 1
        return True


class FootballBaseline(_TemporalModel):
    """Elo plus smoothed independent Poisson with Dixon-Coles low-score terms."""

    sport = "football"

    def __init__(self, league: str):
        super().__init__(league)
        self._matches: list[tuple[str, str, int, int]] = []
        self._home: dict[str, list[int]] = {}
        self._away: dict[str, list[int]] = {}
        self._home_goals = 0
        self._away_goals = 0
        self._draws = 0

    def observe(self, match: CompletedMatch, *, as_of: datetime) -> None:
        self._validate(match, as_of=as_of)
        self.elo.observe(match.home_team, match.away_team, match.home_score, match.away_score)
        self._home.setdefault(match.home_team, [0, 0, 0])
        self._away.setdefault(match.away_team, [0, 0, 0])
        home = self._home[match.home_team]
        away = self._away[match.away_team]
        home[0] += 1
        home[1] += match.home_score
        home[2] += match.away_score
        away[0] += 1
        away[1] += match.away_score
        away[2] += match.home_score
        self._home_goals += match.home_score
        self._away_goals += match.away_score
        self._draws += match.home_score == match.away_score
        self._matches.append((match.home_team, match.away_team, match.home_score, match.away_score))

    def _goal_rates(self, home: str, away: str) -> tuple[float, float]:
        n = self.training_matches
        league_home = (self._home_goals + 20 * 1.35) / (n + 20)
        league_away = (self._away_goals + 20 * 1.10) / (n + 20)
        h = self._home.get(home, [0, 0, 0])
        a = self._away.get(away, [0, 0, 0])
        prior = 6
        h_for = (h[1] + prior * league_home) / (h[0] + prior)
        h_against = (h[2] + prior * league_away) / (h[0] + prior)
        a_for = (a[1] + prior * league_away) / (a[0] + prior)
        a_against = (a[2] + prior * league_home) / (a[0] + prior)
        home_rate = h_for * a_against / league_home
        away_rate = a_for * h_against / league_away
        return max(0.2, min(4.0, home_rate)), max(0.2, min(4.0, away_rate))

    @staticmethod
    def _tau(home_goals: int, away_goals: int, home_rate: float, away_rate: float, rho: float) -> float:
        if home_goals == 0 and away_goals == 0:
            return 1.0 - home_rate * away_rate * rho
        if home_goals == 0 and away_goals == 1:
            return 1.0 + home_rate * rho
        if home_goals == 1 and away_goals == 0:
            return 1.0 + away_rate * rho
        if home_goals == 1 and away_goals == 1:
            return 1.0 - rho
        return 1.0

    def _rho(self) -> float:
        if self.training_matches < 30:
            return -0.05
        # Re-estimate only the low-score correction on past games.  The fixed
        # candidate grid prevents an unstable optimizer from leaking into a
        # small historical sample.
        candidates = (-0.15, -0.10, -0.05, 0.0, 0.05, 0.10)
        sample = self._matches[-500:]
        best_rho, best_log_likelihood = -0.05, float("-inf")
        for rho in candidates:
            score = 0.0
            valid = True
            for home, away, hg, ag in sample:
                if hg > 1 or ag > 1:
                    continue
                h_rate, a_rate = self._goal_rates(home, away)
                tau = self._tau(hg, ag, h_rate, a_rate, rho)
                if tau <= 0:
                    valid = False
                    break
                score += log(tau)
            if valid and score > best_log_likelihood:
                best_rho, best_log_likelihood = rho, score
        return best_rho

    def _elo_probabilities(self, home: str, away: str) -> dict[str, float]:
        rh = self.elo.ratings.get(home, 1500.0) + self.elo.home_advantage
        ra = self.elo.ratings.get(away, 1500.0)
        x, y = 10 ** (rh / 400.0), 10 ** (ra / 400.0)
        draw_rate = (self._draws + 20 * 0.27) / (self.training_matches + 20)
        draw_factor = 2.0 * draw_rate / (1.0 - draw_rate)
        tied = draw_factor * (x * y) ** 0.5
        return _normalize({"home": x, "draw": tied, "away": y})

    def predict(self, home: str, away: str) -> FootballForecast:
        if not home or not away or home == away:
            raise ValueError("forecast teams must be distinct")
        h_rate, a_rate = self._goal_rates(home, away)
        rho = self._rho()
        if any(
            self._tau(hg, ag, h_rate, a_rate, rho) <= 0
            for hg, ag in ((0, 0), (0, 1), (1, 0), (1, 1))
        ):
            rho = 0.0
        h_p = [exp(-h_rate) * h_rate ** goals / factorial(goals) for goals in range(9)]
        a_p = [exp(-a_rate) * a_rate ** goals / factorial(goals) for goals in range(9)]
        scorelines = _normalize({
            f"{hg}-{ag}": h_p[hg] * a_p[ag] * self._tau(hg, ag, h_rate, a_rate, rho)
            for hg in range(9) for ag in range(9)
        })
        dc = _normalize({
            "home": sum(p for score, p in scorelines.items() if int(score[0]) > int(score[2])),
            "draw": sum(p for score, p in scorelines.items() if score[0] == score[2]),
            "away": sum(p for score, p in scorelines.items() if int(score[0]) < int(score[2])),
        })
        elo = self._elo_probabilities(home, away)
        combined = _normalize({outcome: 0.5 * elo[outcome] + 0.5 * dc[outcome] for outcome in dc})
        return FootballForecast(
            elo=elo,
            dixon_coles=dc,
            combined=combined,
            scorelines=scorelines,
            expected_home_goals=h_rate,
            expected_away_goals=a_rate,
            dixon_coles_rho=rho,
            training_matches=self.training_matches,
        )


class BasketballBaseline(_TemporalModel):
    """Home-adjusted Elo win probabilities for the standard NBA league."""

    sport = "basketball"

    def observe(self, match: CompletedMatch, *, as_of: datetime) -> None:
        if match.home_score == match.away_score:
            raise ValueError("basketball final score cannot be tied")
        self._validate(match, as_of=as_of)
        self.elo.observe(match.home_team, match.away_team, match.home_score, match.away_score)

    def predict(self, home: str, away: str) -> BasketballForecast:
        if not home or not away or home == away:
            raise ValueError("forecast teams must be distinct")
        home_probability = self.elo.expected_home_score(home, away)
        return BasketballForecast(
            elo=_normalize({"home": home_probability, "away": 1.0 - home_probability}),
            training_matches=self.training_matches,
        )
