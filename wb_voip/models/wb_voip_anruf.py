import ast
import logging
from datetime import timedelta
from urllib.parse import quote, urlsplit

from dateutil.relativedelta import relativedelta

import psycopg2

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.tools import html2plaintext, plaintext2html

from odoo.addons.wb_license_client.models.wb_license_client import license_required
from odoo.addons.web.controllers.utils import clean_action

from ..tools import serverfelder
from ..tools.eingaben import limit_pruefen
from ..tools.rechte import nur_voip_benutzer
from ..tools.nummer import e164
from . import PRODUKT_CODE
from .wb_voip_aktion import URL_PLATZHALTER_MUSTER, WbVoipAktion, serveraktion_erlaubt

_logger = logging.getLogger(__name__)

# Anrufe ohne Lebenszeichen gelten nach dieser Zeit als verloren (Tab geschlossen o. Ä.)
AUFRAEUM_STUNDEN = 4
OFFENE_ZUSTAENDE = ('klingelt', 'laeuft')
FINALE_ZUSTAENDE = ('beendet', 'abgelehnt', 'besetzt', 'fehlgeschlagen')
RICHTUNGEN = ('eingehend', 'ausgehend')
# Endzustand eines nicht angenommenen Anrufs je SIP-Grund; alles andere -> fehlgeschlagen
SIP_GRUND_ZUSTAND = {
    '603': 'abgelehnt',  # Decline: abgelehnt
    '487': 'abgelehnt',  # Request Terminated: vor der Annahme abgebrochen
    '486': 'besetzt',    # Busy Here
    '600': 'besetzt',    # Busy Everywhere
}
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
KONTEXT_AKTIONEN = 100
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
        raise UserError(env._("Ungültige Angabe: %s.", bezeichnung))


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
        return dict(zusatz)
    dict_knoten = baum.body
    if not isinstance(dict_knoten, ast.Dict) or None in dict_knoten.keys \
            or dict_knoten.end_lineno is None:
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
            return dict(zusatz)
        stelle = versatz(letzter.end_lineno, letzter.end_col_offset)
        extra = ', ' + extra
    else:
        stelle = versatz(dict_knoten.lineno, dict_knoten.col_offset) + 1
    ergebnis = quelle[:stelle].decode('utf-8') + extra + quelle[stelle:].decode('utf-8')
    try:  # z. B. ein eingeklammerter letzter Wert würde hier ungültig: dann nur ``zusatz``
        ast.parse(ergebnis, mode='eval')
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return dict(zusatz)
    return ergebnis


class WbVoipAnruf(models.Model):
    _name = 'wb.voip.anruf'
    _description = 'WB VoIP: Anruf'
    _order = 'beginn desc, id desc'

    # Nur der Server darf diese Felder ändern (siehe write/create). Die RPC-Methoden
    # prüfen zuerst per check_access('write'), dass der Datensatz dem Aufrufer gehört,
    # und schreiben danach per sudo(). Ein Kontext-Schlüssel würde nicht reichen, weil
    # Clients per RPC beliebigen Kontext mitschicken können.
    _WB_VOIP_NUR_SERVER = (
        'sip_call_id', 'zustand', 'annahme', 'ende', 'dauer_sek', 'nummer_e164',
        'user_id', 'zuletzt_gesehen', 'richtung', 'beginn',
        'partner_id', 'res_model', 'res_id', 'protokoll_id',
    )

    user_id = fields.Many2one(
        'res.users', string='Benutzer', required=True, index=True,
        default=lambda self: self.env.user, ondelete='cascade')
    richtung = fields.Selection(
        [('eingehend', 'Eingehend'), ('ausgehend', 'Ausgehend')],
        string='Richtung', required=True)
    nummer = fields.Char(string='Nummer')
    nummer_e164 = fields.Char(string='Nummer (E.164)', index=True)
    sip_call_id = fields.Char(string='SIP-Call-ID', index=True)
    zustand = fields.Selection(
        [('neu', 'Neu'), ('klingelt', 'Klingelt'), ('laeuft', 'Läuft'),
         ('beendet', 'Beendet'), ('abgelehnt', 'Abgelehnt'), ('besetzt', 'Besetzt'),
         ('fehlgeschlagen', 'Fehlgeschlagen')],
        string='Zustand', required=True, default='neu', index=True)
    partner_id = fields.Many2one('res.partner', string='Kontakt', ondelete='set null')
    res_model = fields.Char(string='Datensatz-Modell')
    res_id = fields.Many2oneReference(
        string='Datensatz', model_field='res_model')
    beginn = fields.Datetime(string='Beginn', default=fields.Datetime.now)
    annahme = fields.Datetime(string='Annahme')
    ende = fields.Datetime(string='Ende')
    dauer_sek = fields.Integer(string='Dauer (s)')
    zuletzt_gesehen = fields.Datetime(string='Zuletzt gesehen')
    notiz = fields.Text(string='Notiz')
    protokoll_id = fields.Many2one(
        'wb.voip.protokoll', string='Protokoll', copy=False, index=True, ondelete='set null')

    _sip_call_id_je_benutzer = models.Constraint(
        'unique(user_id, sip_call_id)',
        'Diese SIP-Call-ID existiert für den Benutzer schon.')

    # --- Schutz der Serverfelder ---

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                serverfelder.pruefen(self.env, vals, self._WB_VOIP_NUR_SERVER)
            self = self.with_context(
                serverfelder.kontext_ohne_defaults(self.env, self._WB_VOIP_NUR_SERVER))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            serverfelder.pruefen(self.env, vals, self._WB_VOIP_NUR_SERVER)
        return super().write(vals)

    # --- Hilfen ---

    def _wb_voip_eigener(self):
        """Check that the single record belongs to the caller; return it with sudo."""
        self.ensure_one()
        self.check_access('write')  # Datensatzregel: fremde Anrufe sind unsichtbar
        if self.user_id != self.env.user:
            raise UserError(self.env._("Dies ist nicht Ihr Anruf."))
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

    def _wb_voip_client_daten(self):
        """Return the call data the browser client needs (no credentials)."""
        self.ensure_one()
        # Kontakt nur nennen, wenn der Benutzer ihn (noch) lesen darf
        partner = self.partner_id
        if partner and not self.env.su and not partner.has_access('read'):
            partner = partner.browse()
        return {
            'id': self.id,
            'richtung': self.richtung,
            'nummer': self.nummer or False,
            'zustand': self.zustand,
            'partner': (partner.id, partner.display_name) if partner else False,
            'res_model': self.res_model or False,
            'res_id': self.res_id or False,
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
        return [anruf._wb_voip_client_daten() for anruf in anrufe]

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
            raise UserError(self.env._("Ungültige Richtung."))
        _text_pruefen(self.env, nummer, self.env._("Nummer"), MAX_NUMMER, pflicht=True)
        _text_pruefen(self.env, sip_call_id, self.env._("SIP-Call-ID"), MAX_SIP_CALL_ID,
                      pflicht=True)
        _text_pruefen(self.env, res_model, self.env._("Datensatz-Modell"), MAX_RES_MODEL,
                      pflicht=False)
        if res_id is not False and type(res_id) is not int:
            raise UserError(self.env._("Ungültige Angabe: %s.", self.env._("Datensatz")))
        benutzer = self.env.user
        vorhanden = self.sudo().search([
            ('user_id', '=', benutzer.id), ('sip_call_id', '=', sip_call_id)], limit=1)
        if not vorhanden:
            land = benutzer.sudo().wb_voip_anlage_id.country_id.code or 'DE'
            nummer_e164 = e164(nummer, land)
            ziel = self._wb_voip_ziel(res_model, res_id)
            partner_id = self._wb_voip_kontakt(ziel, nummer_e164)
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

    @nur_voip_benutzer
    def wb_voip_angenommen(self):
        """Mark the call as answered (state ``laeuft``)."""
        anruf = self._wb_voip_eigener()
        if anruf.zustand in ('neu', 'klingelt'):
            jetzt = fields.Datetime.now()
            anruf.write({'zustand': 'laeuft', 'annahme': jetzt, 'zuletzt_gesehen': jetzt})
        return anruf.with_user(self.env.user)._wb_voip_client_daten()

    @nur_voip_benutzer
    def wb_voip_beendet(self, notiz, sip_grund, angenommen=False):
        """Finish the call. Mapping of the final state:

        * answered before (``annahme`` set)          -> ``beendet``
        * ``angenommen`` True but ``annahme`` empty  -> ``annahme`` now, ``beendet``
          (the client saw the answer, ``wb_voip_angenommen`` lost the race)
        * not answered, ``sip_grund`` ``603``/``487`` -> ``abgelehnt``
        * not answered, ``486`` or ``600``            -> ``besetzt``
        * not answered, anything else / no reason     -> ``fehlgeschlagen``

        (see ``SIP_GRUND_ZUSTAND``). ``dauer_sek`` counts from the answer. A call
        that is already in a final state is returned unchanged (no-op).
        """
        _text_pruefen(self.env, notiz, self.env._("Notiz"), MAX_NOTIZ, pflicht=False)
        _text_pruefen(self.env, sip_grund, self.env._("SIP-Grund"), MAX_SIP_GRUND, pflicht=False)
        if type(angenommen) is not bool:
            raise UserError(self.env._("Ungültige Angabe: %s.", self.env._("Angenommen")))
        anruf = self._wb_voip_eigener()
        if anruf.zustand in OFFENE_ZUSTAENDE or anruf.zustand == 'neu':
            jetzt = fields.Datetime.now()
            if angenommen and not anruf.annahme:
                anruf.write({'annahme': jetzt})
            werte = {'ende': jetzt, 'zuletzt_gesehen': jetzt}
            if anruf.annahme:
                werte['zustand'] = 'beendet'
                werte['dauer_sek'] = max(0, int((jetzt - anruf.annahme).total_seconds()))
            else:
                werte['zustand'] = SIP_GRUND_ZUSTAND.get(sip_grund or '', 'fehlgeschlagen')
            if notiz:
                werte['notiz'] = notiz
            anruf.write(werte)
        if not anruf.protokoll_id:
            anruf._wb_voip_protokollieren()
        return anruf.with_user(self.env.user)._wb_voip_client_daten()

    def _wb_voip_protokollieren(self):
        """Create the log entry of a finished call exactly once and post to the chatter."""
        self.ensure_one()
        anruf = self.sudo()
        if anruf.protokoll_id:
            return anruf.protokoll_id
        if anruf.zustand == 'beendet' and anruf.annahme:
            ergebnis = 'erreicht'
        elif anruf.zustand == 'besetzt':
            ergebnis = 'besetzt'
        else:
            ergebnis = 'nicht_erreicht'
        Protokoll = self.env['wb.voip.protokoll'].sudo()
        vorhanden = Protokoll.search([('anruf_id', '=', anruf.id)], limit=1)
        if vorhanden:
            anruf.protokoll_id = vorhanden
            return vorhanden
        werte = {
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
            'name': self.env._("Rufnummer zuordnen"),
            'res_model': 'wb.voip.zuordnen',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_anruf_id': anruf.id},
        }

    def _wb_voip_partner_setzen(self, partner):
        """Set the contact on the caller's own call and on its log, then tell the browser.

        Private: the call must be the caller's own and the contact readable by the
        caller; only after these checks the values are written with sudo.
        """
        anruf = self._wb_voip_eigener()
        partner = partner.with_user(self.env.user)
        partner.ensure_one()
        if not partner.has_access('read'):
            raise AccessError(self.env._("Sie dürfen diesen Kontakt nicht lesen."))
        anruf.write({'partner_id': partner.id})
        if anruf.protokoll_id:
            anruf.protokoll_id.write({'partner_id': partner.id})
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

        ``{anruf, kontakt, protokolle, aufgaben, aktionen}``; everything is read as the
        caller, ``kontakt`` is False (and lists stay empty) unless the contact is readable.
        """
        anruf = self._wb_voip_eigener()
        eigen = anruf.with_user(self.env.user)
        partner = eigen._wb_voip_lesbarer_kontakt()
        return {
            'anruf': eigen._wb_voip_client_daten(),
            'kontakt': eigen._wb_voip_kontakt_daten(partner),
            'protokolle': eigen._wb_voip_kontakt_protokolle(partner),
            'aufgaben': eigen._wb_voip_kontakt_aufgaben(partner),
            'aktionen': eigen._wb_voip_aktionen(),
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

    def _wb_voip_kontakt_protokolle(self, partner):
        """Return the caller's latest call logs of the contact (plain text, shortened)."""
        if not partner:
            return []
        Protokoll = self.env['wb.voip.protokoll']
        texte = dict(Protokoll._fields['ergebnis']._description_selection(self.env))
        protokolle = Protokoll.search([
            ('user_id', '=', self.env.uid), ('partner_id', '=', partner.id),
        ], limit=KONTEXT_PROTOKOLLE)
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

    def _wb_voip_kontakt_aufgaben(self, partner):
        """Return the caller's open activities on the contact or on the call's record."""
        self.ensure_one()
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
            order='date_deadline, id', limit=KONTEXT_AUFGABEN)
        return [{
            'id': aktivitaet.id,
            'titel': aktivitaet.summary or aktivitaet.activity_type_id.name or '',
            'faellig': str(aktivitaet.date_deadline) if aktivitaet.date_deadline else False,
            'res_model': aktivitaet.res_model,
            'res_id': aktivitaet.res_id,
        } for aktivitaet in aktivitaeten]

    def _wb_voip_aktionen(self):
        """Return the buttons that apply to this call for the caller (client data)."""
        self.ensure_one()
        anruf = self.sudo()
        zustand = 'beendet' if anruf.zustand in FINALE_ZUSTAENDE else anruf.zustand
        aktionen = self.env['wb.voip.aktion'].search([
            '|', '|', ('ort_anrufer_fenster', '=', True), ('ort_protokoll_popup', '=', True),
            ('ort_softphone', '=', True),
            ('richtung', 'in', ['beide', anruf.richtung]),
            ('zustand', 'in', ['immer', zustand]),
        ], limit=KONTEXT_AKTIONEN)
        return [aktion._wb_voip_fuer_client() for aktion in aktionen
                if aktion._wb_voip_passend(self)]

    @nur_voip_benutzer
    @license_required(PRODUKT_CODE)
    def wb_voip_aktion_ausfuehren(self, aktion_id):
        """Run the call button ``aktion_id`` on the caller's own call, as the caller.

        Returns an action dict for the web client (or False). Never runs anything with
        more rights than the caller has without WB VoIP.
        """
        if type(aktion_id) is not int or aktion_id <= 0:
            raise UserError(self.env._("Ungültige Angabe: %s.", self.env._("Button")))
        anruf = self._wb_voip_eigener()
        eigen = anruf.with_user(self.env.user)
        aktion = self.env['wb.voip.aktion'].browse(aktion_id).exists()
        if not aktion or not aktion.active or not aktion._wb_voip_passend(eigen):
            raise UserError(self.env._("Dieser Button passt nicht zu diesem Anruf."))
        partner = eigen._wb_voip_lesbarer_kontakt()
        if aktion.art == 'fenster':
            return eigen._wb_voip_fenster(aktion, partner)
        if aktion.art == 'serveraktion':
            return eigen._wb_voip_serveraktion(aktion, partner)
        if aktion.art == 'url':
            return eigen._wb_voip_url(aktion, partner)
        if aktion.art == 'neuer_kontakt':
            return eigen._wb_voip_neuer_kontakt()
        raise UserError(self.env._("Dieser Button passt nicht zu diesem Anruf."))

    def _wb_voip_fenster(self, aktion, partner):
        """Return the button's window action as a cleaned dict (only readable models)."""
        # Fensteraktionen darf nur der Administrator lesen: sudo nur zum Zusammenbauen,
        # die Rechte prüfen wir vorher als Benutzer.
        fenster = aktion.sudo().act_window_id
        res_model = fenster.res_model
        if not fenster or not res_model or res_model not in self.env:
            raise UserError(self.env._("Die Fensteraktion des Buttons ist ungültig."))
        if fenster.group_ids and not (fenster.group_ids & self.env.user.all_group_ids):
            raise AccessError(self.env._("Sie dürfen diese Fensteraktion nicht öffnen."))
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
        """
        server = aktion.sudo().server_action_id
        if not serveraktion_erlaubt(server):
            raise UserError(self.env._(
                "Diese Art von Serveraktion ist für Anruf-Buttons nicht erlaubt."))
        if server.warning:
            raise UserError(self.env._("Die Serveraktion des Buttons ist fehlerhaft."))
        # wie run(): Gruppen der Serveraktion (gilt auch für object_write ohne run())
        if server.group_ids and not (server.group_ids & self.env.user.all_group_ids):
            raise AccessError(self.env._("Sie dürfen diese Serveraktion nicht ausführen."))
        modell = server.model_id.model
        if modell == self._name:
            datensatz = self
        elif modell == 'res.partner' and partner:
            datensatz = partner
        else:
            ziel = self._wb_voip_ziel(self.sudo().res_model, self.sudo().res_id)
            if ziel is None or ziel._name != modell:
                raise UserError(self.env._("Die Serveraktion passt nicht zu diesem Anruf."))
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
            raise UserError(self.env._("Die Webadresse des Buttons ist ungültig."))
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def _wb_voip_neuer_kontakt(self):
        """Return the form for a new contact with the call's number."""
        self.env['res.partner'].check_access('create')
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Neuer Kontakt"),
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
