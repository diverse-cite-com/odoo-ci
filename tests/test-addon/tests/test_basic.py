from odoo.tests import TransactionCase


class TestBasic(TransactionCase):
    """Basic tests to validate CI setup."""

    def test_addon_installed(self):
        """Test that the addon is properly installed."""
        module = self.env["ir.module.module"].search([("name", "=", "test_addon")])
        self.assertTrue(module, "test_addon module should exist")
        self.assertEqual(module.state, "installed", "test_addon should be installed")

    def test_base_partner_exists(self):
        """Test that base partner records exist."""
        partners = self.env["res.partner"].search([], limit=1)
        self.assertTrue(partners, "At least one partner should exist")

    def test_simple_create(self):
        """Test basic ORM create operation."""
        partner = self.env["res.partner"].create({"name": "CI Test Partner"})
        self.assertTrue(partner.id, "Partner should be created with an ID")
        self.assertEqual(partner.name, "CI Test Partner")
