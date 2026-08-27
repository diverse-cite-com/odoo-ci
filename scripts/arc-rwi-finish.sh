#!/usr/bin/env bash
# Finish the RWI ARC runner install once the GitHub App's org installation has
# been approved. Idempotent: safe to re-run, and it refuses to do anything
# until the installation actually exists.
#
#   scripts/arc-rwi-finish.sh /path/to/bemade-arc-rwi.<date>.private-key.pem
#
# Everything before this point (controller, namespaces, registry pull secret)
# is already in place; this only needs the App credential.
set -euo pipefail

PEM="${1:?usage: arc-rwi-finish.sh <path-to-app-private-key.pem>}"
APP_ID="${APP_ID:-4737817}"
NS="${NS:-arc-runners}"
RELEASE="${RELEASE:-arc-rwi}"
VALUES="$(cd "$(dirname "$0")/.." && pwd)/arc-rwi-values.yaml"
CHART="oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set"
VERSION="${VERSION:-0.14.2}"

[ -f "$PEM" ] || { echo "No such key: $PEM" >&2; exit 1; }

echo "==> Resolving installation id for app ${APP_ID}"
INSTALL_ID="$(APP_ID="$APP_ID" PEM="$PEM" python3 - <<'PY'
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
target = [i for i in installs
          if i["account"]["login"] == "Refractories-West-Inc"]
if not target:
    print("no installation on Refractories-West-Inc yet "
          f"(app has {len(installs)} installation(s)) — still pending approval",
          file=sys.stderr)
    sys.exit(2)
print(target[0]["id"])
PY
)"

echo "==> Installation id: ${INSTALL_ID}"

# Recreate rather than patch: the key may have been rotated between runs.
echo "==> Writing secret arc-rwi-github in ${NS}"
kubectl create secret generic arc-rwi-github \
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
echo "Done. The scale set registers as 'arc-rwi-odoo'; confirm it appears under"
echo "  https://github.com/Refractories-West-Inc/odoo/settings/actions/runners"
