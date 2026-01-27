#!/bin/bash
# Build Odoo Docker image for CI/CD
# Required environment variables:
#   CI_REGISTRY, CI_DEPLOY_USER, CI_DEPLOY_PASSWORD
#   CONTAINER_IMAGE, ODOO_VERSION
# Optional:
#   COMMUNITY - set to use community Dockerfile
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

# Determine which dockerfile to use
if [[ $COMMUNITY ]]; then
  dockerfile="${ODOO_CI_DIR}/Dockerfile-community"
else
  dockerfile="${ODOO_CI_DIR}/Dockerfile"
fi

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

# Build the Docker image with date and latest tags
# Production uses: odoo-enterprise-19.0:latest
docker build \
  --no-cache \
  -f "${dockerfile}" \
  --build-arg ODOO_VERSION=${ODOO_VERSION} \
  --build-arg REGISTRY=${CI_REGISTRY} \
  --build-arg BASE_IMAGE_TAG="odoo-enterprise-${ODOO_VERSION}:latest" \
  -t "${CONTAINER_IMAGE}:${BUILD_DATE}" \
  -t "${CONTAINER_IMAGE}:latest" \
  .

# Push the production image with date and latest tags
docker push "${CONTAINER_IMAGE}:${BUILD_DATE}"
docker push "${CONTAINER_IMAGE}:latest" | tee push_output.txt

grep "digest:" push_output.txt | cut -d' ' -f3 > image-digest.txt

# Build test image if testing is enabled
if [ "$TEST_ENABLED" = "true" ]; then
  echo "Building test image..."
  # CI uses: odoo-enterprise-ci:19.0
  docker build \
    --no-cache \
    -f "${dockerfile}" \
    --build-arg ODOO_VERSION=${ODOO_VERSION} \
    --build-arg REGISTRY=${CI_REGISTRY} \
    --build-arg BASE_IMAGE_TAG="odoo-enterprise-ci:${ODOO_VERSION}" \
    -t "${CONTAINER_IMAGE}:test" \
    .
  docker push "${CONTAINER_IMAGE}:test"

  # Detect changed modules and save as artifact for test stage
  echo "Detecting changed modules..."
  CODEPENDS_FLAG=""
  if [ "$TEST_INCLUDE_CODEPENDS" = "true" ]; then
    CODEPENDS_FLAG="--include-codepends"
    pip install --no-cache-dir manifestoo || true
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
