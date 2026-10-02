from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from release_version import next_version, release_bump


def pr(*labels, merged=True):
    return {'merged_at': '2026-10-01T00:00:00Z' if merged else None,
            'labels': [{'name': name} for name in labels]}


class ReleaseVersionTests(unittest.TestCase):
    def test_numeric_order_ignores_prereleases(self):
        self.assertEqual(next_version(['v0.1.9', 'v0.1.10', 'v0.2.0-rc.42']), 'v0.1.11')

    def test_semantic_bumps_reset_lower_components(self):
        self.assertEqual(next_version(['v1.3.9'], 'minor'), 'v1.4.0')
        self.assertEqual(next_version(['v1.3.9'], 'major'), 'v2.0.0')

    def test_largest_label_across_contributor_prs_wins(self):
        self.assertEqual(release_bump([pr('release:patch'), pr('release:major'), pr('release:minor')]), 'major')
        self.assertEqual(release_bump([pr('release:minor'), pr()]), 'minor')

    def test_unlabeled_or_unmerged_changes_are_patches(self):
        self.assertEqual(release_bump([pr(), pr('bug')]), 'patch')
        self.assertEqual(release_bump([pr('release:major', merged=False)]), 'patch')


if __name__ == '__main__':
    unittest.main()
