FROM docker.bemade.org/bemade/odoo17.0e

COPY --chown=odoo:odoo ./addons /mnt/extra-addons
RUN pip install -r requirements.txt --break-system-packages
USER odoo