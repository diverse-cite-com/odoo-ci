ARG ODOO_VERSION=18.0
ARG REGISTRY=registry.bemade.org:443

# Build stage - install build deps and compile wheels
FROM ${REGISTRY}/bemade/docker-odoo-enterprise/odoo-enterprise-${ODOO_VERSION} AS builder

USER 0

# Copy project files first to check what's needed
# Using wildcards so missing files don't fail the build
COPY requirements.tx* build-packages.tx* runtime-packages.tx* /tmp/

# Install build dependencies (build-essential + python3-dev always needed for compilation)
# Plus any project-specific build packages from build-packages.txt
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    $(if [ -f /tmp/build-packages.txt ]; then cat /tmp/build-packages.txt | tr '\n' ' '; fi) \
    && rm -rf /var/lib/apt/lists/*

# Build wheels for all requirements
RUN pip wheel --wheel-dir=/tmp/wheels --no-cache-dir pytz \
    && if [ -f /tmp/requirements.txt ]; then \
         pip wheel --wheel-dir=/tmp/wheels --no-cache-dir -r /tmp/requirements.txt; \
       fi

# Final stage - clean image with only runtime deps
FROM ${REGISTRY}/bemade/docker-odoo-enterprise/odoo-enterprise-${ODOO_VERSION}

USER 0

COPY runtime-packages.tx* /tmp/

# Install project-specific runtime packages if specified
RUN if [ -f /tmp/runtime-packages.txt ] && [ -s /tmp/runtime-packages.txt ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
        $(cat /tmp/runtime-packages.txt | tr '\n' ' ') \
        && rm -rf /var/lib/apt/lists/*; \
    fi \
    && rm -f /tmp/runtime-packages.txt

# Check if pip version is >= 23.0.0 and note whether we need to break system packages
RUN if [ "$(pip --version | awk '{print $2}' | awk -F. '{ printf("%d%03d%03d\n", $1, $2, $3); }')" -ge "$(echo "23.0.0" | awk -F. '{ printf("%d%03d%03d\n", $1, $2, $3); }')" ]; then \
        echo '--break-system-packages' > /tmp/break_sys_packages; \
    else \
        echo '' > /tmp/break_sys_packages; \
    fi

# Copy wheels from builder and install
COPY --from=builder /tmp/wheels /tmp/wheels
RUN pip install $(cat /tmp/break_sys_packages) --no-cache-dir --ignore-installed /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

COPY --chown=odoo:odoo ./addons /mnt/extra-addons

USER odoo
