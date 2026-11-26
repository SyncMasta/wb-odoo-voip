import ast
import logging
from datetime import datetime, timedelta
from itertools import groupby
from urllib.parse import quote, urlsplit

from dateutil.relativedelta import relativedelta

import psycopg2

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.fields import Domain
from odoo.tools import clean_context, html2plaintext, plaintext2html

from odoo.addons.wb_license_client.models.wb_license_client import license_required
from odoo.addons.web.controllers.utils import clean_action

from ..tools import serverfelder
from ..tools.eingaben import limit_pruefen
from ..tools.rechte import nur_voip_benutzer
from ..tools.nummer import e164
from . import PRODUKT_CODE
from .wb_voip_aktion import (
    URL_PLATZHALTER_MUSTER, WbVoipAktion, assistenten_aktion, serveraktion_erlaubt)

_logger = logging.getLogger(__name__)

# Anrufe ohne Lebenszeichen gelten nach dieser Zeit als verloren (Tab geschlossen o. Ä.)
AUFRAEUM_STUNDEN = 4
OFFENE_ZUSTAENDE = ('klingelt', 'laeuft')
FINALE_ZUSTAENDE = ('beendet', 'abgelehnt', 'besetzt', 'fehlgeschlagen', 'verpasst', 'anderswo')
RICHTUNGEN = ('eingehend', 'ausgehend')
# Endzustand eines nicht angenommenen Anrufs je SIP-Grund; alles andere -> fehlgeschlagen
SIP_GRUND_ZUSTAND = {
    '603': 'abgelehnt',  # Decline: abgelehnt
    '480': 'abgelehnt',  # Temporarily Unavailable: manche Anlagen melden so die Ablehnung
    '487': 'abgelehnt',  # Request Terminated: vor der Annahme abgebrochen
    '486': 'besetzt',    # Busy Here
    '600': 'besetzt',    # Busy Everywhere
}
# Eingehend gilt anders: Abbruch des Anrufers (487) und eigene automatische Abweisung (480) sind
# „verpasst“, die Annahme an einem anderen Gerät („anderswo“) ist weder verpasst noch abgelehnt.
SIP_GRUND_ZUSTAND_EINGEHEND = {
    **SIP_GRUND_ZUSTAND,
    '487': 'verpasst',
    '480': 'verpasst',
    'anderswo': 'anderswo',
}
# Gleichzeitige Anrufe (Anlage klingelt bei mehreren Benutzern): gleiche E.164-Nummer, eingehend,
# anderer Benutzer, gemeinsame Firma, Beginn höchstens so viele Sekunden auseinander
GLEICHZEITIG_SEKUNDEN = 10
GLEICHZEITIG_HOECHSTENS = 50
# Ein „anderswo“ ohne eigene Annahme darf der Client nur so kurz nach dem Beginn noch als
# angenommen melden (z. B. Zentrale mit einer Nummer für zwei Anrufe); später ist es ein No-op
NACHTRAEGLICH_ANGENOMMEN_SEKUNDEN = 120
# Obergrenze der Verpasst-Einträge, die eine Annahme oder ein Rückruf auf einmal erledigt
ERLEDIGEN_HOECHSTENS = 500
# Verpasst-Liste: höchstens so viele offene Einträge je Abruf, Notiz des Kollegen gekürzt
VERPASST_HOECHSTENS = 2000
VERPASST_NOTIZ_MAX = 300
# Höchstlänge des Erledigt-Texts (auch in mail_activity.py)
ERLEDIGT_TEXT_MAX = 2000
RUECKRUF_TYP = 'wb_voip.wb_voip_activity_type_rueckruf'
# Höchstlängen der Angaben an die RPC-Methoden
MAX_NUMMER = 64
MAX_SIP_CALL_ID = 255
MAX_RES_MODEL = 128
MAX_NOTIZ = 10_000
MAX_SIP_GRUND = 8
# Löschfrist: Systemparameter in Monaten (0 = nie), gelöscht wird in Stapeln
AUFBEWAHRUNG_PARAMETER = 'wb_voip.aufbewahrung_monate'
AUFBEWAHRUNG_STANDARD = 12
LOESCH_STAPEL = 1000
# Anrufer-Kontext: Obergrenzen je Abruf
KONTEXT_PROTOKOLLE = 5
KONTEXT_AUFGABEN = 10
# Höchstzahl je Liste, auch wenn das Layout mehr verlangt
KONTEXT_HOECHSTENS = 20
# Obergrenze der Buttons je Abruf (nach Ort, Richtung, Zustand und Gruppe vorgefiltert)
KONTEXT_AKTIONEN = 200
AKTION_ORTE = ('anrufer_fenster', 'protokoll_popup', 'softphone')
KONTEXT_TEXT_MAX = 300
KONTEXT_HTML_MAX = 10_000
# Kontext-Schlüssel des Aufrufers, die an eine Serveraktion weitergehen (alles andere nicht)
SERVERAKTION_KONTEXT = ('lang', 'tz', 'allowed_company_ids')


def _text_pruefen(env, wert, bezeichnung, hoechstens, pflicht):
    """Raise ``UserError`` unless ``wert`` is a str of at most ``hoechstens`` characters.

    ``pflicht``: must not be empty; otherwise ``False`` is accepted as "no value".
    """
    if not pflicht and wert is False:
        return
    if not isinstance(wert, str) or len(wert) > hoechstens or (pflicht and not wert.strip()):
        raise UserError(env._("Invalid value: %s.", bezeichnung))


def _kontext_mischen(text, zusatz):
    """Return the context of a window action with ``zusatz`` added.

    A literal dict is merged here. A dict with expressions (e.g. ``uid``) stays a string
    for the web client to evaluate; ``zusatz`` (ints/strings only) is appended inside its
    braces. Anything else is dropped (only ``zusatz`` remains). Nothing is evaluated here.
    """
    if not text:
        return dict(zusatz)
    try:
        wert = ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        wert = None
    if isinstance(wert, dict):
        return {**wert, **zusatz}
    try:
        baum = ast.parse(text.strip(), mode='eval')
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        _logger.debug("[wb_voip] Kontext der Fensteraktion nicht mischbar, nur Zusatzschlüssel")
        return dict(zusatz)
    dict_knoten = baum.body
    if not isinstance(dict_knoten, ast.Dict) or None in dict_knoten.keys \
            or dict_knoten.end_lineno is None:
        _logger.debug("[wb_voip] Kontext der Fensteraktion nicht mischbar, nur Zusatzschlüssel")
        return dict(zusatz)
    # Einfügestelle aus dem Syntaxbaum: direkt hinter dem letzten Wert (bei leerem Dict hinter
    # der öffnenden Klammer). So bleiben Kommentare und ein Komma dahinter unberührt.
    # (col_offset zählt UTF-8-Bytes)
    quelle = text.strip().encode('utf-8')
    zeilen = quelle.split(b'\n')

    def versatz(zeile, spalte):
        return sum(len(z) + 1 for z in zeilen[:zeile - 1]) + spalte

    extra = ', '.join(f'{k!r}: {v!r}' for k, v in zusatz.items())
    if dict_knoten.values:
        letzter = dict_knoten.values[-1]
        if letzter.end_lineno is None:
            _logger.debug("[wb_voip] Kontext der Fensteraktion nicht mischbar, nur Zusatzschlüssel")
            return dict(zusatz)
        stelle = versatz(letzter.end_lineno, letzter.end_col_offset)
        extra = ', ' + extra
    else:
        stelle = versatz(dict_knoten.lineno, dict_knoten.col_offset) + 1
    ergebnis = quelle[:stelle].decode('utf-8') + extra + quelle[stelle:].decode('utf-8')
    try:  # z. B. ein eingeklammerter letzter Wert würde hier ungültig: dann nur ``zusatz``
        ast.parse(ergebnis, mode='eval')
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        _logger.debug("[wb_voip] Kontext der Fensteraktion nicht mischbar, nur Zusatzschlüssel")
        return dict(zusatz)
    return ergebnis


class WbVoipAnruf(models.Model):
    _name = 'wb.voip.anruf'
    _description = 'WB VoIP: Call'
    _order = 'beginn desc, id desc'

    # Nur der Server darf diese Felder ändern (siehe write/create). Die RPC-Methoden
    # prüfen zuerst per check_access('write'), dass der Datensatz dem Aufrufer gehört,
    # und schreiben danach per sudo(). Ein Kontext-Schlüssel würde nicht reichen, weil
    # Clients per RPC beliebigen Kontext mitschicken können.
    _WB_VOIP_NUR_SERVER = (
        'sip_call_id', 'zustand', 'annahme', 'ende', 'dauer_sek', 'nummer_e164',
        'user_id', 'zuletzt_gesehen', 'richtung', 'beginn',
        'partner_id', 'res_model', 'res_id', 'protokoll_id',
        'company_id', 'angenommen_anruf_id', 'rueckruf_aktivitaet_id', 'erledigt',
        'erledigt_am', 'erledigt_text', 'erledigt_durch_protokoll_id', 'gesehen',
    )

    user_id = fields.Many2one(
        'res.users', string='User', required=True, index=True,
        default=lambda self: self.env.user, ondelete='cascade')
    richtung = fields.Selection(
        [('eingehend', 'Incoming'), ('ausgehend', 'Outgoing')],
        string='Direction', required=True)
    nummer = fields.Char(string='Number')
    nummer_e164 = fields.Char(string='Number (E.164)', index=True)
    sip_call_id = fields.Char(string='SIP call ID', index=True)
    zustand = fields.Selection(
        [('neu', 'New'), ('klingelt', 'Ringing'), ('laeuft', 'Running'),
         ('beendet', 'Ended'), ('abgelehnt', 'Rejected'), ('besetzt', 'Busy'),
         ('fehlgeschlagen', 'Failed'), ('verpasst', 'Missed'),
         ('anderswo', 'Answered elsewhere')],
        string='State', required=True, default='neu', index=True)
    partner_id = fields.Many2one('res.partner', string='Contact', ondelete='set null')
    res_model = fields.Char(string='Record model')
    res_id = fields.Many2oneReference(
        string='Record', model_field='res_model')
    beginn = fields.Datetime(string='Start', default=fields.Datetime.now)
    annahme = fields.Datetime(string='Answer')
    ende = fields.Datetime(string='End')
    dauer_sek = fields.Integer(string='Duration (s)')
    zuletzt_gesehen = fields.Datetime(string='Last seen')
    notiz = fields.Text(string='Note')
    protokoll_id = fields.Many2one(
        'wb.voip.protokoll', string='Call log', copy=False, index=True, ondelete='set null')
    company_id = fields.Many2one(
        'res.company', string='Company', index=True, default=lambda self: self.env.company)
    # Verpasst-Liste: Anruf des Kollegen, der angenommen hat („anderswo“)
    angenommen_anruf_id = fields.Many2one(
        'wb.voip.anruf', string='Answered with', copy=False, ondelete='set null',
        index='btree_not_null')
    rueckruf_aktivitaet_id = fields.Many2one(
        'mail.activity', string='Callback activity', copy=False, ondelete='set null',
        index='btree_not_null')
    erledigt = fields.Boolean(string='Done', copy=False, index=True)
    erledigt_am = fields.Datetime(string='Done on', copy=False)
    erledigt_text = fields.Text(string='Done how', copy=False)
    erledigt_durch_protokoll_id = fields.Many2one(
        'wb.voip.protokoll', string='Done by call log', copy=False, ondelete='set null',
        index='btree_not_null')
    gesehen = fields.Boolean(string='Seen', copy=False)

    _sip_call_id_je_benutzer = models.Constraint(
        'unique(user_id, sip_call_id)',
        'This SIP call ID already exists for the user.')

    # --- Schutz der Serverfelder ---

    @api.model
    def _wb_voip_nur_server(self):
        """Return the server-only field names; extensions add theirs through ``super()``."""
        return self._WB_VOIP_NUR_SERVER

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            nur_server = self._wb_voip_nur_server()
            for vals in vals_list:
                serverfelder.pruefen(self.env, vals, nur_server)
            self = self.with_context(
                serverfelder.kontext_ohne_defaults(self.env, nur_server))
        anrufe = super().create(vals_list)
        # Neuer Anruf, der gleich klingelt oder läuft: nur melden, wenn sich der Status ändert
        besitzer = anrufe.sudo().filtered(lambda a: a.zustand in OFFENE_ZUSTAENDE).user_id
        vorher = besitzer._wb_voip_team_status(ausser=anrufe)
        nachher = besitzer._wb_voip_team_status()
        besitzer.filtered(lambda u: vorher[u.id] != nachher[u.id])._wb_voip_team_melden()
        return anrufe

    def write(self, vals):
        if not self.env.su:
            serverfelder.pruefen(self.env, vals, self._wb_voip_nur_server())
        if 'zustand' not in vals:
            return super().write(vals)
        # Team-Status: nur Besitzer melden, deren abgeleiteter Status sich wirklich ändert
        besitzer = self.sudo().filtered(lambda a: a.zustand != vals['zustand']).user_id
        vorher = besitzer._wb_voip_team_status()
        ergebnis = super().write(vals)
        nachher = besitzer._wb_voip_team_status()
        besitzer.filtered(lambda u: vorher[u.id] != nachher[u.id])._wb_voip_team_melden()
        return ergebnis

    # --- Hilfen ---

    def _wb_voip_eigener(self):
        """Check that the single record belongs to the caller; return it with sudo."""
        self.ensure_one()
        self.check_access('write')  # Datensatzregel: fremde Anrufe sind unsichtbar
        if self.user_id != self.env.user:
            raise UserError(self.env._("This is not your call."))
        return self.sudo()

    @api.model
    def _wb_voip_kontakt(self, ziel, nummer_e164):
        """Pick the contact id for a new call (only contacts the caller may read).

        ``ziel`` is the checked record from ``_wb_voip_ziel`` or None. Order: the
        named partner, the partner of the named record, the unique contact owning
        the number.
        """
        Partner = self.env['res.partner']
        partner = Partner.browse()
        if ziel is not None:
            if ziel._name == 'res.partner':
                partner = ziel
            elif 'partner_id' in ziel._fields \
                    and ziel._fields['partner_id'].comodel_name == 'res.partner':
                partner = ziel.sudo().partner_id
        if not partner:
            partner = Partner._wb_voip_finden(nummer_e164)
        partner = partner.sudo().with_user(self.env.user)
        return partner.id if partner and partner.has_access('read') else False

    @api.model
    def _wb_voip_ziel(self, res_model, res_id):
        """Return the named record, or None unless the caller may use it.

        The model must be registered, regular (not abstract/transient), ``res_id``
        a positive int, the record must exist and the caller (not sudo) must be
        allowed to read it. Never raises on bad client input.
        """
        if not isinstance(res_model, str) or res_model not in self.env:
            return None
        if type(res_id) is not int or res_id <= 0:
            return None
        modell = self.env[res_model]
        if modell._abstract or modell._transient:
            return None
        ziel = modell.with_user(self.env.user).browse(res_id).exists()
        if not ziel or not ziel.has_access('read'):
            return None
        return ziel

    @api.model
    def _wb_voip_zuordnung(self, partner, ziel):
        """Extension point: propose the record a new call belongs to (default: none).

        Called as the caller (never sudo) when a call is created, after the contact was
        chosen. ``partner`` is that contact (``res.partner`` in the caller's environment,
        readable, possibly empty); ``ziel`` the record passed when dialling (checked by
        ``_wb_voip_ziel``) or None. Return one record or None. The result is used only if
        no record was passed or the passed record is the contact itself, and only after
        ``_wb_voip_ziel`` confirmed the caller may read it.
        """
        return None

    @api.model
    def _wb_voip_ziel_und_kontakt(self, res_model, res_id, nummer_e164):
        """Return ``(ziel, partner_id)`` for a new call of the caller.

        ``ziel`` is the passed record if readable, else the record proposed by
        ``_wb_voip_zuordnung`` (if readable), else None.
        """
        ziel = self._wb_voip_ziel(res_model, res_id)
        partner_id = self._wb_voip_kontakt(ziel, nummer_e164)
        partner = self.env['res.partner'].browse(partner_id)
        vorschlag = self._wb_voip_zuordnung(partner, ziel)
        # ein übergebener Datensatz hat Vorrang; nur der Kontakt selbst darf ersetzt werden
        if vorschlag is not None and (ziel is None or ziel == partner) \
                and isinstance(vorschlag, models.BaseModel) and len(vorschlag) == 1:
            gepruefter = self._wb_voip_ziel(vorschlag._name, vorschlag.id)
            if gepruefter is not None:
                ziel = gepruefter
        return ziel, partner_id

    def _wb_voip_datensaetze(self):
        """Return ``{call id: (res_model, res_id, res_name)}`` for the calls in ``self``.

        Only records that exist and that the caller may read (as superuser: all existing);
        checked per model in one go. A failing name compute gives ``res_name`` False.
        """
        paare = {}
        for anruf in self.sudo():
            if anruf.res_model and type(anruf.res_id) is int and anruf.res_id > 0:
                paare.setdefault(anruf.res_model, []).append((anruf.id, anruf.res_id))
        ergebnis = {}
        for res_model, eintraege in paare.items():
            if res_model not in self.env:
                continue
            modell = self.env[res_model]
            if modell._abstract or modell._transient:
                continue
            datensaetze = modell.browse({res_id for _anruf, res_id in eintraege}).exists()
            if not self.env.su:
                datensaetze = datensaetze._filtered_access('read')
            namen = self._wb_voip_namen(datensaetze)
            for anruf_id, res_id in eintraege:
                if res_id in namen:
                    ergebnis[anruf_id] = (res_model, res_id, namen[res_id])
        return ergebnis

    @api.model
    def _wb_voip_namen(self, datensaetze):
        """Return ``{id: display_name or False}``; never raises for a failing name compute."""
        try:
            return {d.id: d.display_name or False for d in datensaetze}
        except (AccessError, MissingError):
            pass
        namen = {}
        for datensatz in datensaetze:
            try:
                namen[datensatz.id] = datensatz.with_prefetch().display_name or False
            except (AccessError, MissingError):
                namen[datensatz.id] = False
        return namen

    def _wb_voip_client_daten(self, datensaetze=None):
        """Return the call data the browser client needs (no credentials).

        ``datensaetze``: result of ``_wb_voip_datensaetze`` for several calls (lists);
        without it the record of this call is checked here.
        """
        self.ensure_one()
        # Kontakt nur nennen, wenn der Benutzer ihn (noch) lesen darf
        partner = self.partner_id
        if partner and not self.env.su and not partner.has_access('read'):
            partner = partner.browse()
        if datensaetze is None:
            datensaetze = self._wb_voip_datensaetze()
        res_model, res_id, res_name = datensaetze.get(self.id, (False, False, False))
        return {
            'id': self.id,
            'richtung': self.richtung,
            'nummer': self.nummer or False,
            'zustand': self.zustand,
            'partner': (partner.id, partner.display_name) if partner else False,
            # Datensatz (z. B. Vorgang) nur, wenn der Benutzer ihn lesen darf
            'res_model': res_model,
            'res_id': res_id,
            'res_name': res_name,
            'beginn': fields.Datetime.to_string(self.beginn) if self.beginn else False,
            'dauer_sek': self.dauer_sek,
            'protokoll_id': self.protokoll_id.id or False,
        }

    # --- RPC-Methoden ---

    @api.model
    @nur_voip_benutzer
    def wb_voip_liste(self, limit=50):
        """Return the caller's own latest calls (newest first) for the softphone tab.

        Also administrators get only their own calls here.
        """
        limit_pruefen(self.env, limit)
        anrufe = self.search([('user_id', '=', self.env.uid)], limit=limit)
        datensaetze = anrufe._wb_voip_datensaetze()
        return [anruf._wb_voip_client_daten(datensaetze) for anruf in anrufe]

    @api.model
    @nur_voip_benutzer
    @license_required(PRODUKT_CODE)
    def wb_voip_beginnen(self, richtung, nummer, sip_call_id, res_model, res_id):
        """Start (or fetch) the call of the current user for a SIP call id.

        Idempotent per (user, sip_call_id); new calls begin in state ``klingelt``.
        ``res_model``/``res_id`` are stored only if they name a record the caller
        may read, otherwise both stay empty.
        """
        if not isinstance(richtung, str) or richtung not in RICHTUNGEN:
            raise UserError(self.env._("Invalid direction."))
        _text_pruefen(self.env, nummer, self.env._("Number"), MAX_NUMMER, pflicht=True)
        _text_pruefen(self.env, sip_call_id, self.env._("SIP call ID"), MAX_SIP_CALL_ID,
                      pflicht=True)
        _text_pruefen(self.env, res_model, self.env._("Record model"), MAX_RES_MODEL,
                      pflicht=False)
        if res_id is not False and type(res_id) is not int:
            raise UserError(self.env._("Invalid value: %s.", self.env._("Record")))
        benutzer = self.env.user
        vorhanden = self.sudo().search([
            ('user_id', '=', benutzer.id), ('sip_call_id', '=', sip_call_id)], limit=1)
        if not vorhanden:
            land = benutzer.sudo().wb_voip_anlage_id.country_id.code or 'DE'
            nummer_e164 = e164(nummer, land)
            ziel, partner_id = self._wb_voip_ziel_und_kontakt(res_model, res_id, nummer_e164)
            werte = {
                'user_id': benutzer.id,
                'richtung': richtung,
                'nummer': nummer,
                'nummer_e164': nummer_e164,
                'sip_call_id': sip_call_id,
                'zustand': 'klingelt',
                'partner_id': partner_id,
                'res_model': ziel._name if ziel is not None else False,
                'res_id': ziel.id if ziel is not None else False,
                'beginn': fields.Datetime.now(),
                'zuletzt_gesehen': fields.Datetime.now(),
                # aktuelle Firma des Benutzers (ohne sudo geprüft gegen seine Firmen)
                'company_id': self.env.company.id,
            }
            # Zweiter paralleler Aufruf mit derselben (Benutzer, SIP-Call-ID): Savepoint,
            # bei Unique-Verletzung den vorhandenen Anruf liefern statt IntegrityError.
            try:
                with self.env.cr.savepoint():
                    vorhanden = self.sudo().create(werte)
            except psycopg2.errors.UniqueViolation:
                vorhanden = self.sudo().search([
                    ('user_id', '=', benutzer.id), ('sip_call_id', '=', sip_call_id)], limit=1)
                if not vorhanden:
                    raise
        return vorhanden.with_user(benutzer)._wb_voip_client_daten()

    @api.model
    @nur_voip_benutzer
    @license_required(PRODUKT_CODE)
    def wb_voip_abgewiesen_melden(self, nummer, sip_call_id):
        """Record an incoming call the phone rejected itself (third call, 480) as missed.

        Created directly in a final state, so the team never sees a short ``klingelt``; log
        entry, callback activity and missed-list message as for any missed call (``anderswo``
        if a colleague already answered it). Idempotent per (user, sip_call_id).
        """
        _text_pruefen(self.env, nummer, self.env._("Number"), MAX_NUMMER, pflicht=True)
        _text_pruefen(self.env, sip_call_id, self.env._("SIP call ID"), MAX_SIP_CALL_ID,
                      pflicht=True)
        benutzer = self.env.user
        vorhanden = self.sudo().search([
            ('user_id', '=', benutzer.id), ('sip_call_id', '=', sip_call_id)], limit=1)
        if vorhanden:
            return vorhanden.with_user(benutzer)._wb_voip_client_daten()
        land = benutzer.sudo().wb_voip_anlage_id.country_id.code or 'DE'
        nummer_e164 = e164(nummer, land)
        ziel, partner_id = self._wb_voip_ziel_und_kontakt(False, False, nummer_e164)
        jetzt = fields.Datetime.now()
        werte = {
            'user_id': benutzer.id,
            'richtung': 'eingehend',
            'nummer': nummer,
            'nummer_e164': nummer_e164,
            'sip_call_id': sip_call_id,
            'zustand': 'verpasst',
            'partner_id': partner_id,
            'res_model': ziel._name if ziel is not None else False,
            'res_id': ziel.id if ziel is not None else False,
            'beginn': jetzt,
            'ende': jetzt,
            'zuletzt_gesehen': jetzt,
            'company_id': self.env.company.id,
        }
        try:
            with self.env.cr.savepoint():
                anruf = self.sudo().create(werte)
        except psycopg2.errors.UniqueViolation:
            anruf = self.sudo().search([
                ('user_id', '=', benutzer.id), ('sip_call_id', '=', sip_call_id)], limit=1)
            if not anruf:
                raise
            return anruf.with_user(benutzer)._wb_voip_client_daten()
        if anruf._wb_voip_angenommen_von():
            anruf.zustand = 'anderswo'
        if anruf.zustand != 'anderswo':
            anruf._wb_voip_protokollieren()
        anruf._wb_voip_nach_beenden(False)
        return anruf.with_user(benutzer)._wb_voip_client_daten()

    @nur_voip_benutzer
    def wb_voip_angenommen(self):
        """Mark the call as answered (state ``laeuft``).

        Also a call set to ``anderswo`` by a colleague's answer counts as answered when the
        caller really answers it (``annahme`` still empty): e.g. a switchboard sending one
        number for two different calls. Its link and end are cleared. Only within
        ``NACHTRAEGLICH_ANGENOMMEN_SEKUNDEN`` after the start; later it is a no-op.
        """
        anruf = self._wb_voip_eigener()
        if anruf.zustand in ('neu', 'klingelt') or anruf._wb_voip_anderswo_offen():
            jetzt = fields.Datetime.now()
            werte = {'zustand': 'laeuft', 'annahme': jetzt, 'zuletzt_gesehen': jetzt}
            if anruf.zustand == 'anderswo':
                werte.update({'angenommen_anruf_id': False, 'ende': False})
            anruf.write(werte)
            if anruf.richtung == 'eingehend':
                anruf._wb_voip_nach_annahme()
        return anruf.with_user(self.env.user)._wb_voip_client_daten()

    @nur_voip_benutzer
    def wb_voip_beendet(self, notiz, sip_grund, angenommen=False):
        """Finish the call. Mapping of the final state:

        * answered before (``annahme`` set)          -> ``beendet``
        * ``angenommen`` True but ``annahme`` empty  -> ``annahme`` now, ``beendet``
          (the client saw the answer, ``wb_voip_angenommen`` lost the race)
        * not answered, outgoing, ``603``/``480``/``487`` -> ``abgelehnt``
        * not answered, incoming, ``487``/``480``     -> ``verpasst`` (log: not reached), but
          ``anderswo`` if a colleague already answered the same call
        * ``anderswo`` without ``annahme`` and ``angenommen`` True -> answered here, ``beendet``
          (only within ``NACHTRAEGLICH_ANGENOMMEN_SEKUNDEN`` after the start, else no-op)
        * not answered, incoming, ``anderswo``       -> ``anderswo`` (answered on another
          device; no log entry)
        * not answered, ``603`` -> ``abgelehnt``; ``486`` or ``600`` -> ``besetzt``
        * not answered, anything else / no reason     -> ``fehlgeschlagen``

        (see ``SIP_GRUND_ZUSTAND`` and ``SIP_GRUND_ZUSTAND_EINGEHEND``). ``dauer_sek`` counts from the answer. A call
        that is already in a final state is returned unchanged (no-op).
        """
        _text_pruefen(self.env, notiz, self.env._("Note"), MAX_NOTIZ, pflicht=False)
        _text_pruefen(self.env, sip_grund, self.env._("SIP reason"), MAX_SIP_GRUND, pflicht=False)
        if type(angenommen) is not bool:
            raise UserError(self.env._("Invalid value: %s.", self.env._("Answered")))
        anruf = self._wb_voip_eigener()
        uebergang = neu_angenommen = False
        # ``anderswo`` ohne Annahme, aber der Client hat angenommen: wie wb_voip_angenommen
        if anruf.zustand in OFFENE_ZUSTAENDE or anruf.zustand == 'neu' \
                or (angenommen and anruf._wb_voip_anderswo_offen()):
            uebergang = True
            jetzt = fields.Datetime.now()
            if angenommen and not anruf.annahme:
                anruf.write({'annahme': jetzt, 'angenommen_anruf_id': False})
                neu_angenommen = True
            werte = {'ende': jetzt, 'zuletzt_gesehen': jetzt}
            if anruf.annahme:
                werte['zustand'] = 'beendet'
                werte['dauer_sek'] = max(0, int((jetzt - anruf.annahme).total_seconds()))
            else:
                zuordnung = (SIP_GRUND_ZUSTAND_EINGEHEND if anruf.richtung == 'eingehend'
                             else SIP_GRUND_ZUSTAND)
                werte['zustand'] = zuordnung.get(sip_grund or '', 'fehlgeschlagen')
                # schlichtes 487, obwohl ein Kollege schon angenommen hat: nicht verpasst
                if werte['zustand'] == 'verpasst' and anruf._wb_voip_angenommen_von():
                    werte['zustand'] = 'anderswo'
            if notiz:
                werte['notiz'] = notiz
            anruf.write(werte)
        if not anruf.protokoll_id and anruf.zustand != 'anderswo':
            anruf._wb_voip_protokollieren()
        if uebergang:
            anruf._wb_voip_nach_beenden(neu_angenommen)
        return anruf.with_user(self.env.user)._wb_voip_client_daten()

    # --- Verpasst, anderswo angenommen, Rückruf ---

    def _wb_voip_nach_beenden(self, neu_angenommen):
        """Follow-up after the call reached its final state (once per call).

        ``verpasst``: callback activity; ``anderswo``: link to the colleague's answered call;
        an incoming call answered only now (``neu_angenommen``): like ``wb_voip_angenommen``.
        """
        self.ensure_one()
        anruf = self.sudo()
        if anruf.zustand == 'verpasst':
            anruf._wb_voip_rueckruf_aktivitaet()
            anruf._wb_voip_verpasst_melden()
        elif anruf.zustand == 'anderswo':
            angenommen = anruf._wb_voip_angenommen_von()
            if angenommen:
                anruf.angenommen_anruf_id = angenommen
            anruf._wb_voip_verpasst_melden()
        elif neu_angenommen and anruf.richtung == 'eingehend':
            anruf._wb_voip_nach_annahme()
        elif anruf.richtung == 'ausgehend' and anruf.zustand == 'beendet' \
                and anruf.protokoll_id.ergebnis == 'erreicht':
            # Rückruf angenommen: offene Verpasst-Einträge der Nummer sind erledigt
            anruf._wb_voip_nummer_erreicht(anruf.protokoll_id)

    def _wb_voip_anderswo_offen(self):
        """Return True for a fresh ``anderswo`` call that was never answered on this device.

        Fresh: started at most ``NACHTRAEGLICH_ANGENOMMEN_SEKUNDEN`` ago. A late client message
        must not turn an old entry into an answered call (and close the colleagues' entries).
        """
        self.ensure_one()
        anruf = self.sudo()
        if anruf.zustand != 'anderswo' or anruf.annahme or not anruf.beginn:
            return False
        grenze = fields.Datetime.now() - timedelta(seconds=NACHTRAEGLICH_ANGENOMMEN_SEKUNDEN)
        return anruf.beginn >= grenze

    def _wb_voip_angenommen_von(self):
        """Return (sudo) the earliest answered simultaneous call of a colleague, if any."""
        self.ensure_one()
        return self._wb_voip_gleichzeitige(('laeuft', 'beendet')).filtered(
            'annahme').sorted(lambda a: (a.annahme, a.id))[:1]

    def _wb_voip_gleichzeitige(self, zustaende):
        """Return (sudo) the calls of other users that belong to the same incoming call.

        Same E.164 number (never without one), both incoming, start at most
        ``GLEICHZEITIG_SEKUNDEN`` apart, state in ``zustaende`` and a shared company: the
        company of each call belongs to the companies of the other call's user. At most
        ``GLEICHZEITIG_HOECHSTENS``, earliest first.
        """
        self.ensure_one()
        anruf = self.sudo()
        if anruf.richtung != 'eingehend' or not anruf.nummer_e164 or not anruf.beginn \
                or not anruf.company_id:
            return anruf.browse()
        fenster = timedelta(seconds=GLEICHZEITIG_SEKUNDEN)
        return anruf.search([
            ('id', '!=', anruf.id),
            ('user_id', '!=', anruf.user_id.id),
            ('richtung', '=', 'eingehend'),
            ('zustand', 'in', list(zustaende)),
            ('nummer_e164', '=', anruf.nummer_e164),
            ('beginn', '>=', anruf.beginn - fenster),
            ('beginn', '<=', anruf.beginn + fenster),
            ('user_id.company_ids', 'in', anruf.company_id.ids),
            ('company_id', 'in', anruf.user_id.company_ids.ids),
        ], order='beginn, id', limit=GLEICHZEITIG_HOECHSTENS)

    def _wb_voip_rueckruf_ziel(self):
        """Extension point: return the record that gets the callback activity.

        ``self`` is the missed call (sudo). Default: its contact. The result is checked in
        ``_wb_voip_rueckruf_aktivitaet`` (one existing record with activities, readable by
        the call's user); otherwise the contact is used.
        """
        self.ensure_one()
        return self.partner_id

    def _wb_voip_rueckruf_datensatz(self):
        """Return the checked target of the callback activity (as the call's user) or None.

        First ``_wb_voip_rueckruf_ziel``, then the contact; each must be a single existing
        record with activities that the call's user may read (with that user's companies,
        not the caller's active ones).
        """
        anruf = self.sudo()
        benutzer = anruf.user_id
        for kandidat in (anruf._wb_voip_rueckruf_ziel(), anruf.partner_id):
            if not isinstance(kandidat, models.BaseModel) or len(kandidat) != 1 \
                    or not hasattr(kandidat, 'activity_schedule'):
                continue
            kandidat = kandidat.with_user(benutzer).with_context(
                allowed_company_ids=benutzer.company_ids.ids)
            if kandidat.exists() and kandidat.has_access('read'):
                return kandidat
        return None

    def _wb_voip_rueckruf_aktivitaet(self):
        """Give the missed call a callback activity (owner = call's user).

        On the record of ``_wb_voip_rueckruf_ziel`` (default: the contact), else on the
        contact; only if the call's user may read it (``_wb_voip_rueckruf_datensatz``), none
        without such a record. An open (active) callback activity of the same user on that
        record is reused. Created with sudo after the read check, so no write right on the
        record is needed.
        """
        self.ensure_one()
        anruf = self.sudo()
        if anruf.zustand != 'verpasst' or anruf.rueckruf_aktivitaet_id.active:
            return anruf.rueckruf_aktivitaet_id
        benutzer = anruf.user_id
        datensatz = anruf._wb_voip_rueckruf_datensatz()
        typ = self.env.ref(RUECKRUF_TYP, raise_if_not_found=False)
        if datensatz is None or not typ:
            return self.env['mail.activity']
        aktivitaet = self.env['mail.activity'].sudo().search([
            ('activity_type_id', '=', typ.id), ('res_model', '=', datensatz._name),
            ('res_id', '=', datensatz.id), ('user_id', '=', benutzer.id), ('active', '=', True),
        ], order='id', limit=1)
        if not aktivitaet:
            sprache = self.with_context(lang=benutzer.lang).env
            # Kontext ohne default_*-Schlüssel des Clients; sudo erst nach der Leseprüfung
            ziel = datensatz.with_context(clean_context(self.env.context)).sudo()
            try:
                with self.env.cr.savepoint():
                    aktivitaet = ziel.activity_schedule(
                        act_type_xmlid=RUECKRUF_TYP,
                        summary=sprache._("Callback: missed call"),
                        note=plaintext2html(sprache._("Missed call from %s", anruf.nummer or '')),
                        user_id=benutzer.id)
            except Exception:  # noqa: BLE001 - die Aktivität darf das Beenden nie verhindern
                _logger.exception("[wb_voip] Rückruf-Aktivität fehlgeschlagen (Anruf %s)", anruf.id)
                return self.env['mail.activity']
        anruf.rueckruf_aktivitaet_id = aktivitaet
        return aktivitaet

    def _wb_voip_nach_annahme(self):
        """An incoming call was answered: simultaneous calls of colleagues become ``anderswo``.

        Ringing, missed and ``anderswo`` calls of other users (see ``_wb_voip_gleichzeitige``)
        are linked to this call. A call that counted as missed loses its callback activity
        and its untouched log "not reached" (``_wb_voip_verpasst_aufraeumen``). Then the open
        missed entries of the number are closed (``_wb_voip_nummer_erreicht``).
        """
        self.ensure_one()
        anruf = self.sudo()
        andere = anruf._wb_voip_gleichzeitige(('klingelt', 'verpasst', 'anderswo'))
        geaendert = anruf.browse()
        jetzt = fields.Datetime.now()
        for anderer in andere:
            if anderer.zustand == 'anderswo':
                # „anderswo“ kam vor dieser Annahme an: nur verknüpfen
                if not anderer.angenommen_anruf_id:
                    anderer.angenommen_anruf_id = anruf
                    geaendert |= anderer
                continue
            aktivitaet = anderer.rueckruf_aktivitaet_id
            protokoll = anderer.protokoll_id
            anderer.write({
                'zustand': 'anderswo',
                'angenommen_anruf_id': anruf.id,
                'ende': anderer.ende or jetzt,
                'rueckruf_aktivitaet_id': False,
            })
            anderer._wb_voip_verpasst_aufraeumen(aktivitaet, protokoll)
            geaendert |= anderer
        geaendert._wb_voip_verpasst_melden()
        anruf._wb_voip_nummer_erreicht()

    def _wb_voip_verpasst_aufraeumen(self, aktivitaet, protokoll):
        """Undo the follow-up of a call that counted as missed but was answered elsewhere.

        Deletes ``aktivitaet`` only if it is an active callback activity of the call's user
        that no other call uses, and ``protokoll`` only if it is this call's own log with
        result "not reached", no description and never changed. Call after the activity
        was detached from the call.
        """
        self.ensure_one()
        anruf = self.sudo()
        typ = self.env.ref(RUECKRUF_TYP, raise_if_not_found=False)
        aktivitaet = aktivitaet.sudo().exists()
        if aktivitaet and aktivitaet.active and typ and aktivitaet.activity_type_id == typ \
                and aktivitaet.user_id == anruf.user_id \
                and not anruf.search_count([('rueckruf_aktivitaet_id', '=', aktivitaet.id)]):
            aktivitaet.unlink()
        protokoll = protokoll.sudo().exists()
        if protokoll and protokoll.anruf_id == anruf and protokoll.user_id == anruf.user_id \
                and protokoll.ergebnis == 'nicht_erreicht' \
                and not html2plaintext(protokoll.beschreibung or '').strip() \
                and protokoll.write_date == protokoll.create_date:
            anruf.protokoll_id = False
            protokoll.unlink()

    def _wb_voip_nummer_erreicht(self, protokoll=None, bis=None, text=None):
        """The number of this call was reached: close the open missed entries of all users.

        Entries: state ``verpasst``, not done, same E.164 number, same company, started before
        this call (with ``bis``: started at the latest at ``bis``, e.g. the end of a call that
        a late synchronisation with the PBX found). ``text`` replaces the closing text (e.g. when
        no user answered, only a device); it is a string or a function of the environment in the
        language of each entry's owner. Text "Zurückgerufen von <name>" (outgoing) or
        "Angenommen von <name>"; with ``protokoll`` (callback log) the entry remembers it.
        With ``protokoll`` also the open entries that this log closed before and that were
        reopened (``erledigt_durch_protokoll_id``, state in ``_wb_voip_wieder_oeffnen_zustaende``)
        close again, e.g. an entry that became ``anderswo`` meanwhile. Active callback activities
        that no entry left open uses are marked done with this text. Other ``anderswo`` entries
        stay.
        """
        self.ensure_one()
        anruf = self.sudo()
        if not anruf.nummer_e164 or not anruf.company_id or not anruf.beginn:
            return anruf.browse()
        offen = anruf.search([
            ('id', '!=', anruf.id),
            ('zustand', '=', 'verpasst'),
            ('erledigt', '=', False),
            ('nummer_e164', '=', anruf.nummer_e164),
            ('company_id', '=', anruf.company_id.id),
            ('beginn', '<=', bis) if bis else ('beginn', '<', anruf.beginn),
        ], order='beginn, id', limit=ERLEDIGEN_HOECHSTENS)
        if protokoll:
            # vom selben Rückruf früher erledigt und wieder geöffnet (auch andere Zustände)
            offen |= anruf.search([
                ('id', '!=', anruf.id),
                ('erledigt_durch_protokoll_id', '=', protokoll.id),
                ('erledigt', '=', False),
                ('zustand', 'in', list(self._wb_voip_wieder_oeffnen_zustaende())),
            ], order='beginn, id', limit=ERLEDIGEN_HOECHSTENS)
        if not offen:
            return offen
        name = anruf.user_id.name
        # Der Text wird gespeichert: je Besitzer des Eintrags in dessen Sprache erzeugen (ein Serverlauf
        # hat keine Sprache im Kontext), nicht in der des Anrufers.
        texte = {}
        for besitzer in offen.user_id:
            sprache = self.with_context(lang=self._wb_voip_sprache(besitzer)).env
            if callable(text):
                texte[besitzer] = text(sprache)
            elif text:
                texte[besitzer] = text
            elif anruf.richtung == 'ausgehend':
                texte[besitzer] = sprache._("Called back by %s", name)
            else:
                texte[besitzer] = sprache._("Answered by %s", name)
        # Verknüpfung stets neu setzen, damit ein früheres Protokoll den Eintrag nicht mehr berührt
        durch = protokoll.id if protokoll else False
        gruppen = []
        for besitzer, eintraege in groupby(offen.sorted(lambda a: a.user_id.id), key=lambda a: a.user_id):
            eintraege = anruf.browse([e.id for e in eintraege])
            eintraege.write({'erledigt': True, 'erledigt_am': fields.Datetime.now(),
                             'erledigt_text': texte[besitzer], 'erledigt_durch_protokoll_id': durch})
            gruppen.append((eintraege.rueckruf_aktivitaet_id, texte[besitzer]))
        # erst nach dem Schließen aller Einträge: eine Aktivität, die noch ein offener Eintrag nutzt, bleibt
        for aktivitaeten, gruppentext in gruppen:
            anruf._wb_voip_aktivitaeten_abschliessen(aktivitaeten, gruppentext)
        offen._wb_voip_verpasst_melden()
        return offen

    def _wb_voip_sprache(self, benutzer=None):
        """Return the language for texts that are stored (not shown).

        Order: language of ``benutzer``, else of the calls' company (when all records belong
        to one company), else of the current company, else English. Server jobs (cron) have no
        language in the context and run in the main company, so ``env._`` would return the
        English source for everybody and ``env.company`` is not the company of the call.
        """
        sprache = benutzer.sudo().lang if benutzer else False
        if not sprache:
            # Firma der Anrufe vor der aktuellen Firma: ein Serverlauf läuft in der Hauptfirma
            firma = self.sudo().company_id
            if len(firma) == 1:
                sprache = firma.partner_id.lang
        return sprache or self.env.company.sudo().partner_id.lang or 'en_US'

    def _wb_voip_aktivitaeten_abschliessen(self, aktivitaeten, text):
        """Mark active callback activities done with ``text`` (plain text; Odoo escapes it in the chatter).

        Call after the entries were closed. An activity that a still open entry uses (e.g.
        another number of the same contact) stays open. Each activity is closed as its owner
        in a savepoint; a failure is logged and never aborts the caller.
        """
        typ = self.env.ref(RUECKRUF_TYP, raise_if_not_found=False)
        for aktivitaet in aktivitaeten.sudo().exists().filtered(
                lambda a: a.active and a.activity_type_id == typ):
            if self.sudo().search_count([('rueckruf_aktivitaet_id', '=', aktivitaet.id),
                                         ('erledigt', '=', False)]):
                continue
            try:
                with self.env.cr.savepoint():
                    # als Besitzer (Autor der Nachricht), sudo für fehlende Schreibrechte
                    aktivitaet.with_user(aktivitaet.user_id).sudo().action_feedback(
                        feedback=text or False)
            except Exception:  # noqa: BLE001 - eine Aktivität darf das Erledigen nicht abbrechen
                _logger.exception("[wb_voip] Rückruf-Aktivität %s nicht abschließbar", aktivitaet.id)

    def _wb_voip_wieder_oeffnen(self):
        """Reopen done missed entries (the result of the callback log changed).

        Each entry in a state of ``_wb_voip_wieder_oeffnen_zustaende`` is open again (no text, no
        date, unseen). A ``verpasst`` entry's owner gets a callback activity again
        (``_wb_voip_rueckruf_aktivitaet``: an active one is reused, an archived one never); other
        states get none. The link to the log stays, so the entries close again when the result
        returns to reached.
        """
        zustaende = self._wb_voip_wieder_oeffnen_zustaende()
        eintraege = self.sudo().filtered(lambda a: a.zustand in zustaende and a.erledigt)
        if not eintraege:
            return eintraege
        eintraege.write({'erledigt': False, 'erledigt_am': False, 'erledigt_text': False,
                         'gesehen': False})
        for eintrag in eintraege.filtered(lambda a: a.zustand == 'verpasst'):
            eintrag._wb_voip_rueckruf_aktivitaet()
        eintraege._wb_voip_verpasst_melden()
        return eintraege

    @api.model
    def _wb_voip_wieder_oeffnen_zustaende(self):
        """Extension point: states of done entries that a changed callback result reopens.

        Default ``('verpasst',)``; e.g. an entry that a later synchronisation turned into
        ``anderswo`` can be added (it gets no new callback activity).
        """
        return ('verpasst',)

    def _wb_voip_durch_aktivitaet_erledigt(self, text):
        """Close open missed entries because their callback activity was marked done."""
        eintraege = self.sudo().filtered(lambda a: a.zustand == 'verpasst' and not a.erledigt)
        if eintraege:
            eintraege.write({
                'erledigt': True, 'erledigt_am': fields.Datetime.now(), 'erledigt_text': text,
                'erledigt_durch_protokoll_id': False})
            eintraege._wb_voip_verpasst_melden()
        return eintraege

    def _wb_voip_verpasst_melden(self):
        """Tell the owners' browser tabs that their missed-call list changed (empty payload)."""
        for benutzer in self.sudo().user_id:
            benutzer._bus_send('wb_voip/verpasst', {})

    # --- Verpasst-Liste: Zeilen, Zähler, Erledigen (RPC) ---

    @api.model
    def _wb_voip_verpasst_offen(self, zusatz=(), limit=VERPASST_HOECHSTENS):
        """Return (sudo) the caller's open entries: missed, or answered elsewhere and visible.

        Visible ``anderswo`` entries: see ``_wb_voip_anderswo_sichtbar_domain``.

        ``zusatz``: extra domain terms (e.g. one group); ``limit`` None = unlimited.
        """
        domain = Domain.AND([
            Domain('user_id', '=', self.env.uid) & Domain('erledigt', '=', False),
            Domain('zustand', '=', 'verpasst')
            | (Domain('zustand', '=', 'anderswo') & Domain(self._wb_voip_anderswo_sichtbar_domain())),
            Domain(list(zusatz)),
        ])
        return self.sudo().search(domain, order='beginn desc, id desc', limit=limit)

    @api.model
    def _wb_voip_anderswo_sichtbar_domain(self):
        """Extension point: which ``anderswo`` entries appear in the missed-call list.

        Default: those linked to the colleague's answered call. Extensions widen it with
        ``super() | Domain(...)`` (e.g. calls answered on a known device of the PBX).
        """
        return Domain('angenommen_anruf_id', '!=', False)

    @staticmethod
    def _wb_voip_verpasst_schluessel(anruf):
        return anruf.nummer_e164 or anruf.nummer or ''

    def _wb_voip_angenommen_info(self):
        """Return what the owner may see of the colleague who answered: name, duration, note.

        Only for the caller's own ``anderswo`` entry with a link to a call of the same company;
        otherwise ``False``. The note is plain text, cut to ``VERPASST_NOTIZ_MAX`` characters.
        Nothing else of the colleague's call is revealed.
        """
        self.ensure_one()
        anruf = self.sudo()
        if anruf.user_id != self.env.user or anruf.zustand != 'anderswo':
            return False
        kollege = anruf.angenommen_anruf_id
        if not kollege or not anruf.company_id or kollege.company_id != anruf.company_id:
            return False
        return {
            'name': kollege.user_id.name,
            'dauer_sek': kollege.dauer_sek,
            'notiz': (kollege.notiz or '')[:VERPASST_NOTIZ_MAX],
        }

    def _wb_voip_verpasst_zeile(self):
        """Return the list row of a group of entries (newest first, all the caller's own)."""
        neuester = self[0]
        partner = neuester._wb_voip_lesbarer_kontakt()
        art = 'anderswo' if neuester.zustand == 'anderswo' else 'verpasst'
        return {
            'schluessel': self._wb_voip_verpasst_schluessel(neuester),
            'anruf_id': neuester.id,
            'anzahl': len(self),
            'art': art,
            'nummer': neuester.nummer or False,
            'partner': [partner.id, partner.display_name] if partner else False,
            'letzter_beginn': fields.Datetime.to_string(neuester.beginn) if neuester.beginn
            else False,
            'kollege': neuester._wb_voip_angenommen_info() if art == 'anderswo' else False,
        }

    @api.model
    @nur_voip_benutzer
    def wb_voip_verpasst_liste(self, limit=50):
        """Return the caller's open missed calls grouped per number, newest group first.

        Group key: E.164 number, else the dialled number. ``limit`` counts groups. Works
        without a license (existing data). The contact is named only if the caller may read it.
        """
        limit_pruefen(self.env, limit)
        gruppen = {}
        for anruf in self._wb_voip_verpasst_offen():
            gruppen.setdefault(self._wb_voip_verpasst_schluessel(anruf), anruf.browse())
            gruppen[self._wb_voip_verpasst_schluessel(anruf)] |= anruf
        zeilen = [g.sorted(lambda a: (a.beginn or datetime.min, a.id), reverse=True)._wb_voip_verpasst_zeile()
                  for g in gruppen.values()]
        zeilen.sort(key=lambda z: (z['letzter_beginn'] or '', z['anruf_id']), reverse=True)
        return zeilen[:limit]

    @api.model
    @nur_voip_benutzer
    def wb_voip_verpasst_zaehler(self):
        """Return the number of the caller's open, unseen missed calls (never "elsewhere")."""
        return self.sudo().search_count([
            ('user_id', '=', self.env.uid), ('zustand', '=', 'verpasst'),
            ('erledigt', '=', False), ('gesehen', '=', False)])

    @api.model
    @nur_voip_benutzer
    def wb_voip_verpasst_gesehen(self):
        """Mark all the caller's open entries as seen; return the new counter."""
        neu = self._wb_voip_verpasst_offen().filtered(lambda a: not a.gesehen)
        if neu:
            neu.write({'gesehen': True})
            neu._wb_voip_verpasst_melden()
        return self.wb_voip_verpasst_zaehler()

    @nur_voip_benutzer
    def wb_voip_verpasst_erledigen(self, text):
        """Close all open entries of this entry's group (check mark "how done?").

        ``text``: str of at most ``ERLEDIGT_TEXT_MAX`` characters, may be empty; it becomes the
        feedback of the callback activity (plain text). Already done: no-op. Returns the new
        list row of the group or ``False`` if nothing is left open.
        """
        _text_pruefen(self.env, text, self.env._("Text"), ERLEDIGT_TEXT_MAX, pflicht=False)
        anruf = self._wb_voip_eigener()
        if anruf.erledigt:
            return False
        if anruf.zustand not in ('verpasst', 'anderswo') or (
                anruf.zustand == 'anderswo'
                and not anruf.filtered_domain(self._wb_voip_anderswo_sichtbar_domain())):
            raise UserError(self.env._("This is not an open missed-call entry."))
        # Gruppe direkt suchen (nicht über die gekappte Liste)
        gruppe = self._wb_voip_verpasst_offen(
            [('nummer_e164', '=', anruf.nummer_e164)] if anruf.nummer_e164
            else [('nummer_e164', '=', False), ('nummer', '=', anruf.nummer)], limit=None)
        aktivitaeten = gruppe.rueckruf_aktivitaet_id
        gruppe.write({
            'erledigt': True, 'erledigt_am': fields.Datetime.now(), 'erledigt_text': text or False,
            'erledigt_durch_protokoll_id': False})
        anruf._wb_voip_aktivitaeten_abschliessen(aktivitaeten, text or self.env._("Done"))
        gruppe._wb_voip_verpasst_melden()
        rest = self._wb_voip_verpasst_offen(
            [('nummer_e164', '=', anruf.nummer_e164)] if anruf.nummer_e164
            else [('nummer_e164', '=', False), ('nummer', '=', anruf.nummer)], limit=None)
        return rest._wb_voip_verpasst_zeile() if rest else False

    def _wb_voip_protokollieren(self):
        """Create the log entry of a finished call exactly once and post to the chatter."""
        self.ensure_one()
        # Die Texte werden gespeichert (Beschreibung, Chatter): in der Sprache des Benutzers des Anrufs,
        # auch wenn ein Serverlauf ohne Sprache im Kontext beendet.
        self = self.with_context(lang=self._wb_voip_sprache(self.sudo().user_id))
        anruf = self.sudo()
        if anruf.protokoll_id:
            return anruf.protokoll_id
        Protokoll = self.env['wb.voip.protokoll'].sudo()
        vorhanden = Protokoll.search([('anruf_id', '=', anruf.id)], limit=1)
        if vorhanden:
            anruf.protokoll_id = vorhanden
            return vorhanden
        werte = anruf._wb_voip_protokoll_werte()
        # Paralleler Aufruf oder untergeschobenes Protokoll: Savepoint, bei Unique-Verletzung
        # das vorhandene liefern statt die Transaktion abzubrechen.
        try:
            with self.env.cr.savepoint():
                protokoll = Protokoll.create(werte)
        except psycopg2.errors.UniqueViolation:
            protokoll = Protokoll.search([('anruf_id', '=', anruf.id)], limit=1)
            if not protokoll:
                raise
            anruf.protokoll_id = protokoll
            return protokoll
        anruf.protokoll_id = protokoll
        self._wb_voip_chatter(protokoll)
        return protokoll

    def _wb_voip_protokoll_werte(self):
        """Return the values of the log entry of this finished call (extension point).

        Extensions may change e.g. ``ergebnis`` or ``beschreibung`` through ``super()``;
        ``anruf_id`` and ``user_id`` must stay. Called with sudo by ``_wb_voip_protokollieren``.
        """
        self.ensure_one()
        anruf = self.sudo()
        if anruf.zustand == 'beendet' and anruf.annahme:
            ergebnis = 'erreicht'
        elif anruf.zustand == 'besetzt':
            ergebnis = 'besetzt'
        else:
            ergebnis = 'nicht_erreicht'
        return {
            'anruf_id': anruf.id,
            'user_id': anruf.user_id.id,
            'datum': anruf.beginn or fields.Datetime.now(),
            'partner_id': anruf.partner_id.id,
            'res_model': anruf.res_model or False,
            'res_id': anruf.res_id or False,
            'richtung': anruf.richtung,
            'ergebnis': ergebnis,
            'dauer_sek': anruf.dauer_sek,
            'beschreibung': plaintext2html(anruf.notiz) if anruf.notiz else False,
        }

    def _wb_voip_chatter(self, protokoll):
        """Post the call as note on the linked record, else on the contact.

        Like Odoo's own rule for posting: only on a target the caller may write;
        sudo is used for posting only after that check.
        """
        anruf = self.sudo()
        # der Aufrufer ist der Benutzer des Anrufs (Prüfung in _wb_voip_eigener)
        benutzer = anruf.user_id
        ziel = anruf.with_user(benutzer)._wb_voip_ziel(anruf.res_model, anruf.res_id)
        if ziel is None or not hasattr(ziel, 'message_post') or not ziel.has_access('write'):
            ziel = anruf.partner_id.with_user(benutzer)
            if not ziel or not ziel.has_access('write'):
                return
        text = protokoll._wb_voip_text()
        try:
            with self.env.cr.savepoint():
                ziel.sudo().message_post(
                    body=Markup("<p>%s</p>") % text,
                    subtype_xmlid='mail.mt_note',
                    message_type='comment',
                    author_id=benutzer.partner_id.id)
        except Exception:  # noqa: BLE001 - Chatter darf das Beenden nie verhindern
            _logger.exception("[wb_voip] Chatter-Eintrag fehlgeschlagen (Anruf %s)", anruf.id)

    # --- Rufnummer zuordnen ---

    @nur_voip_benutzer
    def action_wb_voip_zuordnen(self):
        """Open the wizard that assigns the number of the caller's own call to a contact."""
        anruf = self._wb_voip_eigener()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Assign phone number"),
            'res_model': 'wb.voip.zuordnen',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_anruf_id': anruf.id},
        }

    @nur_voip_benutzer
    def action_wb_voip_neuer_kontakt(self):
        """Open the form for a new contact with the number of the caller's own call.

        Fixed button of the caller window (independent of configured call buttons);
        the caller needs the right to create contacts.
        """
        anruf = self._wb_voip_eigener()
        return anruf.with_user(self.env.user)._wb_voip_neuer_kontakt()

    def _wb_voip_partner_setzen(self, partner):
        """Set the contact on the caller's own call and on its log, then tell the browser.

        Private: the call must be the caller's own and the contact readable by the
        caller; only after these checks the values are written with sudo.
        """
        anruf = self._wb_voip_eigener()
        partner = partner.with_user(self.env.user)
        partner.ensure_one()
        if not partner.has_access('read'):
            raise AccessError(self.env._("You are not allowed to read this contact."))
        anruf.write({'partner_id': partner.id})
        if anruf.protokoll_id:
            # als Benutzer: der Verfolgungseintrag nennt den echten Autor
            anruf.protokoll_id.with_user(self.env.user).write({'partner_id': partner.id})
        anruf._wb_voip_kontext_melden()

    # --- Anrufer-Kontext und Anruf-Buttons ---

    def _wb_voip_kontext_melden(self):
        """Tell the owner's browser tabs that the context of these calls changed.

        Payload is only the call id; the client fetches the data via ``wb_voip_kontext``.
        """
        for anruf in self.sudo():
            anruf.user_id._bus_send('wb_voip/kontext', {'anruf_id': anruf.id})

    def _wb_voip_lesbarer_kontakt(self):
        """Return the call's contact as the caller (empty unless the caller may read it)."""
        self.ensure_one()
        partner = self.sudo().partner_id.with_user(self.env.user)
        if partner and not partner.has_access('read'):
            return partner.browse()
        return partner

    @nur_voip_benutzer
    def wb_voip_kontext(self):
        """Return the caller window data of the caller's own call.

        ``{anruf, kontakt, protokolle, aufgaben, aktionen, layout}``; everything is read as
        the caller, ``kontakt`` is False (and lists stay empty) unless the contact is readable.
        ``layout`` is the caller's caller-window layout with formatted field values; it also
        decides how many logs and activities are loaded (none without such a block).
        """
        anruf = self._wb_voip_eigener()
        eigen = anruf.with_user(self.env.user)
        partner = eigen._wb_voip_lesbarer_kontakt()
        layout = self.env.user._wb_voip_layout('anrufer_fenster')
        return {
            'anruf': eigen._wb_voip_client_daten(),
            'kontakt': eigen._wb_voip_kontakt_daten(partner),
            'protokolle': eigen._wb_voip_kontakt_protokolle(
                partner, layout._wb_voip_anzahl('protokolle')),
            'aufgaben': eigen._wb_voip_kontakt_aufgaben(partner, layout._wb_voip_anzahl('aufgaben')),
            'aktionen': eigen._wb_voip_aktionen(),
            'layout': layout._wb_voip_fuer_client(partner, anruf=eigen),
        }

    def _wb_voip_kontakt_daten(self, partner):
        """Return the contact card (``partner`` is readable or empty)."""
        if not partner:
            return False
        firma = partner.parent_id._filtered_access('read')

        def lesbar(name):
            return partner[name] or False if partner._has_field_access(
                partner._fields[name], 'read') else False

        return {
            'id': partner.id,
            'name': partner.name or partner.display_name,
            'firma': firma.name if firma else lesbar('company_name'),
            'telefon': lesbar('phone'),
            'email': lesbar('email'),
            'bild_url': f'/web/image/res.partner/{partner.id}/avatar_128',
        }

    def _wb_voip_kontakt_protokolle(self, partner, anzahl=KONTEXT_PROTOKOLLE, ohne=None):
        """Return the caller's latest call logs of the contact (plain text, shortened).

        At most ``anzahl`` (capped at 20) entries, without the log ``ohne``.
        """
        anzahl = min(anzahl or 0, KONTEXT_HOECHSTENS)
        if not partner or not anzahl:
            return []
        Protokoll = self.env['wb.voip.protokoll']
        texte = dict(Protokoll._fields['ergebnis']._description_selection(self.env))
        domain = [('user_id', '=', self.env.uid), ('partner_id', '=', partner.id)]
        if ohne:
            domain.append(('id', '!=', ohne.id))
        protokolle = Protokoll.search(domain, limit=anzahl)
        ergebnis = []
        for protokoll in protokolle:
            html = (protokoll.beschreibung or '')[:KONTEXT_HTML_MAX]
            text = html2plaintext(html) if html else ''
            ergebnis.append({
                'id': protokoll.id,
                'datum': fields.Datetime.to_string(protokoll.datum) if protokoll.datum else False,
                'ergebnis_label': texte.get(protokoll.ergebnis, False),
                'dauer_sek': protokoll.dauer_sek,
                'beschreibung_text': text.strip()[:KONTEXT_TEXT_MAX],
            })
        return ergebnis

    def _wb_voip_kontakt_aufgaben(self, partner, anzahl=KONTEXT_AUFGABEN):
        """Return the caller's open activities on the contact or on the call's record.

        At most ``anzahl`` (capped at 20) entries.
        """
        self.ensure_one()
        anzahl = min(anzahl or 0, KONTEXT_HOECHSTENS)
        if not anzahl:
            return []
        orte = []
        if partner:
            orte.append(Domain('res_model', '=', 'res.partner') & Domain('res_id', '=', partner.id))
        ziel = self._wb_voip_ziel(self.sudo().res_model, self.sudo().res_id)
        if ziel is not None:
            orte.append(Domain('res_model', '=', ziel._name) & Domain('res_id', '=', ziel.id))
        if not orte:
            return []
        aktivitaeten = self.env['mail.activity'].search(
            Domain('user_id', '=', self.env.uid) & Domain.OR(orte),
            order='date_deadline, id', limit=anzahl)
        return [{
            'id': aktivitaet.id,
            'titel': aktivitaet.summary or aktivitaet.activity_type_id.name or '',
            'faellig': str(aktivitaet.date_deadline) if aktivitaet.date_deadline else False,
            'res_model': aktivitaet.res_model,
            'res_id': aktivitaet.res_id,
        } for aktivitaet in aktivitaeten]

    def _wb_voip_aktionen(self, ort=None):
        """Return the buttons that apply to this call for the caller (client data).

        ``ort`` (``anrufer_fenster``, ``protokoll_popup``, ``softphone``) limits the list to
        the buttons shown at that place; without it every place counts. "New contact" is
        left out while the call has a contact the caller may read.
        """
        self.ensure_one()
        anruf = self.sudo()
        if anruf.user_id != self.env.user:
            return []
        zustand = 'beendet' if anruf.zustand in FINALE_ZUSTAENDE else anruf.zustand
        if ort is None:
            ort_domain = ['|', '|', ('ort_anrufer_fenster', '=', True),
                          ('ort_protokoll_popup', '=', True), ('ort_softphone', '=', True)]
        else:
            ort_domain = [(f'ort_{ort}', '=', True)]
        domain = ort_domain + [
            ('richtung', 'in', ['beide', anruf.richtung]),
            ('zustand', 'in', ['immer', zustand]),
            # Gruppen schon in der Suche, damit fremde Buttons das Limit nicht aufbrauchen
            '|', ('group_ids', '=', False), ('group_ids', 'in', self.env.user.all_group_ids.ids),
        ]
        if self._wb_voip_lesbarer_kontakt():
            domain.append(('art', '!=', 'neuer_kontakt'))
        # sudo nur zum Suchen: Gruppen sind für Benutzer nicht lesbar (Eigentümer oben geprüft);
        # Obergrenze gegen übergroße Antworten, gezeigt werden ohnehin nur wenige
        aktionen = self.env['wb.voip.aktion'].sudo().search(domain, limit=KONTEXT_AKTIONEN)
        aktionen = aktionen.with_user(self.env.user)
        return [aktion._wb_voip_fuer_client() for aktion in aktionen
                if aktion._wb_voip_passend(self)]

    @nur_voip_benutzer
    def wb_voip_aktionen(self, ort):
        """Return only the buttons of place ``ort`` for the caller's own call (client data).

        Lighter than ``wb_voip_kontext`` (no contact, logs or activities); used by the
        softphone call view.
        """
        if not isinstance(ort, str) or ort not in AKTION_ORTE:
            raise UserError(self.env._("Invalid value: %s.", self.env._("Location")))
        anruf = self._wb_voip_eigener()
        return anruf.with_user(self.env.user)._wb_voip_aktionen(ort=ort)

    @nur_voip_benutzer
    @license_required(PRODUKT_CODE)
    def wb_voip_aktion_ausfuehren(self, aktion_id):
        """Run the call button ``aktion_id`` on the caller's own call, as the caller.

        Returns an action dict for the web client (or False). Never runs anything with
        more rights than the caller has without WB VoIP.
        """
        if type(aktion_id) is not int or aktion_id <= 0:
            raise UserError(self.env._("Invalid value: %s.", self.env._("Button")))
        anruf = self._wb_voip_eigener()
        eigen = anruf.with_user(self.env.user)
        aktion = self.env['wb.voip.aktion'].browse(aktion_id).exists()
        if not aktion or not aktion.active or not aktion._wb_voip_passend(eigen):
            raise UserError(self.env._("This button does not match this call."))
        partner = eigen._wb_voip_lesbarer_kontakt()
        # wie in der Auswahl: „Neuer Kontakt“ nur ohne lesbaren Kontakt
        if aktion.art == 'neuer_kontakt' and partner:
            raise UserError(self.env._("This button does not match this call."))
        # Ausführung je Art (_wb_voip_aktion_<art> am Button), andere Module ergänzen Arten
        return aktion._wb_voip_ausfuehren(eigen, partner)

    def _wb_voip_fenster(self, aktion, partner):
        """Return the button's window action as a cleaned dict (only readable models)."""
        # Fensteraktionen darf nur der Administrator lesen: sudo nur zum Zusammenbauen,
        # die Rechte prüfen wir vorher als Benutzer.
        fenster = aktion.sudo().act_window_id
        res_model = fenster.res_model
        if not fenster or not res_model or res_model not in self.env:
            raise UserError(self.env._("The window action of the button is invalid."))
        if fenster.group_ids and not (fenster.group_ids & self.env.user.all_group_ids):
            raise AccessError(self.env._("You are not allowed to open this window action."))
        self.env[res_model].check_access('read')
        daten = fenster.read(
            ['name', 'res_model', 'view_mode', 'views', 'target', 'domain', 'context'])[0]
        zusatz = {}
        if partner:
            zusatz['default_partner_id'] = partner.id
        zusatz['wb_voip_anruf_id'] = self.id
        kontext = _kontext_mischen(daten['context'], zusatz)
        return {
            'type': 'ir.actions.act_window',
            'name': daten['name'],
            'res_model': res_model,
            'view_mode': daten['view_mode'],
            'views': daten['views'],
            'target': daten['target'] or 'current',
            'domain': daten['domain'] or False,
            'context': kontext,
        }

    def _wb_voip_serveraktion(self, aktion, partner):
        """Run the button's server action as the caller on the call, contact or record.

        Odoo 19 runs most server action types with superuser rights (``object_write``,
        ``object_create``, ``object_copy``, ``multi`` children, webhooks, mail types).
        Allowed are only ``code`` (``run()`` evaluates it with the caller's env) and
        ``object_write`` on a field of the target itself, which is written here with
        ``write()`` as the caller (ACL, record rules, field groups apply), not via ``run()``.

        Server actions made by the button wizard carry a technical group nobody has, so
        Odoo refuses them everywhere else (``run()``, action menu). For such buttons the
        group check is skipped here and ``code`` runs through Odoo's code runner
        ``_run_action_code_multi`` with an evaluation context built as the caller (the
        same context ``run()`` would build), never through ``run()``.
        """
        server = aktion.sudo().server_action_id
        if not serveraktion_erlaubt(server):
            raise UserError(self.env._(
                "This type of server action is not allowed for call buttons."))
        if server.warning:
            raise UserError(self.env._("The server action of the button is faulty."))
        # Assistenten-Aktion nur mit Kennzeichen am Button und genau der technischen Gruppe
        assistent = aktion.sudo().wb_voip_assistent and assistenten_aktion(server)
        # wie run(): Gruppen der Serveraktion (gilt auch für object_write ohne run())
        if not assistent and server.group_ids \
                and not (server.group_ids & self.env.user.all_group_ids):
            raise AccessError(self.env._("You are not allowed to run this server action."))
        modell = server.model_id.model
        if modell == self._name:
            datensatz = self
        elif modell == 'res.partner' and partner:
            datensatz = partner
        else:
            ziel = self._wb_voip_ziel(self.sudo().res_model, self.sudo().res_id)
            if ziel is None or ziel._name != modell:
                raise UserError(self.env._("The server action does not match this call."))
            datensatz = ziel
        datensatz = datensatz.with_user(self.env.user)
        datensatz.check_access('write')
        kontext = {k: self.env.context[k] for k in SERVERAKTION_KONTEXT if k in self.env.context}
        kontext.update({
            'active_model': modell,
            'active_id': datensatz.id,
            'active_ids': [datensatz.id],
            'wb_voip_anruf_id': self.id,
            'wb_voip_partner_id': partner.id or False,
        })
        Server = self.env['ir.actions.server'].with_context(kontext)
        if server.state == 'object_write':
            # Wert mit Odoos eigener Auswertung bestimmen (Kontext als Benutzer),
            # geschrieben wird als Benutzer
            auswertung = Server._get_eval_context(server)
            wert = server.with_context(kontext)._eval_value(eval_context=auswertung)[server.id]
            datensatz.with_context(kontext).write({server.update_field_id.name: wert})
            return False
        if assistent:
            # Wie run(): Kontext als Benutzer (env = Benutzer, kein su), Code per sudo lesen
            # (Feldgruppe base.group_system), ausführen mit Odoos Code-Ausführer
            auswertung = Server._get_eval_context(server)
            ergebnis = server.with_context(kontext)._run_action_code_multi(eval_context=auswertung)
        else:
            ergebnis = Server.browse(server.id).run()
        if isinstance(ergebnis, dict) and ergebnis:
            return clean_action(ergebnis, env=self.env)
        return False

    def _wb_voip_url(self, aktion, partner):
        """Return the button's web address with URL-encoded placeholder values."""
        vorlage = aktion.sudo().url_vorlage or ''
        anruf = self.sudo()
        werte = {
            'nummer': anruf.nummer or '',
            'nummer_e164': anruf.nummer_e164 or '',
            'partner_id': str(partner.id) if partner else '',
        }
        url = URL_PLATZHALTER_MUSTER.sub(
            lambda treffer: quote(werte.get(treffer.group(1), ''), safe=''), vorlage)
        if not WbVoipAktion._wb_voip_url_ok(url) \
                or urlsplit(url).netloc != urlsplit(vorlage).netloc:
            raise UserError(self.env._("The web address of the button is invalid."))
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def _wb_voip_neuer_kontakt(self):
        """Return the form for a new contact with the call's number."""
        self.env['res.partner'].check_access('create')
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("New contact"),
            'res_model': 'res.partner',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
            'context': {'default_phone': self.sudo().nummer or False,
                        'wb_voip_anruf_id': self.id},
        }

    @nur_voip_benutzer
    def wb_voip_lebenszeichen(self):
        """Record that the client still holds this call (keeps the cron away)."""
        anruf = self._wb_voip_eigener()
        if anruf.zustand in OFFENE_ZUSTAENDE:
            anruf.write({'zuletzt_gesehen': fields.Datetime.now()})

    @api.model
    def _wb_voip_aufraeumen(self):
        """Cron: close calls without sign of life for 4 hours and log them.

        Without any sign of life (``zuletzt_gesehen`` empty) the start counts. An answered
        call (``annahme`` set) ends as ``beendet`` at its last sign of life (log ``erreicht``),
        any other as ``fehlgeschlagen`` (log ``nicht_erreicht``).
        """
        jetzt = fields.Datetime.now()
        grenze = jetzt - timedelta(hours=AUFRAEUM_STUNDEN)
        alte = self.sudo().search([
            ('zustand', 'in', OFFENE_ZUSTAENDE),
            '|', ('zuletzt_gesehen', '<', grenze),
            '&', ('zuletzt_gesehen', '=', False), ('beginn', '<', grenze)])
        for anruf in alte:
            if anruf.annahme:
                ende = max(anruf.annahme, min(anruf.zuletzt_gesehen or anruf.annahme, jetzt))
                anruf.write({
                    'zustand': 'beendet', 'ende': ende,
                    'dauer_sek': int((ende - anruf.annahme).total_seconds())})
            else:
                anruf.write({'zustand': 'fehlgeschlagen', 'ende': jetzt})
            try:
                with self.env.cr.savepoint():
                    anruf._wb_voip_protokollieren()
            except Exception:  # noqa: BLE001 - ein Anruf darf den Lauf nicht abbrechen
                _logger.exception("[wb_voip] Protokoll für verwaisten Anruf %s fehlgeschlagen",
                                  anruf.id)
        if alte:
            _logger.info("[wb_voip] %s verwaiste Anrufe geschlossen", len(alte))

    @api.model
    def _wb_voip_aufbewahrung_monate(self):
        """Return the retention period in months (0 = keep forever)."""
        wert = self.env['ir.config_parameter'].sudo().get_param(
            AUFBEWAHRUNG_PARAMETER, AUFBEWAHRUNG_STANDARD)
        try:
            monate = int(wert)
        except (TypeError, ValueError):
            monate = -1
        if monate < 0:
            _logger.warning("[wb_voip] Ungültige Löschfrist %r, es wird nichts gelöscht", wert)
            return 0
        return monate

    @api.model
    def _wb_voip_loeschfrist(self):
        """Cron: delete finished calls older than the retention period.

        Call logs (``wb.voip.protokoll``) stay; their ``anruf_id`` becomes empty.
        Deletes in batches; commits per batch only when running as cron.
        """
        monate = self._wb_voip_aufbewahrung_monate()
        if not monate:
            return
        grenze = fields.Datetime.now() - relativedelta(months=monate)
        domain = [('zustand', 'in', FINALE_ZUSTAENDE), ('beginn', '<', grenze)]
        Anruf = self.sudo().with_context(active_test=False)
        im_cron = bool(self.env.context.get('ir_cron_progress_id'))
        geloescht = 0
        while True:
            stapel = Anruf.search(domain, limit=LOESCH_STAPEL, order='id')
            if not stapel:
                break
            anzahl = len(stapel)
            stapel.unlink()
            geloescht += anzahl
            if im_cron:
                rest = Anruf.search_count(domain)
                if self.env['ir.cron']._commit_progress(anzahl, remaining=rest) <= 0:
                    break
        if geloescht:
            _logger.info("[wb_voip] %s Anrufe nach Ablauf der Löschfrist gelöscht", geloescht)
