FROM docker.bemade.org/bemade/odoo18.0e

USER 0
COPY --chown=odoo:odoo ./addons /mnt/extra-addons
RUN pip install -r requirements.txt --break-system-packages
USER odoo