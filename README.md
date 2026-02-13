# Odoo CI/CD Pipeline

Shared GitLab CI/CD pipeline for building, testing, and deploying Odoo applications.

## Quick Start

### Minimal Setup

Include this pipeline in your project's `.gitlab-ci.yml`:

```yaml
include:
  - project: 'bemade/odoo-ci'
    ref: 'main'
    file: 'odoo-ci-dind.yaml'
```

Then configure via **GitLab Project Variables** (Settings > CI/CD > Variables):

| Variable | Example | Description |
|----------|---------|-------------|
| `ODOO_VERSION` | `19.0` | Odoo version for base image |
| `BUILD_BRANCHES` | `19.0\|19.0-staging` | Regex: which branches trigger builds |
| `TEST_BRANCHES` | `19.0\|19.0-staging` | Regex: which branches run tests |
| `DEPLOY_BRANCHES` | `19.0` | Regex: which branches can deploy |
| `DEPLOY_TARGETS` | *(file variable)* | YAML mapping branches to k8s targets |

### Legacy Setup (Backwards Compatible)

```yaml
variables:
  ODOO_VERSION: "19.0"
  ALLOWED_BRANCHES: "19.0|19.0-staging"  # Falls back from BUILD_BRANCHES
  STAGING_BRANCH: "19.0-staging"          # Falls back from DEPLOY_BRANCHES

include:
  - project: 'bemade/odoo-ci'
    ref: 'main'
    file: 'odoo-ci-dind.yaml'
```

## Pipeline Stages

### 1. Build Stage

Builds Docker images with your Odoo addons based on the enterprise base image.

**Runs when:** `BUILD_BRANCHES` matches (or `ALLOWED_BRANCHES` fallback)

**Produces:**
- Production image: `${CI_REGISTRY_IMAGE}/odoo-${BRANCH}:latest`
- Test image: `${CI_REGISTRY_IMAGE}/odoo-${BRANCH}:test`

### 2. Test Stage

Runs Odoo tests on all installable modules in `/mnt/extra-addons`.

**Runs when:** `TEST_BRANCHES` matches current branch

**Features:**
- OCA two-step testing: installs dependencies first (without tests), then runs your addon tests
- Coverage reports (Cobertura XML + HTML)
- JUnit XML test reports for GitLab UI integration

### 3. Deploy Stage

Deploys the built image to Kubernetes by patching an OdooInstance CRD.

**Runs when:** `DEPLOY_BRANCHES` matches (or `STAGING_BRANCH` fallback)

**Deploy Target Configuration (choose one):**

#### Option 1: DEPLOY_TARGETS File Variable (Recommended)

Create a GitLab **file variable** named `DEPLOY_TARGETS` with YAML content:

```yaml
targets:
  - 19.0-staging:
      namespace: bemade
      instance: bemade-staging
  - 19.0:
      namespace: bemade
      instance: bemade-prod
```

#### Option 2: Simple CI Variables

For single-target deployments:

| Variable | Description |
|----------|-------------|
| `KUBECTL_NAMESPACE` | Kubernetes namespace |
| `ODOO_INSTANCE_NAME` | Name of the OdooInstance resource |

## Testing Approach

The test stage uses the **OCA two-step testing approach**:

1. **Install dependencies** without `--test-enable` (so their tests don't run)
2. **Install your addons** with `--test-enable` (only your tests run)

This ensures you only see test results for your own code, not upstream dependencies.

**Test artifacts:**
- `test-output.log` - Full Odoo test output
- `coverage.xml` - Cobertura coverage report
- `htmlcov/` - HTML coverage report
- `junit-report.xml` - JUnit test report for GitLab UI

## Project Structure

Your project should have this structure:

```
your-project/
├── .gitlab-ci.yml          # Include this pipeline
├── .gitmodules             # Submodule definitions
├── .repos/                 # Git submodules
│   ├── bemade-addons/
│   └── other-repos/
├── addons/                 # Your Odoo modules
│   ├── direct_module/      # Direct module
│   ├── symlinked_module -> ../.repos/bemade-addons/module  # Symlink
│   └── ...
├── requirements.txt        # Python dependencies (optional)
├── build-packages.txt      # APT build packages (optional)
└── runtime-packages.txt    # APT runtime packages (optional)
```

## Files

| File | Description |
|------|-------------|
| `odoo-ci-dind.yaml` | Main CI/CD pipeline definition |
| `Dockerfile` | Client Odoo image with addons (enterprise and community) |
| `docker-bake.hcl` | BuildKit bake file for parallel builds |
| `scripts/build.sh` | Docker image build script |
| `scripts/odoo_log_to_junit.py` | Converts Odoo test logs to JUnit XML |
| `test-local.sh` | Local testing script for CI images |

## Build Performance

The build stage uses **Docker BuildKit** with `buildx bake` for:

- **Parallel builds**: Production and test images build simultaneously
- **Inline caching**: Layers are cached in the registry for faster rebuilds
- **Latest Docker**: Uses the latest Docker-in-Docker for best performance

## CI/CD Variables

### Required Variables

Set these in your GitLab project settings (Settings > CI/CD > Variables):

| Variable | Type | Description |
|----------|------|-------------|
| `SSH_KEY_GITHUB` | File | SSH key for accessing private repos |
| `CI_DEPLOY_USER` | Variable | Docker registry username |
| `CI_DEPLOY_PASSWORD` | Variable | Docker registry password (masked) |

### Branch Control Variables

| Variable | Type | Description |
|----------|------|-------------|
| `BUILD_BRANCHES` | Variable | Regex: branches that trigger builds |
| `TEST_BRANCHES` | Variable | Regex: branches that run tests |
| `DEPLOY_BRANCHES` | Variable | Regex: branches that can deploy |
| `DEPLOY_TARGETS` | File | YAML mapping branches to k8s targets |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ODOO_VERSION` | `18.0` | Odoo version for base image |
| `ODOO_CI_REF` | `main` | Branch of odoo-ci repo to use |

## Local Testing

Test CI images locally without consuming GitLab CI resources:

```bash
# Test community-ci image
./test-local.sh community

# Test enterprise-ci image
./test-local.sh enterprise

# Test with parallel workers
./test-local.sh enterprise --parallel 4

# Test a specific client image
./test-local.sh bemade
```

## Scripts

### odoo_log_to_junit.py

Converts Odoo test output to JUnit XML format for GitLab UI integration:

```bash
python scripts/odoo_log_to_junit.py test-output.log -o junit-report.xml
```

### build.sh

Builds production and test Docker images. Called by the CI pipeline.
