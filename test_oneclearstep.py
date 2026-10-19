import copy
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest

import oneclearstep as app


def competing_write(path, task, queue):
    try:
        queue.put(app.update(path, task, 'done', 'Synthetic check', task, 0)['revision'])
    except ValueError as error:
        queue.put(str(error))


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((Path(__file__).parent / 'evidence/synthetic-plan.json').read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'journal.jsonl'
        app.initialize(self.path, self.plan)

    def test_uncertain_precedes_ready_and_blocks_invites(self):
        result = app.next_action(self.plan)
        self.assertEqual(result['next']['id'], 'booking')
        invites = next(t for t in result['tasks'] if t['id'] == 'invites')
        self.assertEqual(invites['state'], 'waiting')
        self.assertEqual(invites['blocked_by'], ['booking'])

    def test_confirmation_unlocks_and_reopening_blocks_descendants(self):
        app.update(self.path, 'booking', 'done', 'Confirmed by user', 'confirm', 0)
        plan, revision, _, _ = app.read(self.path)
        self.assertEqual(revision, 1)
        self.assertEqual(app.next_action(plan)['next']['id'], 'invites')
        app.update(self.path, 'invites', 'done', 'User recorded', 'invite', 1)
        app.update(self.path, 'booking', 'uncertain', 'Receipt missing', 'reopen', 2)
        plan, _, _, _ = app.read(self.path)
        self.assertEqual(next(t for t in app.next_action(plan)['tasks'] if t['id'] == 'invites')['state'], 'waiting')

    def test_idempotent_retry_and_reused_id_rejection(self):
        app.update(self.path, 'booking', 'done', 'Checked', 'one-op', 0)
        before = self.path.read_bytes()
        self.assertTrue(app.update(self.path, 'booking', 'done', 'Checked', 'one-op', 0)['duplicate'])
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'different content'):
            app.update(self.path, 'booking', 'pending', 'Checked', 'one-op', 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_stale_revision_leaves_bytes_unchanged(self):
        app.update(self.path, 'booking', 'done', '', 'first', 0)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Revision conflict'):
            app.update(self.path, 'agenda', 'done', '', 'second', 0)
        self.assertEqual(before, self.path.read_bytes())

    def test_concurrent_writers_one_commit(self):
        context = multiprocessing.get_context('spawn')
        queue = context.Queue()
        workers = [context.Process(target=competing_write, args=(str(self.path), task, queue)) for task in ['booking', 'agenda']]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        answers = [queue.get(timeout=2), queue.get(timeout=2)]
        self.assertEqual(sum(x == 1 for x in answers), 1)
        self.assertEqual(sum(isinstance(x, str) and 'Revision conflict' in x for x in answers), 1)
        self.assertEqual(app.read(self.path)[1], 1)

    def test_tampering_and_partial_write_refuse_mutation(self):
        original = self.path.read_bytes()
        for raw in [original.replace(b'Community workshop', b'Community changed!'), original[:-1], original + b'{']:
            self.path.write_bytes(raw)
            with self.assertRaises(ValueError):
                app.update(self.path, 'booking', 'done', '', 'attempt', 0)
            self.assertEqual(self.path.read_bytes(), raw)

    def test_invalid_dependency_graphs(self):
        for mutate in [lambda p: p['tasks'][0]['depends_on'].append('missing'),
                       lambda p: p['tasks'][0]['depends_on'].append('invites'),
                       lambda p: p['tasks'].append(copy.deepcopy(p['tasks'][0])),
                       lambda p: p['tasks'][0].update(priority=True)]:
            changed = copy.deepcopy(self.plan); mutate(changed)
            with self.assertRaises(ValueError): app.validate(changed)

    def test_symlink_and_existing_output_refused(self):
        link = self.path.parent / 'alias.jsonl'; link.symlink_to(self.path)
        before = self.path.read_bytes()
        with self.assertRaises(OSError): app.update(link, 'booking', 'done', '', 'op', 0)
        with self.assertRaises(FileExistsError): app.initialize(self.path, self.plan)
        self.assertEqual(before, self.path.read_bytes())

    def test_complete_is_recorded_not_external_proof(self):
        for task in self.plan['tasks']: task['status'] = 'done'
        result = app.next_action(self.plan)
        self.assertTrue(result['complete']); self.assertIsNone(result['next'])
        self.assertIn('do not verify external', result['boundary'])

    def test_duplicate_json_keys_refused(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
            app.parse_json('{"title":"a","title":"b"}')


if __name__ == '__main__': unittest.main()
