import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

# ICE-Server: nur STUN (RFC 7064), ohne Leerzeichen, begrenzt in Anzahl und Länge.
# TURN braucht Benutzername und Passwort (sonst wirft RTCPeerConnection), kommt später.
ICE_URL_MUSTER = re.compile(r'^stun:\S+$', re.IGNORECASE)
ICE_MAX_ZEILEN = 10
ICE_MAX_LAENGE = 255
# Heranholen: Wahlpräfix der Anlage, nur Ziffern, * und # (kein Wählstring mit Steuerzeichen)
HERANHOLEN_MUSTER = re.compile(r'[0-9*#]{1,8}')
# Felder, an denen der Browser sich anmeldet: nur die Systemverwaltung ändert sie
VERBINDUNGSFELDER = frozenset({'ws_url', 'sip_domain', 'ice_server_urls'})


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

    heranholen_praefix = fields.Char(
        string='Heranholen-Präfix',
        help="Wahlpräfix der Anlage, um einen klingelnden Anruf eines Kollegen heranzuholen, "
             "z. B. **. Höchstens 8 Zeichen aus 0-9, * und #. Leer = Heranholen aus.")

    verbindung_gesperrt = fields.Boolean(
        string='Verbindungsdaten gesperrt', compute='_compute_verbindung_gesperrt')

    @api.depends_context('uid')
    def _compute_verbindung_gesperrt(self):
        gesperrt = not self.env.user._is_system()
        for anlage in self:
            anlage.verbindung_gesperrt = gesperrt

    def _verbindung_pruefen(self, vals):
        """Raise ``AccessError`` if a user without system right changes connection data.

        Browsers register the SIP account at ``ws_url``; a rogue address would receive the
        credentials of every user. Internal code running as superuser stays allowed.
        """
        if VERBINDUNGSFELDER & set(vals) and not (self.env.su or self.env.user._is_system()):
            raise AccessError(self.env._("Verbindungsdaten der Anlage darf nur die Systemverwaltung ändern."))

    def _nur_system(self, text):
        """Raise ``AccessError`` with ``text`` unless the caller is a system user (or superuser code)."""
        if not (self.env.su or self.env.user._is_system()):
            raise AccessError(text)

    @api.model_create_multi
    def create(self, vals_list):
        # Auch Vorgabewerte (Kontext default_*, ir.default) könnten eine fremde Adresse einsetzen
        self._nur_system(self.env._("Anlagen darf nur die Systemverwaltung anlegen."))
        for vals in vals_list:
            self._verbindung_pruefen(vals)
            self._ws_url_bereinigen(vals)
        return super().create(vals_list)

    def unlink(self):
        self._nur_system(self.env._("Anlagen darf nur die Systemverwaltung löschen."))
        return super().unlink()

    def write(self, vals):
        self._verbindung_pruefen(vals)
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

    @api.constrains('heranholen_praefix')
    def _check_heranholen_praefix(self):
        for anlage in self:
            wert = anlage.heranholen_praefix
            if wert and not HERANHOLEN_MUSTER.fullmatch(wert):
                raise ValidationError(self.env._(
                    "Heranholen-Präfix: höchstens 8 Zeichen, nur Ziffern, * und #."))
