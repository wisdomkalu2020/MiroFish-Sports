"""Sports seed material for MiroFish's existing graph workflow.

These records describe source evidence, not agent speech or predictions.  Source
adapters supply the records; :func:`build_match_dossier` enforces the as-of
cutoff before any text is handed to the graph builder.
"""

from .dossier import CutoffViolation, MatchDossier, build_match_dossier
from .api_sports import ApiSportsFootballAdapter, ApiSportsNBAAdapter, SportsSourceError
from .schema import FactCategory, MatchData, MatchFixture, Provenance, SportAdapter, SportsFact
from .snapshots import SnapshotError, SnapshotStore, SnapshotUnavailable

__all__ = [
    "CutoffViolation",
    "ApiSportsFootballAdapter",
    "ApiSportsNBAAdapter",
    "FactCategory",
    "MatchData",
    "MatchDossier",
    "MatchFixture",
    "Provenance",
    "SportAdapter",
    "SnapshotError",
    "SnapshotStore",
    "SnapshotUnavailable",
    "SportsSourceError",
    "SportsFact",
    "build_match_dossier",
]
