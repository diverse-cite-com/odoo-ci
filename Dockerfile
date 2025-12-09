ARG ODOO_VERSION=18.0
ARG REGISTRY=registry.bemade.org:443

# Build stage - install build deps and compile wheels
FROM ${REGISTRY}/bemade/docker-odoo-enterprise/odoo-enterprise-${ODOO_VERSION} AS builder

USER 0

# Install build dependencies for Python packages that require compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    libcups2-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

# Build wheels for all requirements
RUN pip wheel --wheel-dir=/tmp/wheels --no-cache-dir pytz \
    && if [ -f /tmp/requirements.txt ]; then \
         pip wheel --wheel-dir=/tmp/wheels --no-cache-dir -r /tmp/requirements.txt; \
       fi

# Final stage - clean image with only runtime deps
FROM ${REGISTRY}/bemade/docker-odoo-enterprise/odoo-enterprise-${ODOO_VERSION}

USER 0

# Install runtime dependency for pycups (libcups2 without -dev)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcups2 \
    && rm -rf /var/lib/apt/lists/*

# Check if pip version is >= 23.0.0 and note whether we need to break system packages
RUN if [ "$(pip --version | awk '{print $2}' | awk -F. '{ printf("%d%03d%03d\n", $1, $2, $3); }')" -ge "$(echo "23.0.0" | awk -F. '{ printf("%d%03d%03d\n", $1, $2, $3); }')" ]; then \
        echo '--break-system-packages' > /tmp/break_sys_packages; \
    else \
        echo '' > /tmp/break_sys_packages; \
    fi

# Copy wheels from builder and install
COPY --from=builder /tmp/wheels /tmp/wheels
RUN pip install $(cat /tmp/break_sys_packages) --no-cache-dir /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels

COPY --chown=odoo:odoo ./addons /mnt/extra-addons

USER odoo
