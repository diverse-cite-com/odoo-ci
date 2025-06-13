ARG ODOO_VERSION=18.0
FROM docker.bemade.org/bemade/odoo${ODOO_VERSION}e

USER 0

# Check if pip version is >= 23.0.0 and note whether we need to break system packages
RUN if [ "$(pip --version | awk '{print $2}' | awk -F. '{ printf("%d%03d%03d\n", $1, $2, $3); }')" -ge "$(echo "23.0.0" | awk -F. '{ printf("%d%03d%03d\n", $1, $2, $3); }')" ]; then \
        echo '--break-system-packages' > /tmp/break_sys_packages; \
    else \
        echo '' > /tmp/break_sys_packages; \
    fi

# Install packages with appropriate flags
RUN pip install --upgrade $(cat /tmp/break_sys_packages) pytz

COPY --chown=odoo:odoo ./addons /mnt/extra-addons
COPY --chown=odoo:odoo requirements.txt /mnt/extra-addons/

# Install requirements if present
RUN if [ -f /mnt/extra-addons/requirements.txt ]; then \
      pip install -r /mnt/extra-addons/requirements.txt $(cat /tmp/break_sys_packages); \
    fi
USER odoo
