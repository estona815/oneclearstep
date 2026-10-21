"""Compare both published engines to a transitive-closure reference.

This exhausts forward-edge graphs on four ordered tasks, not all possible plans.
It imports no production implementation into the reference calculation.
"""
from pathlib import Path
import importlib.util
import itertools
import json
import subprocess
import hashlib
import datetime
import argparse
import shutil
import os

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('published_engine', ROOT / 'oneclearstep.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
NODE = os.environ.get('ONECLEARSTEP_NODE') or shutil.which('node')
if not NODE:
    raise SystemExit('Node.js is required; add node to PATH or set ONECLEARSTEP_NODE.')
edges = list(itertools.combinations(range(4), 2))
states = ['pending', 'done', 'uncertain']
profiles = [[0, 0, 0, 0], [1, 2, 3, 4], [100, 0, 100, 0]]
plans, expected = [], []

def compact(result):
    return {'complete': result['complete'], 'next': result['next']['id'] if result['next'] else None,
            'rows': [[t['id'], t['state'], t['blocked_by']] for t in result['tasks']]}

def reference(mask, statuses, priority):
    reach = [[False] * 4 for _ in range(4)]
    direct = [[] for _ in range(4)]
    for bit, (prerequisite, dependent) in enumerate(edges):
        if mask & (1 << bit):
            reach[dependent][prerequisite] = True
            direct[dependent].append(prerequisite)
    for via in range(4):
        for row in range(4):
            for col in range(4):
                reach[row][col] |= reach[row][via] and reach[via][col]
    effective = [statuses[i] == 'done' and all(statuses[j] == 'done' for j in range(4) if reach[i][j]) for i in range(4)]
    rows = []
    for i in range(4):
        blocked = [f't{j}' for j in direct[i] if not effective[j]]
        state = 'done' if effective[i] else 'waiting' if blocked else 'check' if statuses[i] == 'uncertain' else 'ready'
        rows.append([f't{i}', state, blocked])
    selectable = [i for i in range(4) if rows[i][1] in ['check', 'ready']]
    chosen = min(selectable, key=lambda i: (rows[i][1] != 'check', -priority[i], i)) if selectable else None
    return {'complete': all(effective), 'next': f't{chosen}' if chosen is not None else None, 'rows': rows}, direct

for mask in range(64):
    for statuses in itertools.product(states, repeat=4):
        for priority in profiles:
            answer, direct = reference(mask, statuses, priority)
            plan = {'schema': 'oneclearstep/plan-v1', 'title': 'Synthetic dependency audit', 'tasks': [
                {'id': f't{i}', 'title': f'Task {i}', 'depends_on': [f't{j}' for j in direct[i]],
                 'status': statuses[i], 'context': '', 'priority': priority[i]} for i in range(4)]}
            assert compact(engine.next_action(plan)) == answer, ('Python', mask, statuses, priority)
            plans.append(plan)
            expected.append(answer)

js = """
import fs from 'node:fs';
import {nextAction} from './web/engine.mjs';
const plans=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify(plans.map(p=>{const r=nextAction(p);return {complete:r.complete,next:r.next?.id??null,rows:r.tasks.map(t=>[t.id,t.state,t.blocked_by])}})));
"""
proc = subprocess.run([NODE, '--input-type=module', '-e', js], input=json.dumps(plans), cwd=ROOT,
                      text=True, capture_output=True, check=True)
observed = json.loads(proc.stdout)
assert len(observed) == len(expected)
for index, (actual, want) in enumerate(zip(observed, expected)):
    assert actual == want, ('JavaScript', index, actual, want)
report = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'source_files_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                  for name in ['oneclearstep.py', 'web/engine.mjs']},
          'graphs': 64, 'status_assignments_per_graph': 81, 'priority_profiles': profiles,
          'plans_per_engine': len(plans), 'engines': ['Python', 'JavaScript'],
          'comparisons_passed': 2 * len(plans),
          'properties': ['transitive prerequisite satisfaction', 'reopened prerequisites block stale done records',
                         'direct blocker lists', 'uncertain before ready', 'descending priority',
                         'stable task-order ties', 'completion iff all tasks effectively done'],
          'oracle': 'Independent transitive-closure matrix; production engines recurse over prerequisite IDs',
          'result_sha256': hashlib.sha256(proc.stdout.encode()).hexdigest(),
          'limitations': ['Four ordered tasks and forward-edge DAGs only; not all labeled graphs or task counts.',
                         'No browser UI or concurrent-storage behavior covered by this audit.',
                         'No claim that recorded task states verify external completion.']}
parser = argparse.ArgumentParser()
parser.add_argument('--report', type=Path, help='Optional new report path; existing files are refused')
args = parser.parse_args()
if args.report:
    with args.report.open('x') as stream:
        json.dump(report, stream, indent=2)
print(json.dumps(report, indent=2))
