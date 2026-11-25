from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    wb_voip_aufbewahrung_monate = fields.Integer(
        string='Anrufdaten aufbewahren (Monate)',
        config_parameter='wb_voip.aufbewahrung_monate', default=12,
        groups='wb_voip.group_wb_voip_manager',
        help="Beendete Anrufe, die älter sind, löscht ein täglicher Lauf. 0 = nie löschen. "
             "Anrufprotokolle bleiben erhalten.")
    wb_voip_team_aktiv = fields.Boolean(
        related='company_id.wb_voip_team_aktiv', readonly=False,
        groups='wb_voip.group_wb_voip_manager')
    wb_voip_team_dauer = fields.Boolean(
        related='company_id.wb_voip_team_dauer', readonly=False,
        groups='wb_voip.group_wb_voip_manager')
