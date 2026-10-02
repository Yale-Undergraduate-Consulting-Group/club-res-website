"""Runner prerequisites that workflow syntax checks cannot establish on their own."""
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).parents[1]


def jobs(workflow):
    """Job blocks of a workflow, keyed by their leading "name:" line."""
    section = workflow.read_text().split('\njobs:\n', 1)[1]
    return [job for job in re.split(r'^  (?=[\w-]+:\s*$)', section, flags=re.MULTILINE)
            if re.match(r'[\w-]+:\s*\n', job)]


class WorkflowLoadingTests(unittest.TestCase):
    def test_single_maintainer_rules_require_current_base_without_peer_approval(self):
        for name in ('feature-ruleset.proposed.json', 'main-ruleset.proposed.json'):
            ruleset = json.loads(ROOT.joinpath('github', name).read_text())
            pull_request = next(rule for rule in ruleset['rules']
                                if rule['type'] == 'pull_request')['parameters']
            checks = next(rule for rule in ruleset['rules']
                          if rule['type'] == 'required_status_checks')['parameters']
            self.assertEqual(pull_request['required_approving_review_count'], 0)
            self.assertFalse(pull_request['require_extra_approval_for_unattributed_changes'])
            self.assertTrue(checks['strict_required_status_checks_policy'])

    def test_exactly_three_repository_workflows_back_four_sidebar_entries(self):
        workflows = sorted(path.name for path in (ROOT / '.github/workflows').glob('*.yml'))
        self.assertEqual(workflows, ['beta.yml', 'intake.yml', 'production.yml'])
        self.assertEqual((ROOT / '.github/workflows/intake.yml').read_text().splitlines()[0],
                         'name: Intake')
        self.assertEqual((ROOT / '.github/workflows/beta.yml').read_text().splitlines()[0],
                         'name: Beta · Develop to Feature')
        self.assertEqual((ROOT / '.github/workflows/production.yml').read_text().splitlines()[0],
                         'name: Production · Feature to Main')

    def test_each_stage_emits_a_distinct_aggregate_check(self):
        expected = {
            'intake.yml': 'name: intake-required-checks',
            'beta.yml': 'name: feature-required-checks',
            'production.yml': 'name: production-required-checks',
        }
        for workflow, context in expected.items():
            self.assertIn(context, ROOT.joinpath('.github/workflows', workflow).read_text())
        for ruleset, context in (
            ('feature-ruleset.proposed.json', 'feature-required-checks'),
            ('main-ruleset.proposed.json', 'production-required-checks'),
        ):
            payload = json.loads(ROOT.joinpath('github', ruleset).read_text())
            checks = next(rule for rule in payload['rules']
                          if rule['type'] == 'required_status_checks')['parameters']
            self.assertEqual(checks['required_status_checks'][0]['context'], context)

    def test_every_bound_environment_has_a_reviewed_proposal(self):
        """A workflow job bound to an environment that was never created gets an
        unprotected environment on first use: no reviewers, no branch policy."""
        bound = {'beta': 'feature', 'production': 'main'}
        for target in ('beta', 'production'):
            for operation in ('plan', 'apply'):
                bound[f'infrastructure-{target}-{operation}'] = 'main'
        production = ROOT.joinpath('.github/workflows/production.yml').read_text()
        self.assertIn('environment: infrastructure-${{ inputs.target }}-${{ inputs.operation }}', production)
        self.assertIn('options: [production, beta]', production)
        self.assertIn('environment: beta', ROOT.joinpath('.github/workflows/beta.yml').read_text())
        self.assertIn('environment: production', production)
        for environment, branch in bound.items():
            proposal = json.loads(ROOT.joinpath('github', f'{environment}-environment.proposed.json').read_text())
            self.assertFalse(proposal['can_admins_bypass'], environment)
            self.assertTrue(proposal['deployment_branch_policy']['custom_branch_policies'], environment)
            if not environment.endswith('-plan'):
                self.assertTrue(proposal['reviewers'], environment)
            policy = 'infrastructure' if environment.startswith('infrastructure-') else environment
            self.assertEqual(json.loads(ROOT.joinpath('github', f'{policy}-branch.proposed.json').read_text())['name'], branch)

    def test_local_actions_are_loaded_after_checkout_in_each_job(self):
        for workflow in (ROOT / '.github/workflows').glob('*.yml'):
            checked_out = False
            job = ''
            for line in workflow.read_text().splitlines():
                if re.fullmatch(r'  [\w-]+:', line):
                    job, checked_out = line.strip(), False
                if line.strip().startswith('- uses: actions/checkout@'):
                    checked_out = True
                if line.strip().startswith('- uses: ./.github/actions/'):
                    self.assertTrue(checked_out, f'{workflow.name}/{job} loads an action before checkout')
                    action = ROOT / line.split('uses: ', 1)[1] / 'action.yml'
                    self.assertTrue(action.is_file(), str(action))

    def test_privileged_promotion_uses_existing_trusted_script(self):
        for workflow in (ROOT / '.github/workflows').glob('*.yml'):
            promote = next(job for job in jobs(workflow) if job.startswith('promote:'))
            self.assertIn('ref: ${{ github.event.repository.default_branch }}', promote)
            self.assertIn('persist-credentials: false', promote)
            self.assertIn('run: python3 scripts/promote.py', promote)
            self.assertIn('statuses: write', promote)
            # No dependency on a composite that is not present on main at first rollout.
            self.assertNotIn('uses: ./.github/', promote)

    def test_every_job_has_a_bounded_timeout(self):
        for workflow in (ROOT / '.github/workflows').glob('*.yml'):
            for job in jobs(workflow):
                minutes = re.search(r'^    timeout-minutes: (\d+)$', job, flags=re.MULTILINE)
                self.assertTrue(minutes, f'{workflow.name}/{job.split(":", 1)[0]} has no timeout')
                self.assertTrue(5 <= int(minutes.group(1)) <= 15, f'{workflow.name}/{job.split(":", 1)[0]}')

    def test_no_scheduled_workflows(self):
        for workflow in (ROOT / '.github/workflows').glob('*.yml'):
            self.assertNotIn('schedule:', workflow.read_text(), workflow.name)

    def test_scope_checkout_contains_parent_and_target_history(self):
        for workflow in (ROOT / '.github/workflows').glob('*.yml'):
            scope = workflow.read_text().split('\n  scope:', 1)[1].split('\n  backend:', 1)[0]
            self.assertIn('fetch-depth: 0', scope)


if __name__ == '__main__':
    unittest.main()
