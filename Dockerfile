FROM docker.bemade.org/bemade/odoo17.0e

USER 0
COPY --chown=odoo:odoo ./addons /mnt/extra-addons
COPY --chown=odoo:odoo requirements.txt /mnt/extra-addons/requirements.txt
RUN if [ -f requirements.txt ]; then pip install -r /mnt/extra-addons/requirements.txt --break-system-packages; fi
USER odoo