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

### 4. Review Environments (MR Preview)

Ephemeral Odoo instances for merge request review, similar to Odoo's runbot.

**Runs when:** `REVIEW_ENABLED` is `"true"` and MR targets a `MR_TARGET_BRANCHES` branch

**How it works:**
1. MR is created → build + test run as normal
2. `review-deploy` creates an OdooInstance with demo data in the `odoo-review` namespace, pinned to the build job's exact image digest (`image-digest.txt` artifact, not the mutable `:test` tag — OdooInstance pods default to `imagePullPolicy: IfNotPresent`, so a mutable tag would let a node keep serving whatever it cached the first time forever, regardless of how many times `:test` gets re-pushed), and posts a "provisioning" comment on the MR
3. `review-wait` polls the instance until it's actually `Running`, then posts a follow-up "Ready" comment. If the operator reports `InitFailed`, it posts a "Failed" comment (with the init job's log attached as a pipeline artifact) and **deletes the instance** — the operator never auto-retries a failed init job on its own, so leaving it in place would mean it stays broken forever even after a fix is pushed; deleting it lets the next `review-deploy` start completely fresh. If it's still not `Running` after `REVIEW_READY_TIMEOUT_SECONDS` without an explicit failure, it posts a "still not ready" comment instead (and leaves the instance alone — it may just be slow)
4. When the MR is merged or closed, GitLab auto-triggers `review-stop` which deletes the instance and cleans up the container image

**Setup:**

1. Create the review namespace (one-time):
   ```bash
   kubectl create ns odoo-review
   ```

2. Ensure wildcard DNS `*.review.bemade.org` resolves to the cluster ingress

3. Set the CI variable in your project:
   ```
   REVIEW_ENABLED = "true"
   ```

4. (Optional) If your module set installs slowly (large addon/demo-data
   count), raise the wait timeout — default is 1800s (30 min):
   ```
   REVIEW_READY_TIMEOUT_SECONDS = "3600"
   ```

**Review instance details:**
- URL: `https://mr-{MR_IID}-{PROJECT_SLUG}.review.bemade.org`
- Login: `admin` / `admin`
- Modules installed: changed modules detected from the MR diff (with demo data)
- Resources: 1 CPU / 2Gi memory limit
- Labeled with `app.kubernetes.io/part-of: odoo-review` for easy identification

**Cleanup:** Instances are automatically deleted when the MR is merged or closed via GitLab's environment stop mechanism. You can also manually stop the environment from the GitLab UI (Deployments > Environments).

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
| `scripts/odoo_log_to_junit.py` | Converts Odoo test logs to JUnit XML, and gates on silently skipped browser tests |
| `scripts/arc-finish.sh` | Installs a GitHub Actions runner scale set for a client (see `docs/github-actions-runner.md`) |
| `arc-*-values.yaml` | Helm values for each GitHub Actions runner scale set |
| `test-local.sh` | Local testing script for CI images |

## Build Performance

The build stage uses **Docker BuildKit** (`buildx bake`) on a **job-local
`docker:dind`** builder (docker-container driver), with layer reuse across
builds coming from a **registry-backed cache** rather than a persistent daemon:

- **Parallel builds**: production and test images build simultaneously
- **Registry layer cache**: each target imports/exports a project-scoped cache
  ref (`<project>/odoo-buildcache:prod` / `:test`, `mode=max`), so unchanged
  base/apt/pip/COPY layers are reused without a persistent local cache. The ref
  is shared across **all branches** (keyed on the project, not the branch), so a
  new feature-branch/MR build reuses the base/apt/pip layers a prior build wrote
  instead of cold-starting — only the very first build of a project is a full
  miss. Per-target because prod and test build on different base images.
- **No shared build daemon**: the builder is disposable per job, so there is no
  singleton to wedge (the old persistent in-cluster `buildkitd` was retired
  after recurring lockfile/netns/boot-reconcile outages). Each job's own deploy
  token covers pull/push of both the image and its cache refs.
- **Parallel submodule clone**: `--jobs 8` so the project's submodules fetch
  concurrently instead of serially.

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
| `MR_TARGET_BRANCHES` | Variable | Regex: MR target branches that trigger build+test |
| `REVIEW_ENABLED` | Variable | Set to `"true"` to enable review environments for MRs |
| `PROD_BRANCHES` | Variable | Regex: branches treated as production for the release-train gate (`mr_up_to_date` + `revalidate_prod_mrs`). Unset = no gate |

### Release-train gate (`PROD_BRANCHES`)

GitLab CE has no merge trains and no merged-results pipelines, so "this MR's
green describes what will actually land" has to be built by hand. Setting
`PROD_BRANCHES` (a regex over the MR's **target** branch) turns on two jobs:

- **`mr_up_to_date`** — fails a prod-targeted MR whose branch does not contain
  the current tip of prod. Combined with *Pipelines must succeed*, a mergeable
  green is by definition a green against current prod. The fix is
  `git merge origin/<prod>`, **never** a rebase: under the release-train model
  the same branch merges into both staging and prod, and rebasing binds it to
  one of them.
- **`revalidate_prod_mrs`** — on a push to prod, starts a fresh pipeline on every
  still-open MR targeting it, so their now-stale greens stop being mergeable.
  Needs `CI_BOT_TOKEN` (api scope). `allow_failure: true` — a missed
  revalidation degrades the gate but must not redden a merge that already
  landed.

Together they serialize prod merges by *invalidation* rather than by queue. The
residual hole is registration latency: between a merge landing and the new
pipelines registering, another already-green MR can still be merged. That
requires two approvals landing seconds apart, and the failure mode is an
untested *combination*, not untested code.

Also set `allow_merge_on_skipped_pipeline: false` on the project — "skipped
counts as success" defeats the whole gate.

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ODOO_VERSION` | `18.0` | Odoo version for base image |
| `ODOO_CI_REF` | `main` | Branch of odoo-ci repo to use |
| `REVIEW_READY_TIMEOUT_SECONDS` | `1800` | How long `review-wait` polls before giving up on a review instance becoming `Running` |

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
