from unittest.mock import patch

import requests
from cryptography.fernet import Fernet

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import PEERS, TOKEN, URL, AnsitelCase, antwort

TOKEN_A = 'persoenlicher-token-a-456'
HINWEIS_AB = 'AB braucht persönlichen Ansitel-Token oder Freigabe – bitte Administrator fragen.'
HINWEIS_HANDY = 'Bitte unter Mein Profil › Telefonie Ihr Handy eintragen.'
NICHT_ERREICHBAR = 'Ansitel nicht erreichbar.'
LOGGER_USERS = 'odoo.addons.wb_voip_ansitel.models.res_users'
LOGGER_API = 'odoo.addons.wb_voip_ansitel.models.ansitel_api'


class FakeAnlage:
    """Stateful fake of the Ansitel REST API (peers 7 = '100', 8 = '101')."""

    def __init__(self):
        self.fw = {7: None, 8: None}
        self.dnd = {7: '0', 8: '0'}
        # Anrufbeantworter je Token (Ansitel-Benutzer)
        self.vm = {}
        self.info = {'peer': {'1': {'peerName': 'Tel100', 'peerId': 7}}}
        self.fehler = None
        self.aufrufe = []

    def vm_fuer(self, token):
        return self.vm.setdefault(token, {
            'id': '5', 'user': '1', 'active': '0', 'option_immediately_check': '1',
            'option_ringtime_check': '0', 'option_ringtime_seconds': '60'})

    def __call__(self, _sitzung, url, json=None, **_kwargs):
        if self.fehler:
            raise self.fehler
        aktion = json['action']
        self.aufrufe.append(dict(json))
        peer = json.get('pk_peerId')
        if aktion == 'getpeer':
            return antwort(PEERS)
        if aktion == 'getcallfordwardingonpeer':
            return antwort({'status': 200, 'data': {'destination': self.fw[peer]}})
        if aktion == 'setcallfordwardingonpeer':
            self.fw[peer] = None if json['destination'] == '0' else json['destination']
        elif aktion == 'getdndonpeer':
            return antwort({'status': 200, 'data': {'dnd': self.dnd[peer]}})
        elif aktion == 'setdndonpeer':
            self.dnd[peer] = json['dnd']
        elif aktion == 'getinfooncuserbytoken':
            return antwort({'status': 200, 'data': self.info if json['token'] == TOKEN else {'peer': {}}})
        elif aktion == 'getvoicemailoncuserbytoken':
            return antwort({'status': 200, 'data': dict(self.vm_fuer(json['token']))})
        elif aktion == 'setvoicemailoncuserbytoken':
            self.vm_fuer(json['token']).update(
                {k: v for k, v in json.items() if k not in ('token', 'action')})
        return antwort({'status': 200, 'data': None})

    def anzahl(self, aktion):
        return sum(1 for a in self.aufrufe if a['action'] == aktion)

    def letzte(self, aktion):
        return [a for a in self.aufrufe if a['action'] == aktion][-1]


class TastenCase(AnsitelCase):
    """Users A (extension 100) and B (101) on the Ansitel PBX, own test keys."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a.write({'wb_voip_ansitel_nebenstelle': '100'})
        cls.user_b.write({'wb_voip_anlage_id': cls.anlage.id, 'wb_voip_sip_benutzer': '101',
                          'wb_voip_ansitel_nebenstelle': '101'})
        Taste = cls.env['wb.voip.taste']
        Taste.search([]).write({'active': False})
        cls.taste_handy = Taste.create({
            'name': '→ Handy', 'art': 'weiterleitung', 'ziel': '+49 170 1234567', 'sequence': 1})
        cls.taste_mein = Taste.create({
            'name': '→ Mein Handy', 'art': 'weiterleitung', 'ziel_art': 'handy', 'sequence': 2})
        cls.taste_dnd = Taste.create({'name': 'Nicht stören', 'art': 'anrufschutz', 'sequence': 3})
        cls.taste_ab = Taste.create({'name': 'AB', 'art': 'anrufbeantworter', 'sequence': 4})

    def setUp(self):
        super().setUp()
        self.fake = FakeAnlage()
        self.post = self._post(side_effect=self.fake)
        self._lizenz_gueltig()
        self.bus = []
        Users = type(self.env['res.users'])
        original = Users._bus_send

        def mitschreiben(benutzer, kanal, nachricht, **kwargs):
            if str(kanal).startswith('wb_voip/tasten'):
                self.bus.append((benutzer.id, kanal, nachricht))
            return original(benutzer, kanal, nachricht, **kwargs)
        patcher = patch.object(Users, '_bus_send', mitschreiben)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _verwalter(self):
        """User administrator and VoIP manager without system rights."""
        verwalter = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Benutzerverwaltung', 'login': 'wb_voip_ansitel_verwalter',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('base.group_erp_manager').id,
                                  self.env.ref('wb_voip.group_wb_voip_manager').id])]})
        self.assertFalse(verwalter._is_system())
        return verwalter

    def als(self, benutzer):
        return self.env['res.users'].with_user(benutzer)

    @staticmethod
    def aktiv(zustand):
        return {t['name']: t['aktiv'] for t in zustand['tasten']}

    def zustand(self, benutzer=None):
        return self.als(benutzer or self.user_a).wb_voip_tasten_zustand()

    def schalten(self, taste, an=None, benutzer=None):
        return self.als(benutzer or self.user_a).wb_voip_taste_schalten(taste.id, an)


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestTastenFelder(TastenCase):
    """Extension, personal token, central-AB flag and own mobile: rights and checks."""

    def test_benutzer_liest_und_schreibt_keine_ansitel_felder(self):
        for feld in ('wb_voip_ansitel_nebenstelle', 'wb_voip_ansitel_token',
                     'wb_voip_ansitel_token_enc', 'wb_voip_ansitel_ab_zentral'):
            for ziel in (self.user_a, self.user_b):
                with self.subTest(feld=feld, ziel=ziel.login), self.assertRaises(AccessError):
                    ziel.with_user(self.user_a).read([feld])
            with self.subTest(feld=feld, schreiben=True), self.assertRaises(AccessError):
                self.user_a.with_user(self.user_a).write({feld: '999' if 'ab_zentral' not in feld else True})
        self.assertEqual(self.user_a.wb_voip_ansitel_nebenstelle, '100')

    def test_manager_liest_nebenstelle_nicht_token(self):
        self.assertEqual(self.user_a.with_user(self.user_manager).wb_voip_ansitel_nebenstelle, '100')
        for feld in ('wb_voip_ansitel_token', 'wb_voip_ansitel_token_enc', 'wb_voip_ansitel_ab_zentral'):
            with self.subTest(feld=feld), self.assertRaises(AccessError):
                self.user_a.with_user(self.user_manager).read([feld])

    def test_nebenstelle_nur_systemverwaltung(self):
        verwalter = self._verwalter()
        for benutzer in (self.user_manager, verwalter):
            for werte in ({'wb_voip_ansitel_nebenstelle': '101'}, {'wb_voip_ansitel_nebenstelle': False},
                          {'wb_voip_ansitel_token': 'x'}, {'wb_voip_ansitel_ab_zentral': True}):
                with self.subTest(benutzer=benutzer.login, werte=werte), self.assertRaises(AccessError), \
                        self.env.cr.savepoint():
                    self.user_a.with_user(benutzer).write(werte)
        with self.assertRaises(AccessError) as fehler:
            self.user_a.with_user(verwalter).write({'wb_voip_ansitel_nebenstelle': '102'})
        self.assertIn('Systemverwaltung', str(fehler.exception))
        with self.assertRaises(AccessError):
            self.env['res.users'].with_user(verwalter).with_context(no_reset_password=True).create({
                'name': 'Neu', 'login': 'wb_voip_ansitel_neu', 'wb_voip_anlage_id': self.anlage.id,
                'wb_voip_ansitel_nebenstelle': '102'})
        # Unveränderter Wert und andere Felder bleiben erlaubt
        self.user_a.with_user(verwalter).write({'wb_voip_ansitel_nebenstelle': '100'})
        self.assertEqual(self.user_a.wb_voip_ansitel_nebenstelle, '100')
        with self.assertLogs('odoo.addons.wb_voip_ansitel.models.res_users', 'INFO') as logs:
            self.user_a.write({'wb_voip_ansitel_nebenstelle': '102'})
        self.assertEqual(self.user_a.wb_voip_ansitel_nebenstelle, '102')
        self.assertNotIn('102', ' '.join(logs.output))

    def test_token_nur_als_chiffrat(self):
        self.user_a.write({'wb_voip_ansitel_token': TOKEN_A})
        enc = self.user_a.sudo().wb_voip_ansitel_token_enc
        self.assertTrue(enc)
        self.assertNotIn(TOKEN_A, enc)
        self.assertEqual(Fernet(self.voip_schluessel.encode()).decrypt(enc.encode()).decode(), TOKEN_A)
        self.assertFalse(self.user_a.wb_voip_ansitel_token)
        self.assertTrue(self.user_a.with_user(self.user_manager).wb_voip_ansitel_token_gesetzt)
        self.user_a.write({'wb_voip_ansitel_token': False})
        self.assertFalse(self.user_a.sudo().wb_voip_ansitel_token_enc)

    def test_nebenstelle_je_anlage_eindeutig(self):
        self.assertEqual(self.user_b.wb_voip_ansitel_nebenstelle, '101')
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.user_b.write({'wb_voip_ansitel_nebenstelle': ' 100 '})
        andere = self.env['wb.voip.anlage'].create({
            'name': 'Zweite', 'ws_url': 'wss://zweite.test/ws', 'sip_domain': 'zweite.test'})
        self.user_b.write({'wb_voip_anlage_id': andere.id, 'wb_voip_ansitel_nebenstelle': ' 100 '})
        self.assertEqual(self.user_b.wb_voip_ansitel_nebenstelle, '100')
        self.user_b.write({'wb_voip_ansitel_nebenstelle': '  '})
        self.assertFalse(self.user_b.wb_voip_ansitel_nebenstelle)

    def test_unique_index_in_der_datenbank(self):
        self.env.cr.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'res_users'"
            " AND indexdef ILIKE '%%wb_voip_ansitel_nebenstelle%%' AND indexdef ILIKE '%%UNIQUE%%'")
        self.assertTrue(self.env.cr.fetchall())

    def test_handy_nur_selbst(self):
        self.user_a.with_user(self.user_a).write({'wb_voip_handy': ' +49 171 7654321 '})
        self.assertEqual(self.user_a.with_user(self.user_a).read(['wb_voip_handy'])[0]['wb_voip_handy'],
                         '+49 171 7654321')
        with self.assertRaises(AccessError):
            self.user_a.with_user(self.user_b).read(['wb_voip_handy'])
        with self.assertRaises(AccessError):
            self.user_a.with_user(self.user_b).write({'wb_voip_handy': '+49 171 1111111'})
        with self.assertRaises(AccessError):
            self.user_a.with_user(self.user_manager).read(['wb_voip_handy'])
        self.assertEqual(self.user_a.sudo().wb_voip_handy, '+49 171 7654321')

    def test_handy_wird_geprueft(self):
        for falsch in ('abc', '12', '+49 abc 123', '1; DROP', '++49 170 1'):
            with self.subTest(falsch=falsch), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.user_a.with_user(self.user_a).write({'wb_voip_handy': falsch})
        for richtig in ('+49 170 1234567', '0170 1234567', '00491701234567', '0170/123-4567'):
            with self.subTest(richtig=richtig):
                self.user_a.with_user(self.user_a).write({'wb_voip_handy': richtig})
        self.user_a.with_user(self.user_a).write({'wb_voip_handy': ''})
        self.assertFalse(self.user_a.sudo().wb_voip_handy)

    def test_tokens_verwerfen_trifft_benutzer_der_anlage(self):
        andere = self.env['wb.voip.anlage'].create({
            'name': 'Zweite', 'ws_url': 'wss://zweite.test/ws', 'sip_domain': 'zweite.test'})
        self.user_a.write({'wb_voip_ansitel_token': TOKEN_A})
        self.user_b.write({'wb_voip_anlage_id': andere.id, 'wb_voip_ansitel_token': 'token-b'})
        self.anlage._ansitel_tokens_verwerfen()
        self.assertFalse(self.anlage.sudo().ansitel_token_enc)
        self.assertFalse(self.user_a.sudo().wb_voip_ansitel_token_enc)
        self.assertTrue(self.user_b.sudo().wb_voip_ansitel_token_enc)

    def test_anlagenwechsel_ohne_systemrecht_verwirft_token(self):
        verwalter = self._verwalter()
        andere = self.env['wb.voip.anlage'].create({
            'name': 'Zweite', 'ws_url': 'wss://zweite.test/ws', 'sip_domain': 'zweite.test'})
        self.user_a.write({'wb_voip_ansitel_token': TOKEN_A})
        self.user_a.with_user(verwalter).write({'wb_voip_anlage_id': andere.id})
        self.assertFalse(self.user_a.sudo().wb_voip_ansitel_token_enc)
        # Systemverwaltung behält den Token
        self.user_b.write({'wb_voip_ansitel_token': 'token-b'})
        self.user_b.write({'wb_voip_anlage_id': andere.id})
        self.assertTrue(self.user_b.sudo().wb_voip_ansitel_token_enc)


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestTasteModell(TastenCase):
    """Key model: checks, target per user, dialling form, standard keys."""

    def test_standardtasten(self):
        ab = self.env.ref('wb_voip_ansitel.taste_ab')
        handy = self.env.ref('wb_voip_ansitel.taste_handy')
        dnd = self.env.ref('wb_voip_ansitel.taste_dnd')
        self.assertEqual((ab.art, handy.art, handy.ziel_art, dnd.art),
                         ('anrufbeantworter', 'weiterleitung', 'handy', 'anrufschutz'))
        daten = self.env['ir.model.data'].search([
            ('module', '=', 'wb_voip_ansitel'), ('model', '=', 'wb.voip.taste')])
        self.assertEqual(len(daten), 3)
        self.assertTrue(all(daten.mapped('noupdate')))

    def test_ziel_pruefung(self):
        with self.assertRaises(ValidationError):
            self.taste_handy.write({'ziel': 'abc'})
        with self.assertRaises(ValidationError):
            self.taste_ab.write({'ab_sekunden': 0})
        with self.assertRaises(ValidationError):
            self.taste_ab.write({'ab_sekunden': 601})

    def test_ziel_fuer_und_senden(self):
        self.user_a.sudo().wb_voip_handy = '+49 171 7654321'
        self.assertEqual(self.taste_mein._ziel_fuer(self.user_a), '+49 171 7654321')
        self.assertEqual(self.taste_handy._ziel_fuer(self.user_a), '+49 170 1234567')
        Taste = self.env['wb.voip.taste']
        self.assertEqual(Taste._ziel_senden('+49 (170) 123-45 67'), '00491701234567')
        self.assertEqual(Taste._ziel_senden('0170 1234567'), '01701234567')
        self.assertTrue(Taste._ziel_gleich('0049 170 1234567', '+491701234567', 'DE'))
        self.assertTrue(Taste._ziel_gleich('0170 1234567', '+491701234567', 'DE'))
        self.assertFalse(Taste._ziel_gleich('0170 1234568', '+491701234567', 'DE'))
        self.assertFalse(Taste._ziel_gleich('', '', 'DE'))

    def test_benutzer_darf_tasten_nicht_aendern(self):
        with self.assertRaises(AccessError):
            self.taste_handy.with_user(self.user_a).write({'ziel': '+49 170 9999999'})
        with self.assertRaises(AccessError):
            self.env['wb.voip.taste'].with_user(self.user_a).create({'name': 'X', 'art': 'anrufschutz'})
        self.taste_handy.with_user(self.user_manager).write({'name': 'Handy'})

    def test_festes_ziel_nur_systemverwaltung(self):
        manager = self.taste_handy.with_user(self.user_manager)
        with self.assertRaises(AccessError) as fehler:
            manager.write({'ziel': '+49 170 9999999'})
        self.assertIn('Systemverwaltung', str(fehler.exception))
        with self.assertRaises(AccessError):
            self.env['wb.voip.taste'].with_user(self.user_manager).create({
                'name': 'X', 'art': 'weiterleitung', 'ziel': '+49 170 9999999'})
        self.env['wb.voip.taste'].with_user(self.user_manager).create({'name': 'X', 'art': 'anrufschutz'})
        self.taste_handy.write({'ziel': '+49 170 9999999'})
        self.assertEqual(self.taste_handy.ziel, '+49 170 9999999')


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestWeiterleitungsziele(TastenCase):
    """Toll fraud: premium, service, emergency, short and foreign targets are refused."""

    GESPERRT = (
        # Mehrwert- und Servicenummern
        '0900 1234567', '+49 900 1234567', '0049 900 1234567', '0137 1234567', '0138 1234567',
        '0180 5123456', '01805 123456', '0190 1234567', '0191 1234567', '0192 1234567', '0193 1234567',
        '0194 1234567', '0700 12345678',
        # Auskunft, Notruf, Kurzwahl
        '11833', '118 80', '110', '112', '115', '116116', '116 117', '12345',
    )

    def test_gesperrte_ziele(self):
        Taste = self.env['wb.voip.taste']
        for ziel in self.GESPERRT:
            with self.subTest(ziel=ziel):
                self.assertTrue(Taste._ziel_pruefen(ziel, 'DE'))
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    self.user_a.with_user(self.user_a).write({'wb_voip_handy': ziel})
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    self.taste_handy.write({'ziel': ziel})

    def test_erlaubte_ziele(self):
        Taste = self.env['wb.voip.taste']
        for ziel in ('+49 170 1234567', '0170 1234567', '00491701234567', '030 12345678', '+49 30 12345678'):
            with self.subTest(ziel=ziel):
                self.assertFalse(Taste._ziel_pruefen(ziel, 'DE'))

    def test_meldungen(self):
        Taste = self.env['wb.voip.taste']
        self.assertIn('Sonder', Taste._ziel_pruefen('0900 1234567', 'DE'))
        self.assertIn('Notruf', Taste._ziel_pruefen('112', 'DE'))
        self.assertIn('Ausland', Taste._ziel_pruefen('+43 660 1234567', 'DE'))

    def test_ausland_nur_mit_freigabe(self):
        Taste = self.env['wb.voip.taste']
        ziel = '+43 660 1234567'
        self.assertTrue(Taste._ziel_pruefen(ziel, 'DE'))
        self.assertTrue(Taste._ziel_pruefen('0043 660 1234567', 'DE'))
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.user_a.with_user(self.user_a).write({'wb_voip_handy': ziel})
        self.env['ir.config_parameter'].sudo().set_param('wb_voip_ansitel.ausland_erlaubt', '1')
        self.assertFalse(Taste._ziel_pruefen(ziel, 'DE'))
        self.user_a.with_user(self.user_a).write({'wb_voip_handy': ziel})
        self.taste_handy.write({'ziel': ziel})
        zustand = self.schalten(self.taste_handy)
        self.assertEqual(self.fake.fw[7], '00436601234567')
        self.assertTrue(self.aktiv(zustand)['→ Handy'])

    def test_altes_ziel_wird_beim_schalten_geprueft(self):
        # Vor der Regel gespeichert (am ORM vorbei): darf nicht gewählt werden
        self.env.cr.execute("UPDATE wb_voip_taste SET ziel = '0900 1234567' WHERE id = %s", [self.taste_handy.id])
        self.taste_handy.invalidate_recordset(['ziel'])
        zustand = self.schalten(self.taste_handy)
        self.assertIn('Sonder', zustand['fehler'])
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)
        self.assertIsNone(self.aktiv(zustand)['→ Handy'])

    def test_altes_handy_wird_beim_schalten_geprueft(self):
        self.env.cr.execute("UPDATE res_users SET wb_voip_handy = '0137 1234567' WHERE id = %s", [self.user_a.id])
        self.user_a.invalidate_recordset(['wb_voip_handy'])
        zustand = self.schalten(self.taste_mein)
        self.assertIn('Sonder', zustand['fehler'])
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)
        self.assertIsNone(self.aktiv(zustand)['→ Mein Handy'])


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestTastenZustand(TastenCase):
    """Reading the state: grey on errors, only own extension, no secrets."""

    def test_grundzustand(self):
        zustand = self.zustand()
        self.assertTrue(zustand['verfuegbar'])
        self.assertEqual(self.aktiv(zustand), {
            '→ Handy': False, '→ Mein Handy': None, 'Nicht stören': False, 'AB': False})
        # „Mein Handy“ ohne Nummer grau mit Hinweis; AB-Token passt (genau eine Nebenstelle)
        self.assertEqual(zustand['fehler'], HINWEIS_HANDY)
        self.assertFalse(zustand['umgeleitet'])
        self.assertEqual(set(zustand), {'verfuegbar', 'fehler', 'tasten', 'fremde_weiterleitung', 'umgeleitet'})
        self.assertEqual(set(zustand['tasten'][0]), {'id', 'name', 'icon', 'art', 'aktiv'})

    def test_nur_voip_benutzer(self):
        fremd = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Ohne VoIP', 'login': 'wb_voip_ansitel_ohne'})
        for methode, args in (('wb_voip_tasten_zustand', ()), ('wb_voip_taste_schalten', (self.taste_dnd.id,)),
                              ('wb_voip_weiterleitung_aus', ())):
            with self.subTest(methode=methode), self.assertRaises(AccessError):
                getattr(self.als(fremd), methode)(*args)
        self.assertFalse(self.fake.aufrufe)

    def test_ohne_lizenz_grau_ohne_anlage(self):
        self._lizenz_fehlt()
        zustand = self.zustand()
        self.assertTrue(zustand['verfuegbar'])
        self.assertEqual(set(self.aktiv(zustand).values()), {None})
        self.assertEqual(zustand['fehler'], 'Lizenz fehlt oder abgelaufen')
        self.assertFalse(self.fake.aufrufe)

    def test_ohne_nebenstelle_keine_tasten(self):
        self.user_a.write({'wb_voip_ansitel_nebenstelle': False})
        zustand = self.zustand()
        self.assertFalse(zustand['verfuegbar'])
        self.assertEqual(zustand['tasten'], [])
        self.assertFalse(self.fake.aufrufe)

    def test_schnittstelle_aus_keine_tasten(self):
        self.anlage.write({'ansitel_aktiv': False})
        self.assertFalse(self.zustand()['verfuegbar'])
        self.assertFalse(self.fake.aufrufe)

    def test_nebenstelle_unbekannt(self):
        self.user_a.write({'wb_voip_ansitel_nebenstelle': '555'})
        self.assertFalse(self.zustand()['verfuegbar'])

    def test_weiterleitung_erkannt_und_fremde(self):
        self.fake.fw[7] = '00491701234567'
        zustand = self.zustand()
        self.assertTrue(self.aktiv(zustand)['→ Handy'])
        self.assertFalse(zustand['fremde_weiterleitung'])
        self.assertTrue(zustand['umgeleitet'])
        self.fake.fw[7] = '0171999888'
        zustand = self.zustand()
        self.assertFalse(self.aktiv(zustand)['→ Handy'])
        self.assertEqual(zustand['fremde_weiterleitung'], '0171999888')
        self.assertTrue(zustand['umgeleitet'])

    def test_nur_eigene_nebenstelle(self):
        self.fake.dnd[8] = '1'
        self.assertFalse(self.aktiv(self.zustand())['Nicht stören'])
        peers_a = {a['pk_peerId'] for a in self.fake.aufrufe if 'pk_peerId' in a}
        self.fake.aufrufe.clear()
        self.assertTrue(self.aktiv(self.zustand(self.user_b))['Nicht stören'])
        peers_b = {a['pk_peerId'] for a in self.fake.aufrufe if 'pk_peerId' in a}
        self.assertEqual((peers_a, peers_b), ({7}, {8}))

    def test_nicht_erreichbar_grau_ein_aufruf(self):
        self.fake.fehler = requests.ConnectTimeout('https://pbx.example.invalid?token=' + TOKEN)
        with mute_logger('odoo.addons.wb_voip_ansitel.models.ansitel_api'):
            zustand = self.zustand()
        self.assertTrue(zustand['verfuegbar'])
        self.assertEqual(zustand['fehler'], NICHT_ERREICHBAR)
        self.assertEqual(set(self.aktiv(zustand).values()), {None})
        self.assertNotIn(TOKEN, repr(zustand))
        self.assertEqual(self.post.call_count, 1)  # nach „nicht erreichbar“ kein weiterer Versuch

    def test_unverstaendliche_antwort_grau(self):
        self._post(antwort(json_fehler=True), antwort(json_fehler=True), antwort(json_fehler=True),
                   antwort(json_fehler=True), antwort(json_fehler=True))
        with mute_logger('odoo.addons.wb_voip_ansitel.models.ansitel_api'):
            zustand = self.zustand()
        self.assertTrue(zustand['verfuegbar'])
        self.assertEqual(zustand['fehler'], 'Ansitel hat unverständlich geantwortet.')
        self.assertEqual(set(self.aktiv(zustand).values()), {None})

    def test_programmierfehler_nicht_verschluckt(self):
        api = self.env['wb.voip.ansitel']
        with patch.object(type(api), '_aufruf', side_effect=AttributeError('fehler im code')):
            with self.assertRaises(AttributeError):
                api._dnd_lesen(self.anlage, 7)
        # Falsche Antwortform bleibt ein Ansitel-Fehler, im Log mit Typ
        self._post(antwort({'status': 200, 'data': [1]}))
        with self.assertLogs(LOGGER_API, 'WARNING') as logs, self.assertRaises(UserError) as fehler:
            api._dnd_lesen(self.anlage, 7)
        self.assertEqual(str(fehler.exception), 'Ansitel hat unverständlich geantwortet.')
        self.assertIn('_dnd_lesen', ' '.join(logs.output))
        self._post(antwort(PEERS), antwort({'status': 200, 'data': {'peer': {'1': 'kein dict'}}}))
        with mute_logger(LOGGER_API), self.assertRaises(UserError):
            api._zentraler_token_passt(self.anlage, '100', False)

    def test_unerwarteter_fehler_grau(self):
        with patch.object(type(self.env['res.users']), '_wb_voip_tasten_lesen', side_effect=RuntimeError('x')), \
                mute_logger(LOGGER_USERS):
            zustand = self.zustand()
        self.assertTrue(zustand['verfuegbar'])
        self.assertEqual(zustand['fehler'], 'Schnelltasten sind gerade nicht verfügbar.')

    def test_bus_nur_zustand(self):
        self.user_a.write({'wb_voip_ansitel_token': TOKEN_A})
        zustand = self.zustand()
        self.assertEqual(self.bus, [(self.user_a.id, 'wb_voip/tasten', zustand)])
        text = repr(self.bus)
        for geheim in (TOKEN, TOKEN_A, URL, 'pbx.example.invalid'):
            self.assertNotIn(geheim, text)

    def test_ab_nach_rufzeit_zaehlt_nicht_als_umgeleitet(self):
        self.fake.vm_fuer(TOKEN).update({'active': '1', 'option_immediately_check': '0',
                                         'option_ringtime_check': '1', 'option_ringtime_seconds': '20'})
        zustand = self.zustand()
        self.assertFalse(self.aktiv(zustand)['AB'])  # Taste „sofort“ passt nicht
        self.assertFalse(zustand['umgeleitet'])
        self.fake.vm_fuer(TOKEN).update({'option_immediately_check': '1', 'option_ringtime_check': '0'})
        zustand = self.zustand()
        self.assertTrue(self.aktiv(zustand)['AB'])
        self.assertTrue(zustand['umgeleitet'])


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestAbToken(TastenCase):
    """Review Focus 3: token order for the answering machine."""

    def test_persoenlicher_token_zuerst(self):
        self.user_a.write({'wb_voip_ansitel_token': TOKEN_A})
        self.fake.info = {'peer': {}}  # zentraler Token passt nicht – egal
        zustand = self.zustand()
        self.assertFalse(self.aktiv(zustand)['AB'])
        self.assertEqual(self.fake.letzte('getvoicemailoncuserbytoken')['token'], TOKEN_A)
        self.assertEqual(self.fake.anzahl('getinfooncuserbytoken'), 0)
        self.schalten(self.taste_ab)
        self.assertEqual(self.fake.vm_fuer(TOKEN_A)['active'], '1')
        self.assertEqual(self.fake.vm_fuer(TOKEN)['active'], '0')

    def test_zentral_mit_freigabe(self):
        self.fake.info = {'peer': {'1': {'peerName': 'Tel100', 'peerId': 7},
                                   '2': {'peerName': 'Tel101', 'peerId': 8}}}
        self.user_a.write({'wb_voip_ansitel_ab_zentral': True})
        zustand = self.zustand()
        self.assertFalse(self.aktiv(zustand)['AB'])
        self.assertNotEqual(zustand['fehler'], HINWEIS_AB)
        self.assertEqual(self.fake.letzte('getvoicemailoncuserbytoken')['token'], TOKEN)

    def test_freigabe_aber_fremde_nebenstelle(self):
        self.fake.info = {'peer': {'2': {'peerName': 'Tel101', 'peerId': 8}}}
        self.user_a.write({'wb_voip_ansitel_ab_zentral': True})
        zustand = self.zustand()
        self.assertIsNone(self.aktiv(zustand)['AB'])
        self.assertEqual(self.fake.anzahl('getvoicemailoncuserbytoken'), 0)

    def test_zentral_genau_eine_nebenstelle(self):
        zustand = self.zustand()
        self.assertFalse(self.aktiv(zustand)['AB'])
        self.assertEqual(self.fake.letzte('getvoicemailoncuserbytoken')['token'], TOKEN)

    def test_zentral_mehrere_nebenstellen_ohne_freigabe_grau(self):
        self.user_a.sudo().wb_voip_handy = '+49 171 7654321'  # sonst steht der Handy-Hinweis vorn
        self.fake.info = {'peer': {'1': {'peerName': 'Tel100', 'peerId': 7},
                                   '2': {'peerName': 'Tel101', 'peerId': 8}}}
        zustand = self.zustand()
        self.assertIsNone(self.aktiv(zustand)['AB'])
        self.assertEqual(zustand['fehler'], HINWEIS_AB)
        self.assertEqual(self.fake.anzahl('getvoicemailoncuserbytoken'), 0)
        zustand = self.schalten(self.taste_ab)
        self.assertEqual(zustand['fehler'], HINWEIS_AB)
        self.assertEqual(self.fake.anzahl('setvoicemailoncuserbytoken'), 0)

    def test_genau_eine_aber_andere(self):
        self.fake.info = {'peer': {'2': {'peerName': 'Tel101', 'peerId': 8}}}
        self.assertIsNone(self.aktiv(self.zustand())['AB'])

    def test_pruefung_zwischengespeichert(self):
        self.zustand()
        self.zustand()
        self.assertEqual(self.fake.anzahl('getinfooncuserbytoken'), 1)

    def test_pruefung_je_benutzer_zwischengespeichert(self):
        self.zustand()
        self.user_a.write({'wb_voip_ansitel_nebenstelle': False})
        self.env.flush_all()  # sonst sieht die Prüfung die alte Nebenstelle von A
        self.user_b.write({'wb_voip_ansitel_nebenstelle': '100'})
        self.fake.info = {'peer': {'2': {'peerName': 'Tel101', 'peerId': 8}}}
        self.assertIsNone(self.aktiv(self.zustand(self.user_b))['AB'])
        self.assertEqual(self.fake.anzahl('getinfooncuserbytoken'), 2)

    def test_ab_setzen_nur_bekannte_felder(self):
        self.schalten(self.taste_ab)
        gesetzt = self.fake.letzte('setvoicemailoncuserbytoken')
        self.assertEqual(sorted(k for k in gesetzt if k not in ('action', 'token')), [
            'active', 'option_immediately_check', 'option_ringtime_check', 'option_ringtime_seconds'])
        self.assertEqual((gesetzt['active'], gesetzt['option_immediately_check'], gesetzt['option_ringtime_check']),
                         ('1', '1', '0'))

    def test_ab_aus_auch_in_anderem_modus(self):
        self.fake.vm_fuer(TOKEN).update({'active': '1', 'option_immediately_check': '0',
                                         'option_ringtime_check': '1', 'option_ringtime_seconds': '20'})
        zustand = self.schalten(self.taste_ab, an=False)
        self.assertEqual(self.fake.vm_fuer(TOKEN)['active'], '0')
        self.assertFalse(self.aktiv(zustand)['AB'])
        # Umschalten (None) bei anderem Modus: Taste war „aus“ → Modus dieser Taste einschalten
        self.fake.vm_fuer(TOKEN).update({'active': '1'})
        self.schalten(self.taste_ab)
        vm = self.fake.vm_fuer(TOKEN)
        self.assertEqual((vm['active'], vm['option_immediately_check']), ('1', '1'))

    def test_ab_nach_rufzeit(self):
        self.taste_ab.write({'ab_modus': 'nach_rufzeit', 'ab_sekunden': 25})
        zustand = self.schalten(self.taste_ab)
        vm = self.fake.vm_fuer(TOKEN)
        self.assertEqual((vm['active'], vm['option_ringtime_check'], vm['option_ringtime_seconds']),
                         ('1', '1', '25'))
        self.assertTrue(self.aktiv(zustand)['AB'])
        self.assertFalse(zustand['umgeleitet'])


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestTastenSchalten(TastenCase):
    """Switching: licence, wish state (Review Focus 5), mutual exclusion."""

    def test_ohne_lizenz_kein_schalten(self):
        self._lizenz_fehlt()
        with self.assertRaises(UserError):
            self.schalten(self.taste_dnd)
        with self.assertRaises(UserError):
            self.als(self.user_a).wb_voip_weiterleitung_aus()
        self.assertFalse(self.fake.aufrufe)

    def test_umschalten(self):
        zustand = self.schalten(self.taste_dnd)
        self.assertEqual(self.fake.dnd[7], '1')
        self.assertTrue(self.aktiv(zustand)['Nicht stören'])
        self.assertTrue(zustand['umgeleitet'])
        self.assertEqual(self.bus[-1], (self.user_a.id, 'wb_voip/tasten', zustand))
        zustand = self.schalten(self.taste_dnd)
        self.assertEqual(self.fake.dnd[7], '0')
        self.assertFalse(self.aktiv(zustand)['Nicht stören'])

    def test_wunschzustand_zwei_tabs(self):
        # Beide Tabs sahen „aus“ und wollen „an“: nur ein Schaltaufruf, Taste bleibt an
        self.schalten(self.taste_dnd, an=True)
        zustand = self.schalten(self.taste_dnd, an=True)
        self.assertEqual(self.fake.dnd[7], '1')
        self.assertEqual(self.fake.anzahl('setdndonpeer'), 1)
        self.assertTrue(self.aktiv(zustand)['Nicht stören'])
        self.schalten(self.taste_dnd, an=False)
        self.schalten(self.taste_dnd, an=False)
        self.assertEqual(self.fake.dnd[7], '0')
        self.assertEqual(self.fake.anzahl('setdndonpeer'), 2)

    def test_wunschzustand_weiterleitung_und_ab(self):
        self.schalten(self.taste_handy, an=True)
        self.schalten(self.taste_handy, an=True)
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 1)
        self.schalten(self.taste_ab, an=True)
        self.schalten(self.taste_ab, an=True)
        self.assertEqual(self.fake.anzahl('setvoicemailoncuserbytoken'), 1)
        self.schalten(self.taste_ab, an=False)
        self.schalten(self.taste_ab, an=False)
        self.assertEqual(self.fake.anzahl('setvoicemailoncuserbytoken'), 2)
        self.assertEqual(self.fake.vm_fuer(TOKEN)['active'], '0')

    def test_ungueltiger_wunschzustand(self):
        for falsch in ('ja', 1, 0, [True]):
            with self.subTest(falsch=falsch):
                zustand = self.schalten(self.taste_dnd, an=falsch)
                self.assertEqual(zustand['fehler'], 'Ungültiger Wunschzustand.')
        self.assertEqual(self.fake.anzahl('setdndonpeer'), 0)

    def test_ungueltige_taste(self):
        for falsch in (0, -1, 999999, 'x', None, True):
            with self.subTest(falsch=falsch):
                zustand = self.als(self.user_a).wb_voip_taste_schalten(falsch)
                self.assertEqual(zustand['fehler'], 'Diese Taste gibt es nicht mehr.')
        self.taste_dnd.active = False
        self.assertEqual(self.schalten(self.taste_dnd)['fehler'], 'Diese Taste gibt es nicht mehr.')
        self.assertFalse(any(a['action'].startswith('set') for a in self.fake.aufrufe))

    def test_weiterleitung_fest(self):
        zustand = self.schalten(self.taste_handy)
        self.assertEqual(self.fake.letzte('setcallfordwardingonpeer'), {
            'token': TOKEN, 'action': 'setcallfordwardingonpeer', 'pk_peerId': 7, 'destination': '00491701234567'})
        self.assertTrue(self.aktiv(zustand)['→ Handy'])
        zustand = self.schalten(self.taste_handy)
        self.assertIsNone(self.fake.fw[7])
        self.assertEqual(self.fake.letzte('setcallfordwardingonpeer')['destination'], '0')
        self.assertFalse(self.aktiv(zustand)['→ Handy'])

    def test_weiterleitung_eigenes_handy(self):
        zustand = self.schalten(self.taste_mein)
        self.assertEqual(zustand['fehler'], HINWEIS_HANDY)
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)
        self.user_a.with_user(self.user_a).write({'wb_voip_handy': '0171 7654321'})
        zustand = self.schalten(self.taste_mein)
        self.assertEqual(self.fake.fw[7], '01717654321')
        self.assertTrue(self.aktiv(zustand)['→ Mein Handy'])
        self.assertFalse(self.aktiv(zustand)['→ Handy'])
        self.assertFalse(zustand['fremde_weiterleitung'])

    def test_feste_taste_ohne_ziel_ausgeblendet(self):
        self.taste_handy.write({'ziel': False})
        namen = [t['name'] for t in self.zustand()['tasten']]
        self.assertNotIn('→ Handy', namen)
        self.assertIn('→ Mein Handy', namen)

    def test_nur_eigene_nebenstelle_geschaltet(self):
        for taste in (self.taste_dnd, self.taste_handy, self.taste_dnd):
            self.schalten(taste, benutzer=self.user_b)
        self.assertEqual(self.fake.dnd, {7: '0', 8: '1'})
        self.assertEqual({a['pk_peerId'] for a in self.fake.aufrufe if 'pk_peerId' in a}, {8})
        self.assertEqual(self.bus[-1][0], self.user_b.id)

    def test_gegenseitiger_ausschluss(self):
        self.schalten(self.taste_dnd)
        zustand = self.schalten(self.taste_handy)
        self.assertEqual((self.fake.dnd[7], self.fake.fw[7]), ('0', '00491701234567'))
        self.assertEqual(self.aktiv(zustand)['Nicht stören'], False)
        zustand = self.schalten(self.taste_ab)
        self.assertEqual((self.fake.vm_fuer(TOKEN)['active'], self.fake.fw[7]), ('1', None))
        self.assertTrue(self.aktiv(zustand)['AB'])
        zustand = self.schalten(self.taste_dnd)
        self.assertEqual((self.fake.vm_fuer(TOKEN)['active'], self.fake.dnd[7]), ('0', '1'))
        self.assertEqual(self.aktiv(zustand), {
            '→ Handy': False, '→ Mein Handy': None, 'Nicht stören': True, 'AB': False})

    def test_ausschalten_schaltet_andere_nicht(self):
        self.schalten(self.taste_dnd)
        self.fake.fw[7] = '0171999888'
        self.schalten(self.taste_dnd, an=False)
        self.assertEqual(self.fake.fw[7], '0171999888')

    def test_ausschluss_ohne_ab_token(self):
        self.fake.info = {'peer': {}}
        self.fake.vm_fuer(TOKEN)['active'] = '1'
        self.schalten(self.taste_dnd)
        self.assertEqual(self.fake.dnd[7], '1')
        self.assertEqual(self.fake.anzahl('setvoicemailoncuserbytoken'), 0)

    def test_weiterleitung_aus(self):
        self.fake.fw[7] = '0171999888'
        zustand = self.als(self.user_a).wb_voip_weiterleitung_aus()
        self.assertIsNone(self.fake.fw[7])
        self.assertFalse(zustand['fremde_weiterleitung'])
        self.assertFalse(zustand['umgeleitet'])

    def test_nicht_erreichbar_beim_schalten(self):
        self.fake.fehler = requests.ConnectionError('boom')
        with mute_logger('odoo.addons.wb_voip_ansitel.models.ansitel_api'):
            zustand = self.schalten(self.taste_dnd)
        self.assertEqual(zustand['fehler'], NICHT_ERREICHBAR)
        self.assertTrue(zustand['verfuegbar'])
        self.assertEqual(set(self.aktiv(zustand).values()), {None})
        self.assertEqual(self.post.call_count, 1)  # kein erneutes Lesen nach dem Fehler

    def test_abgelehnt_beim_schalten_liest_neu(self):
        original = self.fake.__call__

        def ablehnen(sitzung, url, json=None, **kwargs):
            if json['action'] == 'setdndonpeer':
                return antwort({'status': 400}, status_code=400)
            return original(sitzung, url, json=json, **kwargs)
        self._post(side_effect=ablehnen)
        with mute_logger('odoo.addons.wb_voip_ansitel.models.ansitel_api'):
            zustand = self.schalten(self.taste_dnd)
        self.assertEqual(zustand['fehler'], 'Ansitel lehnt die Anfrage ab.')
        self.assertFalse(self.aktiv(zustand)['Nicht stören'])
