from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.addons.base.models.res_partner import _tz_get

from odoo.addons.wb_voip.tools import geheimnis
from odoo.addons.wb_voip.tools.rechte import nur_voip_manager

from ..tools.adresse import url_ist_https
from .ansitel_api import AnsitelFehler


class WbVoipAnlage(models.Model):
    _inherit = 'wb.voip.anlage'

    ansitel_aktiv = fields.Boolean(
        string='Ansitel-Schnittstelle', copy=False,
        help="Schnelltasten und Abgleich der Anrufliste über die REST-Schnittstelle der Anlage.")
    ansitel_api_url = fields.Char(
        string='Ansitel-REST-URL', copy=False,
        help="Nur https, z. B. https://anlage.example/awi/api/rest.")
    # Nur schreibbar: liefert beim Lesen immer leer, gespeichert wird das Chiffrat
    ansitel_token = fields.Char(
        string='Ansitel-Token (zentral)', compute='_compute_ansitel_token',
        inverse='_inverse_ansitel_token', store=False, copy=False, groups='base.group_system')
    ansitel_token_enc = fields.Char(
        string='Ansitel-Token (verschlüsselt)', copy=False, groups='base.group_system')
    ansitel_token_gesetzt = fields.Boolean(
        string='Ansitel-Token gespeichert', compute='_compute_ansitel_token_gesetzt')
    ansitel_tz = fields.Selection(
        _tz_get, string='Zeitzone der Anrufliste', default='Europe/Berlin', required=True,
        help="Die Anlage liefert die Zeiten der Anrufliste in Ortszeit dieser Zone.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._ansitel_url_bereinigen(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._ansitel_url_bereinigen(vals)
        return super().write(vals)

    @staticmethod
    def _ansitel_url_bereinigen(vals):
        """Strip the URL so that the stored value is the checked one."""
        if isinstance(vals.get('ansitel_api_url'), str):
            vals['ansitel_api_url'] = vals['ansitel_api_url'].strip() or False

    @api.constrains('ansitel_aktiv', 'ansitel_api_url')
    def _check_ansitel_api_url(self):
        for anlage in self:
            if anlage.ansitel_api_url and not url_ist_https(anlage.ansitel_api_url):
                raise ValidationError(self.env._(
                    "Die Ansitel-URL muss mit https:// beginnen und einen Host haben, "
                    "ohne Benutzer und Passwort, z. B. https://anlage.example/awi/api/rest."))
            if anlage.ansitel_aktiv and not anlage.ansitel_api_url:
                raise ValidationError(self.env._(
                    "Für die Ansitel-Schnittstelle fehlt die REST-URL."))

    def _compute_ansitel_token(self):
        for anlage in self:
            anlage.ansitel_token = False

    def _inverse_ansitel_token(self):
        for anlage in self:
            klartext = anlage.ansitel_token
            chiffrat = geheimnis.verschluesseln(self.env, klartext) if klartext else False
            anlage.sudo().ansitel_token_enc = chiffrat
        # Klartext nicht im Cache lassen: nächstes Lesen rechnet neu (leer)
        self.invalidate_recordset(['ansitel_token'])

    @api.depends('ansitel_token_enc')
    def _compute_ansitel_token_gesetzt(self):
        for anlage in self:
            anlage.ansitel_token_gesetzt = bool(anlage.sudo().ansitel_token_enc)

    @nur_voip_manager
    def action_ansitel_testen(self):
        """Read the peer list as a harmless call and show the result as a notification."""
        self.ensure_one()
        try:
            self.env['wb.voip.ansitel']._aufruf(self, 'getpeer')
        except (AnsitelFehler, ValidationError) as fehler:
            art, text = 'danger', str(fehler)
        else:
            art, text = 'success', self.env._("Verbindung zur Anlage steht.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': self.env._("Ansitel"), 'message': text, 'type': art, 'sticky': False},
        }
