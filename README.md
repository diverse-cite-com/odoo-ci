# Odoo CI/CD Pipeline

Shared GitLab CI/CD pipeline for building, testing, and deploying Odoo applications.

## Quick Start

Include this pipeline in your project's `.gitlab-ci.yml`:

```yaml
variables:
  ODOO_VERSION: "18.0"
  ALLOWED_BRANCHES: "18.0"
  # Enable testing (optional)
  TEST_ENABLED: "true"
  TEST_INCLUDE_CODEPENDS: "true"

include:
  - project: 'bemade/odoo-ci'
    ref: 'main'
    file: 'odoo-ci-dind.yaml'
```

## Pipeline Stages

### 1. Build Stage

Builds a Docker image with your Odoo addons based on the enterprise base image.

**Always runs** on allowed branches.

### 2. Test Stage (Optional)

Runs Odoo tests on modules that have changed since the last commit.

**Configuration Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `TEST_ENABLED` | `false` | Set to `"true"` to enable testing |
| `TEST_INCLUDE_CODEPENDS` | `false` | Set to `"true"` to also test modules that depend on changed modules |
| `TEST_DATABASE` | `test_ci` | Name of the test database |

**How it works:**

1. Detects which modules changed using `git diff`
2. Handles both direct modules in `addons/` and symlinked modules from `.repos/`
3. Optionally finds co-dependent modules using [manifestoo](https://github.com/acsone/manifestoo)
4. Runs `odoo --init=<modules> --test-enable --stop-after-init`

### 3. Deploy Stage (Optional)

Deploys the built image to a Kubernetes cluster by patching an OdooInstance CRD.

**Configuration Variables:**

| Variable | Required | Description |
|----------|----------|-------------|
| `KUBECTL_NAMESPACE` | Yes | Kubernetes namespace |
| `ODOO_INSTANCE_NAME` | Yes | Name of the OdooInstance resource |
| `STAGING_BRANCH` | Yes | Branch that triggers deployment |

## Module Detection

The test stage automatically detects which Odoo modules have changed:

### Direct Modules
Changes in `addons/<module_name>/` are detected directly.

### Symlinked Modules
Changes in `.repos/<submodule>/<module_name>/` are detected if the module is symlinked in `addons/`.

### Co-dependencies
With `TEST_INCLUDE_CODEPENDS=true`, modules that depend on changed modules are also tested. This uses the [manifestoo](https://github.com/acsone/manifestoo) tool.

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
| `Dockerfile` | Enterprise Odoo image with addons |
| `Dockerfile-community` | Community Odoo image with addons |
| `Dockerfile-test` | Extended image with testing tools |
| `scripts/detect_changed_modules.py` | Module detection script |
| `scripts/test_detect_changed_modules.py` | Unit tests for detection script |

## CI/CD Variables

Set these in your GitLab project settings:

| Variable | Description |
|----------|-------------|
| `SSH_KEY_GITHUB` | SSH key file for accessing private repos |
| `CI_DEPLOY_USER` | Docker registry username |
| `CI_DEPLOY_PASSWORD` | Docker registry password |

## Local Development

### Running Module Detection Locally

```bash
# Detect changed modules
python scripts/detect_changed_modules.py \
  --addons-dir ./addons \
  --base-ref origin/main \
  --head-ref HEAD \
  --verbose

# Include co-dependencies (requires manifestoo)
pip install manifestoo
python scripts/detect_changed_modules.py \
  --addons-dir ./addons \
  --include-codepends \
  --verbose
```

### Running Tests

```bash
cd scripts
pip install pytest
pytest test_detect_changed_modules.py -v
```

## Future Enhancements

- **Upgrade workflow**: Run `odoo -u <modules>` to verify upgrade scripts
- **Coverage reports**: Generate test coverage reports
- **Parallel testing**: Split tests across multiple jobs
- **MR testing**: Run tests on merge requests before merging
