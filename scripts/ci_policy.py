"""Select local or AWS-ready checks and reject missing selected results."""
import argparse
import json
import os
import subprocess

LANES = ('backend', 'frontend', 'infra', 'image')
# Everything that ends up in, or runs, the application image on the box.
RUNTIME_PREFIXES = ('frontend/', 'backend/', 'docker/', 'ops/')


def is_documentation(path):
    return path.startswith('docs/') or ('/' not in path and path.endswith('.md'))


def is_runtime(path):
    return path.startswith(RUNTIME_PREFIXES) or path == '.dockerignore'


def classify(paths, stage, phase='verify'):
    if stage not in {'intake', 'dev', 'production'} or phase not in {'verify', 'ship'}:
        raise ValueError('Unknown stage or phase')
    selected = dict.fromkeys((*LANES, 'security'), False)
    changed = [path for path in paths if path and not is_documentation(path)]
    selected['deploy'] = bool(changed) and stage != 'intake'
    if not changed:
        return selected
    selected['security'] = True
    if stage == 'intake':
        for path in changed:
            if path.startswith('frontend/'):
                selected['frontend'] = True
            elif path.startswith('backend/'):
                selected['backend'] = True
            elif path.startswith('ops/'):
                selected['infra'] = True
            elif not path.startswith('terraform/'):
                # Workflows, actions, scripts, tests and image files can weaken
                # or break any lane, so they rerun every unit-test lane.
                selected.update(backend=True, frontend=True, infra=True)
        # infra runs only local controller tests in Intake, never Terraform.
    else:
        # Recheck compatibility at both AWS boundaries, including ship runs.
        selected.update(backend=True, frontend=True, infra=True)
        # Every deployment ships the image, so a successful deploy proves the box
        # runs this revision; verification builds it only for runtime changes.
        selected['image'] = phase == 'ship' or any(is_runtime(path) for path in changed)
    return selected


def check_results(scope, results):
    errors = []
    for name in LANES:
        actual = results.get(name, {}).get('result', 'missing')
        if not scope[name] and actual == 'missing':
            continue
        expected = 'success' if scope[name] else 'skipped'
        if actual != expected:
            errors.append(f'{name}: expected {expected}, received {actual}')
    return errors


def comparison_base(stage, phase, event, head, supplied_base=''):
    if phase == 'verify':
        # Compare the complete candidate with its destination, not just its last push.
        ref = 'origin/' + {'intake': 'integration', 'dev': 'dev', 'production': 'prod'}[stage]
    elif supplied_base and set(supplied_base) != {'0'}:
        return supplied_base
    else:
        ref = f'{head}^1'
    return subprocess.check_output(['git', 'rev-parse', '--verify', ref], text=True).strip()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['intake', 'dev', 'production'])
    args = parser.parse_args()
    head = os.environ['GITHUB_SHA']
    phase = os.environ.get('PHASE', 'verify')
    base = comparison_base(args.stage, phase, os.environ.get('GITHUB_EVENT_NAME', ''),
                           head, os.environ.get('BASE_SHA', ''))
    paths = subprocess.check_output(['git', 'diff', '--no-renames', '--name-only', '-z', base, head]).decode().split('\0')
    scope = classify(paths, args.stage, phase)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for name, enabled in scope.items():
            output.write(f'{name}={str(enabled).lower()}\n')
        output.write('scope=' + json.dumps(scope) + '\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
        summary.write(f'## {args.stage.title()} {phase}\n\n')
        summary.write('| Check | Selected |\n| --- | --- |\n')
        for name in (*LANES, 'security', 'deploy'):
            summary.write(f'| {name} | {scope[name]} |\n')
        summary.write('\nIntake has no Terraform or AWS gate. Infrastructure apply remains a separate reviewed operation.\n')
