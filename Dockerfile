ARG ODOO_VERSION=18.0
FROM docker.bemade.org/bemade/odoo${ODOO_VERSION}e

USER 0
RUN pip install --upgrade --break-system-packages pytz
COPY --chown=odoo:odoo ./addons /mnt/extra-addons
RUN if [ -f requirements.txt ]; then \
        cp requirements.txt /mnt/extra-addons/requirements.txt && \
        chown odoo:odoo /mnt/extra-addons/requirements.txt; \
    fi
RUN if [ -f /mnt/extra-addons/requirements.txt ]; then pip install -r /mnt/extra-addons/requirements.txt --break-system-packages; fi
USER odoo
