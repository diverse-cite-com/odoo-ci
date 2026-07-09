#!/bin/bash
# Build Odoo Docker image for CI/CD
# Required environment variables:
#   CI_REGISTRY, CI_DEPLOY_USER, CI_DEPLOY_PASSWORD
#   CONTAINER_IMAGE, ODOO_VERSION
# Optional:
#   COMMUNITY - set to use community base image
#   TEST_ENABLED - set to "true" to also build test image

set -ex

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODOO_CI_DIR="$(dirname "$SCRIPT_DIR")"

# Validate essential environment variables
if [ -z "$CI_REGISTRY" ] || [ -z "$CI_DEPLOY_USER" ] || [ -z "$CI_DEPLOY_PASSWORD" ]; then
  echo "ERROR: Missing required Docker registry credentials"
  exit 1
fi

# Check the dind daemon is up or fail fast.
docker image ls

# Login to the registry: writes ~/.docker/config.json, which buildx/dind uses
# per-build as THIS job's registry credentials (pull the base image, push the
# result). The builder holds no static credentials of its own.
echo "${CI_DEPLOY_PASSWORD}" | docker login ${CI_REGISTRY} -u "${CI_DEPLOY_USER}" --password-stdin

# Set up SSH
source "${SCRIPT_DIR}/setup_ssh.sh"

# Clone submodules.
# --jobs 8: fetch the (~18) submodules in parallel rather than serially.
#   This was ~3m40s of wall-clock on durpro builds, dominated by per-repo
#   SSH round-trips, and is the single largest pre-Docker cost in the build.
# --recommend-shallow: honour `shallow = true` in .gitmodules where set.
#   We deliberately do NOT force a blanket --depth 1: several submodules are
#   pinned at non-tip SHAs, and a forced shallow fetch can fail to contain
#   that commit ("reference is not a tree"). odoo-ci-dind.yaml deepens
#   submodules later (git submodule foreach ... fetch --unshallow) for the
#   staging_diff step.
#
# Self-healing: the CI cache restores .repos/ + .git/modules/ together, but if
# that pair is ever inconsistent (e.g. a partial/old cache, or a submodule
# gitlink whose .git/modules dir is missing, or a stale cache restored over a
# fresh checkout after a pointer bump) the update aborts. Rather than fail the
# build, deinit + purge the submodule state and re-clone clean.
#
# Same-host private submodules over https: the runner only injects credentials
# during GetSources — inside the job, only SSH is configured (setup_ssh.sh).
# Any in-job clone of a private https submodule (the self-heal path, or a
# relative URL resolved against a plain-https origin) dies with "could not
# read Username". Reuse the job token so those clones authenticate.
if [ -n "${CI_JOB_TOKEN:-}" ] && [ -n "${CI_SERVER_HOST:-}" ]; then
  git config --global url."https://gitlab-ci-token:${CI_JOB_TOKEN}@${CI_SERVER_HOST}/".insteadOf "https://${CI_SERVER_HOST}/"
fi
# Ephemeral CI clone: a fetch can spawn a detached `git gc --auto` that keeps
# writing to .git/modules/<sub> while the self-heal purge runs, so rm -rf
# fails "Directory not empty" and the retry then mistakes the half-deleted
# module dir for an existing clone ("not a git repository" / "BUG: submodule
# considered for cloning"). No maintenance needed on a throwaway checkout.
git config --global gc.auto 0
git submodule sync --recursive || true
if ! git submodule update --init --recursive --recommend-shallow --jobs 8; then
  echo "submodule update failed (stale/inconsistent cache?) - purging and retrying clean"
  git submodule deinit -f --all || true
  # Belt over the gc.auto suspenders: retry the purge if a straggling git
  # process (spawned before gc was disabled) still holds the dir open.
  for _ in 1 2 3 4 5; do
    rm -rf .git/modules/.repos .repos && break
    echo "purge incomplete (background git maintenance?) - retrying"
    sleep 3
  done
  git submodule update --init --recursive --recommend-shallow --jobs 8
fi

# Single Dockerfile for both enterprise and community
# (docker-bake.hcl handles base image selection via COMMUNITY flag)
dockerfile="${ODOO_CI_DIR}/Dockerfile"

# Prepare the build context
if [ -f ".odoo-deploy/odoo-ci/prepare-build.sh" ]; then
  bash .odoo-deploy/odoo-ci/prepare-build.sh
fi

# Materialize symlinks in ./addons into real directories.
#
# The Dockerfile downstream does `COPY ./addons /mnt/extra-addons`, which
# preserves symlinks as symlinks. Anything pointing into `.repos/` would
# end up dangling inside the image since `.repos/` is not part of the
# COPY context.
#
# Previous implementation used `find -exec mv` + a fragile `${target#./}`
# strip that handled `./relative` paths only. That silently failed on
# `../path` and `../../path` symlinks (they'd be `mv`'d outside the repo,
# the mv would error, find would not propagate the failure, and the
# original symlink would survive into the image as a dangling link).
# We hit this in diverse-odoo on `bemade_mail_gateway`, `login_as_any_user`
# and `auto_database_backup`, which had `../../.repos/...` targets.
#
# New behaviour:
#   * iterate symlinks at ./addons/* (any depth would be unusual)
#   * resolve via `readlink -f` (absolute path, follows chains)
#   * fail loudly if any symlink is broken — don't ship a known-bad image
#   * cp -RL the resolved target into ./addons/<basename> so the COPY in
#     the Dockerfile picks up real directories
if [ -d "./addons" ]; then
  broken_count=0
  for link in $(find ./addons -maxdepth 1 -type l); do
    if [ ! -e "$link" ]; then
      target=$(readlink "$link")
      echo "ERROR: broken addons symlink: $link -> $target" >&2
      echo "  resolved against repo root: $(readlink -f "$link" 2>&1 || echo '(unresolvable)')" >&2
      broken_count=$((broken_count + 1))
      continue
    fi
    target_abs="$(readlink -f "$link")"
    base="$(basename "$link")"
    rm "$link"
    cp -RL "$target_abs" "./addons/$base"
  done
  if [ "$broken_count" -gt 0 ]; then
    echo "ERROR: $broken_count broken symlink(s) under ./addons/ — refusing to build" >&2
    echo "  Fix the symlink targets (paths are relative to ./addons/) or remove them." >&2
    exit 1
  fi
fi

# Set build date
BUILD_DATE=$(date +%Y-%m-%d)

# Ensure optional files exist (empty is fine, Dockerfile handles it)
touch requirements.txt build-packages.txt runtime-packages.txt

# Ensure ./vendored exists so the Dockerfile's `COPY ./vendored` never fails on a
# repo that hasn't been migrated to vendoring yet (empty dir is fine). Vendored
# addons are already real dirs, so — unlike ./addons — they need no symlink
# materialization above.
mkdir -p vendored

# Set up a job-local buildx builder inside dind. `docker buildx create` (no
# --driver) uses the docker-container driver, which — unlike the built-in
# `docker` driver — supports registry cache export (--cache-to below). The
# builder is disposable per job; cross-build layer reuse comes from the
# registry-backed cache, not a persistent daemon.
docker buildx create --use --name builder 2>/dev/null || docker buildx use builder

# Determine which target group to build
if [ "$CI_PIPELINE_SOURCE" = "merge_request_event" ]; then
  # MR pipelines: only build the test image, skip production tags
  BAKE_TARGET="test-only"
elif [ -n "$TEST_BRANCHES" ] || [ "$TEST_ENABLED" = "true" ]; then
  BAKE_TARGET="with-test"
else
  BAKE_TARGET="default"
fi

# Registry-backed layer cache. Unchanged base/apt/pip/COPY layers are imported
# from a per-target cache ref in THIS image's own repo instead of being rebuilt
# — this is what replaces the retired persistent buildkitd's warm PVC cache.
# Per-target refs (production and test build on DIFFERENT base images, so a
# shared ref would thrash). mode=max also caches intermediate layers. The first
# build is a cache miss (ref absent) and proceeds normally, seeding the cache.
#
# All cache refs live in ${CONTAINER_IMAGE}'s own repo, so this job's deploy
# token covers pull+push and no cross-repo (foreign-scope) token is ever
# requested — which is exactly why the direct `--push` below is safe again now
# that the builder is a fresh per-job dind with no other projects' layers in its
# store (the reason the old shared-buildkitd path had to detour via skopeo).
CACHE_FLAGS=(
  --set "production.cache-from=type=registry,ref=${CONTAINER_IMAGE}:buildcache-prod"
  --set "production.cache-to=type=registry,ref=${CONTAINER_IMAGE}:buildcache-prod,mode=max"
  --set "test.cache-from=type=registry,ref=${CONTAINER_IMAGE}:buildcache-test"
  --set "test.cache-to=type=registry,ref=${CONTAINER_IMAGE}:buildcache-test,mode=max"
)

ODOO_VERSION=${ODOO_VERSION} \
REGISTRY=${CI_REGISTRY} \
CONTAINER_IMAGE=${CONTAINER_IMAGE} \
BUILD_DATE=${BUILD_DATE} \
COMMUNITY=${COMMUNITY:-} \
BUILDX_BAKE_ENTITLEMENTS_FS=0 \
docker buildx bake --push \
  --set "*.platform=linux/amd64" \
  --set "*.dockerfile=${dockerfile}" \
  --set "*.pull=true" \
  "${CACHE_FLAGS[@]}" \
  --provenance=false \
  --sbom=false \
  -f "${ODOO_CI_DIR}/docker-bake.hcl" \
  ${BAKE_TARGET}
# --set "*.pull=true": re-resolve the FROM tag (e.g. odoo-enterprise-ci:19.0,
# a MUTABLE tag) against the registry on every build. The persistent buildkitd
# otherwise reuses its cached tag->digest resolution and could build on a stale
# base for up to the GC window when docker-odoo-enterprise re-pushes the tag.
# This is only a cheap manifest check: if the digest is unchanged the cached
# layers are reused (no re-download); if it moved, only the delta is pulled.

# Record the pushed manifest digest for the downstream deploy job. `bake --push`
# above already uploaded every tag defined in docker-bake.hcl (production:
# :BUILD_DATE + :latest, test: :test) straight to ${CONTAINER_IMAGE}'s repo.
if [ "$CI_PIPELINE_SOURCE" = "merge_request_event" ]; then
  DIGEST_TAG="test"
else
  DIGEST_TAG="latest"
fi
docker buildx imagetools inspect "${CONTAINER_IMAGE}:${DIGEST_TAG}" --format '{{json .Manifest.Digest}}' | tr -d '"' > image-digest.txt

# Detect changed modules if testing is enabled. The alpine `docker` job image
# has no python3, so guard on its presence: without it we just emit an empty
# list (changed-modules.txt is currently advisory and unconsumed downstream).
# This avoids the recurring "python3: command not found" noise in build logs.
if [ "$CI_PIPELINE_SOURCE" = "merge_request_event" ] || [ -n "$TEST_BRANCHES" ] || [ "$TEST_ENABLED" = "true" ]; then
  if command -v python3 >/dev/null 2>&1; then
    echo "Detecting changed modules..."
    CODEPENDS_FLAG=""
    if [ "$TEST_INCLUDE_CODEPENDS" = "true" ]; then
      CODEPENDS_FLAG="--include-codepends"
    fi

    python3 "${SCRIPT_DIR}/detect_changed_modules.py" \
      --addons-dir ./addons \
      --vendored-dir ./vendored \
      --base-ref "${CI_COMMIT_BEFORE_SHA:-HEAD~1}" \
      --head-ref "${CI_COMMIT_SHA:-HEAD}" \
      --output comma \
      --verbose \
      $CODEPENDS_FLAG > changed-modules.txt || echo "" > changed-modules.txt
  else
    echo "python3 not available in build image - skipping changed-module detection."
    echo "" > changed-modules.txt
  fi

  echo "Changed modules: $(cat changed-modules.txt)"
fi
