import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import subprocess
import tempfile

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('ci_policy', ROOT / 'scripts/ci_policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

CHECKS = ('backend', 'frontend', 'infra', 'security')


class PolicyTests(unittest.TestCase):
    def test_quick_patch_lane_is_narrow_and_never_changes_production_scope(self):
        self.assertEqual(policy.delivery_lane(['docs/guide.md']), 'patch')
        self.assertEqual(policy.delivery_lane(['frontend/src/theme.css']), 'patch')
        for paths in (['backend/app/main.py'], ['frontend/src/App.tsx'],
                      ['frontend/package-lock.json'], ['.github/workflows/intake.yml'],
                      ['terraform/main.tf']):
            self.assertEqual(policy.delivery_lane(paths), 'standard')
        scope = policy.classify(['frontend/src/theme.css'], 'production')
        self.assertTrue(all(scope[k] for k in CHECKS))

    def test_dispatch_uses_target_for_verification_and_first_parent_for_shipping(self):
        for stage, phase, expected in [('intake', 'verify', 'origin/develop'),
                                       ('beta', 'verify', 'origin/feature'),
                                       ('production', 'verify', 'origin/main'),
                                       ('production', 'ship', 'candidate^1')]:
            with self.subTest(stage=stage, phase=phase), patch.object(policy.subprocess, 'check_output', return_value='base\n') as git:
                self.assertEqual(policy.comparison_base(stage, phase, 'workflow_dispatch', 'candidate'), 'base')
                self.assertEqual(git.call_args.args[0][-1], expected)

    def test_native_push_keeps_its_recorded_base(self):
        with patch.object(policy.subprocess, 'check_output') as git:
            self.assertEqual(policy.comparison_base('beta', 'ship', 'push', 'new', 'old'), 'old')
            git.assert_not_called()

    def test_missing_dispatch_history_fails_instead_of_guessing_deploy_scope(self):
        with patch.object(policy.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, 'git')):
            with self.assertRaises(subprocess.CalledProcessError):
                policy.comparison_base('production', 'ship', 'workflow_dispatch', 'candidate')

    def test_workflow_only_dispatch_does_not_ship_existing_runtime_files(self):
        # Exercise actual git history: the application exists, but only YAML changed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args], text=True, stderr=subprocess.DEVNULL).strip()
            git('init', '-q')
            git('config', 'user.name', 'CI fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            # Git may detach a background gc/maintenance after a commit; it
            # would still be writing .git/objects/pack while the temporary
            # directory is removed, failing cleanup with "Directory not empty".
            git('config', 'gc.auto', '0')
            git('config', 'maintenance.auto', 'false')
            (root / 'frontend').mkdir()
            (root / 'frontend/package.json').write_text('{}\n')
            git('add', '.')
            git('commit', '-qm', 'Runtime baseline')
            (root / '.github/workflows').mkdir(parents=True)
            (root / '.github/workflows/production.yml').write_text('name: Production\n')
            git('add', '.')
            git('commit', '-qm', 'Workflow only')
            head = git('rev-parse', 'HEAD')
            with patch.dict(policy.os.environ, {'GIT_DIR': str(root / '.git'), 'GIT_WORK_TREE': directory}):
                base = policy.comparison_base('production', 'ship', 'workflow_dispatch', head)
            scope = policy.classify(git('diff', '--name-only', base, head).splitlines(), 'production', 'ship')
            self.assertFalse(scope['runtime'])
            self.assertFalse(scope['frontend'])

    def test_dependency_audit_cannot_be_failed_by_a_registry_outage(self):
        """npm's advisory endpoints have been intermittently unavailable (quick
        endpoint retired July 2026; bulk returns 503 during registry incidents).
        A raw `npm audit` call turns that into a failed release. The frontend
        action must route through the gate that fails only on real
        high/critical advisories."""
        gate = ROOT / 'scripts/npm_audit_gate.sh'
        self.assertTrue(gate.is_file())
        text = ROOT.joinpath('.github/actions/frontend/action.yml').read_text()
        self.assertIn('npm_audit_gate.sh', text)
        self.assertNotIn('run: npm audit', text)
        body = gate.read_text()
        # A real advisory still fails; an unreachable registry never does.
        self.assertIn('exit 1', body)
        self.assertIn('::warning::', body)

    def test_documentation_only_changes_select_no_lane(self):
        for paths in (['docs/readme.md'], ['README.md', 'docs/ci/flow.md']):
            scope = policy.classify(paths, 'intake')
            self.assertFalse(any(scope[k] for k in (*policy.LANES, 'security', 'runtime')), paths)

    def test_unknown_and_workflow_changes_run_all_checks(self):
        for path in ['new-entrypoint.sh', '.github/workflows/intake.yml', 'scripts/promote.py']:
            scope = policy.classify([path], 'beta')
            self.assertTrue(all(scope[k] for k in CHECKS), path)
            self.assertFalse(scope['image'])
            self.assertFalse(scope['runtime'])

    def test_controller_tests_and_rulesets_select_the_infra_lane(self):
        for path in ['tests/test_promotion.py', 'github/main-ruleset.proposed.json', 'terraform/main.tf']:
            scope = policy.classify([path], 'intake')
            self.assertTrue(scope['infra'] and scope['security'], path)
            self.assertFalse(scope['backend'] or scope['frontend'], path)

    def test_intake_never_builds_image(self):
        scope = policy.classify(['backend/main.py'], 'intake')
        self.assertTrue(scope['backend'] and scope['security'] and scope['runtime'])
        self.assertFalse(scope['image'])

    def test_image_only_for_backend_changes_after_intake(self):
        self.assertTrue(policy.classify(['backend/Dockerfile'], 'beta')['image'])
        self.assertTrue(policy.classify(['backend/main.py'], 'production')['image'])
        for paths in (['frontend/src/App.tsx'], ['.github/workflows/beta.yml'], ['terraform/main.tf']):
            self.assertFalse(policy.classify(paths, 'production')['image'], paths)

    def test_ship_phase_only_rebuilds_the_static_site(self):
        scope = policy.classify(['frontend/src/App.tsx', 'backend/main.py'], 'production', 'ship')
        self.assertTrue(scope['frontend'] and scope['runtime'])
        self.assertTrue(scope['frontend_runtime'] and scope['backend_runtime'])
        self.assertFalse(any(scope[k] for k in ('backend', 'infra', 'image', 'security')))
        backend_only = policy.classify(['backend/main.py'], 'beta', 'ship')
        self.assertFalse(any(backend_only[k] for k in (*policy.LANES, 'security')))
        self.assertTrue(backend_only['backend_runtime'])

    def test_intake_push_does_not_rerun_verify(self):
        text = ROOT.joinpath('.github/workflows/intake.yml').read_text()
        self.assertIn('mode=promote', text)
        self.assertIn("steps.route.outputs.mode == 'tests'", text)
        self.assertIn("needs.required-checks.result == 'success'", text)

    def test_promote_runs_when_an_optional_job_is_skipped(self):
        root = ROOT.joinpath('.github/workflows')
        for name in ('intake.yml', 'beta.yml', 'production.yml'):
            text = root.joinpath(name).read_text()
            self.assertNotIn('if: success()', text, name)

    def test_security_is_skippable_on_ship(self):
        root = ROOT.joinpath('.github/workflows')
        names = sorted(path.name for path in root.glob('*.yml'))
        self.assertEqual(names, ['beta.yml', 'intake.yml', 'production.yml'])
        self.assertIn("steps.classify.outputs.security == 'true'", root.joinpath('beta.yml').read_text())

    def test_stage_runners_increase_in_intensity(self):
        backend = ROOT.joinpath('.github/actions/backend/action.yml').read_text()
        frontend = ROOT.joinpath('.github/actions/frontend/action.yml').read_text()
        self.assertIn("inputs.stage == 'intake'", backend)
        self.assertIn('-m "not slow"', backend)
        self.assertIn("inputs.stage != 'intake'", backend)
        self.assertIn("inputs.stage == 'production' && inputs.phase == 'verify'", frontend)
        for name, stage in [('intake.yml', 'intake'), ('beta.yml', 'beta'),
                            ('production.yml', 'production')]:
            workflow = ROOT.joinpath('.github/workflows', name).read_text()
            self.assertIn(f'stage: {stage}', workflow)

    def test_application_markdown_cannot_skip_build(self):
        for path, part in (('backend/app/prompts/draft.md', 'backend_runtime'), ('frontend/src/help.md', 'frontend_runtime')):
            scope = policy.classify([path], 'beta')
            self.assertTrue(scope['runtime'] and scope[part], path)

    def test_production_patch_has_full_gate(self):
        scope = policy.classify(['frontend/src/App.tsx'], 'production')
        self.assertTrue(all(scope[k] for k in CHECKS))

    def test_skipped_required_job_is_failure(self):
        scope = policy.classify(['backend/app/services/llm.py'], 'beta')
        results = {k: {'result': 'success' if scope[k] else 'skipped'} for k in policy.LANES}
        self.assertEqual(policy.check_results(scope, results), [])
        for result in ('skipped', 'cancelled', 'failure', 'missing'):
            results['backend'] = {'result': result} if result != 'missing' else {}
            self.assertTrue(policy.check_results(scope, results), result)

    def test_unselected_lane_may_be_absent_but_never_run(self):
        scope = policy.classify(['frontend/src/App.tsx'], 'intake')
        results = {'scope': {'result': 'success'}, 'backend': {'result': 'skipped'},
                   'frontend': {'result': 'success'}, 'infra': {'result': 'skipped'}}
        self.assertEqual(policy.check_results(scope, results), [])
        results['image'] = {'result': 'success'}
        self.assertTrue(policy.check_results(scope, results))

    def test_gate_does_not_fail_a_superseded_run(self):
        text = ROOT.joinpath('.github/workflows/beta.yml').read_text()
        self.assertIn('always() && !cancelled()', text)

    def test_deploy_jobs_account_for_skipped_needs_before_environment_approval(self):
        root = ROOT.joinpath('.github/workflows')
        for workflow in ('beta.yml', 'production.yml'):
            text = root.joinpath(workflow).read_text()
            deploy = text.split('\n  deploy:', 1)[1].split('\n  promote:', 1)[0]
            self.assertIn('always() && !cancelled()', deploy)
            self.assertIn("needs.required-checks.result == 'success'", deploy)
            self.assertIn("needs.scope.result == 'success'", deploy)
            self.assertIn("needs.frontend.result == 'success'", deploy)


if __name__ == '__main__':
    unittest.main()
