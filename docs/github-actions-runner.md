# GitHub Actions test runner (ARC)

How Bemade runs Odoo tests as a **pre-merge gate on GitHub-hosted projects**,
using self-hosted runners in the Bemade Kubernetes cluster.

This is the GitHub counterpart to the GitLab pipeline in `odoo-ci-dind.yaml`.
Same cluster, same image, same tests — different forge.

---

## 1. Why this exists

Projects that moved to **GitHub + Odoo.sh** lost their pre-merge test gate, and
it is not obvious that they did.

- Odoo.sh runs tests **only on dev-stage branches**, not on staging or
  production branches.
- Odoo.sh reports **nothing back to GitHub** — no commit statuses, no check
  runs. (Verified directly against the API: every recent commit on RWI's
  `19.0-dev` and `19.0-staging` returns an empty `statuses` array.)

So there is no signal for a branch ruleset to require. And with no required
check, `gh pr merge --auto` merges a pull request **the moment it is
mergeable** — the work-plane's ship stage arms auto-merge, and nothing ever
waits for a test.

This runner supplies the missing signal. Odoo.sh keeps its role as the
**post-merge** integration check, on demo data.

---

## 2. Architecture

```
GitHub (client repo)
  │  pull_request → workflow queued
  ▼
ARC listener            (namespace arc-systems, one controller for all clients)
  │  scales up
  ▼
runner pod              (namespace arc-runners, one scale set per client)
  ├── runner container  ghcr.io/actions/actions-runner
  └── dind sidecar      pulls + runs the job container
        │
        ▼
   job container        registry.bemade.org/…/odoo-enterprise-ci:19.0
   + postgres:16 service
```

**One controller, many scale sets.** The `gha-runner-scale-set-controller`
release (`arc` in `arc-systems`) is shared. Each client gets its own
`gha-runner-scale-set` release in `arc-runners`, its own values file, and its
own credential secret.

### Why dind and not kubernetes container mode

1. **Credentials stay in the cluster.** The job image comes from private
   `registry.bemade.org`. In dind mode the runner's docker CLI authenticates
   from a mounted secret, so **no Bemade registry credentials are stored in the
   client's GitHub organisation**. Kubernetes mode would work too, but dind
   made this the default rather than a thing to remember.
2. **No RWX dependency.** Kubernetes mode needs a ReadWriteMany volume for
   `_work`. The available RWX classes here sit on storage that has been
   unreliable, and a gate that blocks every merge should not inherit that.

---

## 3. What lives where

| Thing | Location |
|---|---|
| Controller release | `helm … gha-runner-scale-set-controller`, release `arc`, ns `arc-systems` |
| Scale set values (client) | `arc-<client>-values.yaml` in this repo |
| Scale set values (internal) | `arc-gate-test-values.yaml` |
| Install/finish script | `scripts/arc-finish.sh` |
| Registry pull secret | `registry-bemade-dockerconfig`, ns `arc-runners` |
| App credential secret | `<release>-github`, ns `arc-runners` |
| Workflow | `.github/workflows/tests.yml` in each client repo |
| Skip gate script | `.github/scripts/odoo_log_to_junit.py`, vendored from `scripts/` here |
| Smoke target | `github.com/bemade/arc-gate-test` |

---

## 4. GitHub App credentials — one App per client

ARC authenticates as a **GitHub App**. Each client gets **its own App**.

Do **not** share one App across clients. ARC requires `Administration:
read/write` on the repositories it registers runners for, so a single App means
one private key holding repo-admin access across **every client organisation**
it is installed on. These are separate companies' codebases; a key compromise
should not be a cross-client event. Per-client Apps also mean rotation and
offboarding are independent.

Keep the *branding* consistent and the *instance* separate:
`Bemade CI Runner – <Client>`.

**App settings** (identical every time):

- Webhook: **inactive** (ARC polls)
- Repository permissions: `Actions` read, `Administration` **read/write**,
  `Metadata` read
- "Where can this be installed?" → **Any account** (required to install on a
  client org)

**Installing on a client org needs an org owner.** Repo admin is not enough. If
you are only an org *member*, the install becomes a request an owner must
approve — plan for that latency, and warn the client it is coming so it does
not read as phishing.

---

## 5. Onboarding a new client repo

1. **Create the App** (§4), generate a private key, install it on the client's
   repo. Note the App ID.
2. **Copy a values file** — `cp arc-rwi-values.yaml arc-<client>-values.yaml`
   and update `githubConfigUrl`, `githubConfigSecret`, `runnerScaleSetName`.
3. **Install the scale set:**
   ```bash
   APP_ID=<id> ACCOUNT=<org> RELEASE=arc-<client> \
     VALUES=arc-<client>-values.yaml scripts/arc-finish.sh <app-key.pem>
   ```
   The script resolves the installation id from the key, writes the credential
   secret, and installs the scale set. It **exits non-zero rather than guessing**
   if the installation does not exist yet, so a pending approval fails loudly
   instead of half-configuring.
4. **Add the workflow** — copy `.github/workflows/tests.yml` and
   `.github/scripts/odoo_log_to_junit.py` from an existing client repo. Set
   `runs-on:` to the scale set name and list the target branches.
5. **Prove it green on a throwaway branch first.** Do not add the required
   check until a real run has passed.
6. **Add the required status check** to the branch ruleset, matching the job's
   `name:` exactly.

---

## 6. The workflow, and the two details that are load-bearing

### `--log-handler=odoo.addons:INFO`

`OdooTestResult` logs both `Starting <test> ...` and
`skipped <test> : <reason>` at **INFO**, on the *test module's own* logger.

The GitLab template runs at `--log-level=warn` alone. At that level **neither
line is emitted** — which means the JUnit report comes out empty and skipped
tests are undetectable. Raising just `odoo.addons` to INFO keeps the log
manageable while making test results visible at all.

### The skip gate

When headless Chrome cannot start, Odoo raises `unittest.SkipTest`. There are
six such exits on the browser path in `odoo/tests/common.py` (19.0), including
plain `Chrome executable not found`.

`OdooTestResult.wasSuccessful()` is `failures == errors == 0`, and its
`__str__` never mentions skips. **Odoo exits 0.** An entire fleet can lose all
its browser coverage without one red pipeline — which is exactly what happened
before this was found.

The final workflow step parses the log and fails the build on browser skips.
That step is what makes a green run mean the tours actually ran.

> The job's `name:` is the string the ruleset requires. **Renaming the job
> silently disables the gate** — the required check simply never reports, and
> depending on ruleset settings the PR either blocks forever or sails through.

---

## 7. The smoke target: `bemade/arc-gate-test`

One module, one tour, ~2 minutes. Client repos are an expensive place to debug
a runner — their suites run for tens of minutes and a broken runner blocks real
pull requests.

**If a client gate misbehaves, run this repo first.** Green here means the
runner, dind, registry auth, container job, Postgres service and browser are
all fine, and the problem is in the client repo.

### Proving the gate itself still works

A gate that never fires and a gate that is broken look identical from outside.

Run its workflow manually with **`break_browser: true`**. That disables Chrome
on purpose; the run is **expected to fail** at the skip gate. If it *passes*,
the gate has stopped working and every green tour run since is suspect.

---

## 8. Gotchas already paid for

- **Container jobs default to `sh`, not bash.** Even though the image ships
  bash 5.2 on `PATH`, GitHub runs `run:` steps under `sh -e {0}` in a
  container job, so `set -o pipefail` dies with "Illegal option" before doing
  anything. The workflow sets `defaults.run.shell: bash`. This matters beyond
  tidiness: `pipefail` is what makes the piped test command's exit code get
  checked at all.
- **Registry auth is keyed by the exact host string.**
  `registry.bemade.org` and `registry.bemade.org:443` are *different* auth
  entries to Docker. The cluster pull secret is scoped to the portless form, so
  write the image reference that way. The `:443` form authenticates fine
  locally and fails only on the runner.
- **Pin `controllerServiceAccount`.** The chart's label-based auto-discovery
  fails outside a live cluster, which breaks `helm template` validation. The
  chart's own error message recommends pinning it.
- **Rulesets on private repos need GitHub Pro/Team.** On a Free-plan org the
  API returns 403. Internal repos in an enterprise org are fine.
- **Enterprise addons are not under `/opt/odoo`.** They live in
  `/mnt/enterprise-addons`, exposed via the `ADDONS_PATH` **environment
  variable**, not `odoo.conf`. Always build the addons path from
  `${ADDONS_PATH}`.
