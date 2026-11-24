from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.wb_license_client.models.wb_license_client import license_required

from ..models import PRODUKT_CODE
from ..tools.rechte import nur_voip_benutzer

NICHT_SPEICHERN = 'nicht_speichern'


class WbVoipZuordnen(models.TransientModel):
    _name = 'wb.voip.zuordnen'
    _description = 'WB VoIP: Rufnummer einem Kontakt zuordnen'

    anruf_id = fields.Many2one(
        'wb.voip.anruf', string='Anruf', required=True, ondelete='cascade')
    nummer = fields.Char(string='Rufnummer', related='anruf_id.nummer', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Kontakt')
    feld = fields.Selection(
        selection='_selection_feld', string='Nummer speichern in', default='phone',
        required=True)
    ersetzen = fields.Boolean(
        string='Ersetzen', help="Eine vorhandene Nummer im gewählten Feld überschreiben.")

    @api.model
    def _selection_feld(self):
        """Return the phone fields of the contact (labels from the fields) plus "do not save"."""
        Partner = self.env['res.partner']
        auswahl = [(name, Partner._fields[name]._description_string(self.env))
                   for name in Partner._wb_voip_nummernfelder()]
        return auswahl + [(NICHT_SPEICHERN, self.env._("Nicht speichern"))]

    # --- Schutz: nur eigene Anrufe ---

    def _anruf_pruefen(self, anruf):
        """Raise unless ``anruf`` is the caller's own call."""
        if not anruf:
            raise UserError(self.env._("Der Anruf fehlt."))
        anruf._wb_voip_eigener()

    @api.model
    def default_get(self, fields_list):
        werte = super().default_get(fields_list)
        if werte.get('anruf_id'):
            self._anruf_pruefen(self.env['wb.voip.anruf'].browse(werte['anruf_id']))
        return werte

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('anruf_id'):
                self._anruf_pruefen(self.env['wb.voip.anruf'].browse(vals['anruf_id']))
        return super().create(vals_list)

    # --- Aktion ---

    @nur_voip_benutzer
    @license_required(PRODUKT_CODE)
    def action_zuordnen(self):
        """Assign the call (and its log) to the contact; optionally save the number there.

        Runs as the caller: the call must be the caller's own, the contact readable.
        The number is stored as entered, only if the caller may write that field of
        the contact; otherwise the assignment still happens and a warning is returned.
        """
        self.ensure_one()
        anruf = self.anruf_id
        self._anruf_pruefen(anruf)
        partner = self.partner_id
        if not partner:
            raise UserError(self.env._("Bitte einen Kontakt wählen."))
        if not partner.has_access('read'):
            raise AccessError(self.env._("Sie dürfen diesen Kontakt nicht lesen."))
        nummer = anruf.sudo().nummer
        gespeichert = True
        if self.feld != NICHT_SPEICHERN and nummer:
            if self.feld not in partner._wb_voip_nummernfelder():
                raise UserError(self.env._("Ungültige Angabe: %s.", self.env._("Feld")))
            feld = partner._fields[self.feld]
            if partner.has_access('write') and partner._has_field_access(feld, 'write'):
                vorhanden = partner[self.feld]
                if vorhanden and vorhanden.strip() != nummer.strip() and not self.ersetzen:
                    raise UserError(self.env._(
                        "Das Feld ist schon belegt. „Ersetzen“ anhaken oder ein anderes "
                        "Feld wählen."))
                anruf._wb_voip_partner_setzen(partner)
                if vorhanden != nummer:
                    partner.write({self.feld: nummer})
                return {'type': 'ir.actions.act_window_close'}
            gespeichert = False
        anruf._wb_voip_partner_setzen(partner)
        if not gespeichert:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'warning',
                    'message': self.env._(
                        "Zugeordnet. Die Nummer wurde nicht am Kontakt gespeichert "
                        "(keine Schreibrechte)."),
                    'next': {'type': 'ir.actions.act_window_close'},
                },
            }
        return {'type': 'ir.actions.act_window_close'}
