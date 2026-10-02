#!/bin/bash
# Fail the build on real high/critical advisories; never on npm's registry
# being unreachable.
#
# npm's advisory endpoints have failed intermittently for months: the legacy
# quick-audit endpoint was retired in July 2026 (410/400) and the bulk
# endpoint returns 503/hangs during registry incidents, often while the
# status page still reads operational. Treating that as a vulnerability
# blocked a verified release with nothing wrong in the tree.
#
# Distinguishing the two is exact, not heuristic: `npm audit --json` prints a
# report object with .metadata.vulnerabilities when it reached the registry,
# and an object with .error when it did not.
set -uo pipefail

LEVEL="${AUDIT_LEVEL:-high}"
ATTEMPTS="${AUDIT_ATTEMPTS:-3}"
report=""

for attempt in $(seq 1 "$ATTEMPTS"); do
  report="$(npm audit --audit-level="$LEVEL" --json 2>/dev/null)"
  if printf '%s' "$report" | jq -e '.metadata.vulnerabilities' >/dev/null 2>&1; then
    high="$(printf '%s' "$report" | jq '.metadata.vulnerabilities.high // 0')"
    critical="$(printf '%s' "$report" | jq '.metadata.vulnerabilities.critical // 0')"
    if [ "$high" -gt 0 ] || [ "$critical" -gt 0 ]; then
      echo "::error::npm audit found $critical critical and $high high advisories"
      printf '%s' "$report" | jq -r '
        (.vulnerabilities // {}) | to_entries[]
        | select(.value.severity == "high" or .value.severity == "critical")
        | "  \(.value.severity)\t\(.key)"' | sort -u
      exit 1
    fi
    echo "npm audit: no high or critical advisories (attempt $attempt)"
    exit 0
  fi
  reason="$(printf '%s' "$report" | jq -r '.error.summary // .error.detail // .error // empty' 2>/dev/null)"
  [ -z "$reason" ] && reason="no JSON report on stdout"
  echo "npm audit attempt $attempt/$ATTEMPTS did not reach the registry: $reason"
  [ "$attempt" -lt "$ATTEMPTS" ] && sleep $((attempt * 10))
done

# Registry unreachable after every attempt. Say so loudly and let the release
# proceed; the dependency tree was never evaluated, so this is not a pass.
echo "::warning::npm audit could not reach the npm advisory endpoint after $ATTEMPTS attempts; dependencies were NOT scanned in this run"
{
  echo "### npm audit skipped"
  echo
  echo "The npm advisory endpoint was unreachable after $ATTEMPTS attempts, so dependencies were not scanned for this build."
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
exit 0
