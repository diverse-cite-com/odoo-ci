ARG ODOO_VERSION=18.0
ARG REGISTRY=registry.bemade.org
FROM ${REGISTRY}/bemade/docker-odoo-enterprise/odoo-enterprise-${ODOO_VERSION}

USER 0

# Copy package list files (wildcards so missing files don't fail)
COPY build-packages.tx* runtime-packages.tx* /tmp/

# Install build dependencies for compilation + runtime packages
# Build packages are removed after pip install to keep image smaller
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    $(if [ -f /tmp/build-packages.txt ]; then cat /tmp/build-packages.txt | tr '\n' ' '; fi) \
    $(if [ -f /tmp/runtime-packages.txt ]; then cat /tmp/runtime-packages.txt | tr '\n' ' '; fi) \
    && rm -rf /var/lib/apt/lists/* \
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

# Install requirements if present
RUN if [ -f /mnt/extra-addons/requirements.txt ]; then \
      pip install $(cat /tmp/break_sys_packages) --ignore-installed typing-extensions -r /mnt/extra-addons/requirements.txt; \
    fi

USER odoo
