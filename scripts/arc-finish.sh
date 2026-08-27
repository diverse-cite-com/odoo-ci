#!/usr/bin/env bash
# Finish an ARC runner install once the GitHub App's installation exists.
# Idempotent: safe to re-run, and it refuses to act until the installation is
# actually present rather than guessing.
#
#   APP_ID=<id> ACCOUNT=<org-or-user> RELEASE=<name> VALUES=<file> \
#     scripts/arc-finish.sh /path/to/<app>.private-key.pem
#
# Examples:
#   APP_ID=4737817 ACCOUNT=Refractories-West-Inc RELEASE=arc-rwi \
#     VALUES=arc-rwi-values.yaml scripts/arc-finish.sh ~/Downloads/rwi.pem
#
#   APP_ID=... ACCOUNT=bemade RELEASE=arc-gate-test \
#     VALUES=arc-gate-test-values.yaml scripts/arc-finish.sh ~/Downloads/int.pem
#
# The controller, namespaces and registry pull secret are installed separately
# and are shared by every scale set; this only adds the App credential and the
# scale set itself.
set -euo pipefail

PEM="${1:?usage: arc-finish.sh <path-to-app-private-key.pem>}"
APP_ID="${APP_ID:?set APP_ID to the GitHub App id}"
ACCOUNT="${ACCOUNT:?set ACCOUNT to the org or user the app is installed on}"
NS="${NS:-arc-runners}"
RELEASE="${RELEASE:?set RELEASE to the helm release name}"
VALUES="$(cd "$(dirname "$0")/.." && pwd)/${VALUES:?set VALUES to the values filename}"
SECRET="${SECRET:-${RELEASE}-github}"
CHART="oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set"
VERSION="${VERSION:-0.14.2}"

[ -f "$PEM" ] || { echo "No such key: $PEM" >&2; exit 1; }

echo "==> Resolving installation id for app ${APP_ID} on ${ACCOUNT}"
INSTALL_ID="$(APP_ID="$APP_ID" PEM="$PEM" ACCOUNT="$ACCOUNT" python3 - <<'PY'
import jwt, time, json, os, urllib.request, urllib.error, sys
key = open(os.environ["PEM"]).read()
now = int(time.time())
tok = jwt.encode({"iat": now-60, "exp": now+540, "iss": os.environ["APP_ID"]},
                 key, algorithm="RS256")
req = urllib.request.Request(
    "https://api.github.com/app/installations",
    headers={"Authorization": f"Bearer {tok}",
             "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28"})
try:
    installs = json.load(urllib.request.urlopen(req))
except urllib.error.HTTPError as e:
    print(f"github api {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
    sys.exit(1)
account = os.environ["ACCOUNT"]
target = [i for i in installs
          if i["account"]["login"].lower() == account.lower()]
if not target:
    print(f"no installation on {account} yet "
          f"(app has {len(installs)} installation(s)) — still pending approval",
          file=sys.stderr)
    sys.exit(2)
print(target[0]["id"])
PY
)"

echo "==> Installation id: ${INSTALL_ID}"

# Recreate rather than patch: the key may have been rotated between runs.
echo "==> Writing secret ${SECRET} in ${NS}"
kubectl create secret generic "$SECRET" \
  --namespace "$NS" \
  --from-literal=github_app_id="$APP_ID" \
  --from-literal=github_app_installation_id="$INSTALL_ID" \
  --from-file=github_app_private_key="$PEM" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> helm upgrade --install ${RELEASE}"
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NS" --version "$VERSION" \
  -f "$VALUES" --wait --timeout 5m

echo "==> Listener and runners:"
kubectl get autoscalingrunnerset,pods -n "$NS"
echo
echo "Done. Confirm the scale set appears under the repo's"
echo "Settings -> Actions -> Runners page for ${ACCOUNT}."
