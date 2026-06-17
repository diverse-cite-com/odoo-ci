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
#   RELEASE_TRAIN_TOKEN required — token with rights to merge MRs into the
#                       protected release branches (Maintainer / Allowed-to-merge)
#   CI_API_V4_URL, CI_PROJECT_ID  (GitLab-provided)
set -uo pipefail
: "${RELEASE_BRANCHES:?RELEASE_BRANCHES not set}"
: "${RELEASE_TRAIN_TOKEN:?RELEASE_TRAIN_TOKEN not set (needs MR-merge rights on the protected release branches)}"
API="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}"
AUTH=(--header "PRIVATE-TOKEN: ${RELEASE_TRAIN_TOKEN}")
fail=0
for ver in $RELEASE_BRANCHES; do
  rc="rc-${ver}"
  mr=$(curl -sf "${AUTH[@]}" "${API}/merge_requests?state=opened&source_branch=${rc}&target_branch=${ver}" || echo '[]')
  iid=$(echo "$mr" | jq -r '.[0].iid // empty')
  if [ -z "$iid" ]; then echo "::: [$ver] no open ${rc} -> ${ver} MR — nothing to promote"; continue; fi
  status=$(echo "$mr" | jq -r '.[0].detailed_merge_status // .[0].merge_status // "unknown"')
  echo "::: [$ver] merging MR !${iid} (${rc} -> ${ver}); status=${status}"
  code=$(curl -s -o /tmp/rt_merge.json -w '%{http_code}' --request PUT "${AUTH[@]}" \
           --data "merge_when_pipeline_succeeds=false" "${API}/merge_requests/${iid}/merge" || echo 000)
  if [ "$code" = "200" ]; then
    echo "    [$ver] merged MR !${iid}"
  else
    echo "!!! [$ver] merge FAILED (HTTP ${code}): $(jq -r '.message // .' /tmp/rt_merge.json 2>/dev/null | head -c 200)"
    fail=1
  fi
done
[ "$fail" -eq 0 ] || { echo "release-train: one or more merges failed (conflict / not mergeable / perms)"; exit 1; }
echo "release-train: all clean"
