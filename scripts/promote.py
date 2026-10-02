"""Advance develop → feature → main after a stage passes.

Runs as a job on Intake, Beta, and Production. Reads GitHub metadata only;
never executes PR code. GITHUB_TOKEN cannot start workflows from the events
it creates, so this controller dispatches the next stage after a merge or
after opening a promotion PR. Repository rules stay authoritative; this
controller never uses an admin bypass.
"""
import json
import os
import subprocess
import sys
import time

STAGE_FOR_BASE = {'develop': 'Intake', 'feature': 'Beta', 'main': 'Production'}
WORKFLOW_FOR_BRANCH = {'develop': 'intake.yml', 'feature': 'beta.yml', 'main': 'production.yml'}
PROMOTION_EDGES = {('feature', 'develop'), ('main', 'feature')}
CHECK_FOR_STAGE = {
    'Intake': 'intake-required-checks',
    'Beta': 'feature-required-checks',
    'Production': 'production-required-checks',
}


def api(path, method='GET', payload=None):
    args = ['gh', 'api', path]
    if method != 'GET':
        args += ['--method', method]
    if payload is not None:
        args += ['--input', '-']
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout else None


def held(pr):
    return pr.get('draft') or bool({'hold', 'do-not-merge', 'release:hold'} &
                                  {label['name'] for label in pr.get('labels', [])})


def latest_reviews_block(reviews):
    latest = {}
    for review in sorted(reviews, key=lambda r: r.get('submitted_at') or ''):
        if review['state'] in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'):
            latest[review['user']['login']] = review['state']
    return 'CHANGES_REQUESTED' in latest.values()


def dependency_files_allowed(files):
    allowed = {'frontend/package.json', 'frontend/package-lock.json', 'terraform/.terraform.lock.hcl'}
    return bool(files) and all(item['filename'] in allowed for item in files)


def dispatch_workflow(repo, workflow, ref):
    """Start the next stage when GITHUB_TOKEN cannot trigger it by event."""
    if os.environ.get('PROMOTION_DISPATCH') != '1':
        return
    api(f'repos/{repo}/actions/workflows/{workflow}/dispatches', 'POST', {'ref': ref})


def merge_now(repo, number, sha, base):
    subprocess.run(['gh', 'pr', 'merge', str(number), '--repo', repo,
                    '--auto', '--merge', '--match-head-commit', sha], check=True)
    if os.environ.get('PROMOTION_DISPATCH') != '1':
        return
    for _ in range(30):
        merged = api(f'repos/{repo}/pulls/{number}')
        if merged['state'] == 'closed' and merged.get('merged_at'):
            workflow = WORKFLOW_FOR_BRANCH.get(base)
            if workflow:
                dispatch_workflow(repo, workflow, base)
            return
        time.sleep(2)
    raise RuntimeError(
        f'Pull request #{number} did not merge within 60 seconds; '
        'the next delivery stage was not dispatched'
    )


def publish_merge_check(repo, pr, run):
    """Expose a dispatched stage result as a required status on its exact head."""
    if os.environ.get('PROMOTION_PUBLISH_CHECK') != '1':
        return
    api(f'repos/{repo}/statuses/{run["head_sha"]}', 'POST', {
        'state': 'success',
        'context': CHECK_FOR_STAGE[run['name']],
        'target_url': run['html_url'],
        'description': f'{run["name"]} passed for the current protected-branch base',
    })


def pull_requests_for(run, repo):
    listed = list(run.get('pull_requests') or [])
    if listed:
        return listed
    if run.get('event') != 'workflow_dispatch':
        return []
    sha = run['head_sha']
    open_prs = api(f'repos/{repo}/pulls?state=open&per_page=100') or []
    return [{'number': item['number']} for item in open_prs if item.get('head', {}).get('sha') == sha]


def synchronize(repo, source):
    """Replay trusted feature/main history onto develop.

    develop is not a protected promotion branch. A topic PR here is only noise:
    github-actions[bot] PRs wait for a human to approve Actions, then expire red,
    while the matching workflow_dispatch already did the work.
    """
    prefix = f'repos/{repo}'
    sha = api(f'{prefix}/branches/{source}')['commit']['sha']
    develop_sha = api(f'{prefix}/branches/develop')['commit']['sha']
    comparison = api(f'{prefix}/compare/{develop_sha}...{sha}')
    if comparison['ahead_by'] == 0:
        return
    api(f'{prefix}/merges', 'POST', {
        'base': 'develop',
        'head': sha,
        'commit_message': f'Synchronize {source} ({sha[:12]}) into develop',
    })
    dispatch_workflow(repo, 'intake.yml', 'develop')


def process_pull_requests(run, repo):
    prefix = f'repos/{repo}'
    for candidate in pull_requests_for(run, repo):
        pr = api(f"{prefix}/pulls/{candidate['number']}")
        if pr['state'] != 'open' or held(pr) or pr['head']['repo']['full_name'] != repo:
            continue
        if pr['head']['sha'] != run['head_sha']:
            continue  # Never merge newer, unchecked code using an older run.
        base, head = pr['base']['ref'], pr['head']['ref']
        if run['name'] != STAGE_FOR_BASE.get(base):
            continue
        promotion = (base, head) in PROMOTION_EDGES
        dependency = base == 'develop' and pr['user']['login'] == 'dependabot[bot]'
        # Every same-repository topic PR enters through Intake. Drafts, hold
        # labels and requested changes remain explicit brakes.
        requested = base == 'develop' and pr['user']['login'] != 'dependabot[bot]'
        sync_source = head.split('/')[1].split('-')[0] if head.startswith('sync/') else ''
        sync = base == 'develop' and sync_source in {'main', 'feature'} and pr['head']['sha'] == api(f'{prefix}/branches/{sync_source}')['commit']['sha']
        if not promotion and not dependency and not sync and not requested:
            continue
        reviews = api(f"{prefix}/pulls/{pr['number']}/reviews?per_page=100")
        if len(reviews) == 100 or latest_reviews_block(reviews):
            continue  # Conservative when pagination or human rejection needs attention.
        if dependency:
            files = api(f"{prefix}/pulls/{pr['number']}/files?per_page=100")
            if len(files) == 100 or not dependency_files_allowed(files):
                continue
        tested_base_sha = pr['base']['sha']
        comparison = api(f'{prefix}/compare/{tested_base_sha}...{pr["head"]["sha"]}')
        if comparison['behind_by']:
            continue  # Never merge a source that does not contain the current base.
        current = api(f"{prefix}/pulls/{pr['number']}")
        if (current['state'] != 'open' or held(current)
                or current['head']['sha'] != pr['head']['sha']
                or current['base']['ref'] != base
                or current['base']['sha'] != tested_base_sha):
            continue
        publish_merge_check(repo, current, run)
        merge_now(repo, pr['number'], pr['head']['sha'], base)


def process_branch(run, repo):
    prefix = f'repos/{repo}'
    source = run['head_branch']
    if source not in {'develop', 'feature', 'main'}:
        return
    if api(f'{prefix}/branches/{source}')['commit']['sha'] != run['head_sha']:
        return
    if source == 'main' and run['name'] == 'Production':
        synchronize(repo, 'main')
        return
    target = {'develop': 'feature', 'feature': 'main'}.get(source)
    if not target or run['name'] != STAGE_FOR_BASE[source]:
        return
    comparison = api(f'{prefix}/compare/{target}...{source}')
    if comparison['behind_by']:
        synchronize(repo, target)
        return
    if comparison['ahead_by'] == 0 or (not comparison['files'] and source != 'develop'):
        return
    # Ask two precise questions instead of paging the whole history. The old
    # single state=all query stopped the controller permanently once this
    # edge accumulated 100 promotion PRs, which an active week reaches.
    open_candidates = api(f'{prefix}/pulls?state=open&base={target}&head={repo.split("/")[0]}:{source}&per_page=20')
    if open_candidates:
        # A long-lived develop→feature or feature→main PR follows its source
        # branch automatically. Verify its new exact head instead of leaving a
        # stale candidate waiting with checks from the previous revision.
        dispatch_workflow(repo, WORKFLOW_FOR_BRANCH[target], source)
        return
    # A candidate someone deliberately closed without merging must not be
    # recreated. Only a recently closed PR can carry this exact head, so the
    # newest page answers it without reading years of history.
    recent_closed = api(
        f'{prefix}/pulls?state=closed&base={target}&head={repo.split("/")[0]}:{source}'
        '&sort=updated&direction=desc&per_page=30'
    )
    if any(not pr.get('merged_at') and pr['head']['sha'] == run['head_sha'] for pr in recent_closed):
        return
    api(f'{prefix}/pulls', 'POST', {
        'head': source, 'base': target,
        'title': f'Promote verified {source} to {target}',
        'body': f'Candidate `{run["head_sha"]}` passed [{run["name"]}]({run["html_url"]}).\n\n'
                'The next stage must pass its own required checks. Add `release:hold`, request changes, '
                'or close this PR to stop this candidate. Infrastructure apply uses a separate reviewed saved plan.\n\n'
                'Cost: application promotion adds no resources; infrastructure cost changes require their own plan.'})
    dispatch_workflow(repo, WORKFLOW_FOR_BRANCH[target], source)


def stage_run(event, repo):
    """Accept a workflow_run payload or the native Intake/Beta/Production event."""
    if 'workflow_run' in event:
        return event['workflow_run']
    pull = event.get('pull_request') or {}
    head = pull.get('head') or {}
    return {
        'name': os.environ['PROMOTION_STAGE'],
        'event': os.environ['GITHUB_EVENT_NAME'],
        'conclusion': 'success',
        'head_sha': head.get('sha') or os.environ['GITHUB_SHA'],
        'head_branch': head.get('ref') or os.environ['GITHUB_REF_NAME'],
        'head_repository': {'full_name': repo},
        'html_url': f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}",
        'pull_requests': [{'number': pull['number']}] if pull.get('number') else [],
    }


def process(event, repo):
    run = stage_run(event, repo)
    if run['head_repository']['full_name'] != repo or run['conclusion'] != 'success':
        return
    if run['name'] not in {'Intake', 'Beta', 'Production'}:
        raise RuntimeError('Unexpected workflow')
    if run['event'] == 'pull_request':
        process_pull_requests(run, repo)
        return
    if run['event'] == 'workflow_dispatch':
        process_pull_requests(run, repo)
        process_branch(run, repo)
        return
    if run['event'] != 'push':
        return
    process_branch(run, repo)


if __name__ == '__main__':
    try:
        with open(os.environ['GITHUB_EVENT_PATH']) as event_file:
            process(json.load(event_file), os.environ['GITHUB_REPOSITORY'])
    except subprocess.CalledProcessError as error:
        print(error.stderr or 'GitHub rejected the automation operation', file=sys.stderr)
        raise SystemExit(1)
