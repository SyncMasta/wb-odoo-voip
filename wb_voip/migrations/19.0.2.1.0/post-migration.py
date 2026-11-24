from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Give standard layouts without blocks their delivered blocks (idempotent)."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['wb.voip.layout']._wb_voip_standard_bloecke_sicherstellen()
