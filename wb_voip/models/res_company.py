from odoo import fields, models
from odoo.tools import SQL


class ResCompany(models.Model):
    _inherit = 'res.company'

    # Team-Übersicht zeigt Anwesenheit und Telefonstatus der Kollegen (Leistungs- und
    # Verhaltensdaten). Abwägung: Standard an aus Bequemlichkeit (Entscheidung 25.11.2026), obwohl
    # Art. 25 DSGVO datenschutzfreundliche Voreinstellungen verlangt; die Verantwortung trägt der
    # Betreiber (README: ausschalten, bis Beschäftigte informiert sind, Art. 13 DSGVO; mit Betriebsrat
    # erst nach dessen Zustimmung, § 87 Abs. 1 Nr. 6 BetrVG). Die Gesprächsdauer ist heikler und
    # bleibt Standard aus.
    wb_voip_team_aktiv = fields.Boolean(
        string='WB VoIP: Show team overview', default=True)
    wb_voip_team_dauer = fields.Boolean(
        string='WB VoIP: Show call duration in the team', default=False)

    def write(self, vals):
        ergebnis = super().write(vals)
        if 'wb_voip_team_aktiv' in vals and not vals['wb_voip_team_aktiv']:
            self._wb_voip_lebenszeichen_loeschen()
        return ergebnis

    def _wb_voip_lebenszeichen_loeschen(self):
        """Delete the stored signs of life of all users whose company is one of these.

        Data minimisation when the team overview is switched off. Per SQL like the sign of
        life itself (no ORM write on ``res.users``); the cache of both fields is invalidated.
        """
        if not self:
            return
        felder = ['wb_voip_lebenszeichen', 'wb_voip_registriert']
        Users = self.env['res.users']
        Users.flush_model(felder)
        self.env.cr.execute(SQL(
            "UPDATE res_users SET wb_voip_lebenszeichen = NULL, wb_voip_registriert = FALSE"
            " WHERE company_id IN %s"
            " AND (wb_voip_lebenszeichen IS NOT NULL OR wb_voip_registriert)",
            tuple(self.ids)))
        Users.invalidate_model(felder)
