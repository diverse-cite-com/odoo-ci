#!/usr/bin/env bash
# release_train.sh — daily "release train" promotion.
#
# For each release branch named in RELEASE_BRANCHES (space-separated, e.g.
# "18.0 19.0"), if a release-candidate branch `rc-<branch>` exists, merge it
# INTO the release branch and push, then sync the rc branch back up to the
# release branch (so rc stays "release + pending-approved", never drifting).
#
# The task-worker accumulates client-approved tasks onto `rc-<branch>` via
# cherry-pick (RC-promotion scan); this job is step 6 — promoting that approved
# subset to the live release branch on a daily schedule.
#
# A conflict in either direction aborts THAT branch and fails the job (so GitLab
# notifies); other release branches still process. Requires the .setup_ssh key
# to have push rights to the (protected) release branches.
#
# Env: RELEASE_BRANCHES (required), CI_PROJECT_PATH (GitLab-provided).
set -uo pipefail
: "${RELEASE_BRANCHES:?RELEASE_BRANCHES not set (e.g. \"18.0 19.0\")}"

git config user.email "release-train@bemade.org"
git config user.name  "release-train"
# Push over SSH (CI_JOB_TOKEN can't push protected branches); .setup_ssh keyed it.
git remote set-url --push origin "git@git.bemade.org:${CI_PROJECT_PATH}.git"
git fetch --prune --quiet origin

fail=0
for ver in $RELEASE_BRANCHES; do
  rc="rc-${ver}"
  if ! git ls-remote --exit-code --heads origin "$rc" >/dev/null 2>&1; then
    echo "::: [$ver] no $rc branch yet — nothing to promote"; continue
  fi
  echo "::: [$ver] promote $rc -> $ver"
  git checkout -B "$ver" "origin/$ver" --quiet
  if ! git merge --no-ff --no-edit -m "release-train: promote $rc -> $ver [skip ci]" "origin/$rc"; then
    echo "!!! [$ver] CONFLICT merging $rc into $ver — aborting this branch"; git merge --abort; fail=1; continue
  fi
  if ! git push origin "HEAD:$ver"; then echo "!!! [$ver] push to $ver FAILED"; fail=1; continue; fi
  # Keep rc current with the (now-advanced) release branch — fast-forward expected.
  git checkout -B "$rc" "origin/$rc" --quiet
  if git merge --ff-only "origin/$ver" --quiet 2>/dev/null; then
    git push origin "HEAD:$rc" || echo "    [$ver] (warn) sync push of $rc failed"
  else
    echo "    [$ver] (warn) $rc not fast-forwardable to $ver — left as-is; will reconcile next run"
  fi
  echo "    [$ver] promoted OK"
done
[ "$fail" -eq 0 ] || { echo "release-train: one or more branches failed"; exit 1; }
echo "release-train: all clean"
