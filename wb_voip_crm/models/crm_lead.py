from odoo import api, fields, models

GRUPPE = 'wb_voip.group_wb_voip_user'


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    wb_voip_anruf_ids = fields.One2many(
        'wb.voip.anruf', 'lead_id', string='Anrufe', groups=GRUPPE)
    wb_voip_anruf_anzahl = fields.Integer(
        string='Anzahl Anrufe', compute='_compute_wb_voip_anruf_anzahl', groups=GRUPPE)
    wb_voip_protokoll_ids = fields.Many2many(
        'wb.voip.protokoll', string='Anrufprotokolle',
        compute='_compute_wb_voip_protokoll_ids', groups=GRUPPE)

    @api.depends_context('uid')
    def _compute_wb_voip_anruf_anzahl(self):
        """Count the calls the current user may see (record rules apply, even in sudo)."""
        zaehler = {}
        if self.ids:
            Anruf = self.env['wb.voip.anruf'].with_user(self.env.user)
            zaehler = dict(Anruf._read_group(
                [('lead_id', 'in', self.ids)], ['lead_id'], ['__count']))
        for lead in self:
            lead.wb_voip_anruf_anzahl = zaehler.get(lead._origin, 0)

    @api.depends_context('uid')
    def _compute_wb_voip_protokoll_ids(self):
        """List the call logs of these leads that the current user may see."""
        Protokoll = self.env['wb.voip.protokoll'].with_user(self.env.user)
        for lead in self:
            lead.wb_voip_protokoll_ids = Protokoll.search([
                ('res_model', '=', 'crm.lead'), ('res_id', '=', lead._origin.id or 0)])

    def action_wb_voip_anrufe(self):
        """Open the calls of this lead (the record rules of WB VoIP apply)."""
        self.ensure_one()
        self.check_access('read')
        self.env['wb.voip.anruf'].check_access('read')
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Anrufe"),
            'res_model': 'wb.voip.anruf',
            'view_mode': 'list,form',
            'domain': [('lead_id', '=', self.id)],
            'context': {'create': False},
        }
