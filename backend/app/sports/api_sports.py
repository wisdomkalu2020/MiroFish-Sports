"""API-Sports football/NBA loaders backed by as-of response snapshots.

The APIs expose current state, not a historical as-of query.  Consequently a
past cutoff can use only responses this application actually captured by that
time.  A current response is never backdated from game or article dates.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Callable, Literal, Mapping, Protocol
from urllib.parse import urlencode

import httpx

from .schema import FactCategory, MatchData, MatchFixture, Provenance, SportsFact
from .snapshots import Snapshot, SnapshotStore, SnapshotUnavailable, _aware_utc


FOOTBALL_BASE = "https://v3.football.api-sports.io"
NBA_BASE = "https://v2.nba.api-sports.io"
FOOTBALL_FINAL_STATUSES = frozenset({"FT", "AET", "PEN"})


class SportsSourceError(RuntimeError):
    """A provider response, credential, or transport failed explicitly."""


class PartialSourceData(SportsSourceError):
    """A provider sent only one page of a multi-page result."""


class SportsTransport(Protocol):
    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> bytes: ...


def _http_get(url: str, headers: Mapping[str, str], timeout: float) -> bytes:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            return response.content
    except httpx.HTTPStatusError as error:
        raise SportsSourceError(f"Sports provider returned HTTP {error.response.status_code}") from error
    except httpx.HTTPError as error:
        raise SportsSourceError("Sports provider request failed") from error


def _obj(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _numeric_id(value: object, label: str) -> int:
    if isinstance(value, bool) or not (
        isinstance(value, int) or (isinstance(value, str) and value.isdecimal())
    ):
        raise SportsSourceError(f"{label} is not a numeric provider ID")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise SportsSourceError(f"{label} is not a numeric provider ID") from error
    if number <= 0:
        raise SportsSourceError(f"{label} is not a positive provider ID")
    return number


def _time(value: object, label: str) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise SportsSourceError(f"{label} lacks a full kickoff timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _aware_utc(parsed, label)
    except ValueError as error:
        raise SportsSourceError(f"{label} is not a timezone-aware timestamp") from error


def _score(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _provider_rows(snapshot: Snapshot) -> list[dict]:
    payload = snapshot.payload()
    if payload.get("errors"):
        # Do not echo provider error text: it could include account information.
        raise SportsSourceError("Sports provider reported an API error (check the key or quota)")
    rows = payload.get("response")
    if not isinstance(rows, list):
        raise SportsSourceError("Sports provider response is missing its results array")
    paging = _obj(payload.get("paging"))
    if paging.get("total") is not None:
        try:
            if int(paging["total"]) > 1:
                raise PartialSourceData("Sports provider response is paginated; refusing partial data")
        except (TypeError, ValueError) as error:
            raise SportsSourceError("Sports provider paging metadata is invalid") from error
    if any(not isinstance(row, dict) for row in rows):
        raise SportsSourceError("Sports provider results contain a malformed record")
    return rows


class _ApiClient:
    def __init__(
        self,
        base_url: str,
        store: SnapshotStore,
        key_names: tuple[str, ...],
        *,
        transport: SportsTransport | None,
        clock: Callable[[], datetime] | None,
        timeout_seconds: float,
    ):
        if not isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("timeout_seconds must be between 0 and 60")
        self.base_url = base_url
        self.store = store
        self.key_names = key_names
        self.transport = transport or _http_get
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.timeout_seconds = timeout_seconds

    def url(self, path: str, **params: object) -> str:
        query = urlencode(sorted((name, str(value)) for name, value in params.items()))
        return f"{self.base_url}/{path.lstrip('/')}" + (f"?{query}" if query else "")

    def read(
        self,
        path: str,
        *,
        cutoff_at: datetime,
        refresh_live: bool = False,
        **params: object,
    ) -> Snapshot:
        cutoff_at = _aware_utc(cutoff_at, "cutoff_at")
        source_url = self.url(path, **params)
        old = self.store.latest_as_of(source_url, cutoff_at)
        now = _aware_utc(self.clock(), "clock time")
        if old is not None and (not refresh_live or now > cutoff_at):
            _provider_rows(old)
            return old
        if now > cutoff_at:
            raise SnapshotUnavailable(
                "No source response was captured by the historical cutoff for " + source_url
            )
        key = next((_text(os.environ.get(name)) for name in self.key_names if _text(os.environ.get(name))), None)
        if key is None:
            raise SportsSourceError(
                "Sports API key is missing; set " + " or ".join(self.key_names)
            )
        raw = self.transport(
            source_url,
            {"x-apisports-key": key, "accept": "application/json"},
            self.timeout_seconds,
        )
        if not isinstance(raw, bytes):
            raise SportsSourceError("Sports transport must return JSON response bytes")
        captured_at = _aware_utc(self.clock(), "clock time")
        candidate = Snapshot(source_url, captured_at, raw)
        _provider_rows(candidate)  # Validate auth/errors before recording a usable capture.
        saved = self.store.save(source_url, raw, captured_at)
        if captured_at > cutoff_at:
            raise SnapshotUnavailable(
                "Sports response arrived after the requested cutoff; it was captured but cannot be used"
            )
        return saved


@dataclass(frozen=True, slots=True)
class _CompletedGame:
    game_id: int
    kickoff_at: datetime
    home_id: int
    away_id: int
    home_score: int
    away_score: int


def _history_facts(
    target: MatchFixture,
    home_id: int,
    away_id: int,
    games: list[_CompletedGame],
    source: Provenance,
) -> list[SportsFact]:
    """Compute form/rest/H2H only from finalized games in a captured response."""

    games = sorted(
        (g for g in games if g.kickoff_at < target.kickoff_at and str(g.game_id) != target.match_id),
        key=lambda g: g.kickoff_at,
    )
    facts: list[SportsFact] = []
    for team_id, team_name in ((home_id, target.home_team), (away_id, target.away_team)):
        played = [g for g in games if team_id in (g.home_id, g.away_id)]
        if not played:
            continue
        results: list[str] = []
        for game in played:
            ours = game.home_score if game.home_id == team_id else game.away_score
            theirs = game.away_score if game.home_id == team_id else game.home_score
            results.append("W" if ours > theirs else "L" if ours < theirs else "D")
        latest = played[-5:]
        recent = results[-5:]
        facts.append(
            SportsFact(
                FactCategory.FORM,
                team_name,
                f"Last {len(latest)} completed league games before cutoff: " + "-".join(recent),
                source,
            )
        )
        facts.append(
            SportsFact(
                FactCategory.SEASON_HISTORY,
                team_name,
                f"Completed games in this season before cutoff: {results.count('W')} wins, "
                f"{results.count('D')} draws, {results.count('L')} losses",
                source,
            )
        )
        gap_days = (target.kickoff_at - played[-1].kickoff_at).total_seconds() / 86400
        facts.append(
            SportsFact(
                FactCategory.REST_DAYS,
                team_name,
                f"Previous completed league game kicked off {gap_days:.1f} days before this match",
                source,
            )
        )
    meetings = [
        g for g in games if {g.home_id, g.away_id} == {home_id, away_id}
    ]
    if meetings:
        home_wins = sum(
            (g.home_score > g.away_score if g.home_id == home_id else g.away_score > g.home_score)
            for g in meetings
        )
        away_wins = sum(
            (g.away_score > g.home_score if g.home_id == home_id else g.home_score > g.away_score)
            for g in meetings
        )
        facts.append(
            SportsFact(
                FactCategory.HEAD_TO_HEAD,
                f"{target.home_team} vs {target.away_team}",
                f"This season before cutoff: {len(meetings)} completed meetings, "
                f"{target.home_team} {home_wins} wins, {target.away_team} {away_wins} wins, "
                f"{len(meetings) - home_wins - away_wins} draws",
                source,
            )
        )
    return facts


def _source(snapshot: Snapshot) -> Provenance:
    return Provenance(source=snapshot.source_url, fetched_at=snapshot.fetched_at)


def _stale(snapshot: Snapshot, cutoff_at: datetime, clock: Callable[[], datetime]) -> bool:
    reference = min(cutoff_at, _aware_utc(clock(), "clock time"))
    return reference - snapshot.fetched_at > timedelta(hours=24)


class ApiSportsFootballAdapter:
    """API-Football source for EPL or discovered Nigerian NPFL fixtures."""

    sport = "football"

    def __init__(
        self,
        league: Literal["epl", "npfl"],
        snapshot_store: SnapshotStore,
        *,
        include_lineups: bool = False,
        transport: SportsTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 10.0,
    ):
        if league not in ("epl", "npfl"):
            raise ValueError("league must be 'epl' or 'npfl'")
        if not isinstance(include_lineups, bool):
            raise TypeError("include_lineups must be a boolean")
        self.league = league
        self.include_lineups = include_lineups
        self.client = _ApiClient(
            FOOTBALL_BASE,
            snapshot_store,
            ("API_FOOTBALL_KEY", "API_SPORTS_KEY"),
            transport=transport,
            clock=clock,
            timeout_seconds=timeout_seconds,
        )

    def _npfl_id(self, season: int, cutoff_at: datetime) -> int:
        snapshot = self.client.read(
            "leagues", cutoff_at=cutoff_at, country="Nigeria", search="NPFL"
        )
        candidates = []
        for row in _provider_rows(snapshot):
            league = _obj(row.get("league"))
            country = _obj(row.get("country"))
            if country.get("name") != "Nigeria" or "npfl" not in str(league.get("name", "")).lower():
                continue
            seasons = row.get("seasons")
            if not isinstance(seasons, list) or not any(
                str(_obj(item).get("year")) == str(season) for item in seasons
            ):
                continue
            candidates.append(_numeric_id(league.get("id"), "NPFL league"))
        unique = set(candidates)
        if len(unique) != 1:
            raise SportsSourceError("NPFL league discovery did not yield one season-specific ID")
        return unique.pop()

    def load_match(self, match_id: str, *, cutoff_at: datetime) -> MatchData:
        cutoff_at = _aware_utc(cutoff_at, "cutoff_at")
        requested_id = _numeric_id(match_id, "football fixture")
        fixture_snapshot = self.client.read("fixtures", cutoff_at=cutoff_at, id=requested_id)
        rows = _provider_rows(fixture_snapshot)
        if len(rows) != 1:
            raise SportsSourceError("Football fixture ID did not yield exactly one record")
        row = rows[0]
        fixture_data = _obj(row.get("fixture"))
        league_data = _obj(row.get("league"))
        teams = _obj(row.get("teams"))
        home = _obj(teams.get("home"))
        away = _obj(teams.get("away"))
        home_id = _numeric_id(home.get("id"), "home team")
        away_id = _numeric_id(away.get("id"), "away team")
        if _numeric_id(fixture_data.get("id"), "fixture") != requested_id:
            raise SportsSourceError("Football fixture ID does not match requested ID")
        season = _numeric_id(league_data.get("season"), "football season")
        expected_league = 39 if self.league == "epl" else self._npfl_id(season, cutoff_at)
        if _numeric_id(league_data.get("id"), "football league") != expected_league:
            raise SportsSourceError("Football fixture belongs to a different league")
        kickoff_at = _time(fixture_data.get("date"), "football kickoff")
        status = _text(_obj(fixture_data.get("status")).get("short"))
        timestamp = fixture_data.get("timestamp")
        timestamp_ok = isinstance(timestamp, int) and abs(timestamp - kickoff_at.timestamp()) <= 60
        provenance = _source(fixture_snapshot)
        venue = _text(_obj(fixture_data.get("venue")).get("name"))
        fixture = MatchFixture(
            match_id=str(requested_id),
            sport=self.sport,
            competition=_text(league_data.get("name")) or ("Premier League" if self.league == "epl" else "NPFL"),
            season=str(season),
            kickoff_at=kickoff_at,
            home_team=_text(home.get("name")) or "",
            away_team=_text(away.get("name")) or "",
            venue=venue,
            provenance=provenance,
            kickoff_unverified=not timestamp_ok or status is None,
            kickoff_stale=_stale(fixture_snapshot, cutoff_at, self.client.clock)
            or status in {"PST", "CANC", "ABD", "SUSP"},
        )
        facts = [
            SportsFact(FactCategory.TEAM, fixture.home_team, f"Home team; provider ID {home_id}", provenance),
            SportsFact(FactCategory.TEAM, fixture.away_team, f"Away team; provider ID {away_id}", provenance),
        ]
        if venue:
            facts.append(SportsFact(FactCategory.VENUE, venue, "Reported match venue", provenance))
        warnings: list[str] = []
        lineup_rows: list[dict] = []
        lineup_provenance = provenance
        if self.include_lineups:
            try:
                lineup_snapshot = self.client.read(
                    "fixtures/lineups", cutoff_at=cutoff_at, fixture=requested_id
                )
                lineup_rows = _provider_rows(lineup_snapshot)
                lineup_provenance = _source(lineup_snapshot)
            except SnapshotUnavailable:
                warnings.append("No complete lineup source was captured by the cutoff; coaches and starting XI are unknown")
            except PartialSourceData:
                warnings.append("Lineup source was paginated and incomplete; coaches and starting XI are unknown")
            except SportsSourceError:
                warnings.append("Lineup source request failed; coaches and starting XI are unknown")
            if not lineup_rows and not warnings:
                warnings.append("No lineup was available from the provider by the cutoff")
        for lineup in lineup_rows:
            lineup = _obj(lineup)
            team_name = _text(_obj(lineup.get("team")).get("name"))
            if team_name not in (fixture.home_team, fixture.away_team):
                continue
            coach_name = _text(_obj(lineup.get("coach")).get("name"))
            if coach_name:
                facts.append(SportsFact(FactCategory.COACH, team_name, f"Coach: {coach_name}", lineup_provenance))
            starters = [
                _text(_obj(_obj(entry).get("player")).get("name"))
                for entry in lineup.get("startXI", [])
            ] if isinstance(lineup.get("startXI"), list) else []
            starters = [name for name in starters if name]
            if starters:
                facts.append(
                    SportsFact(
                        FactCategory.INJURY_LINEUP,
                        team_name,
                        "Pre-cutoff published starting XI: " + ", ".join(starters[:11]),
                        lineup_provenance,
                    )
                )
        try:
            season_snapshot = self.client.read(
                "fixtures", cutoff_at=cutoff_at, league=expected_league, season=season
            )
        except SnapshotUnavailable:
            season_snapshot = None
            warnings.append("No season-history source was captured by the cutoff; form, rest, and head-to-head are unknown")
        except PartialSourceData:
            season_snapshot = None
            warnings.append("Season-history source was paginated and incomplete; form, rest, and head-to-head are unknown")
        if season_snapshot is not None:
            past_games = []
            for item in _provider_rows(season_snapshot):
                fixture_item = _obj(item.get("fixture"))
                if _text(_obj(fixture_item.get("status")).get("short")) not in FOOTBALL_FINAL_STATUSES:
                    continue
                try:
                    game_id = _numeric_id(fixture_item.get("id"), "historical fixture")
                    game_time = _time(fixture_item.get("date"), "historical football kickoff")
                    item_teams = _obj(item.get("teams"))
                    game_home = _numeric_id(_obj(item_teams.get("home")).get("id"), "historical home team")
                    game_away = _numeric_id(_obj(item_teams.get("away")).get("id"), "historical away team")
                except SportsSourceError:
                    continue
                goals = _obj(item.get("goals"))
                home_score, away_score = _score(goals.get("home")), _score(goals.get("away"))
                if game_time >= cutoff_at or home_score is None or away_score is None:
                    continue
                past_games.append(_CompletedGame(game_id, game_time, game_home, game_away, home_score, away_score))
            facts.extend(_history_facts(fixture, home_id, away_id, past_games, _source(season_snapshot)))
        return MatchData(replace(fixture, data_warnings=tuple(warnings)), tuple(facts))


class ApiSportsNBAAdapter:
    """API-NBA source for the standard NBA league."""

    sport = "basketball"

    def __init__(
        self,
        snapshot_store: SnapshotStore,
        *,
        transport: SportsTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float = 10.0,
    ):
        self.client = _ApiClient(
            NBA_BASE,
            snapshot_store,
            ("API_NBA_KEY", "API_SPORTS_KEY"),
            transport=transport,
            clock=clock,
            timeout_seconds=timeout_seconds,
        )

    def load_match(self, match_id: str, *, cutoff_at: datetime) -> MatchData:
        cutoff_at = _aware_utc(cutoff_at, "cutoff_at")
        requested_id = _numeric_id(match_id, "NBA game")
        game_snapshot = self.client.read("games", cutoff_at=cutoff_at, id=requested_id)
        rows = _provider_rows(game_snapshot)
        if len(rows) != 1:
            raise SportsSourceError("NBA game ID did not yield exactly one record")
        game = rows[0]
        if _numeric_id(game.get("id"), "NBA game") != requested_id:
            raise SportsSourceError("NBA game ID does not match requested ID")
        if game.get("league") != "standard":
            raise SportsSourceError("NBA game is not in the standard league")
        season = _numeric_id(game.get("season"), "NBA season")
        teams = _obj(game.get("teams"))
        home = _obj(teams.get("home"))
        visitors = _obj(teams.get("visitors"))
        home_id = _numeric_id(home.get("id"), "NBA home team")
        away_id = _numeric_id(visitors.get("id"), "NBA visitors team")
        kickoff_at = _time(_obj(game.get("date")).get("start"), "NBA tipoff")
        status = _obj(game.get("status")).get("short")
        venue = _text(_obj(game.get("arena")).get("name"))
        provenance = _source(game_snapshot)
        fixture = MatchFixture(
            match_id=str(requested_id),
            sport=self.sport,
            competition="NBA",
            season=str(season),
            kickoff_at=kickoff_at,
            home_team=_text(home.get("name")) or "",
            away_team=_text(visitors.get("name")) or "",
            venue=venue,
            provenance=provenance,
            kickoff_unverified=status is None,
            kickoff_stale=_stale(game_snapshot, cutoff_at, self.client.clock)
            or status in {4, 5, 6, "4", "5", "6"},
        )
        facts = [
            SportsFact(FactCategory.TEAM, fixture.home_team, f"Home team; provider ID {home_id}", provenance),
            SportsFact(FactCategory.TEAM, fixture.away_team, f"Visitors team; provider ID {away_id}", provenance),
        ]
        if venue:
            facts.append(SportsFact(FactCategory.VENUE, venue, "Reported game arena", provenance))
        warnings: list[str] = []
        try:
            season_snapshot = self.client.read(
                "games", cutoff_at=cutoff_at, league="standard", season=season
            )
        except SnapshotUnavailable:
            season_snapshot = None
            warnings.append("No season-history source was captured by the cutoff; form, rest, and head-to-head are unknown")
        except PartialSourceData:
            season_snapshot = None
            warnings.append("Season-history source was paginated and incomplete; form, rest, and head-to-head are unknown")
        if season_snapshot is not None:
            past_games = []
            for item in _provider_rows(season_snapshot):
                if _obj(item.get("status")).get("short") not in {3, "3"}:
                    continue
                try:
                    game_id = _numeric_id(item.get("id"), "historical NBA game")
                    game_time = _time(_obj(item.get("date")).get("start"), "historical NBA tipoff")
                    item_teams = _obj(item.get("teams"))
                    game_home = _numeric_id(_obj(item_teams.get("home")).get("id"), "historical NBA home")
                    game_away = _numeric_id(_obj(item_teams.get("visitors")).get("id"), "historical NBA visitors")
                except SportsSourceError:
                    continue
                scores = _obj(item.get("scores"))
                home_score = _score(_obj(scores.get("home")).get("points"))
                away_score = _score(_obj(scores.get("visitors")).get("points"))
                if game_time >= cutoff_at or home_score is None or away_score is None:
                    continue
                past_games.append(_CompletedGame(game_id, game_time, game_home, game_away, home_score, away_score))
            facts.extend(_history_facts(fixture, home_id, away_id, past_games, _source(season_snapshot)))
        return MatchData(replace(fixture, data_warnings=tuple(warnings)), tuple(facts))
