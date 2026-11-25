from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Team-Übersicht zeigt Anwesenheit und Telefonstatus der Kollegen (Leistungs- und
    # Verhaltensdaten): Standard aus, der Betreiber schaltet sie bewusst ein (Art. 25 DSGVO,
    # ggf. Mitbestimmung nach § 87 Abs. 1 Nr. 6 BetrVG)
    wb_voip_team_aktiv = fields.Boolean(
        string='WB VoIP: Team-Übersicht anzeigen', default=False)
    wb_voip_team_dauer = fields.Boolean(
        string='WB VoIP: Gesprächsdauer im Team anzeigen', default=False)
