from datetime import timedelta
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import freeze_time, tagged

from .common import WbVoipCase

START = '2026-11-25 09:00:00'
NUMMER = '+49 170 7654321'


@tagged('wb_voip', 'post_install', '-at_install')
class TestTeam(WbVoipCase):
    """Team overview: status of colleagues, team list, bus, pickup prefix."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        gruppen = [cls.env.ref('base.group_user').id,
                   cls.env.ref('wb_voip.group_wb_voip_user').id]
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.user_b.write({'wb_voip_anlage_id': cls.anlage.id, 'wb_voip_sip_benutzer': '101'})
        cls.firma2 = cls.env['res.company'].create({'name': 'Zweitfirma'})
        # C: andere Firma
        cls.user_c = Users.create({
            'name': 'VoIP Benutzer C', 'login': 'wb_voip_tc', 'email': 'tc@example.com',
            'company_id': cls.firma2.id, 'company_ids': [(6, 0, [cls.firma2.id])],
            'group_ids': [(6, 0, gruppen)],
            'wb_voip_anlage_id': cls.anlage.id, 'wb_voip_sip_benutzer': '102'})
        # D: ohne VoIP-Recht
        cls.user_d = Users.create({
            'name': 'Ohne Recht D', 'login': 'wb_voip_td', 'email': 'td@example.com',
            'group_ids': [(6, 0, gruppen[:1])],
            'wb_voip_anlage_id': cls.anlage.id, 'wb_voip_sip_benutzer': '103'})
        # E: ohne SIP-Benutzer
        cls.user_e = Users.create({
            'name': 'Ohne Nebenstelle E', 'login': 'wb_voip_te', 'email': 'te@example.com',
            'group_ids': [(6, 0, gruppen)], 'wb_voip_anlage_id': cls.anlage.id})
        # F: archiviert
        cls.user_f = Users.create({
            'name': 'Archiv F', 'login': 'wb_voip_tf', 'email': 'tf@example.com',
            'group_ids': [(6, 0, gruppen)],
            'wb_voip_anlage_id': cls.anlage.id, 'wb_voip_sip_benutzer': '104'})
        cls.user_f.active = False

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()
        # Standard ist aus; diese Klasse prüft die eingeschaltete Übersicht
        self.env.company.write({'wb_voip_team_aktiv': True, 'wb_voip_team_dauer': True})
        self._zaehler = 0
        self.bus = []
        Users = type(self.env['res.users'])
        original = Users._bus_send
        meldungen = self.bus

        def mitschreiben(benutzer, typ, nutzlast, *args, **kwargs):
            meldungen.append((benutzer.id, typ, nutzlast))
            return original(benutzer, typ, nutzlast, *args, **kwargs)

        patcher = patch.object(Users, '_bus_send', mitschreiben)
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- Hilfen ---

    def _melden(self, user, registriert=True):
        self.env['res.users'].with_user(user).wb_voip_lebenszeichen_melden(registriert)

    def _team(self, user):
        return self.env['res.users'].with_user(user).wb_voip_team()

    def _kollege(self, user, gesucht):
        return next((k for k in self._team(user)['kollegen'] if k['user_id'] == gesucht.id), None)

    def _anruf(self, user, richtung='eingehend'):
        self._zaehler += 1
        Anruf = self.env['wb.voip.anruf'].with_user(user)
        daten = Anruf.wb_voip_beginnen(richtung, NUMMER, f'team-{self._zaehler}', False, False)
        return Anruf.browse(daten['id'])

    def _team_meldungen(self, user=None):
        return [m for m in self.bus if m[1] == 'wb_voip/team'
                and (user is None or m[0] == user.id)]

    # --- Felder ---

    def test_felder_nur_server(self):
        """The new fields cannot be written by the user himself."""
        self.assertNotIn('wb_voip_lebenszeichen', self.user_a.SELF_WRITEABLE_FIELDS)
        self.assertNotIn('wb_voip_registriert', self.user_a.SELF_WRITEABLE_FIELDS)
        with self.assertRaises(AccessError):
            self.user_a.with_user(self.user_a).write({'wb_voip_registriert': True})
        with self.assertRaises(AccessError):
            self.user_b.with_user(self.user_a).read(['wb_voip_lebenszeichen'])

    def test_melden_prueft_typ(self):
        Users = self.env['res.users'].with_user(self.user_a)
        for falsch in (1, 'ja', None, 0):
            with self.assertRaises(UserError):
                Users.wb_voip_lebenszeichen_melden(falsch)

    def test_melden_nur_voip_benutzer(self):
        with self.assertRaises(AccessError):
            self.env['res.users'].with_user(self.user_d).wb_voip_lebenszeichen_melden(True)
        with self.assertRaises(AccessError):
            self.env['res.users'].with_user(self.user_d).wb_voip_team()

    # --- Team-Zusammensetzung (Review Focus 1) ---

    def test_team_zusammensetzung(self):
        with freeze_time(START):
            self._melden(self.user_b)
            kollegen = self._team(self.user_a)['kollegen']
        ids = [k['user_id'] for k in kollegen]
        self.assertIn(self.user_b.id, ids)
        self.assertNotIn(self.user_a.id, ids, 'Aufrufer erscheint nicht')
        self.assertNotIn(self.user_c.id, ids, 'andere Firma')
        self.assertNotIn(self.user_d.id, ids, 'ohne VoIP-Recht')
        self.assertNotIn(self.user_e.id, ids, 'ohne SIP-Benutzer')
        self.assertNotIn(self.user_f.id, ids, 'archiviert')
        self.assertNotIn(self.user_manager.id, ids, 'Manager ohne Nebenstelle')

    def test_team_nur_dokumentierte_schluessel(self):
        with freeze_time(START):
            self._melden(self.user_b)
            ergebnis = self._team(self.user_a)
        self.assertEqual(set(ergebnis), {'aktiv', 'heranholen_praefix', 'kollegen'})
        self.assertIs(ergebnis['aktiv'], True)
        eintrag = next(k for k in ergebnis['kollegen'] if k['user_id'] == self.user_b.id)
        self.assertEqual(set(eintrag), {'user_id', 'name', 'nebenstelle', 'status', 'seit'})
        self.assertEqual(eintrag['nebenstelle'], '101')

    def test_team_sortiert_und_begrenzt(self):
        self.user_b.name = 'Zzz Zuletzt'
        Users = self.env['res.users'].with_context(no_reset_password=True)
        gruppen = [self.env.ref('base.group_user').id,
                   self.env.ref('wb_voip.group_wb_voip_user').id]
        Users.create({'name': 'Aaa Zuerst', 'login': 'wb_voip_ta', 'email': 'ta@example.com',
                      'group_ids': [(6, 0, gruppen)],
                      'wb_voip_anlage_id': self.anlage.id, 'wb_voip_sip_benutzer': '150'})
        namen = [k['name'] for k in self._team(self.user_a)['kollegen']]
        self.assertEqual(namen, sorted(namen))
        self.assertLess(namen.index('Aaa Zuerst'), namen.index('Zzz Zuletzt'))
        with patch('odoo.addons.wb_voip.models.res_users.TEAM_HOECHSTENS', 1):
            self.assertEqual(len(self._team(self.user_a)['kollegen']), 1)

    def test_team_gemeinsame_firma(self):
        """A colleague sharing at least one company with the caller's active company shows."""
        self.user_c.write({'company_ids': [(4, self.env.company.id)]})
        ids = [k['user_id'] for k in self._team(self.user_a)['kollegen']]
        self.assertIn(self.user_c.id, ids)

    # --- Status (Review Focus 2) ---

    def test_status_abgemeldet_ohne_lebenszeichen(self):
        self.assertEqual(self._kollege(self.user_a, self.user_b)['status'], 'abgemeldet')

    def test_status_frei(self):
        with freeze_time(START):
            self._melden(self.user_b)
            kollege = self._kollege(self.user_a, self.user_b)
        self.assertEqual(kollege['status'], 'frei')
        self.assertFalse(kollege['seit'])

    def test_status_nicht_registriert(self):
        with freeze_time(START):
            self._melden(self.user_b, registriert=False)
            self.assertEqual(self._kollege(self.user_a, self.user_b)['status'], 'abgemeldet')

    def test_status_klingelt(self):
        with freeze_time(START):
            self._melden(self.user_b)
            self._anruf(self.user_b)
            kollege = self._kollege(self.user_a, self.user_b)
        self.assertEqual(kollege['status'], 'klingelt')
        self.assertFalse(kollege['seit'])

    def test_status_ausgehender_ruf_ist_telefoniert(self):
        with freeze_time(START):
            self._melden(self.user_b)
            self._anruf(self.user_b, 'ausgehend')
            kollege = self._kollege(self.user_a, self.user_b)
        self.assertEqual(kollege['status'], 'telefoniert')
        self.assertNotEqual(kollege['status'], 'klingelt')
        self.assertFalse(kollege['seit'])

    def test_status_telefoniert_mit_seit(self):
        with freeze_time(START):
            self._melden(self.user_b)
            anruf = self._anruf(self.user_b)
            anruf.wb_voip_angenommen()
            kollege = self._kollege(self.user_a, self.user_b)
        self.assertEqual(kollege['status'], 'telefoniert')
        self.assertEqual(kollege['seit'], START)

    def test_status_nach_ende_wieder_frei(self):
        with freeze_time(START):
            self._melden(self.user_b)
            anruf = self._anruf(self.user_b)
            anruf.wb_voip_angenommen()
            anruf.wb_voip_beendet(False, False, True)
            self.assertEqual(self._kollege(self.user_a, self.user_b)['status'], 'frei')

    def test_status_lebenszeichen_laeuft_ab(self):
        """After 150 s without sign of life the colleague is logged off, even mid-call."""
        with freeze_time(START) as zeit:
            self._melden(self.user_b)
            anruf = self._anruf(self.user_b)
            anruf.wb_voip_angenommen()
            zeit.tick(timedelta(seconds=150))
            self.assertEqual(self._kollege(self.user_a, self.user_b)['status'], 'telefoniert')
            zeit.tick(timedelta(seconds=1))
            self.assertEqual(self._kollege(self.user_a, self.user_b)['status'], 'abgemeldet')
            self.assertFalse(self._kollege(self.user_a, self.user_b)['seit'])
            self._melden(self.user_b)
            self.assertEqual(self._kollege(self.user_a, self.user_b)['status'], 'telefoniert')

    def test_keine_gegennummer_im_ergebnis(self):
        with freeze_time(START):
            self._melden(self.user_b)
            self._anruf(self.user_b).wb_voip_angenommen()
            text = repr(self._team(self.user_a))
        self.assertNotIn('170', text)
        self.assertNotIn('7654321', text)

    # --- Bus ---

    def test_bus_nur_bei_aenderung(self):
        with freeze_time(START) as zeit:
            self._melden(self.user_b)
            self.assertEqual(len(self._team_meldungen(self.user_a)), 1)
            self.assertEqual(self._team_meldungen(self.user_a)[0][2], {})
            self.assertFalse(self._team_meldungen(self.user_b), 'nicht an sich selbst')
            self.assertFalse(self._team_meldungen(self.user_c), 'nicht an andere Firma')
            self.bus.clear()
            zeit.tick(timedelta(seconds=60))
            self._melden(self.user_b)  # unverändert
            self.assertFalse(self._team_meldungen())
            self._melden(self.user_b, registriert=False)
            self.assertEqual(len(self._team_meldungen(self.user_a)), 1)

    def test_bus_bei_zustandswechsel(self):
        with freeze_time(START):
            self._melden(self.user_b)
            self.bus.clear()
            anruf = self._anruf(self.user_b)
            self.assertEqual(len(self._team_meldungen(self.user_a)), 1, 'klingelt')
            anruf.wb_voip_angenommen()
            self.assertEqual(len(self._team_meldungen(self.user_a)), 2, 'läuft')
            anruf.wb_voip_beendet(False, False, True)
            self.assertEqual(len(self._team_meldungen(self.user_a)), 3, 'beendet')
            self.assertFalse(self._team_meldungen(self.user_c))

    def test_bus_ohne_zustandswechsel_still(self):
        with freeze_time(START):
            anruf = self._anruf(self.user_b)
            self.bus.clear()
            anruf.wb_voip_lebenszeichen()
            anruf.sudo().write({'notiz': 'x'})
            self.assertFalse(self._team_meldungen())

    def test_bus_ein_sendevorgang_je_empfaenger(self):
        """Cron closing several calls of one owner sends once per team member."""
        with freeze_time(START):
            self._melden(self.user_b)
            self._anruf(self.user_b)
            self._anruf(self.user_b)
            anrufe = self.env['wb.voip.anruf'].sudo().search([('user_id', '=', self.user_b.id)])
            self.bus.clear()
            anrufe.write({'zustand': 'fehlgeschlagen'})
            self.assertEqual(len(self._team_meldungen(self.user_a)), 1)

    def test_bus_je_besitzer_auch_an_andere_besitzer(self):
        with freeze_time(START):
            self._melden(self.user_a)
            self._melden(self.user_b)
            anrufe = self._anruf(self.user_a) | self._anruf(self.user_b)
            self.bus.clear()
            anrufe.sudo().write({'zustand': 'fehlgeschlagen'})
            # A hört von B und B von A, keiner von sich selbst
            self.assertEqual(len(self._team_meldungen(self.user_a)), 1)
            self.assertEqual(len(self._team_meldungen(self.user_b)), 1)

    def test_bus_nicht_bei_unveraendertem_status(self):
        with freeze_time(START):
            # abgemeldeter Besitzer: Status bleibt abgemeldet
            anruf = self._anruf(self.user_b)
            anruf.wb_voip_angenommen()
            self.assertFalse(self._team_meldungen())
            # angemeldet, zweite Leitung: telefoniert bleibt telefoniert
            self._melden(self.user_b)
            zweiter = self._anruf(self.user_b, 'ausgehend')
            self.bus.clear()
            zweiter.wb_voip_angenommen()
            self.assertFalse(self._team_meldungen())

    def test_abmelden_loescht_lebenszeichen(self):
        with freeze_time(START):
            self._melden(self.user_b)
            self.assertTrue(self.user_b.sudo().wb_voip_lebenszeichen)
            self._melden(self.user_b, registriert=False)
            self.assertFalse(self.user_b.sudo().wb_voip_lebenszeichen)
            self.assertFalse(self.user_b.sudo().wb_voip_registriert)

    # --- Gleiche Anlage ---

    def test_team_nur_eigene_anlage(self):
        anlage2 = self.env['wb.voip.anlage'].create({
            'name': 'Zweitanlage', 'ws_url': 'wss://zwei.test/ws', 'sip_domain': 'zwei.test'})
        gruppen = [self.env.ref('base.group_user').id,
                   self.env.ref('wb_voip.group_wb_voip_user').id]
        user_g = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Andere Anlage G', 'login': 'wb_voip_tg', 'email': 'tg@example.com',
            'group_ids': [(6, 0, gruppen)],
            'wb_voip_anlage_id': anlage2.id, 'wb_voip_sip_benutzer': '300'})
        with freeze_time(START):
            self._melden(user_g)
            self.assertIsNone(self._kollege(self.user_a, user_g))
            self.assertIsNone(self._kollege(user_g, self.user_a))
            self._melden(self.user_b)
            self.bus.clear()
            self._melden(self.user_b, registriert=False)
            self.assertFalse(self._team_meldungen(user_g))
            self.assertTrue(self._team_meldungen(self.user_a))

    # --- Präfix (Review Focus 5) ---

    def test_praefix_gueltig(self):
        for wert in (False, '', '**', '*8', '#5', '0123456789'[:8]):
            self.anlage.heranholen_praefix = wert

    def test_praefix_ungueltig(self):
        for wert in ('abc', '12 3', '*8\n', '123456789', '+49', '*8;', 'ü'):
            with self.assertRaises(ValidationError, msg=repr(wert)):
                self.anlage.heranholen_praefix = wert

    def test_praefix_nur_manager_schreibt(self):
        with self.assertRaises(AccessError):
            self.anlage.with_user(self.user_a).write({'heranholen_praefix': '**'})
        self.anlage.with_user(self.user_manager).write({'heranholen_praefix': '**'})

    def test_team_liefert_praefix(self):
        self.assertFalse(self._team(self.user_a)['heranholen_praefix'])
        self.anlage.heranholen_praefix = '**'
        self.assertEqual(self._team(self.user_a)['heranholen_praefix'], '**')
        self.assertEqual(self._team(self.user_b)['heranholen_praefix'], '**')

    # --- Schalter je Firma (Betriebsrat, Art. 25 DSGVO) ---

    def test_schalter_standard_aus(self):
        firma = self.env['res.company'].create({'name': 'Neue Firma Team'})
        self.assertIs(firma.wb_voip_team_aktiv, False)
        self.assertIs(firma.wb_voip_team_dauer, False)

    def test_aus_team_leer_mit_praefix(self):
        self.anlage.heranholen_praefix = '**'
        with freeze_time(START):
            self._melden(self.user_b)
            self.env.company.wb_voip_team_aktiv = False
            self.assertEqual(self._team(self.user_a), {
                'aktiv': False, 'heranholen_praefix': '**', 'kollegen': []})

    def test_aus_lebenszeichen_speichert_nichts(self):
        self.env.company.wb_voip_team_aktiv = False
        with freeze_time(START):
            self.assertIs(self.env['res.users'].with_user(self.user_b)
                          .wb_voip_lebenszeichen_melden(True), True)
        self.assertFalse(self.user_b.sudo().wb_voip_lebenszeichen)
        self.assertFalse(self.user_b.sudo().wb_voip_registriert)
        self.assertFalse(self._team_meldungen())

    def test_aus_loescht_vorhandenes_lebenszeichen(self):
        with freeze_time(START):
            self._melden(self.user_b)
            self.env.company.wb_voip_team_aktiv = False
            self.bus.clear()
            self._melden(self.user_b)
        self.assertFalse(self.user_b.sudo().wb_voip_lebenszeichen)
        self.assertFalse(self._team_meldungen())

    def test_aus_kein_team_bus_bei_anrufen(self):
        with freeze_time(START):
            self._melden(self.user_b)
            self.env.company.wb_voip_team_aktiv = False
            self.bus.clear()
            anruf = self._anruf(self.user_b)
            anruf.wb_voip_angenommen()
            anruf.wb_voip_beendet(False, False, True)
        self.assertFalse(self._team_meldungen())

    def test_dauer_aus_ohne_seit(self):
        self.env.company.wb_voip_team_dauer = False
        with freeze_time(START):
            self._melden(self.user_b)
            self._anruf(self.user_b).wb_voip_angenommen()
            kollege = self._kollege(self.user_a, self.user_b)
        self.assertEqual(kollege['status'], 'telefoniert')
        self.assertIs(kollege['seit'], False)

    def test_status_meldet_team_aktiv(self):
        Users = self.env['res.users'].with_user(self.user_a)
        self.assertIs(Users.wb_voip_status()['team_aktiv'], True)
        self.env.company.wb_voip_team_aktiv = False
        self.assertIs(Users.wb_voip_status()['team_aktiv'], False)

    def test_einstellungen(self):
        Settings = self.env['res.config.settings']
        for feld in ('wb_voip_team_aktiv', 'wb_voip_team_dauer'):
            self.assertEqual(Settings._fields[feld].groups, 'wb_voip.group_wb_voip_manager')
            self.assertEqual(Settings._fields[feld].related, f'company_id.{feld}')
        self.env.company.write({'wb_voip_team_aktiv': False, 'wb_voip_team_dauer': False})
        Settings.create({'wb_voip_team_aktiv': True, 'wb_voip_team_dauer': True}).execute()
        self.assertIs(self.env.company.wb_voip_team_aktiv, True)
        self.assertIs(self.env.company.wb_voip_team_dauer, True)

    def test_schalter_nur_admins_schreiben(self):
        with self.assertRaises(AccessError):
            self.env.company.with_user(self.user_a).write({'wb_voip_team_aktiv': False})

    # --- Lebenszeichen ohne ORM-Schreiben am Benutzer ---

    def test_lebenszeichen_ohne_orm_write(self):
        Users = type(self.env['res.users'])
        original = Users.write
        geschrieben = []

        def mitschreiben(benutzer, vals):
            geschrieben.append(set(vals))
            return original(benutzer, vals)

        with patch.object(Users, 'write', mitschreiben), freeze_time(START):
            self._melden(self.user_b)
            self.assertEqual(self._kollege(self.user_a, self.user_b)['status'], 'frei')
            self._melden(self.user_b, registriert=False)
            self.assertEqual(self._kollege(self.user_a, self.user_b)['status'], 'abgemeldet')
        self.assertFalse([v for v in geschrieben
                          if v & {'wb_voip_lebenszeichen', 'wb_voip_registriert'}])

    # --- Abgewiesener dritter Anruf: direkt verpasst, ohne Team-Bus ---

    def _abweisen(self, user, sip_call_id='abgewiesen-1', nummer=NUMMER):
        return self.env['wb.voip.anruf'].with_user(user).wb_voip_abgewiesen_melden(
            nummer, sip_call_id)

    def test_abgewiesen_verpasst_ohne_team_bus(self):
        kunde = self.env['res.partner'].create({'name': 'Kunde Abgewiesen', 'phone': NUMMER})
        with freeze_time(START):
            self._melden(self.user_b)
            self.bus.clear()
            daten = self._abweisen(self.user_b)
            self.assertEqual(self._kollege(self.user_a, self.user_b)['status'], 'frei')
        self.assertFalse(self._team_meldungen(), 'kein kurzes „klingelt“ im Team')
        self.assertEqual(daten['zustand'], 'verpasst')
        anruf = self.env['wb.voip.anruf'].browse(daten['id']).sudo()
        self.assertEqual(anruf.user_id, self.user_b)
        self.assertEqual(anruf.richtung, 'eingehend')
        self.assertEqual(anruf.partner_id, kunde)
        self.assertTrue(anruf.protokoll_id, 'Protokoll wie bei verpasst')
        self.assertTrue(anruf.rueckruf_aktivitaet_id, 'Rückruf-Aktivität wie bei verpasst')
        self.assertTrue([m for m in self.bus if m[1] == 'wb_voip/verpasst'])

    def test_abgewiesen_idempotent(self):
        erst = self._abweisen(self.user_b)
        noch = self._abweisen(self.user_b)
        self.assertEqual(erst['id'], noch['id'])
        self.assertEqual(self.env['wb.voip.anruf'].sudo().search_count([
            ('user_id', '=', self.user_b.id), ('sip_call_id', '=', 'abgewiesen-1')]), 1)

    def test_abgewiesen_prueft_eingaben(self):
        for nummer, sip_call_id in ((False, 'x'), ('', 'x'), (123, 'x'), ('1' * 65, 'x'),
                                    (NUMMER, False), (NUMMER, ''), (NUMMER, 'x' * 256)):
            with self.assertRaises(UserError, msg=repr((nummer, sip_call_id))):
                self._abweisen(self.user_b, sip_call_id=sip_call_id, nummer=nummer)

    def test_abgewiesen_rechte_und_lizenz(self):
        with self.assertRaises(AccessError):
            self._abweisen(self.user_d)
        self._lizenz_fehlt()
        with self.assertRaises(UserError):
            self._abweisen(self.user_b, sip_call_id='ohne-lizenz')

    def test_ausschalten_loescht_lebenszeichen_der_firma(self):
        """Switching the overview off clears the stored signs of life of the company's users."""
        with freeze_time(START):
            self._melden(self.user_b)
            # Benutzer einer anderen Firma bleibt unberührt
            self.user_c.sudo().write({'wb_voip_lebenszeichen': START, 'wb_voip_registriert': True})
            self.bus.clear()
            self.env.company.write({'wb_voip_team_aktiv': False})
        self.assertFalse(self.user_b.sudo().wb_voip_lebenszeichen)
        self.assertFalse(self.user_b.sudo().wb_voip_registriert)
        self.assertTrue(self.user_c.sudo().wb_voip_lebenszeichen)
        self.assertTrue(self.user_c.sudo().wb_voip_registriert)
        self.assertFalse(self._team_meldungen())
