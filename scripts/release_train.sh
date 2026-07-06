#!/usr/bin/env bash
# release_train.sh — daily "release train" via scheduled MR MERGE.
#
# Release branches are PROTECTED: nobody can push or force-push them, so changes
# reach them ONLY by merging an MR. The task-worker accumulates client-approved
# tasks onto rc-<branch> and keeps an open MR  rc-<branch> -> <branch>  (the
# "release PR"). This job, on a daily schedule, MERGES that MR via the GitLab API
# for each branch in RELEASE_BRANCHES. A merge (unlike a push) respects branch
# protection, so it just needs a token allowed to merge into the release branch.
#
# Env:
#   RELEASE_BRANCHES    required, e.g. "18.0 19.0"
#   CI_BOT_TOKEN required — existing group CI var (bot access token); the bot
#                       needs `api` scope AND Allowed-to-merge on the protected
#                       release branches.
#   CI_API_V4_URL, CI_PROJECT_ID  (GitLab-provided)
set -uo pipefail
: "${RELEASE_BRANCHES:?RELEASE_BRANCHES not set}"
: "${CI_BOT_TOKEN:?CI_BOT_TOKEN not set (needs MR-merge rights on the protected release branches)}"
API="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}"
AUTH=(--header "PRIVATE-TOKEN: ${CI_BOT_TOKEN}")
fail=0

# make_release <from_sha> <to_sha> <ver>
# Publish a GitLab Release for the commits promoted by this merge, so every
# promotion produces client-visible patch notes. Notes are the raw commit
# titles in <from>..<to> (merge commits filtered out) — a starting point to be
# curated into the French CHANGELOG. Tag follows the repo's deploy-date scheme
# (AAAA.MM.JJ, same-day deploys increment .1, .2, ...). Non-fatal: a failure
# here never fails the release train.
make_release() {
  local from="$1" to="$2" ver="$3"
  [ -n "$from" ] && [ -n "$to" ] || { echo "    [$ver] release: missing sha, skipping notes"; return 0; }
  local notes
  notes=$(curl -sf "${AUTH[@]}" "${API}/repository/compare?from=${from}&to=${to}" \
            | jq -r '.commits[]? | select((.title // "") | test("^Merge branch") | not) | "- \(.title)"')
  [ -n "$notes" ] || notes="- (aucune modification listable — voir l'historique git)"
  local base tag n=0
  base=$(date +%Y.%m.%d); tag="$base"
  while curl -sf "${AUTH[@]}" "${API}/repository/tags/${tag}" >/dev/null 2>&1; do
    n=$((n+1)); tag="${base}.${n}"
  done
  local body code
  body=$(printf '### Modifications (%s)\n\n%s\n' "$ver" "$notes")
  code=$(curl -s -o /tmp/rt_release.json -w '%{http_code}' --request POST "${AUTH[@]}" \
           --data-urlencode "tag_name=${tag}" --data-urlencode "ref=${to}" \
           --data-urlencode "name=${tag}" --data-urlencode "description=${body}" \
           "${API}/releases" || echo 000)
  if [ "$code" = "201" ]; then
    echo "    [$ver] published release ${tag}"
  else
    echo "    [$ver] WARN: release ${tag} not created (HTTP ${code}): $(jq -r '.message // .' /tmp/rt_release.json 2>/dev/null | head -c 200)"
  fi
}
for ver in $RELEASE_BRANCHES; do
  rc="rc-${ver}"
  mr=$(curl -sf "${AUTH[@]}" "${API}/merge_requests?state=opened&source_branch=${rc}&target_branch=${ver}" || echo '[]')
  iid=$(echo "$mr" | jq -r '.[0].iid // empty')
  if [ -z "$iid" ]; then echo "::: [$ver] no open ${rc} -> ${ver} MR — nothing to promote"; continue; fi
  status=$(echo "$mr" | jq -r '.[0].detailed_merge_status // .[0].merge_status // "unknown"')
  echo "::: [$ver] merging MR !${iid} (${rc} -> ${ver}); status=${status}"
  before=$(curl -sf "${AUTH[@]}" "${API}/repository/branches/${ver}" | jq -r '.commit.id // empty')
  code=$(curl -s -o /tmp/rt_merge.json -w '%{http_code}' --request PUT "${AUTH[@]}" \
           --data "merge_when_pipeline_succeeds=false" "${API}/merge_requests/${iid}/merge" || echo 000)
  if [ "$code" = "200" ]; then
    echo "    [$ver] merged MR !${iid}"
    after=$(jq -r '.merge_commit_sha // .sha // empty' /tmp/rt_merge.json 2>/dev/null)
    make_release "$before" "$after" "$ver"
  else
    echo "!!! [$ver] merge FAILED (HTTP ${code}): $(jq -r '.message // .' /tmp/rt_merge.json 2>/dev/null | head -c 200)"
    fail=1
  fi
done
[ "$fail" -eq 0 ] || { echo "release-train: one or more merges failed (conflict / not mergeable / perms)"; exit 1; }
echo "release-train: all clean"
