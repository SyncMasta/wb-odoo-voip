from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from odoo.addons.wb_voip_ansitel.models import ansitel_api

from .common import PEERS
from .test_tasten import TastenCase

HINWEIS_UNBEKANNT = 'Interne Nebenstelle unbekannt – bitte Systemverwaltung fragen.'


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestInterneWeiterleitung(TastenCase):
    """Forwarding to an internal extension or ring group of the user's own PBX."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # ohne Handy-Taste: deren Hinweis („Mein Handy“ fehlt) stünde sonst vor dem eigenen
        cls.taste_mein.active = False
        cls.taste_intern = cls.env['wb.voip.taste'].create({
            'name': '→ Zentrale', 'art': 'weiterleitung', 'ziel_art': 'intern', 'ziel': '101',
            'sequence': 5})

    def _ziel_setzen(self, ziel):
        """Store a target past the ORM (as if saved before the PBX changed)."""
        self.env.cr.execute("UPDATE wb_voip_taste SET ziel = %s WHERE id = %s", [ziel, self.taste_intern.id])
        self.taste_intern.invalidate_recordset(['ziel'])

    # --- Speichern ---

    def test_ziel_bleibt_wie_eingegeben(self):
        self.taste_intern.write({'ziel': ' 600 '})
        self.assertEqual(self.taste_intern.ziel, '600')
        self.assertEqual(self.taste_intern.ziel_art, 'intern')

    def test_externe_und_ungueltige_nummern_abgelehnt(self):
        for ziel in ('+49 152 28817386', '0152 28817386', '1234567', '1', '10 1', '1-01', 'Tel101',
                     '١٠١', '１０１', '+101'):
            with self.subTest(ziel=ziel):
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    self.taste_intern.write({'ziel': ziel})
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    self.env['wb.voip.taste'].create({
                        'name': 'X', 'art': 'weiterleitung', 'ziel_art': 'intern', 'ziel': ziel})
        # 2 bis 6 Ziffern sind erlaubt, auch 1xx-Nebenstellen
        for ziel in ('10', '123456', '100', '119', '199'):
            with self.subTest(ziel=ziel):
                self.taste_intern.write({'ziel': ziel})

    SPERRE = ('110', '112', '115', '116', '11611', '116117', '118', '11833', '0600', '01', '0100')

    def test_kurzwahl_notruf_und_null_gesperrt(self):
        for ziel in self.SPERRE:
            with self.subTest(ziel=ziel):
                self.assertTrue(self.env['wb.voip.taste']._intern_format_fehler(ziel))
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    self.taste_intern.write({'ziel': ziel})
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    self.anlage.write({'ansitel_interne_ziele': '600\n' + ziel})
        self.assertIn('Notruf', self.env['wb.voip.taste']._intern_format_fehler('112'))

    def test_kurzwahl_am_orm_vorbei_nie_gewaehlt(self):
        self._ziel_setzen('112')
        zustand = self.schalten(self.taste_intern)
        self.assertTrue(zustand['fehler'])
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)

    def test_manager_darf_internes_ziel_nicht_setzen(self):
        Taste = self.env['wb.voip.taste'].with_user(self.user_manager)
        with self.assertRaises(AccessError) as fehler:
            self.taste_intern.with_user(self.user_manager).write({'ziel': '100'})
        self.assertIn('Systemverwaltung', str(fehler.exception))
        with self.assertRaises(AccessError):
            Taste.create({'name': 'X', 'art': 'weiterleitung', 'ziel_art': 'intern', 'ziel': '101'})
        # Auch nicht über die Zielart: aus einer festen Nummer würde sonst ein internes Ziel
        with self.assertRaises(AccessError):
            self.taste_handy.with_user(self.user_manager).write({'ziel_art': 'intern'})
        with self.assertRaises(AccessError):
            self.taste_intern.with_user(self.user_manager).write({'ziel_art': 'fest'})
        with self.assertRaises(AccessError):
            self.taste_intern.with_user(self._verwalter()).write({'ziel': '100'})
        # Beschriftung darf der Manager ändern
        self.taste_intern.with_user(self.user_manager).write({'name': 'Zentrale'})

    def test_interne_ziele_nur_systemverwaltung(self):
        with self.assertRaises(AccessError):
            self.anlage.with_user(self.user_manager).write({'ansitel_interne_ziele': '600'})
        with self.assertRaises(AccessError):
            self.anlage.with_user(self.user_manager).read(['ansitel_interne_ziele'])
        self.anlage.write({'ansitel_interne_ziele': '600\n 601 \n\n'})
        for liste in ('600\nabc', '1', '1234567', '60 0', '٦٠٠', '+600'):
            with self.subTest(liste=liste):
                with self.assertRaises(ValidationError), self.env.cr.savepoint():
                    self.anlage.write({'ansitel_interne_ziele': liste})

    # --- Lesen und Schalten ---

    def test_peer_nebenstelle_schalten(self):
        zustand = self.zustand()
        self.assertIs(self.aktiv(zustand)['→ Zentrale'], False)
        zustand = self.schalten(self.taste_intern)
        # unverändert gewählt, keine 00-/E.164-Umwandlung
        self.assertEqual(self.fake.letzte('setcallfordwardingonpeer')['destination'], '101')
        self.assertEqual(self.fake.fw[7], '101')
        self.assertIs(self.aktiv(zustand)['→ Zentrale'], True)
        self.assertFalse(zustand['fremde_weiterleitung'])
        self.assertTrue(zustand['umgeleitet'])
        zustand = self.schalten(self.taste_intern)
        self.assertIsNone(self.fake.fw[7])
        self.assertIs(self.aktiv(zustand)['→ Zentrale'], False)

    def test_rufgruppe_aus_liste(self):
        self.anlage.write({'ansitel_interne_ziele': '600\n601'})
        self.taste_intern.write({'ziel': '601'})
        zustand = self.schalten(self.taste_intern)
        self.assertEqual(self.fake.letzte('setcallfordwardingonpeer')['destination'], '601')
        self.assertIs(self.aktiv(zustand)['→ Zentrale'], True)
        self.assertFalse(zustand['fehler'])

    def test_unbekannte_nebenstelle(self):
        self.taste_intern.write({'ziel': '555'})
        zustand = self.zustand()
        self.assertIsNone(self.aktiv(zustand)['→ Zentrale'])
        self.assertEqual(zustand['fehler'], HINWEIS_UNBEKANNT)
        zustand = self.schalten(self.taste_intern)
        self.assertEqual(zustand['fehler'], HINWEIS_UNBEKANNT)
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)
        self.assertIsNone(self.aktiv(zustand)['→ Zentrale'])

    def test_rufgruppe_entfernt_wird_beim_schalten_geprueft(self):
        self.anlage.write({'ansitel_interne_ziele': '600'})
        self.taste_intern.write({'ziel': '600'})
        self.anlage.write({'ansitel_interne_ziele': False})
        zustand = self.schalten(self.taste_intern)
        self.assertEqual(zustand['fehler'], HINWEIS_UNBEKANNT)
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)

    def test_eigene_nebenstelle_abgelehnt(self):
        self.taste_intern.write({'ziel': '100'})  # Nebenstelle von A, die von B
        zustand = self.schalten(self.taste_intern)
        self.assertIn('eigene Nebenstelle', zustand['fehler'])
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)
        self.assertIsNone(self.aktiv(zustand)['→ Zentrale'])
        # B darf auf 100 weiterleiten
        self.schalten(self.taste_intern, benutzer=self.user_b)
        self.assertEqual(self.fake.fw[8], '100')

    def test_eigene_nebenstelle_auch_ueber_geraetenamen(self):
        # Nebenstelle als Gerätename gepflegt, Ziel als interne Nummer desselben Peers
        self.user_a.write({'wb_voip_ansitel_nebenstelle': 'Tel100'})
        self.taste_intern.write({'ziel': '100'})
        zustand = self.schalten(self.taste_intern)
        self.assertIn('eigene Nebenstelle', zustand['fehler'])
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)

    def test_eigene_nebenstelle_in_rufgruppenliste(self):
        self.anlage.write({'ansitel_interne_ziele': '100'})
        self.taste_intern.write({'ziel': '100'})
        zustand = self.schalten(self.taste_intern)
        self.assertIn('eigene Nebenstelle', zustand['fehler'])
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)

    def test_am_orm_vorbei_gespeichertes_externes_ziel(self):
        # Eine externe Nummer in einer internen Taste wird nie gewählt
        self._ziel_setzen('015228817386')
        zustand = self.schalten(self.taste_intern)
        self.assertTrue(zustand['fehler'])
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)
        self.assertNotIn('→ Zentrale', self.aktiv(zustand))  # ungültiges Format: ausgeblendet

    def test_weiterleitung_auf_nebenstelle_von_aussen_erkannt(self):
        self.fake.fw[7] = '101'
        zustand = self.zustand()
        self.assertIs(self.aktiv(zustand)['→ Zentrale'], True)
        self.assertIs(self.aktiv(zustand)['→ Handy'], False)
        self.assertFalse(zustand['fremde_weiterleitung'])

    def test_andere_anlage_kennt_nebenstelle_nicht(self):
        # Peer-Liste der Anlage ohne 101: unbekannt
        self.fake.peers = [{'pk_peerId': 7, 'peerInternalCallerId': '100', 'peerName': 'Tel100'}]
        zustand = self.zustand()
        self.assertEqual(zustand['fehler'], HINWEIS_UNBEKANNT)

    # --- Abgleich nur über die interne Nummer, eine Peer-Liste je Anlage ---

    def test_numerischer_geraetename_kein_internes_ziel(self):
        # Gerätename 555 mit interner Nummer 105: nur 105 ist ein internes Ziel
        self.fake.peers = PEERS['data'] + [{'pk_peerId': 9, 'peerInternalCallerId': '105', 'peerName': '555'}]
        self.fake.fw[9] = None
        self.taste_intern.write({'ziel': '555'})
        zustand = self.schalten(self.taste_intern)
        self.assertEqual(zustand['fehler'], HINWEIS_UNBEKANNT)
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)
        self.taste_intern.write({'ziel': '105'})
        self.schalten(self.taste_intern)
        self.assertEqual(self.fake.fw[7], '105')

    def test_eigene_interne_nummer_als_fremder_geraetename(self):
        # A pflegt den Gerätenamen Tel100 (interne Nummer 100); ein anderer Peer heißt „100“
        self.fake.peers = [{'pk_peerId': 9, 'peerInternalCallerId': '109', 'peerName': '100'}] + PEERS['data']
        self.fake.fw[9] = None
        self.user_a.write({'wb_voip_ansitel_nebenstelle': 'Tel100'})
        self.taste_intern.write({'ziel': '100'})
        zustand = self.schalten(self.taste_intern)
        self.assertIn('eigene Nebenstelle', zustand['fehler'])
        self.assertEqual(self.fake.anzahl('setcallfordwardingonpeer'), 0)

    def test_eine_peer_liste_je_anlage(self):
        self.anlage.write({'ansitel_interne_ziele': '600'})
        self.env['wb.voip.taste'].create({
            'name': '→ Gruppe', 'art': 'weiterleitung', 'ziel_art': 'intern', 'ziel': '600', 'sequence': 6})
        zustand = self.zustand()
        self.assertFalse(zustand['fehler'])
        self.assertEqual(self.fake.anzahl('getpeer'), 1)
        self.schalten(self.taste_intern)
        self.assertEqual(self.fake.anzahl('getpeer'), 1)

    def test_rufgruppe_ohne_getpeer(self):
        self.anlage.write({'ansitel_interne_ziele': '600'})
        ansitel_api.cache_leeren()  # kalter Zwischenspeicher
        Users = self.env['res.users']
        self.assertFalse(Users._wb_voip_intern_pruefen(self.anlage, '100', '600'))
        self.assertEqual(self.fake.anzahl('getpeer'), 0)
        self.assertEqual(Users._wb_voip_intern_pruefen(self.anlage, '600', '600'),
                         'Die eigene Nebenstelle kann nicht Weiterleitungsziel sein.')
        self.assertEqual(self.fake.anzahl('getpeer'), 0)
        self.assertFalse(Users._wb_voip_intern_pruefen(self.anlage, '100', '101'))
        self.assertEqual(self.fake.anzahl('getpeer'), 1)
