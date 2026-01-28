#!/bin/bash
#
# Local testing script for odoo-ci images
# Tests the CI images locally before pushing to GitLab
#
# Usage:
#   ./test-local.sh community    # Test community-ci image
#   ./test-local.sh enterprise   # Test enterprise-ci image  
#   ./test-local.sh bemade       # Test with bemade-site addons
#   ./test-local.sh all          # Test all in parallel
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ODOO_VERSION="${ODOO_VERSION:-19.0}"
REGISTRY="${REGISTRY:-registry.bemade.org:443}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Default test mode
TEST_MODE="${1:-community}"

case "$TEST_MODE" in
  community)
    IMAGE="ghcr.io/oca/oca-ci/py3.10-odoo${ODOO_VERSION}:latest"
    log_info "Testing OCA community-ci image: $IMAGE"
    ;;
  community-local)
    IMAGE="odoo-community-ci:${ODOO_VERSION}"
    log_info "Testing locally built community-ci image: $IMAGE"
    ;;
  enterprise)
    IMAGE="${REGISTRY}/bemade/docker-odoo-enterprise/odoo-enterprise-ci:${ODOO_VERSION}"
    log_info "Testing enterprise-ci image: $IMAGE"
    ;;
  bemade)
    IMAGE="${REGISTRY}/bemade/bemade-site:test"
    log_info "Testing bemade-site test image: $IMAGE"
    ;;
  all)
    log_info "Running all tests in parallel..."
    $0 community &
    PID1=$!
    $0 enterprise &
    PID2=$!
    
    FAILED=0
    wait $PID1 || FAILED=1
    wait $PID2 || FAILED=1
    
    if [ $FAILED -eq 0 ]; then
      log_info "All parallel tests passed!"
    else
      log_error "Some parallel tests failed"
    fi
    exit $FAILED
    ;;
  *)
    log_error "Unknown test mode: $TEST_MODE"
    echo "Usage: $0 [community|community-local|enterprise|bemade|all]"
    exit 1
    ;;
esac

# Unique container name for parallel runs
CONTAINER_SUFFIX="${TEST_MODE}-$$"
PG_CONTAINER="odoo-ci-postgres-${CONTAINER_SUFFIX}"

# Log file
LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/test-${TEST_MODE}-$(date +%Y%m%d-%H%M%S).log"
log_info "Logging to: ${LOG_FILE}"

# Start postgres container
log_info "Starting PostgreSQL container: ${PG_CONTAINER}..."
docker rm -f "${PG_CONTAINER}" 2>/dev/null || true
docker run -d \
  --name "${PG_CONTAINER}" \
  -e POSTGRES_USER=odoo \
  -e POSTGRES_PASSWORD=odoo \
  -e POSTGRES_DB=odoo \
  postgres:16

# Wait for postgres
log_info "Waiting for PostgreSQL to be ready..."
sleep 3

# Get postgres IP
POSTGRES_IP=$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${PG_CONTAINER}")
log_info "PostgreSQL IP: $POSTGRES_IP"

# Run the CI tests
log_info "Running CI tests..."
docker run --rm \
  -e PGHOST=$POSTGRES_IP \
  -e PGUSER=odoo \
  -e PGPASSWORD=odoo \
  -e PGDATABASE=odoo \
  -e ADDONS_DIR=/mnt/test-addons \
  -v "${SCRIPT_DIR}/tests/test-addon:/mnt/test-addons/test_addon" \
  "$IMAGE" \
  bash -c '
    set -e
    echo "=== OCA CI Test Run ==="
    echo "Odoo version: $(odoo --version 2>/dev/null || echo unknown)"
    echo "Python version: $(python --version)"
    echo "Chrome version: $(google-chrome --version 2>/dev/null || echo not installed)"
    
    # Wait for postgres
    oca_wait_for_postgres
    
    # List addons
    ADDONS=$(manifestoo --select-addons-dir /mnt/test-addons list --separator=, 2>/dev/null || echo "")
    if [ -z "$ADDONS" ]; then
      echo "No addons found in /mnt/test-addons"
      echo "Running basic Odoo startup test instead..."
      odoo -d odoo --stop-after-init --no-http
      echo "=== Basic startup test passed ==="
      exit 0
    fi
    
    echo "Addons to test: $ADDONS"
    
    # OCA two-step approach:
    # 1. Install dependencies WITHOUT --test-enable
    # 2. Install/upgrade our addons WITH --test-enable
    
    # Step 1: Install dependencies only
    echo "Installing dependencies (without tests)..."
    DEPS=$(manifestoo --select-addons-dir /mnt/test-addons list-depends --separator=, 2>/dev/null || echo "base")
    echo "Dependencies: ${DEPS:-base}"
    unbuffer $(which odoo) \
      -d odoo \
      -i ${DEPS:-base} \
      --http-interface=127.0.0.1 \
      --stop-after-init | oca_checklog_odoo
    
    # Step 2: Run tests on our addons only
    echo "Running tests on our addons..."
    unbuffer coverage run --include "/mnt/test-addons/*" --branch \
      $(which odoo) \
      -d odoo \
      -i ${ADDONS} \
      --test-enable \
      --http-interface=127.0.0.1 \
      --stop-after-init | oca_checklog_odoo
    
    echo "=== Coverage Report ==="
    coverage report || true
    
    echo "=== Tests completed successfully ==="
  ' 2>&1 | tee "${LOG_FILE}"

# Get exit code from docker, not tee
TEST_EXIT_CODE=${PIPESTATUS[0]}

# Cleanup
log_info "Cleaning up..."
docker rm -f "${PG_CONTAINER}" 2>/dev/null || true

if [ $TEST_EXIT_CODE -eq 0 ]; then
  log_info "All tests passed!"
else
  log_error "Tests failed with exit code: $TEST_EXIT_CODE"
fi

exit $TEST_EXIT_CODE
