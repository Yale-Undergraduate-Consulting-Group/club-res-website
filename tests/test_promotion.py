import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from copy import deepcopy

spec = importlib.util.spec_from_file_location('promotion', Path(__file__).parents[1] / 'scripts/promote.py')
promotion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(promotion)


class PromotionTests(unittest.TestCase):
    repo = 'club/website'

    def event(self, name='Intake', event='pull_request', branch='topic'):
        return {'workflow_run': {'head_repository': {'full_name': self.repo},
                'conclusion': 'success', 'name': name, 'event': event,
                'head_sha': 'verified', 'head_branch': branch,
                'pull_requests': [{'number': 7}], 'html_url': 'https://github.com/run'}}

    def pr(self, **changes):
        return dict({'number': 7, 'state': 'open', 'draft': False,
                     'labels': [{'name': 'automerge'}],
                     'base': {'ref': 'develop', 'sha': 'tested-base'},
                     'head': {'ref': 'topic', 'sha': 'verified', 'repo': {'full_name': self.repo}},
                     'user': {'login': 'member'}}, **changes)

    def run_pr(self, pr=None, reviews=None, files=None, event=None, refreshed=None):
        pr = self.pr() if pr is None else pr
        reads = 0

        def fake_api(path, method='GET', payload=None):
            nonlocal reads
            self.assertEqual(method, 'GET')
            if path.endswith('/pulls/7'):
                reads += 1
                return deepcopy(refreshed if reads > 1 and refreshed is not None else pr)
            if '/reviews?' in path:
                return reviews or []
            if '/files?' in path:
                return files or []
            if '/compare/' in path:
                return {'behind_by': 0}
            self.fail(f'Unexpected API request: {path}')

        with patch.object(promotion, 'api', side_effect=fake_api), patch.object(promotion.subprocess, 'run') as merge:
            promotion.process(event or self.event(), self.repo)
            return merge.call_args_list

    def test_same_repository_topic_queues_exact_head_without_admin_bypass(self):
        calls = self.run_pr()
        self.assertEqual(len(calls), 1)
        args = calls[0].args[0]
        self.assertIn('--match-head-commit', args)
        self.assertEqual(args[-1], 'verified')
        self.assertIn('--auto', args)
        self.assertNotIn('--admin', args)

    def test_failed_run_and_wrong_workflow_cannot_merge(self):
        event = self.event()
        event['workflow_run']['conclusion'] = 'failure'
        self.assertFalse(self.run_pr(event=event))
        self.assertFalse(self.run_pr(event=self.event(name='Production')))

    def test_stale_head_and_foreign_repository_do_not_merge(self):
        for key, value in [('sha', 'newer-unchecked'), ('repo', {'full_name': 'outsider/website'})]:
            pr = self.pr()
            pr['head'][key] = value
            self.assertFalse(self.run_pr(pr=pr))

    def test_holds_and_rejection_block_automation(self):
        self.assertFalse(self.run_pr(pr=self.pr(draft=True)))
        self.assertFalse(self.run_pr(pr=self.pr(labels=[{'name': 'release:hold'}])))
        self.assertFalse(self.run_pr(reviews=[{'state': 'CHANGES_REQUESTED', 'user': {'login': 'owner'}}]))
        self.assertEqual(len(self.run_pr(pr=self.pr(labels=[]))), 1)

    def test_last_moment_hold_or_new_head_cannot_merge(self):
        self.assertFalse(self.run_pr(refreshed=self.pr(labels=[{'name': 'hold'}])))
        newer = self.pr()
        newer['head']['sha'] = 'newer'
        self.assertFalse(self.run_pr(refreshed=newer))

    def test_target_base_change_cannot_receive_a_successful_merge_check(self):
        changed_base = self.pr(base={'ref': 'develop', 'sha': 'new-untested-base'},
                               merge_commit_sha='untested-merge')
        with patch.dict(promotion.os.environ, {'PROMOTION_PUBLISH_CHECK': '1'}), \
             patch.object(promotion, 'publish_merge_check') as publish:
            self.assertFalse(self.run_pr(refreshed=changed_base))
        publish.assert_not_called()

    def test_source_behind_current_base_cannot_merge(self):
        pr = self.pr()

        def fake_api(path, method='GET', payload=None):
            if path.endswith('/pulls/7'):
                return deepcopy(pr)
            if '/reviews?' in path:
                return []
            if '/compare/' in path:
                return {'behind_by': 1}
            self.fail(f'Unexpected API request: {path}')

        with patch.object(promotion, 'api', side_effect=fake_api), \
             patch.object(promotion.subprocess, 'run') as merge:
            promotion.process(self.event(), self.repo)
            merge.assert_not_called()

    def test_bot_workflow_update_requires_manual_review(self):
        pr = self.pr(user={'login': 'dependabot[bot]'}, labels=[])
        self.assertFalse(self.run_pr(pr=pr, files=[{'filename': '.github/workflows/intake.yml'}]))
        self.assertEqual(len(self.run_pr(pr=pr, files=[{'filename': 'frontend/package-lock.json'}])), 1)

    def test_stale_success_cannot_synchronize_unchecked_main(self):
        with patch.object(promotion, 'api', return_value={'commit': {'sha': 'newer'}}) as api:
            promotion.process(self.event('Production', 'push', 'main'), self.repo)
            self.assertEqual(api.call_count, 1)

    def test_push_opens_promotion_once_and_respects_closed_candidate(self):
        for open_prs, closed_prs, expected in [
            ([], [], True),
            ([{'state': 'open', 'head': {'sha': 'verified'}}], [], False),
            ([], [{'state': 'closed', 'merged_at': None, 'head': {'sha': 'verified'}}], False),
            # A closed candidate for a different revision must not block this one.
            ([], [{'state': 'closed', 'merged_at': None, 'head': {'sha': 'older'}}], True),
            # A merged candidate is history, not a veto.
            ([], [{'state': 'closed', 'merged_at': '2026-01-01', 'head': {'sha': 'verified'}}], True),
        ]:
            with self.subTest(open_prs=open_prs, closed_prs=closed_prs):
                def fake_api(path, method='GET', payload=None):
                    if '/branches/' in path:
                        return {'commit': {'sha': 'verified'}}
                    if '/compare/' in path:
                        return {'behind_by': 0, 'ahead_by': 1, 'files': [{'filename': 'backend/main.py'}]}
                    if method == 'POST':
                        return {'number': 8}
                    if 'state=open' in path:
                        return open_prs
                    if 'state=closed' in path:
                        return closed_prs
                    raise AssertionError(f'unscoped pull request query: {path}')
                with patch.object(promotion, 'api', side_effect=fake_api) as api:
                    promotion.process(self.event('Intake', 'push', 'develop'), self.repo)
                    creates = [call for call in api.call_args_list
                               if len(call.args) > 1 and call.args[1] == 'POST' and call.args[0].endswith('/pulls')]
                    self.assertEqual(bool(creates), expected)
                    if creates:
                        self.assertEqual(creates[0].args[2]['base'], 'feature')

    def test_long_promotion_history_does_not_stop_the_controller(self):
        """A hundred past promotion PRs on this edge used to raise
        'Promotion history needs review' and stop every future promotion.
        Queries are now scoped by state, so history length is irrelevant."""
        def fake_api(path, method='GET', payload=None):
            if '/branches/' in path:
                return {'commit': {'sha': 'verified'}}
            if '/compare/' in path:
                return {'behind_by': 0, 'ahead_by': 1, 'files': [{'filename': 'backend/main.py'}]}
            if method == 'POST':
                return {'number': 9}
            if 'state=open' in path:
                return []
            if 'state=closed' in path:
                # A full page of unrelated merged promotions.
                return [{'state': 'closed', 'merged_at': '2026-01-01', 'head': {'sha': f'old{i}'}}
                        for i in range(100)]
            raise AssertionError(f'unscoped pull request query: {path}')

        with patch.object(promotion, 'api', side_effect=fake_api) as api:
            promotion.process(self.event('Intake', 'push', 'develop'), self.repo)
        creates = [call for call in api.call_args_list
                   if len(call.args) > 1 and call.args[1] == 'POST' and call.args[0].endswith('/pulls')]
        self.assertEqual(len(creates), 1)

    def test_existing_promotion_pr_dispatches_verification_for_updated_source(self):
        previous = [{'state': 'open', 'head': {'sha': 'verified'}}]

        def fake_api(path, method='GET', payload=None):
            if '/branches/' in path:
                return {'commit': {'sha': 'verified'}}
            if '/compare/' in path:
                return {'behind_by': 0, 'ahead_by': 1, 'files': [{'filename': 'backend/main.py'}]}
            if method == 'POST':
                return {}
            return previous

        with patch.dict(promotion.os.environ, {'PROMOTION_DISPATCH': '1'}), \
             patch.object(promotion, 'api', side_effect=fake_api) as api:
            promotion.process(self.event('Intake', 'push', 'develop'), self.repo)
        dispatches = [call for call in api.call_args_list
                      if len(call.args) > 1 and call.args[1] == 'POST']
        self.assertEqual(dispatches[-1].args[0],
                         'repos/club/website/actions/workflows/beta.yml/dispatches')

    def test_equal_history_push_does_not_promote_forever(self):
        with patch.object(promotion, 'api', side_effect=[{'commit': {'sha': 'verified'}},
                                                        {'behind_by': 0, 'ahead_by': 0, 'files': []}]) as api:
            promotion.process(self.event('Intake', 'push', 'develop'), self.repo)
            self.assertEqual(api.call_count, 2)

    def test_history_only_commits_are_promoted_for_strict_ancestry(self):
        posts = []

        def fake_api(path, method='GET', payload=None):
            if '/branches/' in path:
                return {'commit': {'sha': 'verified'}}
            if '/compare/' in path:
                return {'behind_by': 0, 'ahead_by': 2, 'files': []}
            if method == 'POST':
                posts.append((path, payload))
                return {'number': 8}
            if '/pulls?' in path:
                return []
            self.fail(f'Unexpected API request: {path}')

        with patch.object(promotion, 'api', side_effect=fake_api):
            promotion.process(self.event('Intake', 'push', 'develop'), self.repo)
        self.assertEqual(posts[0][0], 'repos/club/website/pulls')
        self.assertEqual(posts[0][1]['base'], 'feature')

    def test_history_only_feature_does_not_create_a_main_merge_loop(self):
        with patch.object(promotion, 'api', side_effect=[
                {'commit': {'sha': 'verified'}},
                {'behind_by': 0, 'ahead_by': 2, 'files': []},
        ]) as api:
            promotion.process(self.event('Beta', 'push', 'feature'), self.repo)
        self.assertEqual(api.call_count, 2)

    def test_human_hold_and_draft_block(self):
        self.assertTrue(promotion.held({'labels': [{'name': 'release:hold'}]}))
        self.assertTrue(promotion.held({'draft': True}))
        self.assertFalse(promotion.held({'labels': []}))

    def test_review_rejection_survives_comments(self):
        reviews = [{'user': {'login': 'owner'}, 'state': 'CHANGES_REQUESTED', 'submitted_at': '1'},
                   {'user': {'login': 'owner'}, 'state': 'COMMENTED', 'submitted_at': '2'}]
        self.assertTrue(promotion.latest_reviews_block(reviews))
        reviews.append({'user': {'login': 'owner'}, 'state': 'APPROVED', 'submitted_at': '3'})
        self.assertFalse(promotion.latest_reviews_block(reviews))

    def test_workflow_updates_are_not_auto_merged_as_dependencies(self):
        self.assertFalse(promotion.dependency_files_allowed([{'filename': '.github/workflows/production.yml'}]))
        self.assertFalse(promotion.dependency_files_allowed([]))
        self.assertTrue(promotion.dependency_files_allowed([{'filename': 'frontend/package-lock.json'}]))

    def test_stage_result_is_bound_to_the_verified_head_status(self):
        pr = self.pr(merge_commit_sha='merge-revision')
        posts = []

        def fake_api(path, method='GET', payload=None):
            posts.append((path, method, payload))
            return {}

        with patch.dict(promotion.os.environ, {'PROMOTION_PUBLISH_CHECK': '1'}), \
             patch.object(promotion, 'api', side_effect=fake_api):
            promotion.publish_merge_check(self.repo, pr, self.event()['workflow_run'])
        path, method, payload = posts[0]
        self.assertEqual((path, method), ('repos/club/website/statuses/verified', 'POST'))
        self.assertEqual(payload['context'], 'intake-required-checks')
        self.assertEqual(payload['state'], 'success')

    def test_actions_token_dispatches_next_stage_after_merge_and_open(self):
        posts = []

        def fake_api(path, method='GET', payload=None):
            if method == 'POST':
                posts.append((path, payload))
                return {'number': 8}
            if path.endswith('/branches/develop'):
                return {'commit': {'sha': 'verified'}}
            if '/compare/' in path:
                return {'behind_by': 0, 'ahead_by': 1, 'files': [{'filename': 'backend/main.py'}]}
            if '/pulls?' in path:
                return []
            self.fail(f'Unexpected API request: {path}')

        with patch.dict(promotion.os.environ, {'PROMOTION_DISPATCH': '1'}), \
             patch.object(promotion, 'api', side_effect=fake_api), \
             patch.object(promotion.subprocess, 'run'):
            promotion.process(self.event('Intake', 'push', 'develop'), self.repo)
        self.assertEqual(posts[-1], ('repos/club/website/actions/workflows/beta.yml/dispatches',
                                     {'ref': 'develop'}))

        posts.clear()
        pr = self.pr()

        reads = 0

        def merge_api(path, method='GET', payload=None):
            nonlocal reads
            if method == 'POST':
                posts.append((path, payload))
                return {}
            if path.endswith('/pulls/7'):
                reads += 1
                current = deepcopy(pr)
                if reads > 2:
                    current.update(state='closed', merged_at='now')
                return current
            if '/reviews?' in path or '/files?' in path:
                return []
            if '/compare/' in path:
                return {'behind_by': 0}
            self.fail(f'Unexpected API request: {path}')

        with patch.dict(promotion.os.environ, {'PROMOTION_DISPATCH': '1'}), \
             patch.object(promotion, 'api', side_effect=merge_api), \
             patch.object(promotion.subprocess, 'run'):
            promotion.process(self.event(), self.repo)
        self.assertEqual(posts, [('repos/club/website/actions/workflows/intake.yml/dispatches',
                                  {'ref': 'develop'})])

    def test_dispatch_verification_merges_promotion_pr_by_head_sha(self):
        pr = self.pr(labels=[], base={'ref': 'feature', 'sha': 'tested-feature'},
                     head={'ref': 'develop', 'sha': 'verified',
                                                              'repo': {'full_name': self.repo}})
        event = self.event('Beta', 'workflow_dispatch', 'develop')
        event['workflow_run']['pull_requests'] = []
        reads = {'pull': 0}

        def fake_api(path, method='GET', payload=None):
            if path.endswith('/pulls?state=open&per_page=100'):
                return [pr]
            if path.endswith('/pulls/7'):
                reads['pull'] += 1
                return deepcopy(pr)
            if '/reviews?' in path:
                return []
            if path.endswith('/branches/develop'):
                return {'commit': {'sha': 'verified'}}
            if '/compare/' in path:
                return {'behind_by': 0, 'files': []}
            self.fail(f'Unexpected API request: {path}')

        with patch.object(promotion, 'api', side_effect=fake_api), patch.object(promotion.subprocess, 'run') as merge:
            promotion.process(event, self.repo)
            self.assertEqual(len(merge.call_args_list), 1)

    def test_stage_run_reads_native_pull_request_event(self):
        event = {'pull_request': {'number': 7, 'head': {'sha': 'verified', 'ref': 'topic'}}}
        with patch.dict(promotion.os.environ, {'PROMOTION_STAGE': 'Intake', 'GITHUB_EVENT_NAME': 'pull_request',
                                              'GITHUB_SHA': 'ignored', 'GITHUB_REF_NAME': 'develop',
                                              'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_RUN_ID': '9'}):
            run = promotion.stage_run(event, self.repo)
        self.assertEqual(run['name'], 'Intake')
        self.assertEqual(run['head_sha'], 'verified')
        self.assertEqual(run['pull_requests'], [{'number': 7}])

    def test_intake_advances_branches_without_a_fourth_workflow(self):
        text = Path(__file__).parents[1].joinpath('.github/workflows/intake.yml').read_text()
        self.assertIn('PROMOTION_STAGE: Intake', text)
        self.assertIn("needs.required-checks.result == 'success'", text)
        self.assertIn('run: python3 scripts/promote.py', text)
        self.assertIn('statuses: write', text)
        self.assertIn("PROMOTION_PUBLISH_CHECK: '1'", text)
        self.assertFalse(Path(__file__).parents[1].joinpath('.github/workflows/promote.yml').exists())

    def test_bot_promotion_does_not_use_pull_request_triggers(self):
        root = Path(__file__).parents[1].joinpath('.github/workflows')
        self.assertNotIn('pull_request:', root.joinpath('beta.yml').read_text())
        self.assertNotIn('pull_request:', root.joinpath('production.yml').read_text())
        self.assertIn('pull_request:', root.joinpath('intake.yml').read_text())

    def test_synchronize_merges_into_develop_without_opening_a_pr(self):
        posts = []

        def fake_api(path, method='GET', payload=None):
            if method == 'POST':
                posts.append((path, payload))
                return {'sha': 'merged'}
            if path.endswith('/branches/main'):
                return {'commit': {'sha': 'verified'}}
            if path.endswith('/branches/develop'):
                return {'commit': {'sha': 'develop-base'}}
            if path.endswith('/compare/develop-base...verified'):
                return {'ahead_by': 1}
            self.fail(f'Unexpected API request: {path}')

        with patch.dict(promotion.os.environ, {'PROMOTION_DISPATCH': '1'}), \
             patch.object(promotion, 'api', side_effect=fake_api):
            promotion.process(self.event('Production', 'push', 'main'), self.repo)
        self.assertEqual(posts[0], ('repos/club/website/merges', {
            'base': 'develop', 'head': 'verified',
            'commit_message': 'Synchronize main (verified) into develop',
        }))
        self.assertEqual(posts[1], ('repos/club/website/actions/workflows/intake.yml/dispatches',
                                    {'ref': 'develop'}))
        self.assertFalse(any('/pulls' in path for path, _ in posts))


if __name__ == '__main__':
    unittest.main()
