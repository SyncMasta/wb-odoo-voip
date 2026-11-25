import logging

from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

from odoo.addons.wb_license_client.models.wb_license_client import license_required

from ..tools import geheimnis
from ..tools.rechte import GRUPPE_MANAGER, ist_voip_benutzer, nur_voip_benutzer
from . import PRODUKT_CODE

_logger = logging.getLogger(__name__)

# Team-Übersicht: Lebenszeichen des Telefon-Tabs, Gültigkeit und Obergrenze der Liste
LEBENSZEICHEN_SEKUNDEN = 60
LEBENSZEICHEN_GUELTIG = 150
TEAM_HOECHSTENS = 200

# Nebenstelle und Anlage setzen nur VoIP-Administratoren
_NUR_MANAGER = ('wb_voip_sip_benutzer', 'wb_voip_anlage_id')


class ResUsers(models.Model):
    _inherit = 'res.users'

    wb_voip_anlage_id = fields.Many2one(
        'wb.voip.anlage', string='Telefonanlage', ondelete='set null', copy=False)
    wb_voip_sip_benutzer = fields.Char(string='SIP-Benutzer', copy=False)
    # Nur schreibbar: liefert beim Lesen immer leer, gespeichert wird das Chiffrat
    wb_voip_sip_passwort = fields.Char(
        string='SIP-Passwort', compute='_compute_wb_voip_sip_passwort',
        inverse='_inverse_wb_voip_sip_passwort', store=False, copy=False,
        groups='wb_voip.group_wb_voip_user')
    wb_voip_sip_passwort_enc = fields.Char(
        string='SIP-Passwort (verschlüsselt)', groups='base.group_system', copy=False)
    wb_voip_sip_passwort_gesetzt = fields.Boolean(
        string='SIP-Passwort gespeichert', compute='_compute_wb_voip_sip_passwort_gesetzt',
        groups='wb_voip.group_wb_voip_user')

    # Nur der Server schreibt diese Felder (nicht in SELF_WRITEABLE); Lesen nur per sudo
    wb_voip_lebenszeichen = fields.Datetime(
        string='VoIP: letztes Lebenszeichen', copy=False, groups='base.group_system')
    wb_voip_registriert = fields.Boolean(
        string='VoIP: an der Anlage angemeldet', copy=False, groups='base.group_system')

    _wb_voip_nebenstelle_eindeutig = models.UniqueIndex(
        '(wb_voip_anlage_id, wb_voip_sip_benutzer) '
        'WHERE wb_voip_anlage_id IS NOT NULL AND wb_voip_sip_benutzer IS NOT NULL',
        'Nebenstelle ist bereits einem anderen Benutzer zugeordnet.')

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            'wb_voip_anlage_id', 'wb_voip_sip_benutzer', 'wb_voip_sip_passwort_gesetzt']

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ['wb_voip_sip_passwort']

    def _compute_wb_voip_sip_passwort(self):
        for benutzer in self:
            benutzer.wb_voip_sip_passwort = False

    def _inverse_wb_voip_sip_passwort(self):
        for benutzer in self:
            klartext = benutzer.wb_voip_sip_passwort
            chiffrat = geheimnis.verschluesseln(self.env, klartext) if klartext else False
            benutzer.sudo().wb_voip_sip_passwort_enc = chiffrat
        # Klartext nicht im Cache lassen: nächstes Lesen rechnet neu (leer)
        self.invalidate_recordset(['wb_voip_sip_passwort'])

    @api.depends('wb_voip_sip_passwort_enc')
    def _compute_wb_voip_sip_passwort_gesetzt(self):
        for benutzer in self:
            benutzer.wb_voip_sip_passwort_gesetzt = bool(benutzer.sudo().wb_voip_sip_passwort_enc)

    def _wb_voip_ist_manager(self):
        return self.env.su or self.env.user.has_group(GRUPPE_MANAGER)

    def _wb_voip_manager_fehler(self):
        return AccessError(self.env._(
            "Nebenstelle und Telefonanlage setzen nur VoIP-Administratoren."))

    @staticmethod
    def _wb_voip_wert(wert):
        """Normalise a vals value of extension/PBX for comparison (id or str, else False)."""
        if isinstance(wert, models.BaseModel):
            wert = wert.id
        return wert or False

    @api.model_create_multi
    def create(self, vals_list):
        # Leere Werte (z. B. aus copy_data oder dem Formular) sind erlaubt
        if not self._wb_voip_ist_manager() and any(
                self._wb_voip_wert(vals.get(feld)) for vals in vals_list for feld in _NUR_MANAGER):
            raise self._wb_voip_manager_fehler()
        return super().create(vals_list)

    def write(self, vals):
        # Nur echte Änderungen zählen: das Formular schickt unveränderte Werte nicht,
        # andere Aufrufer evtl. schon
        if not self._wb_voip_ist_manager() and set(vals) & set(_NUR_MANAGER):
            for benutzer in self.sudo():
                bisher = {
                    'wb_voip_anlage_id': benutzer.wb_voip_anlage_id.id or False,
                    'wb_voip_sip_benutzer': benutzer.wb_voip_sip_benutzer or False,
                }
                if any(self._wb_voip_wert(vals[feld]) != bisher[feld]
                       for feld in _NUR_MANAGER if feld in vals):
                    raise self._wb_voip_manager_fehler()
        if 'wb_voip_sip_passwort' in vals and not self.env.su \
                and self != self.env.user \
                and not self.env.user.has_group(GRUPPE_MANAGER):
            raise AccessError(self.env._(
                "Das SIP-Passwort anderer Benutzer setzen nur VoIP-Administratoren."))
        return super().write(vals)

    @api.constrains('wb_voip_anlage_id', 'wb_voip_sip_benutzer')
    def _check_wb_voip_nebenstelle(self):
        for benutzer in self.sudo():
            if not benutzer.wb_voip_anlage_id or not benutzer.wb_voip_sip_benutzer:
                continue
            # Ohne Flush suchen: sonst schlägt der Unique-Index vor dieser Meldung an
            self.env.cr.execute(SQL(
                "SELECT 1 FROM res_users WHERE id != %s AND wb_voip_anlage_id = %s"
                " AND wb_voip_sip_benutzer = %s LIMIT 1",
                benutzer.id, benutzer.wb_voip_anlage_id.id, benutzer.wb_voip_sip_benutzer))
            if self.env.cr.fetchone():
                raise ValidationError(self.env._(
                    "Nebenstelle ist bereits einem anderen Benutzer zugeordnet."))

    def _wb_voip_zugang(self):
        """Return the SIP credentials of the current user (never of others)."""
        self.ensure_one()
        if self != self.env.user:
            raise AccessError(self.env._("SIP-Zugangsdaten sind nur für den Benutzer selbst lesbar."))
        # Passwort nur serverseitig per sudo lesen und entschlüsseln, nie weitergeben außer
        # an den Besitzer
        geheimnis.pruefen(self.env)
        benutzer = self.sudo()
        anlage = benutzer.wb_voip_anlage_id
        chiffrat = benutzer.wb_voip_sip_passwort_enc
        return {
            'anlage_id': anlage.id or False,
            'modus': anlage.modus or False,
            'sip_domain': anlage.sip_domain or False,
            'ws_url': anlage.ws_url or False,
            'sip_benutzer': benutzer.wb_voip_sip_benutzer or False,
            'sip_passwort': geheimnis.entschluesseln(self.env, chiffrat) if chiffrat else False,
            'land': anlage.country_id.code or False,
            'ice_server_urls': anlage._wb_voip_ice_urls() if anlage else [],
        }

    @api.model
    @nur_voip_benutzer
    @license_required(PRODUKT_CODE)
    def wb_voip_zugang_lesen(self):
        """Return SIP access data of the current user (license gated)."""
        return self.env.user._wb_voip_zugang()

    def _wb_voip_eingerichtet(self):
        """Return True if the user is a VoIP user with a PBX and a SIP user name."""
        self.ensure_one()
        benutzer = self.sudo()
        return bool(
            self.has_group('wb_voip.group_wb_voip_user')
            and benutzer.wb_voip_anlage_id
            and benutzer.wb_voip_sip_benutzer)

    def _wb_voip_session_aktiv(self):
        """Return True if the browser should start the phone service for this user."""
        if len(self) != 1 or not self._is_internal():
            return False
        return bool(self.has_group('wb_voip.group_wb_voip_user')
                    and self.sudo().wb_voip_anlage_id)

    @api.model
    def wb_voip_status(self):
        """Return the softphone state of the current user without raising.

        ``{'aktiv': True, 'lizenz_gueltig': bool, 'meldung': str,
        'eingerichtet': bool, 'konfiguriert': bool, 'team_aktiv': bool}`` for WB VoIP users and
        ``{'aktiv': False}`` for everybody else; no credentials. ``konfiguriert`` is False if the
        server lacks the encryption key. ``team_aktiv``: the team overview is switched on for the
        caller's active company (only then the phone tab sends signs of life). The browser asks
        this before reading the SIP access, so a missing license shows as such instead of as a
        connection error.
        """
        benutzer = self.env.user
        if not ist_voip_benutzer(self.env):
            return {'aktiv': False}
        try:
            info = self.env['wb.license.client'].check_license(PRODUKT_CODE)
            gueltig = bool(info.is_valid)
            meldung = '' if gueltig else (
                info.user_message or self.env._("Lizenz fehlt oder abgelaufen"))
        except Exception:  # noqa: BLE001 - Status darf nie werfen
            _logger.exception("[wb_voip] Lizenzprüfung für den Softphone-Status fehlgeschlagen")
            gueltig = False
            meldung = self.env._("Lizenz konnte nicht geprüft werden.")
        konfiguriert = geheimnis.schluessel_vorhanden()
        if not konfiguriert:
            # Eigener Zustand: der Client zeigt dann nicht „ohne Lizenz“
            meldung = geheimnis.schluessel_meldung(self.env)
        return {
            'aktiv': True,
            'lizenz_gueltig': gueltig,
            'meldung': meldung,
            'eingerichtet': benutzer._wb_voip_eingerichtet(),
            'konfiguriert': konfiguriert,
            'team_aktiv': self._wb_voip_team_aktiv(),
        }

    def _wb_voip_layout(self, ort):
        """Return the window layout of this user for ``ort``.

        Layouts restricted to groups win by lowest sequence if one of the groups matches;
        layouts without groups apply to everyone; otherwise the standard layout of the place,
        or an unsaved layout with the delivered blocks if that is missing.
        Private helper (not callable via RPC); the search runs as superuser after the group check.
        """
        self.ensure_one()
        if ort not in dict(self.env['wb.voip.layout']._fields['ort'].selection):
            raise ValidationError(self.env._("Unbekanntes Fenster."))
        Layout = self.env['wb.voip.layout'].sudo()
        gruppen = self.sudo().all_group_ids.ids
        passend = Layout.search([
            ('ort', '=', ort), ('ist_standard', '=', False),
            '|', ('group_ids', '=', False), ('group_ids', 'in', gruppen),
        ], order='sequence, id', limit=1)
        # fehlt das Standard-Layout (z. B. per SQL gelöscht), gilt der Auslieferungszustand
        return (passend or Layout.search([('ort', '=', ort), ('ist_standard', '=', True)], limit=1)
                or Layout._wb_voip_ersatz(ort))

    # --- Team-Übersicht ---

    @api.model
    def _wb_voip_team_aktiv(self):
        """Return True if the team overview is switched on for the active company."""
        return bool(self.env.company.sudo().wb_voip_team_aktiv)

    def _wb_voip_lebenszeichen_schreiben(self, zeit, registriert):
        """Store the sign of life of this user per SQL (no ORM write on ``res.users``).

        Keeps ``write_date`` and avoids row-lock conflicts with the user's own preferences;
        the cache of both fields is invalidated afterwards. ``zeit``: datetime or None.
        """
        self.ensure_one()
        felder = ['wb_voip_lebenszeichen', 'wb_voip_registriert']
        self.flush_recordset(felder)
        self.env.cr.execute(SQL(
            "UPDATE res_users SET wb_voip_lebenszeichen = %s, wb_voip_registriert = %s"
            " WHERE id = %s", zeit, bool(registriert), self.id))
        self.invalidate_recordset(felder)

    def _wb_voip_team_mitglieder(self, firmen):
        """Return (sudo) the team members visible to a user of ``firmen``, without ``self``.

        Active internal users with the VoIP group, the same PBX as ``self`` and an SIP user that
        share at least one of ``firmen`` (a company recordset). Sorted by name, at most ``TEAM_HOECHSTENS``.
        """
        gruppe = self.env.ref('wb_voip.group_wb_voip_user')
        kandidaten = self.sudo().search([
            ('id', 'not in', self.ids), ('active', '=', True), ('share', '=', False),
            ('all_group_ids', 'in', gruppe.ids), ('wb_voip_anlage_id', '=', self.sudo().wb_voip_anlage_id.id or -1),
            ('wb_voip_sip_benutzer', '!=', False), ('company_ids', 'in', firmen.ids),
        ], order='name, id', limit=TEAM_HOECHSTENS)
        return kandidaten.filtered(lambda u: u.wb_voip_sip_benutzer)

    def _wb_voip_team_status(self, ausser=None):
        """Return ``{user_id: (status, seit)}`` for these users (read with sudo).

        ``abgemeldet``: no sign of life within ``LEBENSZEICHEN_GUELTIG`` seconds or not
        registered (whatever calls are open); else ``klingelt`` (own incoming call ringing),
        ``telefoniert`` (call running or an outgoing call ringing, ``seit`` = earliest answer,
        empty while ringing) or ``frei``. ``ausser``: calls to ignore (state before their creation).
        """
        jetzt = fields.Datetime.now()
        grenze = jetzt - timedelta(seconds=LEBENSZEICHEN_GUELTIG)
        angemeldet = self.sudo().filtered(
            lambda u: u.wb_voip_registriert and u.wb_voip_lebenszeichen
            and u.wb_voip_lebenszeichen >= grenze)
        anrufe = self.env['wb.voip.anruf'].sudo().search([
            ('user_id', 'in', angemeldet.ids), ('zustand', 'in', ('klingelt', 'laeuft'))])
        if ausser:
            anrufe -= ausser.sudo()
        ergebnis = {}
        for benutzer in self:
            if benutzer not in angemeldet:
                ergebnis[benutzer.id] = ('abgemeldet', False)
                continue
            eigene = anrufe.filtered(lambda a: a.user_id == benutzer)
            if eigene.filtered(lambda a: a.zustand == 'klingelt' and a.richtung == 'eingehend'):
                ergebnis[benutzer.id] = ('klingelt', False)
                continue
            if eigene:  # laufend oder ausgehend klingelnd
                zeiten = [a.annahme for a in eigene if a.zustand == 'laeuft' and a.annahme]
                seit = fields.Datetime.to_string(min(zeiten)) if zeiten else False
                ergebnis[benutzer.id] = ('telefoniert', seit)
            else:
                ergebnis[benutzer.id] = ('frei', False)
        return ergebnis

    def _wb_voip_team_melden(self):
        """Tell the team of each of these users that a status changed (empty payload).

        Per owner: the members of his team except himself, so owners hear of each other. Only
        companies with the team overview switched on count; without any, nothing is sent.
        """
        for besitzer in self.sudo():
            firmen = besitzer.company_ids.filtered('wb_voip_team_aktiv')
            if not firmen:
                continue
            for mitglied in besitzer._wb_voip_team_mitglieder(firmen):
                mitglied._bus_send('wb_voip/team', {})

    @api.model
    @nur_voip_benutzer
    def wb_voip_lebenszeichen_melden(self, registriert):
        """Record the sign of life of the caller's phone tab; tell the team if the status changes.

        ``registriert`` (bool): the tab is registered on the PBX. Never changes anyone else.
        With the team overview switched off for the active company nothing is stored (an old
        sign of life is deleted) and nothing is sent.
        """
        if type(registriert) is not bool:
            raise UserError(self.env._("Ungültige Angabe: %s.", self.env._("Angemeldet")))
        benutzer = self.env.user.sudo()
        if not self._wb_voip_team_aktiv():
            if benutzer.wb_voip_lebenszeichen or benutzer.wb_voip_registriert:
                benutzer._wb_voip_lebenszeichen_schreiben(None, False)
            return True
        vorher = benutzer._wb_voip_team_status()[benutzer.id][0]
        # Abmelden löscht das Lebenszeichen (Datensparsamkeit)
        benutzer._wb_voip_lebenszeichen_schreiben(
            fields.Datetime.now() if registriert else None, registriert)
        if benutzer._wb_voip_team_status()[benutzer.id][0] != vorher:
            benutzer._wb_voip_team_melden()
        return True

    @api.model
    @nur_voip_benutzer
    def wb_voip_team(self):
        """Return the pickup prefix of the caller's PBX and the colleagues' status.

        ``{'aktiv': bool, 'heranholen_praefix': str|False, 'kollegen': [{user_id, name,
        nebenstelle, status, seit}]}``. Colleagues: see ``_wb_voip_team_mitglieder`` with the
        caller's active company; only name, extension, status and, while on a call, the answer
        time (never the number). ``aktiv`` False (switched off for the active company): no
        colleagues. ``seit`` is always False unless the company shows the call duration.
        """
        benutzer = self.env.user.sudo()
        praefix = benutzer.wb_voip_anlage_id.heranholen_praefix or False
        firma = self.env.company.sudo()
        if not firma.wb_voip_team_aktiv:
            return {'aktiv': False, 'heranholen_praefix': praefix, 'kollegen': []}
        mitglieder = benutzer._wb_voip_team_mitglieder(self.env.company)
        status = mitglieder._wb_voip_team_status()
        dauer = firma.wb_voip_team_dauer
        return {
            'aktiv': True,
            'heranholen_praefix': praefix,
            'kollegen': [{
                'user_id': m.id,
                'name': m.name,
                'nebenstelle': m.wb_voip_sip_benutzer,
                'status': status[m.id][0],
                'seit': status[m.id][1] if dauer else False,
            } for m in mitglieder],
        }
