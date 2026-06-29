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

# Builds run against the persistent in-cluster BuildKit (remote buildx
# driver), so there is no local Docker daemon to probe. `docker login` below
# only writes ~/.docker/config.json, which buildx forwards to the remote
# buildkitd as THIS job's registry credentials (to pull the base image and
# push the result). buildkitd holds no static credentials of its own.
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
# gitlink whose .git/modules dir is missing) the update aborts with "could not
# get a repository handle". Rather than fail the build, deinit + purge the
# submodule state and re-clone clean.
git submodule sync --recursive || true
if ! git submodule update --init --recursive --recommend-shallow --jobs 8; then
  echo "submodule update failed (stale/inconsistent cache?) - purging and retrying clean"
  git submodule deinit -f --all || true
  rm -rf .git/modules/.repos .repos || true
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

# Setup buildx against the persistent in-cluster BuildKit daemon (remote
# driver) instead of a throwaway dind buildkit. This keeps a warm layer cache
# (base image, apt, pip) on the buildkitd PVC across builds, so the base image
# isn't re-pulled and unchanged layers are reused. BUILDKIT_HOST defaults to
# the in-cluster service and is overridable (e.g. for local testing).
BUILDKIT_HOST="${BUILDKIT_HOST:-tcp://buildkitd.gitlab-runner.svc.cluster.local:1234}"
docker buildx create --name remote-builder --driver remote --use "${BUILDKIT_HOST}" 2>/dev/null \
  || docker buildx use remote-builder
# Fail fast if buildkitd is unreachable (replaces the old `docker image ls`
# daemon check, which is meaningless now there is no local daemon).
docker buildx inspect --bootstrap

# Determine which target group to build
if [ "$CI_PIPELINE_SOURCE" = "merge_request_event" ]; then
  # MR pipelines: only build the test image, skip production tags
  BAKE_TARGET="test-only"
elif [ -n "$TEST_BRANCHES" ] || [ "$TEST_ENABLED" = "true" ]; then
  BAKE_TARGET="with-test"
else
  BAKE_TARGET="default"
fi

# Build and push all targets in one command using buildx bake
ODOO_VERSION=${ODOO_VERSION} \
REGISTRY=${CI_REGISTRY} \
CONTAINER_IMAGE=${CONTAINER_IMAGE} \
BUILD_DATE=${BUILD_DATE} \
COMMUNITY=${COMMUNITY:-} \
BUILDX_BAKE_ENTITLEMENTS_FS=0 \
docker buildx bake --push \
  --set "*.platform=linux/amd64" \
  --set "*.dockerfile=${dockerfile}" \
  --provenance=false \
  --sbom=false \
  -f "${ODOO_CI_DIR}/docker-bake.hcl" \
  ${BAKE_TARGET}

# Get the digest from the pushed image
if [ "$CI_PIPELINE_SOURCE" = "merge_request_event" ]; then
  DIGEST_TAG="test"
else
  DIGEST_TAG="latest"
fi
docker buildx imagetools inspect "${CONTAINER_IMAGE}:${DIGEST_TAG}" --format '{{json .Manifest.Digest}}' | tr -d '"' > image-digest.txt

# Detect changed modules if testing is enabled
if [ "$CI_PIPELINE_SOURCE" = "merge_request_event" ] || [ -n "$TEST_BRANCHES" ] || [ "$TEST_ENABLED" = "true" ]; then
  echo "Detecting changed modules..."
  CODEPENDS_FLAG=""
  if [ "$TEST_INCLUDE_CODEPENDS" = "true" ]; then
    CODEPENDS_FLAG="--include-codepends"
  fi

  python3 "${SCRIPT_DIR}/detect_changed_modules.py" \
    --addons-dir ./addons \
    --base-ref "${CI_COMMIT_BEFORE_SHA:-HEAD~1}" \
    --head-ref "${CI_COMMIT_SHA:-HEAD}" \
    --output comma \
    --verbose \
    $CODEPENDS_FLAG > changed-modules.txt || echo "" > changed-modules.txt

  echo "Changed modules: $(cat changed-modules.txt)"
fi
