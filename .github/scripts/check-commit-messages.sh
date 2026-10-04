#!/usr/bin/env bash
# Run prek's commit-msg hooks (commitlint) on every non-merge commit in a
# range, so CI checks messages with the same hook as local commits.
#
# Usage: check-commit-messages.sh <base> <head>
#   base: the commit before the range. Empty, all zeros (a new branch) or a
#         commit that is no longer in history (a force push) checks every
#         commit reachable from <head>.

set -euo pipefail

base=${1:-}
head=${2:?usage: $0 <base> <head>}

if [[ -n $base && ! $base =~ ^0+$ ]] && git cat-file -e "$base^{commit}" 2>/dev/null; then
    range=$base..$head
else
    range=$head
fi

msg=$(mktemp)
trap 'rm -f "$msg"' EXIT
status=0
checked=0
while read -r commit; do
    git log -1 --format=%B "$commit" >"$msg"
    echo "--- $(git log -1 --format='%h %s' "$commit")"
    if ! prek run --hook-stage commit-msg --commit-msg-filename "$msg"; then
        status=1
    fi
    checked=$((checked + 1))
done < <(git rev-list --no-merges "$range")

echo "Checked $checked commit(s) in $range."
exit "$status"
