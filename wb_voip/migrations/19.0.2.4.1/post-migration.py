from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Remove blocks a place no longer allows and duplicate notes (idempotent)."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['wb.voip.layout']._wb_voip_unerlaubte_bloecke_entfernen()
