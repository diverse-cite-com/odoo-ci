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

# Check docker is running or fail fast
docker image ls

# Login to Docker registry or fail fast
echo "${CI_DEPLOY_PASSWORD}" | docker login ${CI_REGISTRY} -u "${CI_DEPLOY_USER}" --password-stdin

# Set up SSH
source "${SCRIPT_DIR}/setup_ssh.sh"

# Clone submodules
git submodule update --init --recursive --recommend-shallow

# Single Dockerfile for both enterprise and community
# (docker-bake.hcl handles base image selection via COMMUNITY flag)
dockerfile="${ODOO_CI_DIR}/Dockerfile"

# Prepare the build context
if [ -f ".odoo-deploy/odoo-ci/prepare-build.sh" ]; then
  bash .odoo-deploy/odoo-ci/prepare-build.sh
fi

# Convert symlinks in ./addons to full folders
home="$(pwd)"
if [ -d "./addons" ] && [ "$(ls -A ./addons)" ]; then
  mkdir -p ./addons-new \
  && cd ./addons-new \
  && find ../addons -type l \
    -exec sh -c 'target=$(readlink {}); link_name=$(basename {}); mv ${target#./} $link_name; rm {}' \;
fi

cd "$home"
if [ -d "./addons-new" ] && [ "$(ls -A ./addons-new)" ]; then
  mv -f ./addons-new/* ./addons/ \
    && rm -rf addons-new
fi

# Set build date
BUILD_DATE=$(date +%Y-%m-%d)

# Ensure optional files exist (empty is fine, Dockerfile handles it)
touch requirements.txt build-packages.txt runtime-packages.txt

# Setup buildx
docker buildx create --use --name builder 2>/dev/null || docker buildx use builder

# Determine which target group to build
if [ -n "$TEST_BRANCHES" ] || [ "$TEST_ENABLED" = "true" ]; then
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
docker buildx imagetools inspect "${CONTAINER_IMAGE}:latest" --format '{{json .Manifest.Digest}}' | tr -d '"' > image-digest.txt

# Detect changed modules if testing is enabled
if [ -n "$TEST_BRANCHES" ] || [ "$TEST_ENABLED" = "true" ]; then
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
