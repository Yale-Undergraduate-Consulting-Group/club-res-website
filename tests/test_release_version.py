from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from release_version import next_version


class ReleaseVersionTests(unittest.TestCase):
    def test_numeric_order_ignores_prereleases(self):
        self.assertEqual(next_version(['v0.1.9', 'v0.1.10', 'v0.2.0-rc.42']), 'v0.1.11')

    def test_semantic_bumps_reset_lower_components(self):
        self.assertEqual(next_version(['v1.3.9'], 'minor'), 'v1.4.0')
        self.assertEqual(next_version(['v1.3.9'], 'major'), 'v2.0.0')


if __name__ == '__main__':
    unittest.main()
