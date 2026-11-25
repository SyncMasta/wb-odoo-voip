import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.wb_voip.tools import nummer

# Rufnummer: optional „+“, dann Ziffern mit üblichen Trennzeichen
ZIEL_MUSTER = re.compile(r'^\+?\d[\d\s()/\-]*$')
ZIEL_MIN_ZIFFERN = 3


class WbVoipTaste(models.Model):
    _name = 'wb.voip.taste'
    _description = 'Softphone-Schnelltaste'
    _order = 'sequence, id'

    name = fields.Char(string='Beschriftung', required=True, translate=True)
    sequence = fields.Integer(string='Reihenfolge', default=10)
    active = fields.Boolean(string='Aktiv', default=True)
    icon = fields.Char(string='Symbol', help="Font-Awesome-Klasse, z. B. fa-mobile.")
    art = fields.Selection(
        [('weiterleitung', 'Weiterleitung'), ('anrufschutz', 'Anrufschutz (Nicht stören)'),
         ('anrufbeantworter', 'Anrufbeantworter')],
        string='Art', required=True)
    ziel_art = fields.Selection(
        [('fest', 'Feste Nummer'), ('handy', 'Eigenes Handy des Benutzers')],
        string='Ziel', default='fest', required=True,
        help="Feste Nummer: gilt für alle Benutzer. Eigenes Handy: jeder Benutzer trägt seine "
             "Nummer unter Mein Profil › Telefonie ein (Feld „Mein Handy“).")
    ziel = fields.Char(
        string='Weiterleitungsziel',
        help="Rufnummer, z. B. +49 170 1234567. Eine Weiterleitungstaste mit fester Nummer "
             "ohne gültiges Ziel bleibt ausgeblendet.")
    ab_modus = fields.Selection(
        [('sofort', 'Sofort'), ('nach_rufzeit', 'Nach Rufzeit')],
        string='Anrufbeantworter greift', default='sofort')
    ab_sekunden = fields.Integer(string='Rufzeit in Sekunden', default=20)

    @api.constrains('ziel')
    def _check_ziel(self):
        for taste in self.filtered(lambda t: (t.ziel or '').strip()):
            if not self._ziel_gueltig(taste.ziel):
                raise ValidationError(self.env._(
                    "Das Weiterleitungsziel „%s“ ist keine gültige Rufnummer.", taste.ziel))

    @api.constrains('art', 'ab_sekunden')
    def _check_ab_sekunden(self):
        for taste in self.filtered(lambda t: t.art == 'anrufbeantworter'):
            if not 1 <= taste.ab_sekunden <= 600:
                raise ValidationError(self.env._("Die Rufzeit muss zwischen 1 und 600 Sekunden liegen."))

    @api.model
    def _ziel_gueltig(self, ziel):
        """Return True for a phone number with at least three digits (``+``, digits, separators)."""
        ziel = (ziel or '').strip() if isinstance(ziel, str) else ''
        return bool(ZIEL_MUSTER.match(ziel)) and len(re.sub(r'\D', '', ziel)) >= ZIEL_MIN_ZIFFERN

    @api.model
    def _ziel_normal(self, ziel, land_code=None):
        """Return a comparable form: E.164 if possible, else digits only.

        Ansitel returns national (``0170…``), ``0049…`` or ``+49…`` numbers.
        """
        ziel = (ziel or '').strip()
        if ziel.startswith('00'):
            ziel = '+' + ziel[2:]
        return nummer.e164(ziel, land_code or 'DE') or re.sub(r'\D', '', ziel)

    @api.model
    def _ziel_gleich(self, a, b, land_code=None):
        """Return True if both numbers denote the same target."""
        a, b = self._ziel_normal(a, land_code), self._ziel_normal(b, land_code)
        return bool(a) and a == b

    @api.model
    def _ziel_senden(self, ziel):
        """Return the dialling form for Ansitel: digits only, a leading ``+`` becomes ``00``."""
        ziel = re.sub(r'[\s()/\-]', '', (ziel or '').strip())
        return '00' + ziel[1:] if ziel.startswith('+') else ziel

    def _ziel_fuer(self, benutzer):
        """Return the forwarding target of this key for ``benutzer`` (own mobile or fixed number)."""
        self.ensure_one()
        if self.ziel_art == 'handy':
            return (benutzer.sudo().wb_voip_handy or '').strip()
        return (self.ziel or '').strip()
