import logging
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from odoo.addons.wb_voip.tools import nummer

# Rufnummer: optional „+“, dann ASCII-Ziffern mit üblichen Trennzeichen (keine anderen Unicode-Ziffern)
ZIEL_MUSTER = re.compile(r'^\+?[0-9][0-9 ()/\-]*$', re.ASCII)
TRENNER = re.compile(r'[ ()/\-]', re.ASCII)
NICHT_ZIFFER = re.compile(r'[^0-9]', re.ASCII)
ZIEL_MIN_ZIFFERN = 3
# Weiterleitungsziele brauchen mindestens so viele Ziffern (nationale Form mit 0)
WEITERLEITUNG_MIN_ZIFFERN = 6
# Sonder- und Mehrwertnummern je Ländervorwahl, als nationale Rufnummer ohne führende 0
# (Gebührenbetrug über die Weiterleitung)
SONDER_PRAEFIXE = {
    49: ('900', '137', '138', '180', '181', '182', '183', '184', '185', '186', '187', '188', '189',
         '190', '191', '192', '193', '194', '700', '12', '31'),
    43: ('900', '901', '930', '939'),
    41: ('900', '901', '906'),
}
# Satelliten- und globale Dienste: immer gesperrt, auch mit Auslandsfreigabe
GESPERRTE_VORWAHLEN = (881, 882, 883)
# Notruf, Behördennummer, harmonisierte Dienste, Auskunft
KURZ_NUMMERN = ('110', '112', '115')
KURZ_PRAEFIXE = ('116', '118')
BEKANNTE_VORWAHLEN = (49, 43, 41, 881, 882, 883)
LAENDERVORWAHL_FALLBACK = {'DE': 49, 'AT': 43, 'CH': 41}
PARAM_AUSLAND = 'wb_voip_ansitel.ausland_erlaubt'

_logger = logging.getLogger(__name__)


def _laendervorwahl(land_code):
    """Return the country calling code (int) of an ISO-2 country code."""
    land = (land_code or 'DE').upper()
    try:
        import phonenumbers
        vorwahl = phonenumbers.country_code_for_region(land)
    except ImportError:
        vorwahl = 0
    return vorwahl or LAENDERVORWAHL_FALLBACK.get(land, 49)


def _e164_teile(e164):
    """Return ``(country code, national significant number)`` of an E.164 string or ``None``."""
    try:
        import phonenumbers
        zahl = phonenumbers.parse(e164, None)
    except Exception:  # noqa: BLE001 - phonenumbers wirft eigene Ausnahmen
        return None
    return zahl.country_code, str(zahl.national_number)


def _grob_teile(roh, vorwahl):
    """Return ``(country code, national number)`` from digits without phonenumbers (for messages only).

    ``roh``: ``+``/digits without separators. A trunk ``0`` (also written ``(0)``) after the
    country code is dropped, so ``+49 (0) 900…`` and ``0900…`` give the same parts.
    """
    if roh.startswith('+') or roh.startswith('00'):
        rest = roh[1:] if roh.startswith('+') else roh[2:]
        for kandidat in sorted(BEKANNTE_VORWAHLEN, key=lambda v: -len(str(v))):
            if rest.startswith(str(kandidat)):
                return kandidat, rest[len(str(kandidat)):].lstrip('0')
        return None, rest
    return vorwahl, roh.lstrip('0') if roh.startswith('0') else roh


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

    def _ziel_fehler(self):
        return AccessError(self.env._("Feste Weiterleitungsziele darf nur die Systemverwaltung setzen."))

    @api.model
    def _ziel_laender(self):
        """Return the country codes of the Ansitel PBXs (fixed targets apply to all of them), at least DE."""
        Anlage = self.env['wb.voip.anlage'].sudo()
        return sorted({land for land in Anlage.search([('ansitel_aktiv', '=', True)]).mapped('country_id.code')
                       if land}) or ['DE']

    def _ziel_normalisieren(self, vals):
        """Store a valid fixed target in E.164 form; an invalid one stays for the constraint message."""
        if isinstance(vals.get('ziel'), str) and vals['ziel'].strip():
            for land in self._ziel_laender():
                if not self._ziel_pruefen(vals['ziel'], land):
                    vals['ziel'] = self._ziel_e164(vals['ziel'], land)
                    break

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user._is_system() and any((vals.get('ziel') or '').strip() for vals in vals_list):
            raise self._ziel_fehler()
        for vals in vals_list:
            self._ziel_normalisieren(vals)
        return super().create(vals_list)

    def write(self, vals):
        # Gebührenbetrug: das Ziel einer festen Taste gilt für alle Benutzer
        if 'ziel' in vals and not self.env.user._is_system():
            neu = (vals['ziel'] or '').strip() or False
            if any(((taste.ziel or '').strip() or False) != neu for taste in self.sudo()):
                raise self._ziel_fehler()
        self._ziel_normalisieren(vals)
        return super().write(vals)

    @api.constrains('ziel')
    def _check_ziel(self):
        laender = self._ziel_laender()
        for taste in self.filtered(lambda t: (t.ziel or '').strip()):
            fehler = [self._ziel_pruefen(taste.ziel, land) for land in laender]
            if all(fehler):
                raise ValidationError(fehler[0])

    @api.constrains('art', 'ab_sekunden')
    def _check_ab_sekunden(self):
        for taste in self.filtered(lambda t: t.art == 'anrufbeantworter'):
            if not 1 <= taste.ab_sekunden <= 600:
                raise ValidationError(self.env._("Die Rufzeit muss zwischen 1 und 600 Sekunden liegen."))

    @api.model
    def _ziel_gueltig(self, ziel):
        """Return True for a phone number with at least three ASCII digits (``+``, digits, separators)."""
        ziel = (ziel or '').strip() if isinstance(ziel, str) else ''
        return bool(ZIEL_MUSTER.match(ziel)) and len(NICHT_ZIFFER.sub('', ziel)) >= ZIEL_MIN_ZIFFERN

    @api.model
    def _ziel_e164(self, ziel, land_code=None):
        """Return the E.164 form of ``ziel`` (``+49170…``) or ``False`` (ASCII digits only)."""
        if not self._ziel_gueltig(ziel):
            return False
        roh = TRENNER.sub('', ziel.strip())
        if roh.startswith('00'):
            roh = '+' + roh[2:]
        return nummer.e164(roh, land_code or 'DE') or False

    @api.model
    def _ziel_pruefen(self, ziel, land_code=None):
        """Return the user message why ``ziel`` may not be a forwarding target, or ``False``.

        All checks run on the normalised number (country code + national significant number, so
        ``+49 (0) 900…`` counts as ``0900…``): fewer than six digits, emergency/short/directory
        numbers, premium and service numbers (DE, AT, CH), satellite services (+881/+882/+883,
        always), targets abroad unless ``wb_voip_ansitel.ausland_erlaubt`` is ``1``; finally the
        number must be valid.
        """
        _ = self.env._
        ziel = ziel if isinstance(ziel, str) else ''
        if any(zeichen.isdigit() and zeichen not in '0123456789' for zeichen in ziel):
            return _("Bitte nur die Ziffern 0–9 verwenden.")
        ungueltig = _("„%s“ ist keine gültige Rufnummer (z. B. +49 170 1234567).", ziel)
        if not self._ziel_gueltig(ziel):
            return ungueltig
        vorwahl = _laendervorwahl(land_code)
        e164 = self._ziel_e164(ziel, land_code)
        teile = _e164_teile(e164) if e164 else None
        if not teile:
            # Ungültig – trotzdem die genauere Meldung, wenn es eine gesperrte Nummer ist
            teile = _grob_teile(TRENNER.sub('', ziel.strip()), vorwahl)
        land_vorwahl, rest = teile
        if land_vorwahl in GESPERRTE_VORWAHLEN:
            return _("Satelliten- und globale Sonderdienste (+881, +882, +883) sind als Weiterleitungsziel "
                     "nicht erlaubt.")
        if rest in KURZ_NUMMERN or rest.startswith(KURZ_PRAEFIXE):
            return _("Notruf-, Kurzwahl- und Auskunftsnummern sind als Weiterleitungsziel nicht erlaubt.")
        if len(rest) + 1 < WEITERLEITUNG_MIN_ZIFFERN:
            return _("Ein Weiterleitungsziel braucht mindestens %s Ziffern.", WEITERLEITUNG_MIN_ZIFFERN)
        if rest.startswith(SONDER_PRAEFIXE.get(land_vorwahl, ())):
            return _("Sonder- und Mehrwertnummern (z. B. 0900, 0137, 0180, 0700) sind als "
                     "Weiterleitungsziel nicht erlaubt.")
        if land_vorwahl != vorwahl and \
                self.env['ir.config_parameter'].sudo().get_param(PARAM_AUSLAND) != '1':
            return _("Weiterleitungen ins Ausland sind gesperrt (Freigabe über den Systemparameter %s).",
                     PARAM_AUSLAND)
        if not e164:
            return ungueltig
        return False

    @api.model
    def _ziel_normal(self, ziel, land_code=None):
        """Return a comparable form: E.164 if possible, else digits only.

        Ansitel returns national (``0170…``), ``0049…`` or ``+49…`` numbers.
        """
        ziel = (ziel or '').strip()
        if ziel.startswith('00'):
            ziel = '+' + ziel[2:]
        return nummer.e164(ziel, land_code or 'DE') or NICHT_ZIFFER.sub('', ziel)

    @api.model
    def _ziel_gleich(self, a, b, land_code=None):
        """Return True if both numbers denote the same target."""
        a, b = self._ziel_normal(a, land_code), self._ziel_normal(b, land_code)
        return bool(a) and a == b

    @api.model
    def _ziel_senden(self, ziel):
        """Return the dialling form for Ansitel: digits only, a leading ``+`` becomes ``00``."""
        ziel = TRENNER.sub('', (ziel or '').strip())
        return '00' + ziel[1:] if ziel.startswith('+') else ziel

    def _ziel_fuer(self, benutzer):
        """Return the forwarding target of this key for ``benutzer`` (own mobile or fixed number)."""
        self.ensure_one()
        if self.ziel_art == 'handy':
            return (benutzer.sudo().wb_voip_handy or '').strip()
        return (self.ziel or '').strip()
