ARG ODOO_VERSION=18.0
FROM docker.bemade.org/bemade/odoo${ODOO_VERSION}e

USER 0
COPY --chown=odoo:odoo ./addons /mnt/extra-addons
COPY --chown=odoo:odoo requirements.txt /mnt/extra-addons/requirements.txt
RUN if [ -f /mnt/extra-addons/requirements.txt ]; then pip install -r /mnt/extra-addons/requirements.txt --break-system-packages; fi
USER odoo
