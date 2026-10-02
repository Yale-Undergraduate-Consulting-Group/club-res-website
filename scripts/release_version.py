"""Choose a monotonic version after a successful deployment; retries reuse the same release."""
import json
import os
import re
import subprocess


def next_version(tags, bump='patch'):
    versions = [tuple(map(int, match.groups())) for tag in tags
                if (match := re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', tag))]
    if not versions:
        version = (0, 1, 0)
    else:
        major, minor, patch = max(versions)
        version = {'major': (major + 1, 0, 0), 'minor': (major, minor + 1, 0),
                   'patch': (major, minor, patch + 1)}[bump]
    tag = 'v' + '.'.join(map(str, version))
    return tag


if __name__ == '__main__':
    repo, sha = os.environ['GITHUB_REPOSITORY'], os.environ['RELEASE_SHA']
    pages = json.loads(subprocess.check_output([
        'gh', 'api', '--paginate', '--slurp', f'repos/{repo}/releases?per_page=100'], text=True))
    releases = [release for page in pages for release in page if not release['draft']]
    existing = [r for r in releases if r['target_commitish'] == sha and not r['prerelease']]
    if existing:
        print(existing[0]['html_url'])
    else:
        prs = json.loads(subprocess.check_output(['gh', 'api', f'repos/{repo}/commits/{sha}/pulls'], text=True))
        bumps = {label['name'].split(':')[1] for pr in prs for label in pr['labels']
                 if label['name'] in {'release:major', 'release:minor', 'release:patch'}}
        if len(bumps) > 1:
            raise SystemExit('Conflicting release version labels')
        tag = next_version([r['tag_name'] for r in releases], next(iter(bumps), 'patch'))
        args = ['gh', 'release', 'create', tag, '--repo', repo, '--target', sha, '--generate-notes', '--title', tag]
        subprocess.run(args, check=True)
