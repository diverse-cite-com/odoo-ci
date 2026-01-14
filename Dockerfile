ARG ODOO_VERSION=18.0
ARG REGISTRY=registry.bemade.org:443
FROM ${REGISTRY}/bemade/docker-odoo-enterprise/odoo-enterprise-${ODOO_VERSION}

USER 0

# Copy package list files
COPY build-packages.txt runtime-packages.txt /tmp/

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

# Check if pip version is >= 23.0.0 and note whether we need to break system packages
RUN if [ "$(pip --version | awk '{print $2}' | awk -F. '{ printf("%d%03d%03d\n", $1, $2, $3); }')" -ge "$(echo "23.0.0" | awk -F. '{ printf("%d%03d%03d\n", $1, $2, $3); }')" ]; then \
        echo '--break-system-packages' > /tmp/break_sys_packages; \
    else \
        echo '' > /tmp/break_sys_packages; \
    fi

# Install packages with appropriate flags
RUN pip install $(cat /tmp/break_sys_packages) --upgrade pytz

COPY --chown=odoo:odoo ./addons /mnt/extra-addons

COPY --chown=odoo:odoo requirements.txt /mnt/extra-addons/

# Install requirements from file if present
RUN if [ -f /mnt/extra-addons/requirements.txt ]; then \
      pip install $(cat /tmp/break_sys_packages) --ignore-installed typing-extensions -r /mnt/extra-addons/requirements.txt; \
    fi

USER odoo
