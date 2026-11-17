"""The npm audit gate passes high/critical findings only when every advisory
behind them has an unexpired, recorded exception."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "scripts" / "npm_audit_gate.sh"
EXCEPTED = "GHSA-aaaa-bbbb-cccc"


def report(*advisories):
    """An npm audit --json report whose findings trace to these (id, severity) advisories."""
    vulns, counts = {}, {"high": 0, "critical": 0, "moderate": 0}
    for index, (ghsa, severity) in enumerate(advisories):
        vulns[f"pkg{index}"] = {"severity": severity, "via": [{"severity": severity, "url": f"https://github.com/advisories/{ghsa}"}]}
        vulns[f"parent{index}"] = {"severity": severity, "via": [f"pkg{index}"]}
        counts[severity] += 2
    return {"vulnerabilities": vulns, "metadata": {"vulnerabilities": counts}}


class AuditGate(unittest.TestCase):
    def run_gate(self, audit, exceptions, today="2026-11-20"):
        with tempfile.TemporaryDirectory() as tmp:
            audit_file, exceptions_file = Path(tmp, "audit.json"), Path(tmp, "exceptions.json")
            audit_file.write_text(json.dumps(audit))
            exceptions_file.write_text(json.dumps(exceptions))
            env = {**os.environ, "AUDIT_REPORT_FILE": str(audit_file), "AUDIT_EXCEPTIONS": str(exceptions_file),
                   "AUDIT_TODAY": today, "AUDIT_ATTEMPTS": "1"}
            return subprocess.run(["bash", str(GATE)], env=env, capture_output=True, text=True)

    def test_clean_report_passes(self):
        self.assertEqual(self.run_gate(report(), []).returncode, 0)

    def test_findings_from_an_unexpired_exception_pass_with_a_warning(self):
        result = self.run_gate(report((EXCEPTED, "high")), [{"advisory": EXCEPTED, "expires": "2026-12-01"}])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("excepted", result.stdout)

    def test_expired_exception_fails(self):
        result = self.run_gate(report((EXCEPTED, "high")), [{"advisory": EXCEPTED, "expires": "2026-12-01"}], today="2026-12-02")
        self.assertEqual(result.returncode, 1)

    def test_any_other_high_or_critical_advisory_still_fails(self):
        exceptions = [{"advisory": EXCEPTED, "expires": "2026-12-01"}]
        for other in (("GHSA-xxxx-yyyy-zzzz", "high"), ("GHSA-xxxx-yyyy-zzzz", "critical")):
            result = self.run_gate(report((EXCEPTED, "high"), other), exceptions)
            self.assertEqual(result.returncode, 1, other)
            self.assertIn("GHSA-xxxx-yyyy-zzzz", result.stdout)

    def test_missing_exceptions_file_fails_on_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_file = Path(tmp, "audit.json")
            audit_file.write_text(json.dumps(report((EXCEPTED, "high"))))
            env = {**os.environ, "AUDIT_REPORT_FILE": str(audit_file), "AUDIT_EXCEPTIONS": str(Path(tmp, "none.json")), "AUDIT_ATTEMPTS": "1"}
            self.assertEqual(subprocess.run(["bash", str(GATE)], env=env, capture_output=True, text=True).returncode, 1)


if __name__ == "__main__":
    unittest.main()
