"""Local dependency-aware resumption planner, Python 3.9+, macOS/Linux.

States are user records, never evidence that an external action succeeded.
Journal writes use flock, revision checks, idempotent operation IDs and fsync.
The hash chain detects accidental edits; it is not a signature or trust proof.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys

SCHEMA = 'oneclearstep/plan-v1'
STATES = {'pending', 'done', 'uncertain'}
ID = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')
MAX_BYTES = 2 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def text(value, label, maximum):
    require(isinstance(value, str) and len(value) <= maximum, label + ' is invalid')
    require(not any(ord(c) < 32 and c not in '\n\t' for c in value), label + ' contains control characters')


def validate(plan):
    require(isinstance(plan, dict) and set(plan) == {'schema', 'title', 'tasks'}, 'Invalid plan fields')
    require(plan['schema'] == SCHEMA, 'Unsupported plan schema')
    text(plan['title'], 'Plan title', 160)
    require(plan['title'].strip(), 'Plan title is required')
    tasks = plan['tasks']
    require(isinstance(tasks, list) and 1 <= len(tasks) <= 100, 'Use 1 to 100 tasks')
    by_id = {}
    for task in tasks:
        require(isinstance(task, dict) and set(task) == {'id', 'title', 'depends_on', 'status', 'context', 'priority'}, 'Invalid task fields')
        require(isinstance(task['id'], str) and ID.fullmatch(task['id']) and task['id'] not in by_id, 'Invalid or duplicate task ID')
        text(task['title'], 'Task title', 240)
        text(task['context'], 'Context', 2000)
        require(task['title'].strip() and isinstance(task['status'], str) and task['status'] in STATES, 'Invalid title or state')
        deps = task['depends_on']
        require(isinstance(deps, list) and len(deps) <= 20 and all(isinstance(x, str) for x in deps) and len(set(deps)) == len(deps), 'Invalid dependencies')
        require(type(task['priority']) is int and 0 <= task['priority'] <= 100, 'Invalid priority')
        by_id[task['id']] = task
    visiting, visited = set(), set()

    def visit(tid):
        require(tid in by_id, 'Dependency does not exist: ' + tid)
        require(tid not in visiting, 'Dependency cycle')
        if tid in visited:
            return
        visiting.add(tid)
        for dependency in by_id[tid]['depends_on']:
            visit(dependency)
        visiting.remove(tid)
        visited.add(tid)
    for tid in by_id:
        visit(tid)
    return plan


def next_action(plan):
    validate(plan)
    tasks = plan['tasks']
    by_id = {t['id']: t for t in tasks}
    # A stale 'done' record does not make incomplete prerequisites disappear.
    effective = {}

    def satisfied(tid):
        if tid not in effective:
            t = by_id[tid]
            effective[tid] = t['status'] == 'done' and all(satisfied(d) for d in t['depends_on'])
        return effective[tid]

    rows = []
    for t in tasks:
        blockers = [d for d in t['depends_on'] if not satisfied(d)]
        state = 'done' if satisfied(t['id']) else ('waiting' if blockers else ('check' if t['status'] in {'done', 'uncertain'} else 'ready'))
        rows.append({'id': t['id'], 'title': t['title'], 'recorded_status': t['status'], 'state': state, 'blocked_by': blockers, 'context': t['context']})
    candidates = [r for r in rows if r['state'] in {'check', 'ready'}]
    # First clarify an actionable uncertain result; priority then stable plan order.
    candidates.sort(key=lambda r: (r['state'] != 'check', -by_id[r['id']]['priority']))
    selected = candidates[0] if candidates else None
    return {'complete': all(r['state'] == 'done' for r in rows), 'next': selected, 'tasks': rows,
            'boundary': 'Recorded task states do not verify external completion.'}


def parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))


def replay(raw):
    require(0 < len(raw) <= MAX_BYTES and raw.endswith(b'\n'), 'Empty, oversized or incomplete journal')
    # JSONL records end at LF, not Unicode separators inside JSON strings.
    lines = raw.decode('utf-8').split('\n')[:-1]
    records = [parse_json(line) for line in lines]
    first = records[0]
    require(isinstance(first, dict) and set(first) == {'kind', 'plan', 'hash'} and first['kind'] == 'plan', 'Invalid journal header')
    require(first['hash'] == digest({'kind': 'plan', 'plan': first['plan']}), 'Header integrity mismatch')
    plan = json.loads(canonical(validate(first['plan'])))
    previous = first['hash']
    operations = {}
    for revision, record in enumerate(records[1:], 1):
        require(isinstance(record, dict) and set(record) == {'kind', 'revision', 'op_id', 'task_id', 'status', 'context', 'prev_hash', 'hash'}, 'Invalid journal event')
        body = {k: v for k, v in record.items() if k != 'hash'}
        require(record['kind'] == 'state' and type(record['revision']) is int and record['revision'] == revision and record['prev_hash'] == previous and record['hash'] == digest(body), 'Journal integrity mismatch')
        require(isinstance(record['op_id'], str) and ID.fullmatch(record['op_id']) and record['op_id'] not in operations, 'Invalid operation ID')
        matches = [t for t in plan['tasks'] if t['id'] == record['task_id']]
        require(len(matches) == 1 and isinstance(record['status'], str) and record['status'] in STATES, 'Invalid task event')
        text(record['context'], 'Context', 2000)
        matches[0].update(status=record['status'], context=record['context'])
        operations[record['op_id']] = record
        previous = record['hash']
    return plan, len(records) - 1, previous, operations


def open_regular(path, flags):
    # Opening a read-only FIFO can block before fstat gets to reject it.
    # Regular files ignore O_NONBLOCK; special files are never read below.
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError('Path must be a regular file')
    return fd


def read_plan(path):
    fd = open_regular(path, os.O_RDONLY)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(0 < len(raw) <= MAX_BYTES, 'Plan is empty or too large')
    return parse_json(raw)


def initialize(path, plan):
    validate(plan)
    body = {'kind': 'plan', 'plan': plan}
    raw = (canonical(dict(body, hash=digest(body))) + '\n').encode('utf-8')
    fd = open_regular(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def read(path):
    fd = open_regular(path, os.O_RDONLY)
    with os.fdopen(fd, 'rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        return replay(stream.read(MAX_BYTES + 1))


def update(path, task_id, status, context, op_id, expected_revision):
    require(isinstance(op_id, str) and ID.fullmatch(op_id), 'Invalid operation ID')
    require(type(expected_revision) is int and expected_revision >= 0, 'Expected revision required')
    require(isinstance(status, str) and status in STATES, 'Invalid status')
    text(context, 'Context', 2000)
    fd = open_regular(path, os.O_RDWR | os.O_APPEND)
    with os.fdopen(fd, 'r+b') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        raw = stream.read(MAX_BYTES + 1)
        plan, revision, previous, operations = replay(raw)
        if op_id in operations:
            prior = operations[op_id]
            require(all(prior[k] == v for k, v in {'task_id': task_id, 'status': status, 'context': context}.items()), 'Operation ID reused with different content')
            return {'revision': revision, 'duplicate': True, 'plan': plan}
        require(revision == expected_revision, 'Revision conflict; reload before changing state')
        require(task_id in {t['id'] for t in plan['tasks']}, 'Unknown task')
        body = {'kind': 'state', 'revision': revision + 1, 'op_id': op_id,
                'task_id': task_id, 'status': status, 'context': context, 'prev_hash': previous}
        event = (canonical(dict(body, hash=digest(body))) + '\n').encode('utf-8')
        require(len(raw) + len(event) <= MAX_BYTES, 'Journal size limit; export and start a new plan')
        stream.write(event)
        stream.flush()
        os.fsync(stream.fileno())
        for task in plan['tasks']:
            if task['id'] == task_id:
                task.update(status=status, context=context)
        return {'revision': revision + 1, 'duplicate': False, 'plan': plan}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init'); init.add_argument('plan'); init.add_argument('journal')
    show = sub.add_parser('next'); show.add_argument('journal'); show.add_argument('--json', action='store_true')
    mark = sub.add_parser('mark'); mark.add_argument('journal'); mark.add_argument('task'); mark.add_argument('status', choices=sorted(STATES)); mark.add_argument('--context', default=''); mark.add_argument('--op-id', required=True); mark.add_argument('--expected-revision', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            initialize(args.journal, read_plan(args.plan))
            print('Created local journal; revision 0. No external action performed.')
        elif args.command == 'mark':
            result = update(args.journal, args.task, args.status, args.context, args.op_id, args.expected_revision)
            print(json.dumps({'revision': result['revision'], 'duplicate': result['duplicate']}, ensure_ascii=False))
        else:
            plan, revision, _, _ = read(args.journal)
            result = dict(next_action(plan), revision=revision)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif result['complete']:
                print('All tasks recorded done. External results remain unverified.')
            else:
                n = result['next']
                print(f"Revision {revision} | {n['state'].upper()}: {n['title']}")
                print(n['context'] or 'No context recorded.')
                print(result['boundary'])
        return 0
    except (ValueError, OSError, UnicodeError, RecursionError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
