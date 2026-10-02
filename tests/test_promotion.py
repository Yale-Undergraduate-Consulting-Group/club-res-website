"""Promotion must not advance failed, stale, unshipped, or held candidates."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import promote

REPO = 'club/site'
SHA = 'a' * 40


def run(name='Intake', branch='feature/alice', **changes):
    value = {'name': name, 'path': '.github/workflows/' + promote.WORKFLOWS[name][0],
             'head_branch': branch, 'head_sha': SHA, 'head_repository': {'full_name': REPO},
             'conclusion': 'success', 'event': 'push', 'id': 4, 'run_attempt': 1,
             'run_number': 7, 'html_url': 'https://github.com/club/site/actions/runs/4'}
    return dict(value, **changes)


class PromotionTests(unittest.TestCase):
    def test_route_rejects_wrong_identity_ref_and_event(self):
        for change in ({'path': '.github/workflows/other.yml'}, {'event': 'pull_request'},
                       {'head_branch': 'feature/alice/nested'}, {'head_branch': 'integration'},
                       {'head_repository': {'full_name': 'fork/site'}}, {'conclusion': 'failure'}):
            with self.subTest(change=change), patch.object(promote, 'api') as api:
                promote.process(run(**change), REPO)
                api.assert_not_called()

    def test_stale_head_cannot_create_a_pr(self):
        with patch.object(promote, 'api', return_value={'commit': {'sha': 'b' * 40}}), patch.object(promote, 'prepare_pr') as prepare:
            promote.process(run(), REPO)
            prepare.assert_not_called()

    def test_failed_aggregate_cannot_advance(self):
        with patch.object(promote, 'api', return_value={'commit': {'sha': SHA}}), patch.object(promote, 'jobs_for', return_value={'intake-required-checks': 'failure'}):
            with self.assertRaisesRegex(RuntimeError, 'aggregate'):
                promote.process(run(), REPO)

    def test_dev_without_deployment_blocks_production(self):
        with patch.object(promote, 'api', return_value={'commit': {'sha': SHA}}), patch.object(promote, 'jobs_for', return_value={'dev-required-checks': 'success', 'deploy': 'skipped'}), patch.object(promote, 'docs_only', return_value=False), patch.object(promote, 'dispatch') as dispatch:
            with self.assertRaisesRegex(RuntimeError, 'deployment'):
                promote.process(run('Dev', 'dev'), REPO)
            dispatch.assert_not_called()

    def test_deployed_dev_advances_to_production_verification(self):
        with patch.object(promote, 'api', return_value={'commit': {'sha': SHA}}), patch.object(promote, 'jobs_for', return_value={'dev-required-checks': 'success', 'deploy': 'success'}), patch.object(promote, 'dispatch') as dispatch:
            promote.process(run('Dev', 'dev'), REPO)
            dispatch.assert_called_once_with(REPO, 'production.yml', 'dev')

    def test_production_candidate_requires_exact_dev_deployment(self):
        with patch.object(promote, 'api', return_value={'commit': {'sha': SHA}}), patch.object(promote, 'jobs_for', return_value={'production-required-checks': 'success'}), patch.object(promote, 'docs_only', return_value=False), patch.object(promote, 'deployment_proven', return_value=False), patch.object(promote, 'prepare_pr') as prepare:
            with self.assertRaisesRegex(RuntimeError, 'deployment'):
                promote.process(run('Production', 'dev', event='workflow_dispatch'), REPO)
            prepare.assert_not_called()

    def test_documentation_exception_rejects_renamed_source_and_truncation(self):
        for files in ([{'filename': 'docs/x.md', 'previous_filename': 'backend/main.py'}],
                      [{'filename': 'docs/x.md'}] * 300, []):
            with self.subTest(size=len(files)), patch.object(promote, 'api', return_value={'files': files}):
                self.assertFalse(promote.docs_only(REPO, 'prod', SHA))
        with patch.object(promote, 'api', return_value={'files': [{'filename': 'docs/x.md'}]}):
            self.assertTrue(promote.docs_only(REPO, 'prod', SHA))

    def test_held_candidate_does_not_receive_success_status(self):
        calls = []
        def api(path, method='GET', payload=None):
            calls.append((method, path))
            if '/compare/' in path:
                return {'ahead_by': 1, 'behind_by': 0}
            return [{'number': 1, 'head': {'sha': SHA}, 'labels': [{'name': 'hold'}]}]
        with patch.object(promote, 'api', side_effect=api):
            promote.prepare_pr(REPO, run(), 'integration')
        self.assertFalse(any(method != 'GET' for method, _ in calls))

    def test_candidate_behind_target_cannot_receive_green_status(self):
        with patch.object(promote, 'api', return_value={'ahead_by': 2, 'behind_by': 1}) as api:
            promote.prepare_pr(REPO, run(), 'integration')
            self.assertEqual(api.call_count, 1)

    def test_admin_review_brake_uses_latest_substantive_review(self):
        reviews = [{'user': {'login': 'owner'}, 'state': 'CHANGES_REQUESTED'},
                   {'user': {'login': 'owner'}, 'state': 'COMMENTED'}]
        self.assertTrue(promote.latest_reviews_block(reviews))
        reviews.append({'user': {'login': 'owner'}, 'state': 'APPROVED'})
        self.assertFalse(promote.latest_reviews_block(reviews))

    def test_release_targets_deployed_commit_not_controller_commit(self):
        with patch.object(promote, 'api', return_value={'commit': {'sha': SHA}}), patch.object(promote, 'jobs_for', return_value={'production-required-checks': 'success', 'deploy': 'success'}), patch.object(promote.subprocess, 'run') as execute:
            promote.process(run('Production', 'prod'), REPO)
            self.assertEqual(execute.call_args.kwargs['env']['RELEASE_SHA'], SHA)

    def test_infrastructure_completion_never_releases_application(self):
        jobs = {'production-required-checks': 'skipped', 'infrastructure': 'success'}
        with patch.object(promote, 'api', return_value={'commit': {'sha': SHA}}), patch.object(promote, 'jobs_for', return_value=jobs), patch.object(promote.subprocess, 'run') as execute:
            promote.process(run('Production', 'prod', event='workflow_dispatch'), REPO)
            execute.assert_not_called()

    def test_deployment_evidence_rejects_another_commit(self):
        stale = run('Dev', 'dev', head_sha='b' * 40)
        with patch.object(promote, 'api', return_value={'workflow_runs': [stale]}), patch.object(promote, 'jobs_for', return_value={'deploy': 'success'}):
            self.assertFalse(promote.deployment_proven(REPO, SHA))
        with patch.object(promote, 'api', return_value={'workflow_runs': [run('Dev', 'dev')]}), patch.object(promote, 'jobs_for', return_value={'deploy': 'success'}):
            self.assertTrue(promote.deployment_proven(REPO, SHA))

    def test_required_gate_rejects_executable_changes_without_dev_deployment(self):
        with patch.object(promote, 'docs_only', return_value=False), patch.object(promote, 'deployment_proven', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'deployment'):
                promote.require_dev_deployment(REPO, SHA)


if __name__ == '__main__':
    unittest.main()
