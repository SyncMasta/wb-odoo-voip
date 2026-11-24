import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import SQL

from odoo.addons.wb_license_client.models.wb_license_client import license_required

from ..tools import geheimnis
from ..tools.rechte import GRUPPE_MANAGER, ist_voip_benutzer, nur_voip_benutzer
from . import PRODUKT_CODE

_logger = logging.getLogger(__name__)

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
        'eingerichtet': bool, 'konfiguriert': bool}`` for WB VoIP users and
        ``{'aktiv': False}`` for everybody else; no credentials. ``konfiguriert`` is False if the
        server lacks the encryption key. The browser asks this before reading the SIP access, so a
        missing license shows as such instead of as a connection error.
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
        }
