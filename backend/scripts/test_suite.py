"""Run isolated regression scripts and combine branch coverage. No external services."""
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[1]

def execute(*args):
    try:
        return subprocess.run([sys.executable, '-m', 'coverage', *args], cwd=root, timeout=180).returncode
    except subprocess.TimeoutExpired:
        print('Timed out:', ' '.join(args), flush=True)
        return 1

if execute('erase'):
    raise SystemExit('Install requirements-dev.txt before running coverage')
failed = []
for test in sorted((root / 'tests').glob('test_*.py')):
    print(f'Running {test.name}', flush=True)
    if execute('run', '--branch', '--parallel-mode', '--source=app', str(test)):
        failed.append(test.name)
if execute('combine') or execute('report') or execute('xml', '-o', 'coverage.xml'):
    raise SystemExit(1)
if failed:
    raise SystemExit('Failed: ' + ', '.join(failed))
# Keep new authorization and delivery boundaries covered without hiding legacy debt.
critical_modules = [
    'app/routers/invitations.py', 'app/routers/workspace.py',
    'app/services/dispatch_service.py', 'app/services/dispatch_recovery.py',
    'app/services/mail_address.py',
    'app/services/delivery_policy.py', 'app/services/generation_policy.py',
    'app/services/llm.py', 'app/services/email_verifier.py',
    'app/routers/telemetry.py',
]
for module in critical_modules:
    if execute('report', '--include=' + module, '--fail-under=80'):
        raise SystemExit('Critical-module coverage below 80%: ' + module)
