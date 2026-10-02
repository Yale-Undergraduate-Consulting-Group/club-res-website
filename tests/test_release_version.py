import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('version', Path(__file__).parents[1] / 'scripts/release_version.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VersionTests(unittest.TestCase):
    def test_channel_and_patch_sequence(self):
        self.assertEqual(module.next_version([], 'beta', '7'), 'v0.1.0-beta.7')
        self.assertEqual(module.next_version(['v0.1.9','v0.1.10','v0.2.0-beta.42'], 'production', '43'), 'v0.1.11')
        self.assertEqual(module.next_version(['v1.3.9'], 'production', '1', 'minor'), 'v1.4.0')
        self.assertEqual(module.next_version(['v1.3.9'], 'production', '1', 'major'), 'v2.0.0')
