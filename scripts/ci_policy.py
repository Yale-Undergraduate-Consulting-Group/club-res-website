"""Classify changed paths and account for every required check (no cloud access)."""
import argparse
import json
import os
import subprocess


# Lanes that run as their own jobs. Security runs inside the scope job, so the
# scope job's own success accounts for it.
LANES = ('backend', 'frontend', 'infra', 'image')

SENSITIVE_PATCH_PREFIXES = ('.github/', 'backend/', 'scripts/', 'terraform/')
SENSITIVE_PATCH_FILES = {
    '.github/dependabot.yml', 'frontend/package.json', 'frontend/package-lock.json',
}


def is_documentation(path):
    return path.startswith('docs/') or ('/' not in path and path.endswith('.md'))


def delivery_lane(paths):
    """Classify cheap early verification without weakening Production.

    Documentation and frontend presentation/test edits use the quick patch lane.
    Runtime, dependency, workflow and infrastructure changes use the standard lane.
    The lane is explanatory: required checks still come from ``classify`` and
    Production verification expands every executable change to every boundary.
    """
    relevant = [path for path in paths if path]
    if relevant and all(
        (path.startswith('docs/') or path.endswith('.md') or
         (path.startswith('frontend/') and path not in SENSITIVE_PATCH_FILES and
          path.endswith(('.css', '.md', '.test.ts', '.test.tsx', '.spec.ts', '.spec.tsx'))))
        and not path.startswith(SENSITIVE_PATCH_PREFIXES)
        for path in relevant
    ):
        return 'patch'
    return 'standard'


def classify(paths, stage, phase='verify'):
    selected = dict.fromkeys((*LANES, 'security'), False)
    for path in paths:
        # Documentation-only changes select no lane at all.
        if is_documentation(path):
            continue
        selected['security'] = True
        if path.startswith('frontend/'):
            selected['frontend'] = True
        elif path.startswith('backend/'):
            selected.update(backend=True, image=True)
        elif path.startswith(('terraform/', 'tests/', 'github/')):
            # The infra lane also runs the controller tests and checks the
            # proposed GitHub rulesets.
            selected['infra'] = True
        else:
            # Workflows, composites, scripts and unknown files can affect every lane.
            selected.update(backend=True, frontend=True, infra=True)
    selected['frontend_runtime'] = any(p.startswith('frontend/') for p in paths)
    selected['backend_runtime'] = any(p.startswith('backend/') for p in paths)
    selected['runtime'] = selected['frontend_runtime'] or selected['backend_runtime']
    # Intake is the laptop → develop door: lanes only, never an image or ship.
    if stage == 'intake':
        selected['image'] = False
    # Production verification proves every boundary for executable changes.
    # The backend image is still rebuilt only when the backend changed.
    if stage == 'production' and phase == 'verify' and any(selected[k] for k in ('backend', 'frontend', 'infra')):
        selected.update(backend=True, frontend=True, infra=True, security=True)
    # After a green verification, push/dispatch only rebuilds what ship
    # publishes: the static site. The backend is verified, never shipped.
    if phase == 'ship':
        selected.update(dict.fromkeys((*LANES, 'security'), False))
        selected['frontend'] = selected['frontend_runtime'] and stage in {'beta', 'production'}
    return selected


def check_results(scope, results):
    errors = []
    for name in LANES:
        actual = results.get(name, {}).get('result', 'missing')
        if not scope[name] and actual == 'missing':
            continue  # This stage has no job for an unselected lane (Intake never builds an image).
        expected = 'success' if scope[name] else 'skipped'
        if actual != expected:
            errors.append(f'{name}: expected {expected}, received {actual}')
    return errors


def comparison_base(stage, phase, event, head, supplied_base=''):
    if supplied_base and set(supplied_base) != {'0'}:
        return supplied_base
    if event == 'workflow_dispatch':
        # Dispatch has no event.before. Comparing every tracked file would turn
        # a workflow-only merge into an unintended application deployment.
        ref = f'{head}^1' if phase == 'ship' else 'origin/' + {
            'intake': 'develop', 'beta': 'feature', 'production': 'main'}[stage]
        return subprocess.check_output(['git', 'rev-parse', '--verify', ref], text=True).strip()
    return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['intake', 'beta', 'production'])
    args = parser.parse_args()
    head = os.environ['GITHUB_SHA']
    phase = os.environ.get('PHASE', 'verify')
    base = comparison_base(args.stage, phase, os.environ.get('GITHUB_EVENT_NAME', ''),
                           head, os.environ.get('BASE_SHA', ''))
    if base is None:
        paths = subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')
    else:
        paths = subprocess.check_output(['git', 'diff', '--name-only', '-z', base, head]).decode().split('\0')
    paths = [p for p in paths if p]
    scope = classify(paths, args.stage, phase)
    lane = delivery_lane(paths)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for name, enabled in scope.items():
            output.write(f'{name}={str(enabled).lower()}\n')
        output.write('scope=' + json.dumps(scope) + '\n')
        output.write('lane=' + lane + '\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
        summary.write(f'## {args.stage.title()} {phase} · {lane} lane\n\n')
        summary.write('| Check | Decision |\n| --- | --- |\n')
        for name in (*LANES, 'security'):
            summary.write(f'| {name} | {"Required" if scope[name] else "Not affected by this diff"} |\n')
        summary.write(f'\nRuntime changes: frontend {"yes" if scope["frontend_runtime"] else "no"}, '
                      f'backend {"yes" if scope["backend_runtime"] else "no"}.\n')
        summary.write('\nTerraform apply is a separate, approved saved-plan operation.\n')
