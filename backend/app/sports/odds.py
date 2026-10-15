"""Timestamped decimal 1X2/moneyline odds for manual entry or The Odds API.

The live adapter only requests a single h2h market and one region. No call is
made without ``THE_ODDS_API_KEY``. Provider update times describe the quote;
``recorded_at`` is when this application captured it. Historical API odds
require a separate paid plan and are not silently represented by live quotes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from types import MappingProxyType
from typing import Callable, Mapping
from urllib.parse import urlencode

import httpx

from .evaluation import MarketOdds, devig_decimal_odds


class OddsError(ValueError):
    """An odds quote, credential, or provider response is unusable."""


_SPORT_KEYS = {("football", "EPL"): "soccer_epl", ("basketball", "NBA"): "basketball_nba"}
_REGIONS = frozenset({"us", "us2", "uk", "eu", "au"})
_LABELS = frozenset({"opening", "closing", "current"})
_BASE = "https://api.the-odds-api.com/v4"
_MAX_RESPONSE_BYTES = 5_000_000


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OddsError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _time(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise OddsError(f"{name} must be an ISO 8601 timestamp")
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")), name)
    except ValueError as error:
        raise OddsError(f"{name} must be an ISO 8601 timestamp with timezone") from error


def outcomes_for(sport: str, league: str) -> tuple[str, ...]:
    if (sport, league) not in {("football", "EPL"), ("football", "NPFL"), ("basketball", "NBA")}:
        raise OddsError("unsupported sport/league; use EPL, NPFL, or NBA")
    return ("home", "draw", "away") if sport == "football" else ("home", "away")


@dataclass(frozen=True, slots=True)
class OddsQuote:
    """One bookmaker's full match-winner market captured before kickoff."""

    match_id: str
    sport: str
    league: str
    home_team: str
    away_team: str
    bookmaker: str
    decimal_odds: Mapping[str, float]
    recorded_at: datetime
    kickoff_at: datetime
    source: str
    source_kind: str = "manual"
    label: str = "current"
    provider_event_id: str | None = None
    bookmaker_updated_at: datetime | None = None

    def __post_init__(self) -> None:
        for field in ("match_id", "home_team", "away_team", "bookmaker", "source"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise OddsError(f"{field} must be non-empty text")
            object.__setattr__(self, field, value.strip())
        if self.home_team.casefold() == self.away_team.casefold():
            raise OddsError("home_team and away_team must differ")
        outcomes = outcomes_for(self.sport, self.league)
        if self.source_kind not in {"manual", "the_odds_api"}:
            raise OddsError("source_kind must be manual or the_odds_api")
        if self.label not in _LABELS:
            raise OddsError("label must be opening, closing, or current")
        object.__setattr__(self, "recorded_at", _utc(self.recorded_at, "recorded_at"))
        object.__setattr__(self, "kickoff_at", _utc(self.kickoff_at, "kickoff_at"))
        if self.recorded_at >= self.kickoff_at:
            raise OddsError("odds must be captured before the listed kickoff")
        if self.bookmaker_updated_at is not None:
            updated = _utc(self.bookmaker_updated_at, "bookmaker_updated_at")
            if updated > self.recorded_at:
                raise OddsError("bookmaker update cannot be later than capture")
            object.__setattr__(self, "bookmaker_updated_at", updated)
        if self.provider_event_id is not None and not str(self.provider_event_id).strip():
            raise OddsError("provider_event_id cannot be empty")
        try:
            numbers = {name: float(self.decimal_odds[name]) for name in outcomes}
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise OddsError("decimal odds must contain the league's full outcome set") from error
        if set(self.decimal_odds) != set(outcomes):
            raise OddsError("decimal odds must contain the league's full outcome set")
        if any(not isfinite(value) or value <= 1 for value in numbers.values()):
            raise OddsError("decimal odds must be finite and greater than one")
        object.__setattr__(self, "decimal_odds", MappingProxyType(numbers))

    def devigged(self) -> dict[str, float]:
        return devig_decimal_odds(self.decimal_odds, outcomes_for(self.sport, self.league))

    def as_market_odds(self) -> MarketOdds:
        """Reuse the walk-forward evaluator's market-comparison format."""

        return MarketOdds(
            match_id=self.match_id, recorded_at=self.recorded_at,
            decimal_odds=dict(self.decimal_odds), source=self.source,
        )


def manual_quote(
    *, match_id: str, sport: str, league: str, home_team: str, away_team: str,
    bookmaker: str, decimal_odds: Mapping[str, float], kickoff_at: datetime,
    source: str, label: str = "current", clock: Callable[[], datetime] | None = None,
) -> OddsQuote:
    """Capture manual prices *now*; a claimed old bookmaker time is not trusted."""

    now = clock or (lambda: datetime.now(timezone.utc))
    return OddsQuote(
        match_id=match_id, sport=sport, league=league, home_team=home_team,
        away_team=away_team, bookmaker=bookmaker, decimal_odds=decimal_odds,
        recorded_at=now(), kickoff_at=kickoff_at, source=source,
        source_kind="manual", label=label,
    )


def _http_get(url: str, params: Mapping[str, str], timeout_seconds: float) -> bytes:
    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            if len(response.content) > _MAX_RESPONSE_BYTES:
                raise OddsError("odds provider response exceeds 5 MB")
            return response.content
    except httpx.HTTPStatusError as error:
        # The request URL contains the key, so never include the exception.
        raise OddsError(f"odds provider returned HTTP {error.response.status_code}") from None
    except httpx.HTTPError:
        raise OddsError("odds provider request failed") from None


def fetch_the_odds_api(
    *, match_id: str, sport: str, league: str, home_team: str, away_team: str,
    region: str = "uk", bookmaker: str | None = None, event_id: str | None = None,
    label: str = "current", api_key: str | None = None,
    transport: Callable[[str, Mapping[str, str], float], bytes] | None = None,
    clock: Callable[[], datetime] | None = None, timeout_seconds: float = 15.0,
) -> tuple[OddsQuote, ...]:
    """Fetch a live EPL/NBA h2h event; NPFL uses manual quotes for now.

    Exact team-name matching is required. If multiple provider events match,
    pass the provider ``event_id`` rather than guessing which fixture it is.
    ``transport`` exists for offline tests; an API key is required even there.
    """

    key = (api_key or os.environ.get("THE_ODDS_API_KEY", "")).strip()
    if not key:
        raise OddsError("THE_ODDS_API_KEY is required; use manual odds entry otherwise")
    if (sport, league) not in _SPORT_KEYS:
        raise OddsError("The Odds API adapter supports EPL and NBA; use manual odds for NPFL")
    if region not in _REGIONS:
        raise OddsError("region must be one of us, us2, uk, eu, or au")
    if label not in _LABELS:
        raise OddsError("label must be opening, closing, or current")
    if not isinstance(home_team, str) or not home_team.strip() or not isinstance(away_team, str) or not away_team.strip():
        raise OddsError("home_team and away_team are required")
    if not isinstance(timeout_seconds, (int, float)) or not isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
        raise OddsError("timeout_seconds must be between zero and 60")
    path = f"{_BASE}/sports/{_SPORT_KEYS[(sport, league)]}/odds"
    params = {"apiKey": key, "regions": region, "markets": "h2h",
              "oddsFormat": "decimal", "dateFormat": "iso"}
    read = transport or _http_get
    raw = read(path, params, float(timeout_seconds))
    if not isinstance(raw, bytes) or len(raw) > _MAX_RESPONSE_BYTES:
        raise OddsError("odds provider response is malformed or exceeds 5 MB")
    try:
        events = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise OddsError("odds provider returned invalid JSON") from error
    if not isinstance(events, list):
        raise OddsError("odds provider response must be an events array")
    matching = [event for event in events if isinstance(event, dict) and
                isinstance(event.get("home_team"), str) and
                isinstance(event.get("away_team"), str) and
                event["home_team"].casefold() == home_team.casefold() and
                event["away_team"].casefold() == away_team.casefold() and
                (event_id is None or event.get("id") == event_id)]
    if len(matching) != 1:
        raise OddsError("odds provider event not found or ambiguous; check teams and event_id")
    event = matching[0]
    if not isinstance(event.get("id"), str) or not event["id"].strip():
        raise OddsError("odds provider event has no stable ID")
    if event.get("sport_key") != _SPORT_KEYS[(sport, league)]:
        raise OddsError("odds provider sport key does not match requested league")
    kickoff_at = _time(event.get("commence_time"), "provider kickoff")
    captured_at = _utc((clock or (lambda: datetime.now(timezone.utc)))(), "clock")
    if captured_at >= kickoff_at:
        raise OddsError("odds provider event is live or already started")
    if not isinstance(event.get("bookmakers"), list):
        raise OddsError("odds provider event has no bookmaker array")
    outcomes = outcomes_for(sport, league)
    names = {"home": home_team, "away": away_team}
    if sport == "football":
        names["draw"] = "Draw"
    source = path + "?" + urlencode({key: value for key, value in params.items() if key != "apiKey"})
    quotes = []
    for book in event["bookmakers"]:
        if not isinstance(book, dict) or (bookmaker is not None and book.get("key") != bookmaker):
            continue
        book_key = book.get("key")
        if not isinstance(book_key, str) or not book_key:
            continue
        markets = book.get("markets")
        if not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict) or market.get("key") != "h2h":
                continue
            rows = market.get("outcomes")
            if not isinstance(rows, list):
                continue
            price_by_name = {row.get("name"): row.get("price") for row in rows
                             if isinstance(row, dict) and isinstance(row.get("name"), str)}
            if set(price_by_name) != set(names.values()):
                continue  # Incomplete market; do not invent a missing draw.
            odds = {outcome: price_by_name[name] for outcome, name in names.items()}
            quotes.append(OddsQuote(
                match_id=match_id, sport=sport, league=league,
                home_team=home_team, away_team=away_team,
                bookmaker=book_key, decimal_odds=odds, recorded_at=captured_at,
                kickoff_at=kickoff_at, source=source, source_kind="the_odds_api",
                label=label, provider_event_id=str(event["id"]),
                bookmaker_updated_at=_time(book.get("last_update"), "bookmaker last_update"),
            ))
    if not quotes:
        raise OddsError("odds provider has no complete pre-match h2h quote for this event")
    return tuple(quotes)
