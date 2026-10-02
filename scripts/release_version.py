"""Choose a monotonic version after a successful deployment; retries reuse the same release.

The bump is the largest `release:*` label on any PR merged since the previous
release: major > minor > patch. Unlabeled changes are patches.
"""
import json
import os
import re
import subprocess

BUMPS = ('patch', 'minor', 'major')  # ascending precedence


def next_version(tags, bump='patch'):
    versions = [tuple(map(int, match.groups())) for tag in tags
                if (match := re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', tag))]
    if not versions:
        version = (0, 1, 0)
    else:
        major, minor, patch = max(versions)
        version = {'major': (major + 1, 0, 0), 'minor': (major, minor + 1, 0),
                   'patch': (major, minor, patch + 1)}[bump]
    return 'v' + '.'.join(map(str, version))


def release_bump(prs):
    """Largest release label across merged PRs; patch when none is set."""
    found = {label['name'].removeprefix('release:') for pr in prs if pr.get('merged_at')
             for label in pr['labels'] if label['name'].startswith('release:')}
    return max((bump for bump in found if bump in BUMPS), key=BUMPS.index, default='patch')


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def unreleased_commits(repo, sha, released):
    """Walk history from the deployed commit back to the previous release."""
    page = 1
    while True:
        commits = api(f'repos/{repo}/commits?sha={sha}&per_page=100&page={page}')
        for commit in commits:
            if commit['sha'] in released:
                return
            yield commit['sha']
        if len(commits) < 100:
            return
        page += 1


if __name__ == '__main__':
    repo, sha = os.environ['GITHUB_REPOSITORY'], os.environ['RELEASE_SHA']
    pages = json.loads(subprocess.check_output([
        'gh', 'api', '--paginate', '--slurp', f'repos/{repo}/releases?per_page=100'], text=True))
    releases = [release for page in pages for release in page if not release['draft']]
    existing = [r for r in releases if r['target_commitish'] == sha and not r['prerelease']]
    if existing:
        print(existing[0]['html_url'])
    else:
        released = {r['target_commitish'] for r in releases}
        prs = {pr['number']: pr for commit in unreleased_commits(repo, sha, released)
               for pr in api(f'repos/{repo}/commits/{commit}/pulls')}
        tag = next_version([r['tag_name'] for r in releases], release_bump(prs.values()))
        subprocess.run(['gh', 'release', 'create', tag, '--repo', repo, '--target', sha,
                        '--generate-notes', '--title', tag], check=True)
