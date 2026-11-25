import re

import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from odoo.addons.wb_voip.tools import nummer

# Rufnummer: optional „+“, dann Ziffern mit üblichen Trennzeichen
ZIEL_MUSTER = re.compile(r'^\+?\d[\d\s()/\-]*$')
ZIEL_MIN_ZIFFERN = 3
# Weiterleitungsziele brauchen mindestens so viele Ziffern (Kurzwahlen bleiben draußen)
WEITERLEITUNG_MIN_ZIFFERN = 6
# Deutsche Sonder- und Mehrwertnummern (nationale Form): Gebührenbetrug über die Weiterleitung
SONDER_PRAEFIXE = ('0900', '0137', '0138', '0180', '0190', '0191', '0192', '0193', '0194', '0700', '0118')
# Notruf, Behördennummer, harmonisierte Dienste, Auskunft (ohne Vorwahl gewählt)
KURZ_NUMMERN = ('110', '112', '115')
KURZ_PRAEFIXE = ('116', '118')
# Ländervorwahlen der Länder, in denen eine Anlage steht (Fallback ohne phonenumbers)
LAENDERVORWAHL_FALLBACK = {'DE': 49, 'AT': 43, 'CH': 41}
PARAM_AUSLAND = 'wb_voip_ansitel.ausland_erlaubt'

_logger = logging.getLogger(__name__)


def _laendervorwahl(land_code):
    """Return the country calling code (int) of an ISO-2 country code."""
    try:
        import phonenumbers
        vorwahl = phonenumbers.country_code_for_region((land_code or 'DE').upper())
    except ImportError:
        vorwahl = 0
    return vorwahl or LAENDERVORWAHL_FALLBACK.get((land_code or 'DE').upper(), 49)


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

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user._is_system() and any((vals.get('ziel') or '').strip() for vals in vals_list):
            raise self._ziel_fehler()
        return super().create(vals_list)

    def write(self, vals):
        # Gebührenbetrug: das Ziel einer festen Taste gilt für alle Benutzer
        if 'ziel' in vals and not self.env.user._is_system():
            neu = (vals['ziel'] or '').strip() or False
            if any(((taste.ziel or '').strip() or False) != neu for taste in self.sudo()):
                raise self._ziel_fehler()
        return super().write(vals)

    @api.constrains('ziel')
    def _check_ziel(self):
        Anlage = self.env['wb.voip.anlage'].sudo()
        laender = set(Anlage.search([('ansitel_aktiv', '=', True)]).mapped('country_id.code')) or {'DE'}
        for taste in self.filtered(lambda t: (t.ziel or '').strip()):
            fehler = [self._ziel_pruefen(taste.ziel, land) for land in sorted(laender)]
            if all(fehler):
                raise ValidationError(fehler[0])

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
    def _ziel_pruefen(self, ziel, land_code=None):
        """Return the user message why ``ziel`` may not be a forwarding target, or ``False``.

        Refused: malformed numbers, fewer than six digits, emergency/short/directory numbers,
        German premium and service numbers, and targets abroad (country code other than the
        one of ``land_code``) unless the system parameter ``wb_voip_ansitel.ausland_erlaubt`` is ``1``.
        """
        _ = self.env._
        if not self._ziel_gueltig(ziel):
            return _("„%s“ ist keine gültige Rufnummer (z. B. +49 170 1234567).", ziel or '')
        roh = re.sub(r'[\s()/\-]', '', ziel.strip())
        vorwahl = str(_laendervorwahl(land_code))
        if roh.startswith('+'):
            international = roh[1:]
        elif roh.startswith('00'):
            international = roh[2:]
        else:
            international = None
        if international is None:
            national, inland, deutsch = roh, True, vorwahl == '49'
        elif international.startswith(vorwahl):
            national, inland, deutsch = '0' + international[len(vorwahl):], True, vorwahl == '49'
        else:
            inland = False
            deutsch = international.startswith('49')
            national = '0' + international[2:] if deutsch else international
        if national in KURZ_NUMMERN or national.startswith(KURZ_PRAEFIXE):
            return _("Notruf-, Kurzwahl- und Auskunftsnummern sind als Weiterleitungsziel nicht erlaubt.")
        if len(re.sub(r'\D', '', roh)) < WEITERLEITUNG_MIN_ZIFFERN:
            return _("Ein Weiterleitungsziel braucht mindestens %s Ziffern.", WEITERLEITUNG_MIN_ZIFFERN)
        if deutsch and national.startswith(SONDER_PRAEFIXE):
            return _("Sonder- und Mehrwertnummern (z. B. 0900, 0137, 0180, 0700) sind als "
                     "Weiterleitungsziel nicht erlaubt.")
        if not inland and self.env['ir.config_parameter'].sudo().get_param(PARAM_AUSLAND) != '1':
            return _("Weiterleitungen ins Ausland sind gesperrt (Freigabe über den Systemparameter %s).",
                     PARAM_AUSLAND)
        if not nummer.e164('+' + international if international is not None else roh, land_code or 'DE'):
            return _("„%s“ ist keine gültige Rufnummer (z. B. +49 170 1234567).", ziel)
        return False

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
