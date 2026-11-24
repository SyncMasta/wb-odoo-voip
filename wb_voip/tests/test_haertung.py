from datetime import timedelta

from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import freeze_time, tagged

from odoo.addons.wb_voip.models import wb_voip_anruf

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestHaertung(WbVoipCase):
    """Security hardening: group checks, input checks, chatter rights, constraints."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.user_ohne = Users.create({
            'name': 'Ohne VoIP', 'login': 'wb_voip_ohne_gruppe', 'email': 'ohne@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.user_portal = Users.create({
            'name': 'Portal', 'login': 'wb_voip_portal', 'email': 'portal@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])],
        })
        verwaltung = cls.env.ref('base.group_erp_manager')
        cls.user_erp = Users.create({
            'name': 'Nur Odoo-Verwaltung', 'login': 'wb_voip_nur_erp', 'email': 'erp@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, verwaltung.id,
                                  cls.env.ref('base.group_partner_manager').id])],
        })
        cls.user_voip_admin = Users.create({
            'name': 'VoIP-Verwaltung', 'login': 'wb_voip_verwaltung', 'email': 'vw@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, verwaltung.id,
                                  cls.env.ref('base.group_partner_manager').id,
                                  cls.env.ref('wb_voip.group_wb_voip_manager').id])],
        })
        cls.kontakt = cls.env['res.partner'].create({
            'name': 'Hanna Härtung', 'phone': '+49 170 7654321'})

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()

    def _beginnen(self, user=None, richtung='eingehend', nummer='+49 170 7654321',
                  sip_call_id='h1', res_model=False, res_id=False):
        return self.env['wb.voip.anruf'].with_user(user or self.user_a).wb_voip_beginnen(
            richtung, nummer, sip_call_id, res_model, res_id)

    def _anruf(self, **kw):
        daten = self._beginnen(**kw)
        return self.env['wb.voip.anruf'].with_user(kw.get('user') or self.user_a).browse(daten['id'])

    # --- C1 Gruppenprüfung ---

    def test_ohne_gruppe_kein_zugriff(self):
        for user in (self.user_ohne, self.user_portal):
            with self.assertRaises(AccessError, msg=user.login):
                self._beginnen(user=user)
            with self.assertRaises(AccessError, msg=user.login):
                self.env['res.users'].with_user(user).wb_voip_zugang_lesen()
            self.assertEqual(self.env['res.users'].with_user(user).wb_voip_status(),
                             {'aktiv': False})

    def test_gruppenpruefung_vor_lizenz(self):
        self._lizenz_fehlt()
        with self.assertRaises(AccessError):
            self._beginnen(user=self.user_ohne)
        with self.assertRaises(AccessError):
            self.env['res.users'].with_user(self.user_ohne).wb_voip_zugang_lesen()

    def test_ohne_gruppe_keine_anrufmethoden(self):
        anruf = self._anruf()
        fremd = anruf.with_user(self.user_ohne)
        for methode, args in (('wb_voip_angenommen', ()), ('wb_voip_beendet', (False, False)),
                              ('wb_voip_lebenszeichen', ())):
            with self.assertRaises(AccessError, msg=methode):
                getattr(fremd, methode)(*args)
        with self.assertRaises(AccessError):
            self.kontakt.with_user(self.user_ohne).action_wb_voip_protokolle()

    def test_status_mitglied_aktiv(self):
        status = self.env['res.users'].with_user(self.user_a).wb_voip_status()
        self.assertIs(status['aktiv'], True)

    # --- C2 Chatter nur mit Schreibrecht ---

    def _schreibschutz(self, partner):
        self.env['ir.rule'].create({
            'name': 'Test: nur lesbar', 'model_id': self.env.ref('base.model_res_partner').id,
            'domain_force': "[('id', '!=', %d)]" % partner.id,
            'perm_read': False, 'perm_write': True, 'perm_create': False, 'perm_unlink': False,
        })

    def _fuehren(self, anruf):
        with freeze_time('2026-11-24 10:00:00') as zeit:
            anruf.wb_voip_angenommen()
            zeit.tick(timedelta(seconds=5))
            return anruf.wb_voip_beendet(False, False)

    def test_kein_chatter_am_nur_lesbaren_datensatz(self):
        genannt = self.env['res.partner'].create({'name': 'Nur lesbar'})
        self._schreibschutz(genannt)
        self.assertTrue(genannt.with_user(self.user_a).has_access('read'))
        self.assertFalse(genannt.with_user(self.user_a).has_access('write'))
        anruf = self._anruf(res_model='res.partner', res_id=genannt.id)
        daten = self._fuehren(anruf)
        self.assertTrue(daten['protokoll_id'])
        self.assertFalse(genannt.message_ids.filtered(lambda m: 'Anruf eingehend' in (m.body or '')))

    def test_kein_chatter_am_nur_lesbaren_kontakt(self):
        self._schreibschutz(self.kontakt)
        anruf = self._anruf()
        self.assertEqual(anruf.sudo().partner_id, self.kontakt)
        daten = self._fuehren(anruf)
        self.assertTrue(daten['protokoll_id'])
        self.assertFalse(self.kontakt.message_ids.filtered(lambda m: 'Anruf eingehend' in (m.body or '')))

    def test_chatter_am_kontakt_wenn_datensatz_nur_lesbar(self):
        # Ziel lesbar, aber nicht schreibbar; Kontakt schreibbar: Notiz am Kontakt
        genannt = self.env['wb.voip.protokoll'].sudo().create(
            {'user_id': self.user_a.id, 'partner_id': self.kontakt.id})
        self.env['ir.rule'].create({
            'name': 'Test: Protokoll nur lesbar',
            'model_id': self.env.ref('wb_voip.model_wb_voip_protokoll').id,
            'domain_force': "[('id', '!=', %d)]" % genannt.id,
            'perm_read': False, 'perm_write': True, 'perm_create': False, 'perm_unlink': False,
        })
        self.assertTrue(genannt.with_user(self.user_a).has_access('read'))
        self.assertFalse(genannt.with_user(self.user_a).has_access('write'))
        anruf = self._anruf(res_model='wb.voip.protokoll', res_id=genannt.id)
        self._fuehren(anruf)
        self.assertFalse(genannt.message_ids.filtered(lambda m: 'Anruf eingehend' in (m.body or '')))
        self.assertTrue(self.kontakt.message_ids.filtered(
            lambda m: 'Anruf eingehend' in (m.body or '')))

    # --- C3 Nebenstelle und Anlage nur durch Manager ---

    def test_nebenstelle_selbst_nur_lesbar(self):
        a = self.user_a.with_user(self.user_a)
        werte = a.read(['wb_voip_sip_benutzer', 'wb_voip_anlage_id'])[0]
        self.assertEqual(werte['wb_voip_sip_benutzer'], '100')
        with self.assertRaises(AccessError):
            a.write({'wb_voip_sip_benutzer': '101'})
        with self.assertRaises(AccessError):
            a.write({'wb_voip_anlage_id': False})
        self.assertNotIn('wb_voip_sip_benutzer', self.env['res.users'].SELF_WRITEABLE_FIELDS)

    def test_nebenstelle_nur_voip_administrator(self):
        with self.assertRaises(AccessError):
            self.user_b.with_user(self.user_erp).write({'wb_voip_sip_benutzer': '200'})
        with self.assertRaises(AccessError):
            self.user_b.with_user(self.user_erp).write({'wb_voip_anlage_id': self.anlage.id})
        self.user_b.with_user(self.user_voip_admin).write({
            'wb_voip_anlage_id': self.anlage.id, 'wb_voip_sip_benutzer': '200'})
        self.assertEqual(self.user_b.wb_voip_sip_benutzer, '200')

    def test_nebenstelle_eindeutig_je_anlage(self):
        with self.assertRaises(ValidationError) as fehler:
            self.user_b.write({'wb_voip_anlage_id': self.anlage.id, 'wb_voip_sip_benutzer': '100'})
        self.assertEqual(str(fehler.exception),
                         "Nebenstelle ist bereits einem anderen Benutzer zugeordnet.")
        andere = self.env['wb.voip.anlage'].create({
            'name': 'Zweite', 'ws_url': 'wss://zwei.test/ws', 'sip_domain': 'zwei.test'})
        self.user_b.write({'wb_voip_anlage_id': andere.id, 'wb_voip_sip_benutzer': '100'})

    def test_profil_nebenstelle_schreibgeschuetzt(self):
        view = self.env.ref('base.view_users_form_simple_modif')
        arch = self.env['res.users'].with_user(self.user_a).get_views(
            [(view.id, 'form')])['views']['form']['arch']
        knoten = etree.fromstring(arch).xpath("//field[@name='wb_voip_sip_benutzer']")
        self.assertTrue(knoten)
        self.assertIn(knoten[0].get('readonly'), ('1', 'True'))

    # --- C4 wss:// ---

    def test_ws_url_nur_wss(self):
        for url in ('ws://anlage.test/ws', 'https://anlage.test/ws', 'anlage.test'):
            with self.assertRaises(ValidationError, msg=url) as fehler:
                self.anlage.write({'ws_url': url})
            self.assertIn('wss://anlage.example/ws', str(fehler.exception))
        self.anlage.write({'ws_url': 'wss://neu.test/ws'})

    # --- C5 Eingabeprüfung ---

    def test_beginnen_eingaben(self):
        faelle = [
            {'richtung': 'quer'},
            {'nummer': 1701234567},
            {'nummer': '1' * 65},
            {'nummer': ''},
            {'sip_call_id': 42},
            {'sip_call_id': 'x' * 256},
            {'sip_call_id': ''},
            {'res_model': 42},
            {'res_model': 'm' * 129},
            {'res_model': None},
            {'res_id': '1'},
            {'res_id': 1.5},
            {'res_id': True},
            {'res_id': None},
        ]
        for nr, fall in enumerate(faelle):
            with self.assertRaises(UserError, msg=str(fall)):
                self._beginnen(**dict({'sip_call_id': f'e{nr}'}, **fall))
        # Grenzwerte gehen durch
        self._beginnen(nummer='1' * 64, sip_call_id='y' * 255)

    def test_beendet_eingaben(self):
        anruf = self._anruf()
        for notiz, grund in ((42, False), ('n' * 10001, False), (False, 603),
                             (False, '123456789'), (None, False)):
            with self.assertRaises(UserError, msg=f'{notiz!r:.20} {grund!r}'):
                anruf.wb_voip_beendet(notiz, grund)
        self.assertEqual(anruf.sudo().zustand, 'klingelt')
        anruf.wb_voip_beendet('n' * 10000, '603')
        self.assertEqual(anruf.sudo().zustand, 'abgelehnt')

    def test_ziel_nur_gespeichert_wenn_gueltig(self):
        for nr, (modell, rid) in enumerate((('gibt.es.nicht', 1), ('res.partner', 99999999),
                                            ('res.config.settings', 1), ('res.partner', 0))):
            anruf = self._anruf(sip_call_id=f'z{nr}', res_model=modell, res_id=rid).sudo()
            self.assertFalse(anruf.res_model, modell)
            self.assertFalse(anruf.res_id, modell)
        anruf = self._anruf(sip_call_id='gut', res_model='res.partner', res_id=self.kontakt.id)
        self.assertEqual((anruf.sudo().res_model, anruf.sudo().res_id),
                         ('res.partner', self.kontakt.id))

    # --- C6 Chatter-Text aus Bezeichnungen ---

    def test_chatter_text_bezeichnungen(self):
        anruf = self._anruf(richtung='ausgehend')
        daten = anruf.wb_voip_beendet(False, False)
        protokoll = self.env['wb.voip.protokoll'].browse(daten['protokoll_id'])
        self.assertEqual(protokoll._wb_voip_text(), 'Anruf ausgehend · nicht erreicht · 0:00')
        nachricht = self.kontakt.message_ids[:1]
        self.assertIn('Anruf ausgehend · nicht erreicht · 0:00', nachricht.body)

    # --- C7 SIP-Gründe ---

    def test_sip_gruende_konstanten(self):
        SIP_GRUND_ZUSTAND = getattr(wb_voip_anruf, 'SIP_GRUND_ZUSTAND', {})
        self.assertEqual(SIP_GRUND_ZUSTAND['603'], 'abgelehnt')
        self.assertEqual(SIP_GRUND_ZUSTAND['487'], 'abgelehnt')
        self.assertEqual(SIP_GRUND_ZUSTAND['480'], 'abgelehnt')
        self.assertEqual(SIP_GRUND_ZUSTAND['486'], 'besetzt')
        self.assertEqual(SIP_GRUND_ZUSTAND['600'], 'besetzt')

    def test_487_abgebrochen_ist_abgelehnt(self):
        anruf = self._anruf(richtung='ausgehend')
        daten = anruf.wb_voip_beendet(False, '487')
        self.assertEqual(daten['zustand'], 'abgelehnt')
        protokoll = self.env['wb.voip.protokoll'].browse(daten['protokoll_id'])
        self.assertEqual(protokoll.ergebnis, 'nicht_erreicht')

    # --- C8 Aufräumen ohne Lebenszeichen ---

    def test_aufraeumen_ohne_zuletzt_gesehen(self):
        jetzt = fields.Datetime.now()
        Anruf = self.env['wb.voip.anruf'].sudo()
        alt = Anruf.create({
            'user_id': self.user_a.id, 'richtung': 'eingehend', 'nummer': '1',
            'sip_call_id': 'ohne-alt', 'zustand': 'klingelt',
            'beginn': jetzt - timedelta(hours=4, minutes=1), 'zuletzt_gesehen': False})
        jung = Anruf.create({
            'user_id': self.user_a.id, 'richtung': 'eingehend', 'nummer': '1',
            'sip_call_id': 'ohne-jung', 'zustand': 'laeuft',
            'beginn': jetzt - timedelta(hours=3), 'zuletzt_gesehen': False})
        Anruf._wb_voip_aufraeumen()
        self.assertEqual(alt.zustand, 'fehlgeschlagen')
        self.assertEqual(jung.zustand, 'laeuft')

    # --- C9 Land der Firma des Kontakts ---

    def test_land_aus_firma_des_kontakts(self):
        firma = self.env['res.company'].create({
            'name': 'Firma AT', 'country_id': self.env.ref('base.at').id})
        kontakt = self.env['res.partner'].create({
            'name': 'Österreich', 'phone': '0664 1234567', 'company_id': firma.id})
        self.assertEqual(kontakt.wb_voip_nummern_e164, '+436641234567')

    def test_land_aus_hauptfirma(self):
        hauptfirma = self.env.ref('base.main_company')
        hauptfirma.country_id = self.env.ref('base.ch')
        andere = self.env['res.company'].create({
            'name': 'Firma DE', 'country_id': self.env.ref('base.de').id})
        kontakt = self.env['res.partner'].with_company(andere).create({
            'name': 'Schweiz', 'phone': '044 123 45 67'})
        self.assertEqual(kontakt.wb_voip_nummern_e164, '+41441234567')

    # --- Fix-Runde 1: Duplizieren/Anlegen durch Benutzerverwaltung ohne VoIP-Administrator ---

    def test_benutzerverwaltung_dupliziert_benutzer(self):
        for vorlage in (self.user_a, self.user_b):
            kopie = vorlage.with_user(self.user_erp).copy()
            self.assertFalse(kopie.sudo().wb_voip_sip_benutzer, vorlage.login)
            self.assertFalse(kopie.sudo().wb_voip_anlage_id, vorlage.login)
            self.assertFalse(kopie.sudo().wb_voip_sip_passwort_enc, vorlage.login)

    def test_voip_administrator_dupliziert_eingerichteten_benutzer(self):
        kopie = self.user_a.with_user(self.user_voip_admin).copy()
        self.assertFalse(kopie.sudo().wb_voip_sip_benutzer)

    def test_benutzerverwaltung_legt_benutzer_an(self):
        Users = self.env['res.users'].with_user(self.user_erp).with_context(no_reset_password=True)
        Users.create({'name': 'Neu 1', 'login': 'wb_voip_neu1'})
        Users.create({'name': 'Neu 2', 'login': 'wb_voip_neu2',
                      'wb_voip_sip_benutzer': False, 'wb_voip_anlage_id': False})
        with self.assertRaises(AccessError):
            Users.create({'name': 'Neu 3', 'login': 'wb_voip_neu3', 'wb_voip_sip_benutzer': '300'})
        with self.assertRaises(AccessError):
            Users.create({'name': 'Neu 4', 'login': 'wb_voip_neu4',
                          'wb_voip_anlage_id': self.anlage.id})

    def test_benutzerverwaltung_unveraenderte_werte(self):
        a = self.user_a.with_user(self.user_erp)
        a.write({'wb_voip_sip_benutzer': '100', 'wb_voip_anlage_id': self.anlage.id, 'name': 'A2'})
        self.user_b.with_user(self.user_erp).write({'wb_voip_sip_benutzer': False})
        with self.assertRaises(AccessError):
            a.write({'wb_voip_sip_benutzer': False})

    def test_nebenstelle_eindeutig_in_der_datenbank(self):
        self.env.cr.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'res_users' "
            "AND indexname LIKE %s", ['%wb_voip_nebenstelle_eindeutig'])
        definition = self.env.cr.fetchone()
        self.assertTrue(definition)
        self.assertIn('UNIQUE', definition[0])

    def test_ws_url_ohne_leerzeichen_gespeichert(self):
        anlage = self.env['wb.voip.anlage'].create({
            'name': 'Leer', 'ws_url': '  wss://leer.test/ws ', 'sip_domain': 'leer.test'})
        self.assertEqual(anlage.ws_url, 'wss://leer.test/ws')
        anlage.write({'ws_url': ' wss://neu.test/ws\n'})
        self.assertEqual(anlage.ws_url, 'wss://neu.test/ws')

    def test_benutzerformular_nebenstelle_nur_fuer_voip_administrator(self):
        view = self.env.ref('base.view_users_form')
        for user, readonly in ((self.user_erp, True), (self.user_voip_admin, False)):
            arch = self.env['res.users'].with_user(user).get_views(
                [(view.id, 'form')])['views']['form']['arch']
            for name in ('wb_voip_sip_benutzer', 'wb_voip_anlage_id'):
                knoten = etree.fromstring(arch).xpath(f"//field[@name='{name}']")
                self.assertEqual(len(knoten), 1, (user.login, name))
                self.assertEqual(knoten[0].get('readonly') in ('1', 'True'), readonly,
                                 (user.login, name))
