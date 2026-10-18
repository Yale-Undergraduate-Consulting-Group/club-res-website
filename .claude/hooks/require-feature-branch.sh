#!/bin/sh
# PreToolUse guard: block file edits on shared branches. Work happens on feature/<login>.
branch=$(git -C "${CLAUDE_PROJECT_DIR:-.}" branch --show-current 2>/dev/null)
case "$branch" in
  main|integration|dev|prod)
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Editing on the shared branch %s is blocked. Use the my-branch skill to switch to your one branch, feature/<login>, then retry the edit. Do not create any other branch."}}\n' "$branch"
    ;;
esac
exit 0
