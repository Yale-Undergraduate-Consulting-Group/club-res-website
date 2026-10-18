#!/usr/bin/env bash
# Run in AWS CloudShell in the Organizations MANAGEMENT account.
#
#   bash cloudshell-guardrails.sh report <account-id>
#       Read-only. Walks the account's parents up to the root and prints, for each
#       level and each action this project needs, whether the attached service
#       control policies allow it, and which statement denies it. Send the output
#       to the maintainer.
#   bash cloudshell-guardrails.sh attach-full-access <account-id-or-ou-id-or-root-id>
#       Attaches the AWS-managed FullAWSAccess policy to that one level, after a
#       confirmation. Use it on the level `report` marks NO-ALLOW. Deny rules at
#       any level still apply.
#
# Limits: conditions inside a policy (for example a region condition) are not
# evaluated; a conditional Deny is reported as DENY? and needs a human look.
set -euo pipefail

ACTIONS=(
  bedrock:PutUseCaseForModelAccess bedrock:CreateFoundationModelAgreement
  aws-marketplace:Subscribe aws-marketplace:ViewSubscriptions bedrock:InvokeModel
  iam:CreateOpenIDConnectProvider guardduty:CreateDetector ec2:RunInstances
)
MODE="${1:-}"
TARGET="${2:-}"
[ -n "$MODE" ] && [ -n "$TARGET" ] || { sed -n '2,15p' "$0" >&2; exit 2; }

MANAGEMENT="$(aws organizations describe-organization --query Organization.MasterAccountId --output text)"
[ "$(aws sts get-caller-identity --query Account --output text)" = "$MANAGEMENT" ] \
  || { echo "Run this in the management account ($MANAGEMENT)." >&2; exit 1; }

case "$MODE" in
attach-full-access)
  echo "This attaches FullAWSAccess to $TARGET. Every account under it can then use any action that"
  echo "no Deny rule blocks. It does not remove any Deny rule."
  read -r -p "Type yes to continue: " answer
  [ "$answer" = yes ] || { echo "Cancelled."; exit 1; }
  aws organizations attach-policy --policy-id p-FullAWSAccess --target-id "$TARGET"
  echo "Attached."
  exit 0
  ;;
report) ;;
*) echo "Unknown mode: $MODE" >&2; exit 2 ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

LEVELS=("$TARGET")
current="$TARGET"
while :; do
  parent="$(aws organizations list-parents --child-id "$current" --query 'Parents[0].Id' --output text)"
  LEVELS+=("$parent")
  case "$parent" in r-*) break ;; esac
  current="$parent"
done

# One jq program decides, for a policy document and an action: ALLOW, DENY:<sid>, DENY?:<sid> or -.
cat > "$WORK/eval.jq" <<'JQ'
def lst: if type == "array" then . else [.] end;
def rx: "^" + (gsub("\\."; "\\.") | gsub("\\*"; ".*") | gsub("\\?"; ".")) + "$";
def hit($a):
  if has("Action") then (.Action | lst | any(. as $p | $a | test($p | rx; "i")))
  elif has("NotAction") then (.NotAction | lst | any(. as $p | $a | test($p | rx; "i")) | not)
  else false end;
(.Statement | lst) as $s
| [$s[] | select(.Effect == "Deny") | select(hit($a))] as $deny
| [([$s[] | select(.Effect == "Allow") | select(hit($a))] | length > 0),
   ([$deny[] | select(has("Condition") | not) | (.Sid // "no-sid")] | first // "-"),
   ([$deny[] | select(has("Condition")) | (.Sid // "no-sid")] | first // "-")] | @tsv
JQ

printf '%-22s %-52s %s\n' LEVEL ACTION RESULT
BLOCKERS=()
for level in "${LEVELS[@]}"; do
  ids="$(aws organizations list-policies-for-target --target-id "$level" --filter SERVICE_CONTROL_POLICY --query 'Policies[].Id' --output text)"
  for id in $ids; do
    [ -f "$WORK/$id.json" ] || aws organizations describe-policy --policy-id "$id" --query Policy.Content --output text > "$WORK/$id.json"
  done
  names="$(aws organizations list-policies-for-target --target-id "$level" --filter SERVICE_CONTROL_POLICY --query 'Policies[].Name' --output text)"
  echo "-- $level carries: ${names:-nothing}"
  for action in "${ACTIONS[@]}"; do
    allowed=no; hard=""; cond=""
    for id in $ids; do
      read -r a h c <<< "$(jq -r --arg a "$action" -f "$WORK/eval.jq" "$WORK/$id.json")"
      [ "$a" = true ] && allowed=yes
      [ "$h" != - ] && hard="$hard $id:$h"
      [ "$c" != - ] && cond="$cond $id:$c"
    done
    if [ -n "$hard" ]; then result="DENY$hard"; BLOCKERS+=("$level denies $action:$hard")
    elif [ "$allowed" = no ]; then result="NO-ALLOW"; BLOCKERS+=("$level has no policy that allows $action")
    else result=ALLOW; fi
    [ -z "$cond" ] || result="$result  (conditional deny to check:$cond)"
    printf '%-22s %-52s %s\n' "$level" "$action" "$result"
  done
done

echo
if [ "${#BLOCKERS[@]}" -eq 0 ]; then
  echo "No level blocks any listed action unconditionally. Conditional denies (for example a region lock) were not evaluated; a lock that allows us-east-2 does not block this project."
else
  echo "Blocking findings:"
  printf '  %s\n' "${BLOCKERS[@]}" | sort -u
  echo
  echo "NO-ALLOW at a level means an allow-list policy there omits the action: add the action to that policy,"
  echo "or run: bash $0 attach-full-access <that level id>. DENY names the policy and statement to edit."
fi
