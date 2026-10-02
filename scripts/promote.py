"""Prepare reviewed stage PRs from trusted workflow completion events; never merge."""
import json
import os
import re
import subprocess
from urllib.parse import quote

from ci_policy import is_documentation

WORKFLOWS = {
    'Intake': ('intake.yml', 'intake-required-checks'),
    'Dev': ('dev.yml', 'dev-required-checks'),
    'Production': ('production.yml', 'production-required-checks'),
}


def api(path, method='GET', payload=None):
    args = ['gh', 'api', path, '--method', method]
    if payload is not None:
        args += ['--input', '-']
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout else None


def held(pr):
    return pr.get('draft', False) or bool({'hold', 'do-not-merge', 'release:hold'} &
                                         {label['name'] for label in pr.get('labels', [])})


def latest_reviews_block(reviews):
    latest = {}
    for review in reviews:
        if review['state'] != 'COMMENTED':
            latest[review['user']['login']] = review['state']
    return len(reviews) >= 100 or 'CHANGES_REQUESTED' in latest.values()


def route(run, repo):
    if run.get('conclusion') != 'success' or run.get('head_repository', {}).get('full_name') != repo:
        return None
    if run.get('event') not in {'push', 'workflow_dispatch'}:
        return None
    name, branch = run.get('name'), run.get('head_branch', '')
    if name not in WORKFLOWS or run.get('path') != '.github/workflows/' + WORKFLOWS[name][0]:
        return None
    if name == 'Intake' and re.fullmatch(r'feature/[^/]+', branch):
        return ('pr', 'integration')
    return {('Dev', 'integration'): ('pr', 'dev'),
            ('Dev', 'dev'): ('verify', 'prod'),
            ('Production', 'dev'): ('pr', 'prod'),
            ('Production', 'prod'): ('release', 'prod')}.get((name, branch))


def jobs_for(repo, run):
    data = api(f'repos/{repo}/actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs?per_page=100')
    if data['total_count'] > 100:
        raise RuntimeError('Refusing incomplete job evidence')
    return {job['name']: job['conclusion'] for job in data['jobs']}


def docs_only(repo, base, head):
    result = api(f'repos/{repo}/compare/{quote(base, safe="")}...{head}')
    files = result.get('files', [])
    # GitHub caps compare file lists at 300. Never treat truncation as proof.
    return bool(files) and len(files) < 300 and all(
        is_documentation(f['filename']) and
        ('previous_filename' not in f or is_documentation(f['previous_filename'])) for f in files)


def deployment_proven(repo, sha):
    data = api(f'repos/{repo}/actions/workflows/dev.yml/runs?head_sha={sha}&branch=dev&status=success&per_page=100')
    for run in data['workflow_runs']:
        if (run['head_sha'] == sha and route(run, repo) == ('verify', 'prod')
                and jobs_for(repo, run).get('deploy') == 'success'):
            return True
    return False


def require_dev_deployment(repo, sha):
    if not re.fullmatch(r'[a-f0-9]{40}', sha):
        raise ValueError('Invalid candidate SHA')
    if not docs_only(repo, 'prod', sha) and not deployment_proven(repo, sha):
        raise RuntimeError('No successful dev deployment for this production candidate')


def dispatch(repo, workflow, branch):
    api(f'repos/{repo}/actions/workflows/{workflow}/dispatches', 'POST', {'ref': branch})


def prepare_pr(repo, run, target):
    source, sha = run['head_branch'], run['head_sha']
    comparison = api(f'repos/{repo}/compare/{target}...{quote(source, safe="")}')
    if comparison['behind_by']:
        print(f'Admin must synchronize {target} into {source}, then verify again.')
        return
    if comparison['ahead_by'] == 0:
        return
    prefix = f'repos/{repo}/pulls'
    candidates = api(f'{prefix}?state=open&base={target}&head={repo.split("/")[0]}:{quote(source, safe="")}&per_page=100')
    if candidates:
        pr = candidates[0]
        if held(pr) or pr['head']['sha'] != sha:
            return
        reviews = api(f'{prefix}/{pr["number"]}/reviews?per_page=100')
        if latest_reviews_block(reviews):
            return
    else:
        closed = api(f'{prefix}?state=closed&base={target}&head={repo.split("/")[0]}:{quote(source, safe="")}&sort=updated&direction=desc&per_page=100')
        if any(not pr.get('merged_at') and pr['head']['sha'] == sha for pr in closed):
            return
        pr = api(prefix, 'POST', {
            'head': source, 'base': target,
            'title': f'Promote {source} to {target}',
            'body': f'Candidate `{sha}` passed [{run["name"]}]({run["html_url"]}).\n\n'
                    'An organization admin must review and merge. This controller cannot merge. '
                    'Use a hold label, request changes, or close this PR to stop promotion.'})
    # Dispatched runs are not attached to the PR automatically. Publish only the
    # verified exact head; strict branch rules still require the current base.
    api(f'repos/{repo}/statuses/{sha}', 'POST', {
        'state': 'success', 'context': WORKFLOWS[run['name']][1],
        'target_url': run['html_url'], 'description': 'Verified candidate; organization-admin review and merge required'})
    print(pr['html_url'])


def process(run, repo):
    action = route(run, repo)
    if action is None:
        return
    branch, sha = run['head_branch'], run['head_sha']
    current = api(f'repos/{repo}/branches/{quote(branch, safe="")}')['commit']['sha']
    if current != sha:
        print('Stale run: branch has moved.')
        return
    jobs = jobs_for(repo, run)
    if (run['name'] == 'Production' and jobs.get('infrastructure') == 'success'
            and jobs.get('production-required-checks') == 'skipped'):
        print('Infrastructure operation completed; no application promotion.')
        return
    if jobs.get(WORKFLOWS[run['name']][1]) != 'success':
        raise RuntimeError('Missing successful aggregate check')
    kind, target = action
    if kind == 'verify':
        if jobs.get('deploy') != 'success' and not docs_only(repo, 'prod', sha):
            raise RuntimeError('Production verification requires a successful dev deployment of this exact commit')
        dispatch(repo, 'production.yml', 'dev')
    elif kind == 'pr':
        if target == 'prod':
            require_dev_deployment(repo, sha)
        prepare_pr(repo, run, target)
    elif jobs.get('deploy') == 'success':
        # Release code comes from the default branch; identity comes from the
        # validated completed run, never the controller's own GITHUB_SHA.
        env = dict(os.environ, RELEASE_SHA=sha)
        subprocess.run(['python3', 'scripts/release_version.py'], env=env, check=True)
    else:
        commit = api(f'repos/{repo}/commits/{sha}')
        if not commit['parents'] or not docs_only(repo, commit['parents'][0]['sha'], sha):
            raise RuntimeError('Production code changed without a successful deployment')


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-dev')
    args = parser.parse_args()
    repo = os.environ['GITHUB_REPOSITORY']
    if args.verify_dev:
        require_dev_deployment(repo, args.verify_dev)
    else:
        with open(os.environ['GITHUB_EVENT_PATH']) as source:
            event = json.load(source)
        # Fetch authoritative run metadata; do not accept candidate-authored evidence.
        run = api(f'repos/{repo}/actions/runs/{event["workflow_run"]["id"]}')
        process(run, repo)
