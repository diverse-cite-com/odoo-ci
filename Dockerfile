FROM git.bemade.org/bemade/odoo18.0e

COPY --chown=odoo:odoo ./addons /mnt/extra-addons
RUN pip install -r requirements.txt --break-system-packages
USER odoo