from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    wb_voip_aufbewahrung_monate = fields.Integer(
        string='Retain call data (months)',
        config_parameter='wb_voip.aufbewahrung_monate', default=12,
        groups='wb_voip.group_wb_voip_manager',
        help="A daily job deletes ended calls that are older. 0 = never delete. Call logs are kept.")
    wb_voip_team_aktiv = fields.Boolean(
        related='company_id.wb_voip_team_aktiv', readonly=False,
        groups='wb_voip.group_wb_voip_manager')
    wb_voip_team_dauer = fields.Boolean(
        related='company_id.wb_voip_team_dauer', readonly=False,
        groups='wb_voip.group_wb_voip_manager')
    wb_voip_benutzer_info = fields.Char(
        string='VoIP users', compute='_compute_wb_voip_benutzer_info', readonly=True,
        groups='wb_voip.group_wb_voip_manager')

    @api.depends_context('uid')
    def _compute_wb_voip_benutzer_info(self):
        Benutzer = self.env['res.users']
        anzahl = Benutzer._wb_voip_benutzer_zaehlen()
        grenze = Benutzer._wb_voip_benutzer_grenze()
        if grenze is None:
            text = self.env._("VoIP users: %s (no valid licence)") % anzahl
        elif grenze == 0:
            text = self.env._("VoIP users: %s (unlimited)") % anzahl
        else:
            text = self.env._("VoIP users: %(anzahl)s of %(grenze)s", anzahl=anzahl, grenze=grenze)
        for einstellung in self:
            einstellung.wb_voip_benutzer_info = text
