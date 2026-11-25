import logging
from datetime import timedelta

from psycopg2 import errors

from odoo import api, fields, models
from odoo.exceptions import AccessError, ConcurrencyError, UserError, ValidationError
from odoo.addons.base.models.res_partner import _tz_get

from odoo.addons.wb_voip.tools import geheimnis
from odoo.addons.wb_voip.tools.rechte import nur_voip_manager

from ..tools.adresse import url_ist_https
from .ansitel_api import AnsitelFehler, AnsitelZuVieleAnrufe
from .wb_voip_anruf import MAX_LINKEDID, zweig_ende

_logger = logging.getLogger(__name__)

# Gruppen, deren letzter Zweig später als jetzt − 60 s endet, laufen evtl. noch: nächster Lauf
CDR_RUHE = timedelta(seconds=60)
# Überlappung mit dem vorigen Lauf: die Anlage schreibt eine Zeile erst beim Auflegen, ein langes
# Gespräch erscheint also spät mit frühem ``calldate``. Doppelt Gelesenes ist harmlos (idempotent).
CDR_UEBERLAPPUNG = timedelta(hours=4)
# Erster Lauf ohne Stand (z. B. nach der Einrichtung): keine alten Anrufe importieren
CDR_ERSTES_FENSTER = timedelta(minutes=30)
# Höchstens so viel je Lauf; nach längerem Ausfall holen die folgenden Läufe Stück für Stück nach
CDR_MAX_FENSTER = timedelta(hours=6)
# Liefert die Anlage für ein Fenster zu viele Seiten, wird es halbiert, aber nicht unter diese Länge
CDR_MIN_FENSTER = timedelta(minutes=10)
# Felder, die _wb_voip_ansitel_sql_setzen schreiben darf
CDR_SQL_FELDER = ('ansitel_cdr_stand', 'ansitel_cdr_ab')
# Fehler, nach denen Odoo den ganzen Lauf wiederholt: nie verschlucken
WIEDERHOLBAR = (errors.SerializationFailure, errors.DeadlockDetected, errors.LockNotAvailable,
                ConcurrencyError)


class WbVoipAnlage(models.Model):
    _inherit = 'wb.voip.anlage'

    ansitel_aktiv = fields.Boolean(
        string='Ansitel-Schnittstelle', copy=False, groups='base.group_system',
        help="Schnelltasten und Abgleich der Anrufliste über die REST-Schnittstelle der Anlage.")
    ansitel_api_url = fields.Char(
        string='Ansitel-REST-URL', copy=False, groups='base.group_system',
        help="Nur https, z. B. https://anlage.example/awi/api/rest.")
    # Nur schreibbar: liefert beim Lesen immer leer, gespeichert wird das Chiffrat
    ansitel_token = fields.Char(
        string='Ansitel-Token (zentral)', compute='_compute_ansitel_token',
        inverse='_inverse_ansitel_token', store=False, copy=False, groups='base.group_system')
    ansitel_token_enc = fields.Char(
        string='Ansitel-Token (verschlüsselt)', copy=False, groups='base.group_system')
    ansitel_token_gesetzt = fields.Boolean(
        string='Ansitel-Token gespeichert', compute='_compute_ansitel_token_gesetzt')
    ansitel_tz = fields.Selection(
        _tz_get, string='Zeitzone der Anrufliste', default='Europe/Berlin', required=True,
        help="Die Anlage liefert die Zeiten der Anrufliste in Ortszeit dieser Zone.")
    # Stand und Beginn des Abgleichs: per SQL geschrieben, damit das Schreibdatum (Schlüssel des
    # Peer-Caches) unberührt bleibt
    ansitel_cdr_stand = fields.Datetime(
        string='Anrufliste abgeglichen bis (UTC)', readonly=True, copy=False,
        groups='base.group_system',
        help="Bis hierhin ist die Anrufliste abgeglichen; der nächste Lauf beginnt 4 Stunden davor.")
    ansitel_cdr_ab = fields.Datetime(
        string='Anrufliste abgeglichen ab (UTC)', readonly=True, copy=False,
        groups='base.group_system',
        help="Beginn des ersten Abgleichs; kein Lauf liest weiter zurück.")
    ansitel_geraet_ids = fields.One2many('wb.voip.geraet', 'anlage_id', string='Geräte')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._ansitel_url_bereinigen(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._ansitel_url_bereinigen(vals)
        # Auch über sudo: eine umgebogene Adresse darf keinen gespeicherten Token erhalten
        verwerfen = 'ansitel_api_url' in vals and not self.env.user._is_system()
        ergebnis = super().write(vals)
        if verwerfen:
            self._ansitel_tokens_verwerfen()
        return ergebnis

    def _ansitel_tokens_verwerfen(self):
        """Delete all stored Ansitel tokens of these PBXs: central and personal ones of their users."""
        self.sudo().write({'ansitel_token_enc': False})
        benutzer = self.env['res.users'].sudo().with_context(active_test=False).search([
            ('wb_voip_anlage_id', 'in', self.ids), ('wb_voip_ansitel_token_enc', '!=', False)])
        benutzer.write({'wb_voip_ansitel_token_enc': False})

    @staticmethod
    def _ansitel_url_bereinigen(vals):
        """Strip the URL so that the stored value is the checked one."""
        if isinstance(vals.get('ansitel_api_url'), str):
            vals['ansitel_api_url'] = vals['ansitel_api_url'].strip() or False

    @api.constrains('ansitel_aktiv', 'ansitel_api_url')
    def _check_ansitel_api_url(self):
        for anlage in self.sudo():
            if anlage.ansitel_api_url and not url_ist_https(anlage.ansitel_api_url):
                raise ValidationError(self.env._(
                    "Die Ansitel-URL muss mit https:// beginnen und einen Host haben, "
                    "ohne Benutzer und Passwort, z. B. https://anlage.example/awi/api/rest."))
            if anlage.ansitel_aktiv and not anlage.ansitel_api_url:
                raise ValidationError(self.env._(
                    "Für die Ansitel-Schnittstelle fehlt die REST-URL."))

    def _compute_ansitel_token(self):
        for anlage in self:
            anlage.ansitel_token = False

    def _inverse_ansitel_token(self):
        for anlage in self:
            klartext = anlage.ansitel_token
            chiffrat = geheimnis.verschluesseln(self.env, klartext) if klartext else False
            anlage.sudo().ansitel_token_enc = chiffrat
        # Klartext nicht im Cache lassen: nächstes Lesen rechnet neu (leer)
        self.invalidate_recordset(['ansitel_token'])

    @api.depends('ansitel_token_enc')
    def _compute_ansitel_token_gesetzt(self):
        for anlage in self:
            anlage.ansitel_token_gesetzt = bool(anlage.sudo().ansitel_token_enc)

    @nur_voip_manager
    def action_ansitel_testen(self):
        """Read the peer list as a harmless call and show the result as a notification."""
        self.ensure_one()
        try:
            self.env['wb.voip.ansitel']._aufruf(self, 'getpeer')
        except UserError as fehler:
            art, text = 'danger', str(fehler)
        else:
            art, text = 'success', self.env._("Verbindung zur Anlage steht.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': self.env._("Ansitel"), 'message': text, 'type': art, 'sticky': False},
        }

    # --- Geräte ---

    def action_wb_voip_ansitel_geraete_laden(self):
        """Create a device for every peer of the PBX (``getpeer``) that is not in the table yet.

        Only the system administration: the table maps PBX devices to users. The owner is the
        unique user of this PBX with that SIP user, else with that extension.
        """
        if not self.env.user._is_system():
            raise AccessError(self.env._("Geräte laden darf nur die Systemverwaltung."))
        Geraet = self.env['wb.voip.geraet'].sudo()
        Users = self.env['res.users'].sudo()
        neu = 0
        for anlage in self.sudo():
            try:
                liste = self.env['wb.voip.ansitel']._aufruf(anlage, 'getpeer').get('data') or []
            except AnsitelFehler as fehler:
                raise UserError(str(fehler)) from None
            if not isinstance(liste, list):
                raise UserError(self.env._("Ansitel hat unverständlich geantwortet."))
            vorhanden = set(Geraet.with_context(active_test=False).search(
                [('anlage_id', '=', anlage.id)]).mapped('peer_name'))
            for peer in liste:
                if not isinstance(peer, dict):
                    continue
                peer_name = str(peer.get('peerName') or '').strip()[:128]
                if not peer_name or peer_name in vorhanden:
                    continue
                intern = str(peer.get('peerInternalCallerId') or '').strip()[:64]
                benutzer = Users.search([('wb_voip_anlage_id', '=', anlage.id),
                                         ('wb_voip_sip_benutzer', '=', peer_name)])
                if len(benutzer) != 1 and intern:
                    benutzer = Users.search([('wb_voip_anlage_id', '=', anlage.id),
                                             ('wb_voip_ansitel_nebenstelle', '=', intern)])
                name = str(peer.get('peerDisplayName') or '').strip()[:128] or peer_name
                Geraet.create({
                    'name': name, 'peer_name': peer_name, 'nebenstelle': intern or False,
                    'anlage_id': anlage.id, 'user_id': benutzer.id if len(benutzer) == 1 else False,
                })
                vorhanden.add(peer_name)
                neu += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': self.env._("Ansitel"), 'type': 'success', 'sticky': False,
                       'message': self.env._("%s neue Geräte angelegt.", neu)},
        }

    # --- Abgleich mit der Anrufliste (Cron) ---

    @api.model
    def _wb_voip_ansitel_abgleich_alle(self):
        """Cron: synchronise every active Ansitel PBX with its call list.

        Runs without a licence: it only reads the call list and records calls, it switches
        nothing. A failure only affects its PBX (savepoint, log without token or numbers);
        retryable database errors propagate so that Odoo repeats the run.
        """
        anlagen = self.sudo().search([
            ('ansitel_aktiv', '=', True), ('ansitel_api_url', '!=', False),
            ('ansitel_token_enc', '!=', False)])
        for anlage in anlagen:
            try:
                with self.env.cr.savepoint():
                    anlage._wb_voip_ansitel_abgleich()
            except WIEDERHOLBAR:
                raise
            except Exception as fehler:  # noqa: BLE001 - eine Anlage darf die anderen nicht aufhalten
                _logger.warning("[wb_voip_ansitel] Abgleich: Anlage %s fehlgeschlagen (%s)",
                                anlage.id, type(fehler).__name__)

    def _wb_voip_ansitel_abgleich(self):
        """Read the call list since the last state and align it per ``linkedid``.

        Window: from = max(state − 4 h, first start), to = min(now − 60 s, from + 6 h); without
        state the last 30 minutes, remembered as lower bound. Only groups with an incoming leg
        whose last leg ended by ``to``. A PBX error leaves the state; the next run catches up.
        The lower bound is stored only after the first successful read. Too many legs for the
        window: see ``_wb_voip_ansitel_lesen``. The state never moves backwards.
        """
        Anruf = self.env['wb.voip.anruf'].sudo()
        for anlage in self.sudo():
            if not (anlage.ansitel_aktiv and anlage.ansitel_api_url and anlage.ansitel_token_enc):
                continue
            jetzt = fields.Datetime.now()
            if anlage.ansitel_cdr_stand:
                von = anlage.ansitel_cdr_stand - CDR_UEBERLAPPUNG
                # nie vor den ersten Lauf: die Überlappung importierte sonst alte Anrufe
                if anlage.ansitel_cdr_ab:
                    von = max(von, anlage.ansitel_cdr_ab)
            else:
                von = jetzt - CDR_ERSTES_FENSTER
            bis = min(jetzt - CDR_RUHE, von + CDR_MAX_FENSTER)
            if bis <= von:
                continue
            try:
                zeilen, bis = anlage._wb_voip_ansitel_lesen(von, bis)
            except AnsitelFehler as fehler:
                # feste Meldung ohne Token, URL und Rufnummern
                _logger.warning("[wb_voip_ansitel] Abgleich: Anlage %s nicht gelesen: %s",
                                anlage.id, fehler)
                continue
            if not anlage.ansitel_cdr_stand and not anlage.ansitel_cdr_ab:
                # erst nach dem ersten erfolgreichen Lesen: Beginn des Abgleichs merken
                anlage._wb_voip_ansitel_sql_setzen('ansitel_cdr_ab', von)
            gruppen = {}
            for zeile in zeilen:
                lid = str(zeile.get('linkedid') or '').strip()[:MAX_LINKEDID]
                if lid:
                    gruppen.setdefault(lid, []).append(zeile)
            fehler_anzahl = geaendert = 0
            for zweige in gruppen.values():
                if not any(z.get('direction') == 'incoming' for z in zweige):
                    continue
                # endet ein Zweig nach ``bis``, kann die Gruppe unvollständig sein: nächster Lauf
                if max(zweig_ende(z) for z in zweige) > bis:
                    continue
                try:
                    with self.env.cr.savepoint():
                        geaendert += bool(Anruf._wb_voip_cdr_gruppe(anlage, zweige))
                except WIEDERHOLBAR:
                    raise
                except Exception as fehler:  # noqa: BLE001 - eine Gruppe hält den Lauf nicht auf
                    fehler_anzahl += 1
                    _logger.warning("[wb_voip_ansitel] Abgleich: Anruf der Anlage %s nicht "
                                    "abgeglichen (%s)", anlage.id, type(fehler).__name__)
            # nie hinter den alten Stand zurück
            stand = anlage.ansitel_cdr_stand
            anlage._wb_voip_ansitel_sql_setzen('ansitel_cdr_stand', max(bis, stand) if stand else bis)
            _logger.log(logging.INFO if geaendert or fehler_anzahl else logging.DEBUG,
                        "[wb_voip_ansitel] Abgleich: Anlage %s, %s Anrufe, %s geändert, %s Fehler",
                        anlage.id, len(gruppen), geaendert, fehler_anzahl)

    def _wb_voip_ansitel_lesen(self, von, bis):
        """Return ``(legs, to)`` of the window; shrink it while the PBX has too many legs.

        First the overlap is dropped (from = state), then the window is halved from there, down
        to ``CDR_MIN_FENSTER``. If even that overflows, the slice is given up (known loss): no
        legs, ``to`` of the smallest window so that the state advances, one error in the log.
        """
        self.ensure_one()
        Ansitel = self.env['wb.voip.ansitel']
        stand = self.ansitel_cdr_stand
        while True:
            try:
                return Ansitel._cdr_lesen(self, von, bis), bis
            except AnsitelZuVieleAnrufe:
                if stand and von < stand < bis:
                    von = stand  # erst die Überlappung weglassen
                    continue
                if bis - von <= CDR_MIN_FENSTER:
                    _logger.error("[wb_voip_ansitel] Abgleich: Anlage %s liefert auch für %s Minuten "
                                  "zu viele Anrufe, Fenster übersprungen", self.id,
                                  int((bis - von).total_seconds() // 60))
                    return [], bis
                bis = max(von + (bis - von) / 2, von + CDR_MIN_FENSTER)

    def _wb_voip_ansitel_sql_setzen(self, feld, wert):
        """Write state or start by SQL: ``write`` would change ``write_date`` and with it the key
        of the peer cache, which would then expire on every run."""
        if feld not in CDR_SQL_FELDER:
            raise ValueError(feld)
        self.ensure_one()
        self.env.cr.execute(f'UPDATE wb_voip_anlage SET {feld} = %s WHERE id = %s', [wert, self.id])
        self.invalidate_recordset([feld])
