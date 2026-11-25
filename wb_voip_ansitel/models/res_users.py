import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import SQL

from odoo.addons.wb_license_client.models.wb_license_client import license_required
from odoo.addons.wb_voip.models import PRODUKT_CODE
from odoo.addons.wb_voip.tools import geheimnis
from odoo.addons.wb_voip.tools.rechte import nur_voip_benutzer

from . import ansitel_api
from .ansitel_api import AnsitelFehler, AnsitelNichtErreichbar

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    # Nebenstelle an der Ansitel-Anlage: VoIP-Administratoren lesen sie, setzen darf sie nur die
    # Systemverwaltung (siehe write/create): wer sie setzt, schaltet Weiterleitungen dieser Nebenstelle.
    # Nicht für den Benutzer selbst lesbar: der Zustand der Tasten verrät sie nicht.
    wb_voip_ansitel_nebenstelle = fields.Char(
        string='Ansitel-Nebenstelle', copy=False, groups='wb_voip.group_wb_voip_manager',
        help="Interne Nummer oder Gerätename an der Ansitel-Anlage, z. B. 100. Ohne Nebenstelle "
             "gibt es keine Schnelltasten.")
    # Persönlicher Token: nur Systemverwaltung; liefert beim Lesen immer leer, gespeichert wird das Chiffrat
    wb_voip_ansitel_token = fields.Char(
        string='Ansitel-Token (persönlich)', compute='_compute_wb_voip_ansitel_token',
        inverse='_inverse_wb_voip_ansitel_token', store=False, copy=False, groups='base.group_system',
        help="Token des Ansitel-Benutzers dieses Mitarbeiters. Nur für die AB-Taste nötig, wenn der "
             "zentrale Token mehrere Nebenstellen besitzt.")
    wb_voip_ansitel_token_enc = fields.Char(
        string='Ansitel-Token (verschlüsselt)', copy=False, groups='base.group_system')
    wb_voip_ansitel_token_gesetzt = fields.Boolean(
        string='Ansitel-Token gespeichert', compute='_compute_wb_voip_ansitel_token_gesetzt',
        groups='wb_voip.group_wb_voip_manager')
    wb_voip_ansitel_ab_zentral = fields.Boolean(
        string='AB über zentralen Token schalten', copy=False, default=False, groups='base.group_system',
        help="Nur setzen, wenn alle Nebenstellen des zentralen Ansitel-Benutzers diesem Benutzer gehören "
             "(gemeinsamer AB). Ohne Häkchen gilt der zentrale Token für den AB nur bei genau einer "
             "Nebenstelle.")
    # Wie ein Geheimnis geschützt: die Feldgruppe sperrt alle anderen, der Benutzer selbst
    # liest und schreibt die Nummer über SELF_READABLE/SELF_WRITEABLE
    wb_voip_handy = fields.Char(
        string='Mein Handy', copy=False, groups='base.group_system',
        help="Ziel der Weiterleitungstaste „Eigenes Handy“, z. B. +49 30 23125 101.")

    # Wo die Tastenleiste erscheint; der Benutzer stellt sie selbst ein (wirkt nach dem Neuladen)
    wb_voip_ansitel_tastenleiste = fields.Selection(
        [('softphone', 'Im Softphone'), ('rand', 'Am rechten Rand'), ('beide', 'Softphone und Rand')],
        string='Schnelltasten anzeigen', default='softphone', required=True, copy=False,
        help="Wo die Schnelltasten für Weiterleitung, Nicht stören und Anrufbeantworter erscheinen. "
             "Die Änderung gilt nach dem Neuladen der Seite.")

    _wb_voip_ansitel_nebenstelle_eindeutig = models.UniqueIndex(
        '(wb_voip_anlage_id, wb_voip_ansitel_nebenstelle) '
        'WHERE wb_voip_anlage_id IS NOT NULL AND wb_voip_ansitel_nebenstelle IS NOT NULL',
        'Die Ansitel-Nebenstelle ist an dieser Telefonanlage schon vergeben.')

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ['wb_voip_handy', 'wb_voip_ansitel_tastenleiste']

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ['wb_voip_handy', 'wb_voip_ansitel_tastenleiste']

    def _wb_voip_ansitel_session_wert(self):
        """Return the bar placement for the browser session, or False without a phone setup.

        Only the placement string (no credentials); False keeps the bar away for users
        without WB VoIP.
        """
        if len(self) != 1 or not self._wb_voip_session_aktiv():
            return False
        return self.sudo().wb_voip_ansitel_tastenleiste or 'softphone'

    def _compute_wb_voip_ansitel_token(self):
        for benutzer in self:
            benutzer.wb_voip_ansitel_token = False

    def _inverse_wb_voip_ansitel_token(self):
        for benutzer in self:
            klartext = benutzer.wb_voip_ansitel_token
            chiffrat = geheimnis.verschluesseln(self.env, klartext) if klartext else False
            benutzer.sudo().wb_voip_ansitel_token_enc = chiffrat
        # Klartext nicht im Cache lassen
        self.invalidate_recordset(['wb_voip_ansitel_token'])

    @api.depends('wb_voip_ansitel_token_enc')
    def _compute_wb_voip_ansitel_token_gesetzt(self):
        for benutzer in self:
            benutzer.wb_voip_ansitel_token_gesetzt = bool(benutzer.sudo().wb_voip_ansitel_token_enc)

    @staticmethod
    def _wb_voip_ansitel_bereinigen(vals):
        """Strip extension and mobile so that the stored value is the checked one."""
        for feld in ('wb_voip_ansitel_nebenstelle', 'wb_voip_handy'):
            if feld in vals and (not vals[feld] or isinstance(vals[feld], str)):
                vals[feld] = (vals[feld] or '').strip() or False
        return vals

    def _wb_voip_handy_normalisieren(self, vals, anlage):
        """Store a valid mobile in E.164 form; an invalid one stays for the constraint message."""
        if vals.get('wb_voip_handy'):
            land = anlage.sudo().country_id.code or 'DE'
            Taste = self.env['wb.voip.taste']
            if not Taste._ziel_pruefen(vals['wb_voip_handy'], land):
                vals['wb_voip_handy'] = Taste._ziel_e164(vals['wb_voip_handy'], land)

    def _wb_voip_nebenstelle_fehler(self):
        return AccessError(self.env._("Die Ansitel-Nebenstelle darf nur die Systemverwaltung setzen."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._wb_voip_ansitel_bereinigen(vals)
            self._wb_voip_handy_normalisieren(
                vals, self.env['wb.voip.anlage'].browse(self._wb_voip_wert(vals.get('wb_voip_anlage_id'))))
        mit_nebenstelle = [vals for vals in vals_list if vals.get('wb_voip_ansitel_nebenstelle')]
        if mit_nebenstelle and not self.env.user._is_system():
            raise self._wb_voip_nebenstelle_fehler()
        benutzer = super().create(vals_list)
        if mit_nebenstelle:
            _logger.info("[wb_voip_ansitel] Ansitel-Nebenstelle gesetzt: Benutzer %s durch Benutzer %s",
                         benutzer.filtered(lambda u: u.sudo().wb_voip_ansitel_nebenstelle).ids, self.env.uid)
        return benutzer

    def write(self, vals):
        self._wb_voip_ansitel_bereinigen(vals)
        self._wb_voip_handy_normalisieren(vals, self.env['wb.voip.anlage'].browse(
            self._wb_voip_wert(vals['wb_voip_anlage_id'])) if 'wb_voip_anlage_id' in vals
            else self.sudo()[:1].wb_voip_anlage_id)
        # Nur echte Änderungen zählen (das Formular schickt unveränderte Werte nicht, andere evtl. schon)
        geaendert = self.browse()
        if 'wb_voip_ansitel_nebenstelle' in vals:
            neu = vals['wb_voip_ansitel_nebenstelle'] or False
            geaendert = self.sudo().filtered(lambda u: (u.wb_voip_ansitel_nebenstelle or False) != neu)
            if geaendert and not self.env.user._is_system():
                raise self._wb_voip_nebenstelle_fehler()
        # Wer ohne Systemrecht die Anlage wechselt, nimmt weder Nebenstelle noch persönlichen Token mit:
        # dieselbe Nummer kann an der neuen Anlage jemand anderem gehören
        if 'wb_voip_anlage_id' in vals and not self.env.user._is_system():
            neu = self._wb_voip_wert(vals['wb_voip_anlage_id'])
            wechsel = self.sudo().filtered(lambda u: (u.wb_voip_anlage_id.id or False) != neu)
            if wechsel:
                self._wb_voip_ansitel_loesen(wechsel)
        ergebnis = super().write(vals)
        if geaendert:
            # Nur Kennungen ins Log, keine Nummern
            _logger.info("[wb_voip_ansitel] Ansitel-Nebenstelle geändert: Benutzer %s durch Benutzer %s",
                         geaendert.ids, self.env.uid)
        return ergebnis

    def _wb_voip_ansitel_loesen(self, benutzer):
        """Clear extension and personal token of ``benutzer`` (sudo) before a PBX change.

        Per SQL and before the actual write, so the uniqueness check at the new PBX does not
        see the old extension; the guard for the extension does not apply (nothing is set).
        """
        felder = ['wb_voip_ansitel_nebenstelle', 'wb_voip_ansitel_token_enc']
        benutzer.flush_recordset(felder)
        mit_nebenstelle = benutzer.filtered('wb_voip_ansitel_nebenstelle')
        self.env.cr.execute(SQL(
            "UPDATE res_users SET wb_voip_ansitel_nebenstelle = NULL, wb_voip_ansitel_token_enc = NULL"
            " WHERE id IN %s", tuple(benutzer.ids)))
        benutzer.invalidate_recordset(felder)
        # Das nicht gespeicherte Feld hängt am gelöschten Chiffrat; der Cache wüsste davon nichts
        benutzer.invalidate_recordset(['wb_voip_ansitel_token_gesetzt'])
        if mit_nebenstelle:
            _logger.info("[wb_voip_ansitel] Anlagenwechsel ohne Systemrecht: Ansitel-Nebenstelle von Benutzer %s "
                         "gelöscht (durch Benutzer %s)", mit_nebenstelle.ids, self.env.uid)

    @api.constrains('wb_voip_anlage_id', 'wb_voip_ansitel_nebenstelle')
    def _check_wb_voip_ansitel_nebenstelle(self):
        for benutzer in self.sudo():
            if not benutzer.wb_voip_anlage_id or not benutzer.wb_voip_ansitel_nebenstelle:
                continue
            # Ohne Flush suchen: sonst schlägt der Unique-Index vor dieser Meldung an
            self.env.cr.execute(SQL(
                "SELECT 1 FROM res_users WHERE id != %s AND wb_voip_anlage_id = %s"
                " AND wb_voip_ansitel_nebenstelle = %s LIMIT 1",
                benutzer.id, benutzer.wb_voip_anlage_id.id, benutzer.wb_voip_ansitel_nebenstelle))
            if self.env.cr.fetchone():
                raise ValidationError(self.env._(
                    "Die Ansitel-Nebenstelle %s ist an dieser Telefonanlage schon vergeben.",
                    benutzer.wb_voip_ansitel_nebenstelle))

    @api.constrains('wb_voip_handy', 'wb_voip_anlage_id')
    def _check_wb_voip_handy(self):
        Taste = self.env['wb.voip.taste']
        for benutzer in self.sudo():
            if not benutzer.wb_voip_handy:
                continue
            fehler = Taste._ziel_pruefen(benutzer.wb_voip_handy, benutzer.wb_voip_anlage_id.country_id.code or 'DE')
            if fehler:
                raise ValidationError(fehler)

    # --- Schnelltasten ---
    # Alle Methoden wirken ausschließlich auf den angemeldeten Benutzer (`self.env.user`). Ansitel-Aufrufe
    # nutzen nur Anlage und Nebenstelle, die Administratoren gesetzt haben.

    def _wb_voip_tasten_texte(self):
        """Return the fixed user messages of the keys (translated)."""
        _ = self.env._
        return {
            'allgemein': _("Schnelltasten sind gerade nicht verfügbar."),
            'handy': _("Bitte unter Mein Profil › Telefonie Ihr Handy eintragen."),
            'ab_token': _("AB braucht persönlichen Ansitel-Token oder Freigabe – bitte Administrator fragen."),
            'taste_fehlt': _("Diese Taste gibt es nicht mehr."),
            'wunsch': _("Ungültiger Wunschzustand."),
            'nebenstelle': _("Die Nebenstelle gibt es in der Telefonanlage nicht."),
            'ziel': _("Für diese Taste ist kein gültiges Weiterleitungsziel eingetragen."),
            'intern_unbekannt': _("Interne Nebenstelle unbekannt – bitte Systemverwaltung fragen."),
            'intern_eigen': _("Die eigene Nebenstelle kann nicht Weiterleitungsziel sein."),
        }

    @api.model
    def _wb_voip_intern_pruefen(self, anlage, nebenstelle, ziel, eigene_peer_id=None):
        """Return the message why ``ziel`` is no internal target for ``nebenstelle``, or ``False``.

        Order: own extension as entered (string), the additional targets the system administration
        listed at the PBX (ring groups, checked without ``getpeer``), then peers whose internal number
        (``peerInternalCallerId``) is ``ziel``; never a peer name, never the own extension
        (loop, also compared with its internal number). One cached peer list per PBX. Raises
        ``AnsitelFehler`` if the peer list cannot be read.
        """
        texte = self._wb_voip_tasten_texte()
        fehler = self.env['wb.voip.taste']._intern_format_fehler(ziel)
        if fehler:
            return fehler
        ziel = ziel.strip()
        if ziel == nebenstelle:
            return texte['intern_eigen']
        # Rufgruppen aus der Liste ohne Anlagenabfrage
        if ziel in anlage._ansitel_interne_ziele_liste():
            return False
        A = self.env['wb.voip.ansitel']
        if eigene_peer_id is None:
            eigene_peer_id = A._peer_id(anlage, nebenstelle)
        if eigene_peer_id is not None and ziel == A._peer_interne_nummer(anlage, eigene_peer_id):
            return texte['intern_eigen']
        if A._peer_intern(anlage, ziel) is not None:
            return False
        return texte['intern_unbekannt']

    @api.model
    def _wb_voip_tasten_leer(self, fehler=False):
        return {'verfuegbar': False, 'fehler': fehler, 'tasten': [], 'fremde_weiterleitung': False,
                'umgeleitet': False}

    @api.model
    def _wb_voip_tasten_kontext(self):
        """Return ``(user, PBX, extension)`` (sudo) or ``None`` if the user has no keys."""
        benutzer = self.env.user.sudo()
        if benutzer.share:
            return None
        anlage = benutzer.wb_voip_anlage_id
        nebenstelle = (benutzer.wb_voip_ansitel_nebenstelle or '').strip()
        if not (anlage and nebenstelle and anlage.ansitel_aktiv and anlage.ansitel_api_url
                and anlage.ansitel_token_enc):
            return None
        return benutzer, anlage, nebenstelle

    @api.model
    def _wb_voip_tasten_sichtbar(self):
        """Return the active keys; forwarding keys with a fixed number need a valid target."""
        Taste = self.env['wb.voip.taste'].sudo()
        return Taste.search([]).filtered(
            lambda t: t.art != 'weiterleitung' or t.ziel_art == 'handy'
            or (Taste._intern_gueltig(t.ziel) if t.ziel_art == 'intern' else Taste._ziel_gueltig(t.ziel)))

    @api.model
    def _wb_voip_tasten_grau(self, fehler):
        """Return the bar with all keys grey (``aktiv`` None) and ``fehler``; empty without keys."""
        try:
            if not self._wb_voip_tasten_kontext():
                return self._wb_voip_tasten_leer()
            liste = [{'id': t.id, 'name': t.name, 'icon': t.icon or False, 'art': t.art, 'aktiv': None}
                     for t in self._wb_voip_tasten_sichtbar()]
        except Exception:  # noqa: BLE001 - Ersatzanzeige darf nie werfen
            _logger.exception("[wb_voip_ansitel] Schnelltasten: graue Ersatzanzeige fehlgeschlagen")
            return self._wb_voip_tasten_leer(fehler)
        return {'verfuegbar': True, 'fehler': fehler, 'tasten': liste, 'fremde_weiterleitung': False,
                'umgeleitet': False}

    @api.model
    def _wb_voip_ab_passt(self, daten, taste):
        """Return True if the answering machine is on and in the mode of ``taste``."""
        if str(daten.get('active')) != '1':
            return False
        if taste.ab_modus == 'nach_rufzeit':
            return (str(daten.get('option_ringtime_check')) == '1'
                    and str(daten.get('option_ringtime_seconds')) == str(taste.ab_sekunden or 20))
        return str(daten.get('option_immediately_check')) == '1'

    @api.model
    def _wb_voip_tasten_lesen(self):
        """Read the state fresh from the PBX; a failing part only greys its keys and sets ``fehler``."""
        kontext = self._wb_voip_tasten_kontext()
        if not kontext:
            return self._wb_voip_tasten_leer()
        benutzer, anlage, nebenstelle = kontext
        texte = self._wb_voip_tasten_texte()
        A = self.env['wb.voip.ansitel']
        Taste = self.env['wb.voip.taste'].sudo()
        land = anlage.country_id.code or 'DE'
        tasten = self._wb_voip_tasten_sichtbar()
        arten = set(tasten.mapped('art'))
        fehler = []
        abbruch = []

        def versuch(funktion, *args):
            """Return ``(read, value)``; on a PBX error ``(False, None)`` and remember the message.

            If the PBX is not reachable, the remaining calls are skipped (each would wait for the time-out).
            """
            if abbruch:
                return False, None
            try:
                return True, funktion(*args)
            except UserError as exc:
                text = exc.args[0] if exc.args else texte['allgemein']
                if text not in fehler:
                    fehler.append(text)
                if isinstance(exc, AnsitelNichtErreichbar):
                    abbruch.append(True)
                return False, None

        peer_ok, peer_id = versuch(A._peer_id, anlage, nebenstelle)
        if peer_ok and peer_id is None:
            return self._wb_voip_tasten_leer()  # Nebenstelle gibt es in dieser Anlage nicht
        # Eine fremd gesetzte Weiterleitung zeigen wir auch ohne Weiterleitungstaste
        fw_ok, ziel_ist = versuch(A._weiterleitung_lesen, anlage, peer_id) if peer_ok else (False, None)
        dnd_ok, dnd = (versuch(A._dnd_lesen, anlage, peer_id)
                       if peer_ok and 'anrufschutz' in arten else (False, None))
        ab_da, ab_daten = 'anrufbeantworter' in arten, None
        if ab_da:
            token_ok, token = versuch(A._ab_token, benutzer)
            if token_ok and token:
                ab_ok, daten = versuch(A._ab_lesen, anlage, token)
                ab_daten = daten if ab_ok else None
            elif token_ok and texte['ab_token'] not in fehler:
                fehler.append(texte['ab_token'])  # Hinweis steht schon vor dem Klick
        ab_sofort = (ab_daten is not None and str(ab_daten.get('active')) == '1'
                     and str(ab_daten.get('option_immediately_check')) == '1')
        liste = []
        passt = False
        for taste in tasten:
            if taste.art == 'weiterleitung':
                ziel = taste._ziel_fuer(benutzer)
                intern = taste.ziel_art == 'intern'
                if intern:
                    # Nebenstelle der eigenen Anlage oder gepflegte Rufgruppe, nie die eigene
                    pruef_ok, ziel_fehler = (versuch(self._wb_voip_intern_pruefen, anlage, nebenstelle, ziel,
                                                     peer_id) if peer_ok else (False, None))
                else:
                    pruef_ok = True
                    ziel_fehler = (texte['handy'] if taste.ziel_art == 'handy' and not ziel
                                   else Taste._ziel_pruefen(ziel, land))
                if ziel_fehler:
                    aktiv = None  # grau: kein Handy eingetragen oder Ziel nicht (mehr) erlaubt
                    if ziel_fehler not in fehler:
                        fehler.append(ziel_fehler)
                elif not (fw_ok and pruef_ok):
                    aktiv = None
                else:
                    gleich = (ziel_ist and (ziel_ist.strip() == ziel.strip() if intern
                                            else Taste._ziel_gleich(ziel, ziel_ist, land)))
                    aktiv = bool(ziel_ist and not passt and gleich)
                    passt = passt or aktiv
            elif taste.art == 'anrufschutz':
                aktiv = dnd if dnd_ok else None
            else:
                aktiv = None if ab_daten is None else self._wb_voip_ab_passt(ab_daten, taste)
            liste.append({'id': taste.id, 'name': taste.name, 'icon': taste.icon or False,
                          'art': taste.art, 'aktiv': aktiv})
        fremde = ziel_ist if (fw_ok and ziel_ist and not passt) else False
        return {
            'verfuegbar': True,
            'fehler': fehler[0] if fehler else False,
            'tasten': liste,
            'fremde_weiterleitung': fremde,
            # Der AB zählt nur, wenn er sofort annimmt (nicht „nach Rufzeit“)
            'umgeleitet': bool(fremde) or ab_sofort or any(
                t['aktiv'] for t in liste if t['art'] != 'anrufbeantworter'),
        }

    @api.model
    def _wb_voip_tasten_senden(self, zustand):
        """Send the state (no token, URL or extension) to all tabs of the user; never raises."""
        try:
            self.env.user._bus_send('wb_voip/tasten', dict(zustand))
        except Exception:  # noqa: BLE001 - Zustand kommt trotzdem als Antwort an
            _logger.exception("[wb_voip_ansitel] Schnelltasten: Bus-Nachricht fehlgeschlagen")

    @api.model
    def _wb_voip_lizenz_meldung(self):
        """Return False with a valid licence, else the message for the grey bar."""
        try:
            info = self.env['wb.license.client'].check_license(PRODUKT_CODE)
            if info.is_valid:
                return False
            return info.user_message or self.env._("Lizenz fehlt oder abgelaufen")
        except Exception:  # noqa: BLE001 - Lesen darf nie werfen
            _logger.exception("[wb_voip_ansitel] Lizenzprüfung für die Schnelltasten fehlgeschlagen")
            return self.env._("Lizenz konnte nicht geprüft werden.")

    @api.model
    @nur_voip_benutzer
    def wb_voip_tasten_zustand(self):
        """Return the key state of the current user, read fresh; never raises towards the browser.

        Without a valid licence the PBX is not asked: all keys grey with the licence message.
        """
        meldung = self._wb_voip_lizenz_meldung()
        if meldung:
            zustand = self._wb_voip_tasten_grau(meldung)
        else:
            schluessel = self._wb_voip_tasten_cache_schluessel()
            zustand = ansitel_api.tasten_cache_holen(schluessel) if schluessel else None
            if zustand is not None:
                return zustand  # höchstens 10 s alt: die Tabs holen nicht jeder die Anlage
            try:
                with self.env.cr.savepoint():
                    zustand = self._wb_voip_tasten_lesen()
                if schluessel:
                    ansitel_api.tasten_cache_setzen(schluessel, zustand)
            except Exception:  # noqa: BLE001
                _logger.exception("[wb_voip_ansitel] Schnelltasten: Zustand lesen fehlgeschlagen")
                zustand = self._wb_voip_tasten_grau(self._wb_voip_tasten_texte()['allgemein'])
        self._wb_voip_tasten_senden(zustand)
        return zustand

    @api.model
    def _wb_voip_tasten_cache_schluessel(self):
        """Return the cache key of the current user (database, user, PBX, write date) or ``None``.

        The write date of the PBX belongs to the key: a changed PBX (URL, token) never serves old states.
        """
        kontext = self._wb_voip_tasten_kontext()
        if not kontext:
            return None
        benutzer, anlage, _nebenstelle = kontext
        return (self.env.cr.dbname, benutzer.id, anlage.id, anlage.write_date)

    @api.model
    def _wb_voip_tasten_schalten(self, taste_id, an=None):
        """Switch a key. ``an`` None toggles; True/False establishes exactly that state.

        If the wished state is already there, nothing is sent, so a second tab or a double click
        does not switch back.
        """
        texte = self._wb_voip_tasten_texte()
        if an is not None and not isinstance(an, bool):
            raise AnsitelFehler(texte['wunsch'])
        kontext = self._wb_voip_tasten_kontext()
        if not kontext:
            return
        benutzer, anlage, nebenstelle = kontext
        if type(taste_id) is not int or taste_id <= 0:
            raise AnsitelFehler(texte['taste_fehlt'])
        taste = self.env['wb.voip.taste'].sudo().browse(taste_id).exists()
        if not taste or not taste.active:
            raise AnsitelFehler(texte['taste_fehlt'])
        A = self.env['wb.voip.ansitel']
        if taste.art == 'anrufbeantworter':
            token = A._ab_token(benutzer)
            if not token:
                raise AnsitelFehler(texte['ab_token'])
            daten = A._ab_lesen(anlage, token)
            ist = self._wb_voip_ab_passt(daten, taste)
            wunsch = (not ist) if an is None else an
            if wunsch and not ist:  # an bzw. in den Modus dieser Taste wechseln
                self._wb_voip_andere_aus(benutzer, anlage, nebenstelle, 'anrufbeantworter')
                A._ab_setzen(anlage, token, True, taste.ab_modus or 'sofort', taste.ab_sekunden or 20,
                             daten=daten)
            elif not wunsch and str(daten.get('active')) == '1':  # aus, gleich in welchem Modus
                A._ab_setzen(anlage, token, False, daten=daten)
            return
        peer_id = A._peer_id(anlage, nebenstelle)
        if peer_id is None:
            raise AnsitelFehler(texte['nebenstelle'])
        if taste.art == 'anrufschutz':
            ist = A._dnd_lesen(anlage, peer_id)
            wunsch = (not ist) if an is None else an
            if wunsch != ist:
                if wunsch:
                    self._wb_voip_andere_aus(benutzer, anlage, nebenstelle, 'anrufschutz', peer_id)
                A._dnd_setzen(anlage, peer_id, wunsch)
            return
        ziel = taste._ziel_fuer(benutzer)
        land = anlage.country_id.code or 'DE'
        if not ziel:
            raise AnsitelFehler(texte['handy'] if taste.ziel_art == 'handy' else texte['ziel'])
        # Jedes Mal prüfen: ein vor der Regel gespeichertes Ziel darf nicht gewählt werden
        if taste.ziel_art == 'intern':
            ziel_fehler = self._wb_voip_intern_pruefen(anlage, nebenstelle, ziel, peer_id)
        else:
            ziel_fehler = taste._ziel_pruefen(ziel, land)
        if ziel_fehler:
            raise AnsitelFehler(ziel_fehler)
        aktuell = A._weiterleitung_lesen(anlage, peer_id)
        if taste.ziel_art == 'intern':
            # Nebenstelle wie eingegeben wählen (geprüft: nur Ziffern), keine 00-/E.164-Umwandlung
            senden = ziel.strip()
            ist = bool(aktuell and aktuell.strip() == senden)
        else:
            # Nur die geprüfte Normalform wählen
            senden = taste._ziel_senden(taste._ziel_e164(ziel, land))
            ist = bool(aktuell and taste._ziel_gleich(ziel, aktuell, land))
        wunsch = (not ist) if an is None else an
        if wunsch and not ist:
            self._wb_voip_andere_aus(benutzer, anlage, nebenstelle, 'weiterleitung', peer_id)
            A._weiterleitung_setzen(anlage, peer_id, senden)
        elif ist and not wunsch:
            A._weiterleitung_setzen(anlage, peer_id, None)

    @api.model
    def _wb_voip_andere_aus(self, benutzer, anlage, nebenstelle, art, peer_id=None):
        """Answering machine, forwarding and do-not-disturb exclude each other.

        Before ``art`` is switched on, the other two are switched off; the answering machine only
        if a fitting token exists (otherwise it cannot be switched).
        """
        A = self.env['wb.voip.ansitel']
        if art != 'anrufbeantworter':
            token = A._ab_token(benutzer)
            if token:
                daten = A._ab_lesen(anlage, token)
                if str(daten.get('active')) == '1':
                    A._ab_setzen(anlage, token, False, daten=daten)
        if peer_id is None:
            peer_id = A._peer_id(anlage, nebenstelle)
            if peer_id is None:
                return
        if art != 'weiterleitung' and A._weiterleitung_lesen(anlage, peer_id):
            A._weiterleitung_setzen(anlage, peer_id, None)
        if art != 'anrufschutz' and A._dnd_lesen(anlage, peer_id):
            A._dnd_setzen(anlage, peer_id, False)

    @api.model
    def _wb_voip_weiterleitung_aus(self):
        kontext = self._wb_voip_tasten_kontext()
        if not kontext:
            return
        _benutzer, anlage, nebenstelle = kontext
        A = self.env['wb.voip.ansitel']
        peer_id = A._peer_id(anlage, nebenstelle)
        if peer_id is None:
            raise AnsitelFehler(self._wb_voip_tasten_texte()['nebenstelle'])
        A._weiterleitung_setzen(anlage, peer_id, None)

    @api.model
    def _wb_voip_tasten_nach_aktion(self, aktion, *args):
        """Run ``aktion``, then read and distribute the state.

        If the PBX is not reachable, it is not read again (another time-out): the bar stays grey
        with the message.
        """
        texte = self._wb_voip_tasten_texte()
        fehler = False
        nicht_erreichbar = False
        # Schalten ändert den Zustand: der Zwischenspeicher gilt nicht mehr (auch bei Fehler)
        ansitel_api.tasten_cache_leeren(self.env.cr.dbname, self.env.uid)
        try:
            with self.env.cr.savepoint():
                aktion(*args)
        except UserError as exc:
            fehler = exc.args[0] if exc.args else texte['allgemein']
            nicht_erreichbar = isinstance(exc, AnsitelNichtErreichbar)
        except Exception:  # noqa: BLE001
            _logger.exception("[wb_voip_ansitel] Schnelltasten: Schalten fehlgeschlagen")
            fehler = texte['allgemein']
        if nicht_erreichbar:
            zustand = self._wb_voip_tasten_grau(fehler)
        else:
            try:
                with self.env.cr.savepoint():
                    zustand = self._wb_voip_tasten_lesen()
            except Exception:  # noqa: BLE001
                _logger.exception("[wb_voip_ansitel] Schnelltasten: Zustand lesen fehlgeschlagen")
                zustand = self._wb_voip_tasten_grau(texte['allgemein'])
            if fehler:
                zustand['fehler'] = fehler
        self._wb_voip_tasten_senden(zustand)
        return zustand

    @api.model
    @nur_voip_benutzer
    @license_required(PRODUKT_CODE)
    def wb_voip_taste_schalten(self, taste_id, an=None):
        """Switch a key of the current user; return the new state.

        ``an``: wished state (True/False); None toggles.
        """
        return self._wb_voip_tasten_nach_aktion(self._wb_voip_tasten_schalten, taste_id, an)

    @api.model
    @nur_voip_benutzer
    @license_required(PRODUKT_CODE)
    def wb_voip_weiterleitung_aus(self):
        """Switch off a forwarding that matches no key; return the new state."""
        return self._wb_voip_tasten_nach_aktion(self._wb_voip_weiterleitung_aus)
