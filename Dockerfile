ARG ODOO_VERSION=18.0
ARG REGISTRY=registry.bemade.org:443
# For production: odoo-enterprise-19.0:latest
# For CI: odoo-enterprise-ci:19.0
ARG BASE_IMAGE_TAG=odoo-enterprise-${ODOO_VERSION}:latest
FROM ${REGISTRY}/bemade/docker-odoo-enterprise/${BASE_IMAGE_TAG}

USER 0

# Copy package list files
COPY --chown=odoo:odoo build-packages.txt runtime-packages.txt /tmp/

# Install build dependencies for compilation + runtime packages if any are specified
RUN BUILD_PKGS=$(cat /tmp/build-packages.txt | tr '\n' ' ') \
    && RUNTIME_PKGS=$(cat /tmp/runtime-packages.txt | tr '\n' ' ') \
    && if [ -n "$BUILD_PKGS" ] || [ -n "$RUNTIME_PKGS" ]; then \
         apt-get update && apt-get install -y --no-install-recommends \
           build-essential \
           python3-dev \
           $BUILD_PKGS \
           $RUNTIME_PKGS \
         && rm -rf /var/lib/apt/lists/*; \
       fi \
    && rm -f /tmp/build-packages.txt /tmp/runtime-packages.txt

# uv is already installed in the base image, no action needed

# Install Python requirements BEFORE copying addons. requirements.txt rarely
# changes, while addons change on most commits; doing the dependency install
# first means a code-only change busts only the addons COPY layer below and
# the (cached) pip-install layer is reused. With the persistent BuildKit cache
# this turns most builds into a near-instant COPY + push.
# Use uv with venv for CI images, system for production.
COPY --chown=odoo:odoo requirements.txt /mnt/extra-addons/requirements.txt
COPY constraints.txt /tmp/constraints.txt
RUN if [ -f /mnt/extra-addons/requirements.txt ]; then \
      if [ -d /opt/odoo-venv ]; then \
        uv pip install --python /opt/odoo-venv/bin/python \
          -c /tmp/constraints.txt -r /mnt/extra-addons/requirements.txt; \
      else \
        uv pip install --system --break-system-packages \
          -c /tmp/constraints.txt -r /mnt/extra-addons/requirements.txt; \
      fi \
    fi

# Canari d ABI. Une resolution pip incompatible produit une image qui se
# CONSTRUIT parfaitement puis meurt au demarrage d Odoo, dans
# load_server_wide_modules — et l echec apparait alors sur la MR de
# quelqu un d autre, avec une trace qui accuse son addon. Ici il apparait
# au build, en une ligne, dans le job qui en est responsable.
# Ces quatre imports couvrent les extensions natives qu Odoo charge avant
# tout addon ; c est la ou se manifestent les desaccords d ABI.
RUN PY=$([ -d /opt/odoo-venv ] && echo /opt/odoo-venv/bin/python || echo python3); \
    "$PY" -c "import OpenSSL, lxml.etree, PIL, psycopg2" \
    || { echo "ABI cassee apres installation des requirements — voir constraints.txt"; exit 1; }

# Copy addons last (changes on most commits) so the dependency layer above
# stays cached across code-only changes.
#
# Both client-owned addons (./addons) and vendored shared addons (./vendored,
# materialized real dirs pinned by addons.lock) go into the same
# /mnt/extra-addons the base image already has on its addons_path. `vendor check`
# guarantees no name collides between the two. build.sh ensures ./vendored
# exists (empty is fine) so this COPY never fails on a not-yet-vendored repo.
COPY --chown=odoo:odoo ./addons   /mnt/extra-addons
COPY --chown=odoo:odoo ./vendored /mnt/extra-addons
