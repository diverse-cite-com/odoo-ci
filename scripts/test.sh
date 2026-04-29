#!/bin/bash
# Run Odoo tests on changed modules
# Required environment variables:
#   PGHOST, PGUSER, PGPASSWORD, PGDATABASE - PostgreSQL connection
# Optional:
#   TEST_INCLUDE_CODEPENDS - set to "true" to test co-dependent modules
#   TEST_DATABASE - test database name (default: test_ci)
#   CI_COMMIT_BEFORE_SHA, CI_COMMIT_SHA - git refs for change detection

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Odoo Module Testing ==="

# Read changed modules from build stage artifact
if [ -f "${CI_PROJECT_DIR}/changed-modules.txt" ]; then
  CHANGED_MODULES=$(cat "${CI_PROJECT_DIR}/changed-modules.txt")
else
  echo "ERROR: changed-modules.txt not found - build stage may have failed"
  exit 1
fi

if [ -z "$CHANGED_MODULES" ]; then
  echo "No Odoo modules changed - skipping tests"
  exit 0
fi

echo "Modules to test: $CHANGED_MODULES"

# Set test database name
TEST_DB="${TEST_DATABASE:-test_ci}"

# Wait for PostgreSQL
echo "Waiting for PostgreSQL..."
for i in $(seq 1 30); do
  if pg_isready -h "${PGHOST:-postgres}" -U "${PGUSER:-odoo}" 2>/dev/null; then
    echo "PostgreSQL is ready"
    break
  fi
  echo "  waiting... ($i/30)"
  sleep 2
done

# Run Odoo tests with coverage
echo "Running tests for modules: $CHANGED_MODULES"
coverage run --source="/mnt/extra-addons" --branch \
  $(which odoo) \
  -d "$TEST_DB" \
  -i "$CHANGED_MODULES" \
  --test-enable \
  --stop-after-init \
  --log-level=warn

# Generate coverage report
echo "=== Coverage Report ==="
coverage report
coverage xml -o coverage.xml
coverage html -d htmlcov

echo "=== Tests completed successfully ==="
