"""Small, capped coordinator for future historical OASIS agent evaluations.

The expensive operations are callbacks into MiroFish's existing graph build,
simulation, and report APIs. This module never creates a replacement graph or
memory service. It validates *all* inputs before invoking a graph callback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from ..config import Config
from ..models.project import ProjectManager, ProjectStatus
from .agent_layer import SportsAgentError, is_sports_dossier, validate_forecast_context
from .forecast_context import load_forecast_context


class AgentBacktestError(ValueError):
    """The capped agent sample cannot run safely."""


def max_agent_backtest_graphs() -> int:
    raw = os.environ.get("MIROFISH_SPORTS_BACKTEST_MAX_GRAPHS", "3")
    try:
        limit = int(raw)
    except ValueError as error:
        raise AgentBacktestError(
            "MIROFISH_SPORTS_BACKTEST_MAX_GRAPHS must be an integer from 1 to 10"
        ) from error
    if not 1 <= limit <= 10:
        raise AgentBacktestError(
            "MIROFISH_SPORTS_BACKTEST_MAX_GRAPHS must be an integer from 1 to 10"
        )
    return limit


@dataclass(frozen=True)
class AgentBacktestItem:
    project_id: str
    graph_id: str | None
    context: dict[str, Any]


@dataclass(frozen=True)
class AgentBacktestPlan:
    items: tuple[AgentBacktestItem, ...]
    graph_limit: int
    graphs_to_create: int
    leakage_method: str = "anonymized"


def plan_agent_backtest(
    project_ids: Sequence[str],
    *,
    project_loader: Callable[[str], Any] = ProjectManager.get_project,
    context_loader: Callable[[str], dict[str, Any]] = load_forecast_context,
    dossier_loader: Callable[[str], str | None] = ProjectManager.get_extracted_text,
    max_graphs: int | None = None,
) -> AgentBacktestPlan:
    """Preflight a small anonymized sample before any new Zep graph exists."""

    limit = max_agent_backtest_graphs() if max_graphs is None else max_graphs
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise AgentBacktestError("agent backtest graph limit must be 1 to 10")
    ids = tuple(project_ids)
    if not ids or len(ids) > limit or len(set(ids)) != len(ids):
        raise AgentBacktestError(
            f"Agent backtest sample must contain 1 to {limit} distinct projects"
        )
    items: list[AgentBacktestItem] = []
    for project_id in ids:
        if not isinstance(project_id, str) or not project_id.startswith("proj_"):
            raise AgentBacktestError("agent backtest requires existing MiroFish project IDs")
        project = project_loader(project_id)
        dossier = dossier_loader(project_id)
        if project is None or not is_sports_dossier(dossier):
            raise AgentBacktestError(f"{project_id} is not a captured sports project")
        if project.status not in {ProjectStatus.ONTOLOGY_GENERATED, ProjectStatus.GRAPH_COMPLETED}:
            raise AgentBacktestError(f"{project_id} is not ready for a graph or simulation")
        context = context_loader(project_id)
        try:
            validated = validate_forecast_context(
                context, project_id=project_id,
                simulation_id=f"sim_backtest_{project_id}", dossier_text=dossier,
            )
        except SportsAgentError as error:
            raise AgentBacktestError(f"{project_id}: {error}") from error
        if validated.get("agent_backtest_leakage_method") != "anonymized":
            raise AgentBacktestError(
                f"{project_id} must carry an explicit anonymized agent-backtest "
                "dossier marker before historical OASIS discussion"
            )
        graph_id = project.graph_id if project.status == ProjectStatus.GRAPH_COMPLETED else None
        if project.status == ProjectStatus.GRAPH_COMPLETED and not graph_id:
            raise AgentBacktestError(f"{project_id} has no completed graph ID")
        items.append(AgentBacktestItem(project_id, graph_id, validated))
    new_graphs = sum(item.graph_id is None for item in items)
    if new_graphs > limit:
        raise AgentBacktestError(f"Agent backtest would create {new_graphs} graphs, above cap {limit}")
    return AgentBacktestPlan(tuple(items), limit, new_graphs)


def run_agent_backtest_sample(
    project_ids: Sequence[str],
    *,
    create_graph: Callable[[str], str],
    simulate_and_report: Callable[[str, str, dict[str, Any]], dict[str, Any]],
    project_loader: Callable[[str], Any] = ProjectManager.get_project,
    context_loader: Callable[[str], dict[str, Any]] = load_forecast_context,
    dossier_loader: Callable[[str], str | None] = ProjectManager.get_extracted_text,
    max_graphs: int | None = None,
    require_zep: bool = True,
) -> list[dict[str, Any]]:
    """Use existing MiroFish operations only after the complete cap preflight.

    ``create_graph`` should invoke the existing graph build endpoint and wait
    for completion; ``simulate_and_report`` should run the existing persona,
    OASIS, Zep-ingestion, and ReportAgent sequence. On the first failure the
    sample stops without making further graphs.
    """

    plan = plan_agent_backtest(
        project_ids, project_loader=project_loader, context_loader=context_loader,
        dossier_loader=dossier_loader, max_graphs=max_graphs,
    )
    if require_zep and not Config.ZEP_API_KEY:
        raise AgentBacktestError("Zep Cloud API key is required for the existing graph and memory pipeline")
    outputs: list[dict[str, Any]] = []
    created = 0
    for item in plan.items:
        graph_id = item.graph_id
        if graph_id is None:
            if created >= plan.graph_limit:
                raise AgentBacktestError("agent backtest graph cap reached")
            graph_id = create_graph(item.project_id)
            created += 1
        if not isinstance(graph_id, str) or not graph_id:
            raise AgentBacktestError(f"{item.project_id} did not yield a Zep graph ID")
        result = simulate_and_report(item.project_id, graph_id, item.context)
        if not isinstance(result, dict):
            raise AgentBacktestError("agent backtest callback must return a result object")
        outputs.append(result)
    return outputs
