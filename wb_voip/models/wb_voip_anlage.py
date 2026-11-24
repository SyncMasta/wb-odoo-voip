import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

# ICE-Server: nur STUN (RFC 7064), ohne Leerzeichen, begrenzt in Anzahl und Länge.
# TURN braucht Benutzername und Passwort (sonst wirft RTCPeerConnection), kommt später.
ICE_URL_MUSTER = re.compile(r'^stun:\S+$', re.IGNORECASE)
ICE_MAX_ZEILEN = 10
ICE_MAX_LAENGE = 255


class WbVoipAnlage(models.Model):
    """PBX (telephone system) that users register their SIP extension on."""

    _name = 'wb.voip.anlage'
    _description = 'Telefonanlage'
    _order = 'name'

    name = fields.Char(string='Name', required=True)
    sip_domain = fields.Char(string='SIP-Domain', required=True)
    ws_url = fields.Char(string='WebSocket-URL', required=True,
                         help="z. B. wss://anlage.example/ws")
    modus = fields.Selection(
        [('test', 'Test'), ('prod', 'Produktiv')],
        string='Modus', required=True, default='test',
    )
    # Standard leer: der Browser fragt dann keinen fremden STUN-Server (keine Daten an Dritte)
    ice_server_urls = fields.Text(
        string='ICE-Server (STUN)',
        help="Optional, eine URL je Zeile, z. B. stun:stun.example.org:3478. "
             "TURN-Server werden noch nicht unterstützt. Leer = kein STUN; das reicht, wenn die "
             "Anlage Medien selbst vermittelt.")
    country_id = fields.Many2one(
        'res.country', string='Land',
        default=lambda self: self.env.ref('base.de', raise_if_not_found=False),
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._ws_url_bereinigen(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._ws_url_bereinigen(vals)
        return super().write(vals)

    @staticmethod
    def _ws_url_bereinigen(vals):
        """Strip ``ws_url`` so that the stored value is the checked one."""
        if isinstance(vals.get('ws_url'), str):
            vals['ws_url'] = vals['ws_url'].strip()

    @api.constrains('ws_url')
    def _check_ws_url(self):
        for anlage in self:
            if not (anlage.ws_url or '').lower().startswith('wss://'):
                raise ValidationError(self.env._(
                    "Die WebSocket-URL muss mit wss:// beginnen, z. B. wss://anlage.example/ws."))

    def _wb_voip_ice_urls(self):
        """Return the ICE server URLs of this PBX as a list (empty lines dropped)."""
        self.ensure_one()
        return [zeile.strip() for zeile in (self.ice_server_urls or '').splitlines() if zeile.strip()]

    @api.constrains('ice_server_urls')
    def _check_ice_server_urls(self):
        for anlage in self:
            urls = anlage._wb_voip_ice_urls()
            if len(urls) > ICE_MAX_ZEILEN or any(
                    len(url) > ICE_MAX_LAENGE or not ICE_URL_MUSTER.match(url) for url in urls):
                raise ValidationError(self.env._(
                    "ICE-Server: höchstens %(anzahl)s Zeilen, je eine URL mit stun:, z. B. "
                    "stun:stun.example.org:3478. Nur stun: ist erlaubt, TURN wird noch nicht unterstützt.", anzahl=ICE_MAX_ZEILEN))
