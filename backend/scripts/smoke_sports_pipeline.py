"""Manual live integration check using existing MiroFish APIs.

Requires a running local model and Zep Cloud key. Creates one retained synthetic
project/graph for inspection. No real fixture, odds, or betting operation occurs.
Run from backend: python -m scripts.smoke_sports_pipeline --execute --record PATH
Reuse the same record to resume completed stages without creating another graph.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--record', required=True, type=Path)
    parser.add_argument('--stage-timeout', type=int, default=7200)
    parser.add_argument('--retry-startup-failure', action='store_true',
                        help='Retry an inspected failure only if no round or action ran')
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute is required: this test writes one Zep graph')
    if args.stage_timeout <= 0:
        parser.error('--stage-timeout must be positive')
    os.environ['LLM_PROVIDER'] = 'local'
    os.environ['MIROFISH_SPORTS_AGENT_COUNT'] = '10'
    os.environ['MIROFISH_SPORTS_MAX_ROUNDS'] = '2'

    from app import create_app
    from app.config import Config
    from app.models.project import ProjectManager, ProjectStatus
    from app.models.task import TaskManager
    from app.services.simulation_manager import SimulationManager
    from app.sports.forecast_context import load_forecast_context
    from app.sports.snapshots import SnapshotStore
    from app.utils.local_llm_runtime import local_run_budget_snapshot

    if not Config.ZEP_API_KEY:
        raise RuntimeError('ZEP_API_KEY is required')
    args.record = args.record.resolve()
    args.record.parent.mkdir(parents=True, exist_ok=True)
    record = json.loads(args.record.read_text()) if args.record.exists() else {
        'synthetic': True, 'purpose': 'integration only; no accuracy measurement',
        'started_at': datetime.now(timezone.utc).isoformat(),
    }
    if record.get('synthetic') is not True:
        raise RuntimeError('Refusing to resume a non-synthetic record')

    def save(stage: str) -> None:
        record['stage'] = stage
        record['updated_at'] = datetime.now(timezone.utc).isoformat()
        args.record.write_text(json.dumps(record, indent=2), encoding='utf-8')
        print(f'[sports-e2e] {stage}', flush=True)

    client = create_app().test_client()

    def call(path: str, payload: dict | None = None) -> dict:
        response = (client.get(path, headers={'Accept-Language': 'en'}) if payload is None
                    else client.post(path, json=payload, headers={'Accept-Language': 'en'}))
        body = response.get_json() or {}
        if response.status_code >= 400 or not body.get('success'):
            raise RuntimeError(f'{path}: HTTP {response.status_code}: {body.get("error", "unknown error")}')
        return body.get('data', {})

    def wait_task(task_id: str, stage: str) -> None:
        deadline = time.monotonic() + args.stage_timeout
        previous = None
        while time.monotonic() < deadline:
            task = TaskManager().get_task(task_id)
            if task is None:
                raise RuntimeError(f'{stage}: task disappeared')
            state = task.to_dict()
            progress = (state['status'], state['progress'], state['message'])
            if progress != previous:
                print(f'[sports-e2e] {stage}: {progress}', flush=True)
                previous = progress
            if state['status'] == 'completed':
                return
            if state['status'] == 'failed':
                raise RuntimeError(f'{stage}: {state["error"]}')
            time.sleep(5)
        raise TimeoutError(f'{stage} exceeded stage timeout; inspect retained project before resuming')

    try:
        if not record.get('project_id'):
            save('creating synthetic dossier and ontology')
            now = datetime.now(timezone.utc)
            kickoff = now + timedelta(days=7)
            fixture_id = 9999998
            snapshot_root = args.record.parent / 'synthetic-football-snapshots'
            os.environ['MIROFISH_SPORTS_SNAPSHOT_DIR'] = str(snapshot_root)
            store = SnapshotStore(snapshot_root)
            teams = [{'id': 999101, 'name': 'Synthetic North FC'},
                     {'id': 999102, 'name': 'Synthetic South FC'}]
            fixture = {
                'fixture': {'id': fixture_id, 'date': kickoff.isoformat(),
                            'timestamp': int(kickoff.timestamp()), 'status': {'short': 'NS'},
                            'venue': {'name': 'Synthetic Test Ground'}},
                'league': {'id': 39, 'season': kickoff.year, 'name': 'Premier League'},
                'teams': {'home': teams[0], 'away': teams[1]},
            }
            lineups = [{'team': team, 'coach': {'name': f'Synthetic Coach {i}'},
                       'startXI': [{'player': {'name': f'Synthetic Player {i}-{j}',
                                               'id': 999200 + 10*i + j, 'pos': 'M'}}
                                   for j in range(1, 4)]}
                      for i, team in enumerate(teams, 1)]
            base = 'https://v3.football.api-sports.io'
            for url, rows in {
                f'{base}/fixtures?id={fixture_id}': [fixture],
                f'{base}/fixtures/lineups?fixture={fixture_id}': lineups,
                f'{base}/fixtures?league=39&season={kickoff.year}': [],
            }.items():
                store.save(url, json.dumps({'response': rows, 'paging': {'total': 1}}).encode(), now)
            data = call('/api/sports/dossiers', {
                'sport': 'football', 'league': 'epl', 'match_id': fixture_id,
                'kickoff_at': kickoff.isoformat(), 'cutoff_hours': 24,
            })
            record['project_id'] = data['project_id']
            save('dossier ready')
        project_id = record['project_id']
        project = ProjectManager.get_project(project_id)
        if project is None:
            raise RuntimeError('Recorded project is missing; do not silently recreate it')
        if project.status == ProjectStatus.CREATED and not project.ontology:
            # Resume a request interrupted after its dossier was saved.
            from app.api.sports import _SIMULATION_REQUIREMENT
            from app.services.ontology_generator import OntologyGenerator
            from app.utils.local_llm_runtime import local_model_run
            context = load_forecast_context(project_id)
            markdown = ProjectManager.get_extracted_text(project_id)
            if not markdown or 'Synthetic North FC' not in markdown:
                raise RuntimeError('Interrupted project is not the expected synthetic dossier')
            save('resuming ontology for saved dossier')
            with local_model_run(project_id):
                ontology = OntologyGenerator().generate(
                    document_texts=[markdown],
                    simulation_requirement=_SIMULATION_REQUIREMENT,
                    domain='sports',
                )
            project.ontology = {key: ontology.get(key, [])
                                for key in ('entity_types', 'edge_types')}
            project.analysis_summary = ontology.get('analysis_summary', '')
            project.simulation_requirement = _SIMULATION_REQUIREMENT
            project.total_text_length = len(markdown)
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            ProjectManager.save_project(project)
            save('dossier ready')
        if project.status != ProjectStatus.GRAPH_COMPLETED:
            save('building graph')
            data = call('/api/graph/build', {'project_id': project_id,
                                           'graph_name': 'SYNTHETIC football integration test'})
            wait_task(data['task_id'], 'graph')
        record['graph_id'] = ProjectManager.get_project(project_id).graph_id
        save('graph ready')
        if not record.get('simulation_id'):
            data = call('/api/simulation/create', {'project_id': project_id,
                        'enable_twitter': False, 'enable_reddit': True})
            record['simulation_id'] = data['simulation_id']
            save('simulation created')
        simulation_id = record['simulation_id']
        data = call('/api/simulation/prepare', {'simulation_id': simulation_id,
                    'use_llm_for_profiles': True, 'parallel_profile_count': 1})
        if data.get('task_id'):
            wait_task(data['task_id'], 'personas and environment')
        record['profile_count'] = len(SimulationManager().get_profiles(simulation_id))
        save('personas and environment ready')
        run = call(f'/api/simulation/{simulation_id}/run-status')
        if run['runner_status'] not in ('completed', 'stopped'):
            startup_retry = (args.retry_startup_failure and run['runner_status'] == 'failed'
                             and run.get('current_round') == 0
                             and run.get('total_actions_count') == 0)
            if run['runner_status'] == 'stopping' or (run['runner_status'] == 'failed' and not startup_retry):
                raise RuntimeError('Recorded simulation failed/stopping; inspect before retrying')
            if startup_retry:
                record.setdefault('startup_failures', []).append(run)
                save('retrying inspected startup failure')
            if run['runner_status'] not in ('running', 'starting', 'paused'):
                call('/api/simulation/start', {'simulation_id': simulation_id, 'platform': 'reddit',
                     'max_rounds': 2, 'enable_graph_memory_update': True})
            save('OASIS discussion running')
            deadline = time.monotonic() + args.stage_timeout
            previous = None
            while time.monotonic() < deadline:
                run = call(f'/api/simulation/{simulation_id}/run-status')
                progress = (run['runner_status'], run.get('current_round'), run.get('total_actions_count'))
                if progress != previous:
                    print(f'[sports-e2e] OASIS: {progress}', flush=True)
                    previous = progress
                if run['runner_status'] in ('completed', 'stopped'):
                    break
                if run['runner_status'] == 'failed':
                    raise RuntimeError('OASIS failed; inspect simulation.log and run_state.json')
                time.sleep(5)
            else:
                call('/api/simulation/stop', {'simulation_id': simulation_id})
                raise TimeoutError('OASIS exceeded stage timeout; stop requested')
        record['run_state'] = run
        save('discussion complete; collecting votes and generating report')
        if not record.get('report_id'):
            data = call('/api/report/generate', {'simulation_id': simulation_id})
            if data.get('task_id'):
                wait_task(data['task_id'], 'votes and ReportAgent')
            report = call(f'/api/report/by-simulation/{simulation_id}')
            record['report_id'] = report['report_id']
        context = load_forecast_context(project_id)
        record['baseline_probabilities'] = context['baseline_probabilities']
        record['project_budget'] = local_run_budget_snapshot(project_id)
        record['simulation_budget'] = local_run_budget_snapshot(simulation_id)
        record.pop('error', None)
        save('completed')
    except Exception as exc:
        record['error'] = f'{type(exc).__name__}: {exc}'
        save('failed')
        raise


if __name__ == '__main__':
    main()
