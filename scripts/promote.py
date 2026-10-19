"""Prepare reviewed stage PRs from trusted workflow completion events; never merge."""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import quote

from ci_policy import is_documentation

WORKFLOWS = {
    'Intake': ('intake.yml', 'intake-required-checks'),
    'Dev': ('dev.yml', 'dev-required-checks'),
    'Production': ('production.yml', 'production-required-checks'),
}
REVIEW_WORKFLOW = '.github/workflows/review.yml'
# Stage PRs into these branches carry the review-gate status the rulesets require.
GATED_TARGETS = {'dev', 'prod'}
REVIEW_TEAM = 'club-res-website-maintainers'
SMALL_CHANGE_LINES = 50
# Changes here alter delivery, infrastructure, permissions, dependencies, or agent
# policy, so they need approval at any size.
SENSITIVE_PREFIXES = ('terraform/', '.github/', 'github/', 'scripts/', 'ops/', 'docker/', 'organization/', '.claude/')
SENSITIVE_FILES = {'AGENTS.md', 'CLAUDE.md', '.dockerignore', '.trivyignore',
                   'backend/requirements.txt', 'backend/requirements.lock', 'backend/requirements-dev.txt',
                   'frontend/package.json', 'frontend/package-lock.json'}
DISPATCH_WAIT_SECONDS = 25 * 60
DISPATCH_POLL_SECONDS = 20


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


def destination_changes_files(repo, source, target):
    """Whether target holds file changes that source lacks. Unproven counts as yes.

    PR merge commits land on the destination and never flow back, so the destination
    is routinely "ahead" by commits that change no files. Those cannot invalidate what
    the candidate's checks tested; a destination with real changes still must be
    merged into the candidate first.
    """
    files = api(f'repos/{repo}/compare/{quote(source, safe="")}...{target}').get('files')
    return not isinstance(files, list) or bool(files)


def is_sensitive(path):
    return path.startswith(SENSITIVE_PREFIXES) or path in SENSITIVE_FILES


def small_change(files):
    """Docs-only, or at most SMALL_CHANGE_LINES changed code lines outside sensitive paths.

    Missing, empty, or truncated (GitHub caps compare at 300 files) evidence counts as large.
    """
    if not isinstance(files, list) or not files or len(files) >= 300:
        return False
    paths = [p for f in files for p in (f['filename'], f.get('previous_filename')) if p]
    if any(is_sensitive(p) for p in paths):
        return False
    code = [f for f in files if not (is_documentation(f['filename']) and
                                     is_documentation(f.get('previous_filename') or f['filename']))]
    return sum(f.get('changes', SMALL_CHANGE_LINES + 1) for f in code) <= SMALL_CHANGE_LINES


def maintainer_approved(repo, pr):
    """A repository admin (the documented maintainer role) approved the current head."""
    reviews = api(f'repos/{repo}/pulls/{pr["number"]}/reviews?per_page=100')
    if latest_reviews_block(reviews):
        return False
    latest = {}
    for review in reviews:
        if review['state'] != 'COMMENTED':
            latest[review['user']['login']] = review
    for login, review in latest.items():
        if review['state'] == 'APPROVED' and review.get('commit_id') == pr['head']['sha']:
            permission = api(f'repos/{repo}/collaborators/{quote(login, safe="")}/permission')
            if permission.get('permission') == 'admin':
                return True
    return False


def apply_review_gate(repo, pr):
    """Publish review-gate on the PR head: success when small or maintainer-approved."""
    sha, base = pr['head']['sha'], pr['base']['ref']
    files = api(f'repos/{repo}/compare/{quote(base, safe="")}...{sha}').get('files')
    if small_change(files):
        state, description = 'success', 'Small change: no approval required'
    elif maintainer_approved(repo, pr):
        state, description = 'success', 'Approved by a maintainer'
    else:
        state, description = 'pending', 'Large or sensitive change: maintainer approval required'
        requested = api(f'repos/{repo}/pulls/{pr["number"]}/requested_reviewers')
        if not any(team['slug'] == REVIEW_TEAM for team in requested.get('teams', [])):
            try:
                api(f'repos/{repo}/pulls/{pr["number"]}/requested_reviewers', 'POST', {'team_reviewers': [REVIEW_TEAM]})
            except subprocess.CalledProcessError as error:
                # The request only notifies; the pending gate still blocks the merge.
                print(f'Could not request review from {REVIEW_TEAM}: {error.stderr.strip()}')
    api(f'repos/{repo}/statuses/{sha}', 'POST', {
        'state': state, 'context': 'review-gate', 'target_url': pr['html_url'], 'description': description})
    print(f'review-gate {state} on #{pr["number"]}: {description}')


def refresh_review_gates(repo, sha):
    for base in sorted(GATED_TARGETS):
        for pr in api(f'repos/{repo}/pulls?state=open&base={base}&per_page=100'):
            if pr['head']['sha'] == sha and pr['head']['repo']['full_name'] == repo:
                apply_review_gate(repo, pr)


def wait_for_dispatched_run(repo, workflow, branch, sha, since):
    """The dispatch API returns no run ID, and runs started by GITHUB_TOKEN never
    emit workflow_run, so the controller follows its own dispatch to completion."""
    deadline = time.monotonic() + DISPATCH_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(DISPATCH_POLL_SECONDS)
        runs = api(f'repos/{repo}/actions/workflows/{workflow}/runs?event=workflow_dispatch'
                   f'&branch={quote(branch, safe="")}&head_sha={sha}&created=%3E%3D{since}&per_page=10')['workflow_runs']
        if runs and runs[0]['status'] == 'completed':
            if runs[0]['conclusion'] != 'success':
                raise RuntimeError(f'{workflow} on {branch} ended {runs[0]["conclusion"]}: {runs[0]["html_url"]}')
            return api(f'repos/{repo}/actions/runs/{runs[0]["id"]}')
    raise RuntimeError(f'{workflow} on {branch} did not complete within {DISPATCH_WAIT_SECONDS // 60} minutes')


def prepare_pr(repo, run, target):
    source, sha = run['head_branch'], run['head_sha']
    comparison = api(f'repos/{repo}/compare/{target}...{quote(source, safe="")}')
    if comparison['behind_by'] and destination_changes_files(repo, source, target):
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
                    'A maintainer must review and merge. This controller cannot merge. '
                    'Use a hold label, request changes, or close this PR to stop promotion.'})
    # Dispatched runs are not attached to the PR automatically. Publish only the
    # verified exact head; strict branch rules still require the current base.
    api(f'repos/{repo}/statuses/{sha}', 'POST', {
        'state': 'success', 'context': WORKFLOWS[run['name']][1],
        'target_url': run['html_url'], 'description': 'Verified candidate; maintainer merge required'})
    print(pr['html_url'])
    if target in GATED_TARGETS:
        apply_review_gate(repo, pr)


def process(run, repo):
    if run.get('name') == 'Review':
        # Review runs execute no candidate code; they only signal that a review changed.
        if (run.get('conclusion') == 'success' and run.get('path') == REVIEW_WORKFLOW
                and run.get('head_repository', {}).get('full_name') == repo):
            refresh_review_gates(repo, run['head_sha'])
        return
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
        since = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        dispatch(repo, 'production.yml', 'dev')
        process(wait_for_dispatched_run(repo, 'production.yml', 'dev', sha, since), repo)
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
