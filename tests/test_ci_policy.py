"""Behavioral boundaries between local work and AWS delivery."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from ci_policy import LANES, check_results, classify, comparison_base


class PolicyTests(unittest.TestCase):
    def test_local_terraform_change_does_not_require_cloud_compatibility(self):
        scope = classify(['terraform/main.tf'], 'intake')
        self.assertFalse(any(scope[lane] for lane in LANES))
        self.assertTrue(scope['security'])
        self.assertFalse(scope['deploy'])

    def test_local_backend_needs_tests_not_image_or_cloud(self):
        scope = classify(['backend/main.py'], 'intake')
        self.assertTrue(scope['backend'])
        self.assertFalse(scope['image'])
        self.assertFalse(scope['deploy'])

    def test_downstream_ship_cannot_erase_verification(self):
        for stage in ('dev', 'production'):
            with self.subTest(stage=stage):
                scope = classify(['frontend/app/page.tsx'], stage, 'ship')
                self.assertTrue(all(scope[lane] for lane in ('frontend', 'backend', 'infra', 'security', 'deploy')))
                self.assertFalse(scope['image'])

    def test_backend_runtime_keeps_release_blocking_information(self):
        scope = classify(['backend/main.py'], 'dev', 'ship')
        self.assertTrue(scope['backend_runtime'])
        self.assertTrue(scope['image'])
        self.assertTrue(scope['deploy'])

    def test_documentation_does_not_deploy(self):
        scope = classify(['docs/CI_CD.md', 'README.md'], 'production', 'ship')
        self.assertFalse(any(scope.values()))
        self.assertEqual(check_results(scope, {lane: {'result': 'skipped'} for lane in LANES}), [])

    def test_selected_skip_and_missing_result_fail_gate(self):
        scope = classify(['frontend/app/page.tsx'], 'intake')
        results = {lane: {'result': 'skipped'} for lane in LANES}
        self.assertTrue(check_results(scope, results))
        del results['frontend']
        self.assertTrue(check_results(scope, results))
        results['frontend'] = {'result': 'success'}
        self.assertEqual(check_results(scope, results), [])

    def test_candidate_diff_includes_earlier_pushes(self):
        with tempfile.TemporaryDirectory() as tmp:
            def git(*args):
                return subprocess.check_output(['git', '-C', tmp, *args], text=True).strip()
            git('init', '-q')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'base')
            base = git('rev-parse', 'HEAD')
            git('update-ref', 'refs/remotes/origin/integration', base)
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'first')
            first = git('rev-parse', 'HEAD')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'second')
            original = subprocess.check_output
            def in_repo(args, **kwargs):
                return original(args, cwd=tmp, **kwargs)
            with patch('ci_policy.subprocess.check_output', side_effect=in_repo):
                self.assertEqual(comparison_base('intake', 'verify', 'push', 'HEAD', first), base)
                self.assertEqual(comparison_base('dev', 'ship', 'push', 'HEAD', first), first)

    def test_renaming_runtime_into_docs_still_requires_deployment(self):
        import json
        import os

        with tempfile.TemporaryDirectory() as tmp:
            def git(*args):
                return subprocess.check_output(['git', '-C', tmp, *args], text=True).strip()
            git('init', '-q')
            Path(tmp, 'backend').mkdir()
            Path(tmp, 'backend/main.py').write_text('print("service")\n')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'service')
            git('update-ref', 'refs/remotes/origin/dev', git('rev-parse', 'HEAD'))
            Path(tmp, 'docs').mkdir()
            git('mv', 'backend/main.py', 'docs/main.md')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qam', 'move')
            output = Path(tmp, 'output')
            env = dict(os.environ, GITHUB_SHA=git('rev-parse', 'HEAD'), PHASE='verify',
                       GITHUB_EVENT_NAME='push', GITHUB_OUTPUT=str(output),
                       GITHUB_STEP_SUMMARY=str(Path(tmp, 'summary')))
            subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'scripts/ci_policy.py'), 'dev'],
                           cwd=tmp, env=env, check=True)
            scope = json.loads(next(line[6:] for line in output.read_text().splitlines() if line.startswith('scope=')))
            self.assertTrue(scope['deploy'])
            self.assertTrue(scope['backend_runtime'])


if __name__ == '__main__':
    unittest.main()
