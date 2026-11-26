import os
from unittest.mock import patch

import requests

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.wb_voip_ansitel.models import ansitel_api
from odoo.addons.wb_voip_ansitel.models.ansitel_api import AnsitelFehler

from .common import PEERS, TOKEN, URL, AnsitelCase, antwort

LOGGER = 'odoo.addons.wb_voip_ansitel.models.ansitel_api'


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestAufruf(AnsitelCase):
    """``_aufruf``: the only network point; errors never leak token or URL."""

    def _fehler(self, **kwargs):
        with self.assertRaises(AnsitelFehler) as fehler:
            self.api._aufruf(self.anlage, 'getpeer', **kwargs)
        return fehler.exception

    def _kein_geheimnis(self, ausnahme, logs=()):
        text = ' '.join([str(ausnahme), repr(ausnahme.args), *logs])
        for geheim in (TOKEN, 'pbx.example.invalid', URL, 'awi/api'):
            self.assertNotIn(geheim, text)
        self.assertIsNone(ausnahme.__cause__)
        self.assertIsNone(ausnahme.__context__)

    def test_fehlerklasse(self):
        self.assertTrue(issubclass(AnsitelFehler, UserError))

    def test_erfolg_und_aufrufform(self):
        post = self._post(antwort(PEERS))
        daten = self.api._aufruf(self.anlage, 'getpeer', pk_peerId=7)
        self.assertEqual(daten, PEERS)
        args, kwargs = post.call_args
        self.assertEqual(args[1:], (URL,))
        self.assertIs(args[0].trust_env, False)
        self.assertIs(kwargs['verify'], True)
        self.assertEqual(kwargs['timeout'], 10)
        self.assertIs(kwargs['allow_redirects'], False)
        self.assertEqual(kwargs['json'], {'token': TOKEN, 'action': 'getpeer', 'pk_peerId': 7})

    def test_werte_ueberschreiben_token_nicht(self):
        post = self._post(antwort({'status': 200}))
        self.api._aufruf(self.anlage, 'x', token='eigener', **{'action': 'boese'})
        self.assertEqual(post.call_args.kwargs['json']['token'], 'eigener')
        self.assertEqual(post.call_args.kwargs['json']['action'], 'x')

    def test_persoenlicher_token(self):
        post = self._post(antwort({'status': 200}))
        self.api._aufruf(self.anlage, 'getvoicemail', token='persoenlich')
        self.assertEqual(post.call_args.kwargs['json']['token'], 'persoenlich')

    def test_nicht_eingerichtet(self):
        post = self._post(antwort({'status': 200}))
        for werte in ({'ansitel_aktiv': False}, {'ansitel_aktiv': True, 'ansitel_token': False}):
            self.anlage.write(werte)
            self._fehler()
        post.assert_not_called()

    def test_inaktiv_ist_nicht_eingerichtet(self):
        self.anlage.write({'ansitel_aktiv': False})
        self.assertIn('not set up', str(self._fehler()))

    def test_nicht_https_wird_vor_dem_aufruf_abgewiesen(self):
        post = self._post(antwort({'status': 200}))
        # Am Feld vorbei in die Datenbank geschrieben (z. B. alter Bestand)
        self.env.cr.execute(
            "UPDATE wb_voip_anlage SET ansitel_api_url = 'http://pbx.example.invalid/api' WHERE id = %s",
            [self.anlage.id])
        self.anlage.invalidate_recordset()
        fehler = self._fehler()
        post.assert_not_called()
        self._kein_geheimnis(fehler)

    def test_netzfehler_ohne_geheimnis(self):
        # requests-Ausnahmen tragen oft die volle URL; sie darf nirgends auftauchen
        post = self._post(side_effect=requests.ConnectionError(
            f'HTTPSConnectionPool(host=pbx.example.invalid): {URL}?token={TOKEN}'))
        with self.assertLogs(LOGGER, level='WARNING') as logs:
            fehler = self._fehler()
        post.assert_called_once()
        self._kein_geheimnis(fehler, logs.output)
        self.assertIn('[wb_voip_ansitel]', logs.output[0])

    def test_zeitueberschreitung(self):
        self._post(side_effect=requests.Timeout(f'{URL} {TOKEN}'))
        with self.assertLogs(LOGGER, level='WARNING') as logs:
            fehler = self._fehler()
        self._kein_geheimnis(fehler, logs.output)
        self.assertIn('unreachable', str(fehler))

    def test_http_fehler(self):
        for code, text in ((401, 'rejects the token'), (403, 'rejects the token'),
                           (400, 'rejects the request'), (500, 'unreachable'),
                           (302, 'unreachable')):
            with self.subTest(code=code):
                self._post(antwort({'status': 200}, status_code=code))
                with self.assertLogs(LOGGER, level='WARNING') as logs:
                    fehler = self._fehler()
                self.assertIn(text, str(fehler))
                self._kein_geheimnis(fehler, logs.output)

    def test_geaenderte_adresse_ohne_alten_token(self):
        # Verwalter ohne Systemrecht (auch über sudo) ändert die Adresse: Token ist weg, kein Aufruf
        post = self._post(antwort(PEERS))
        self.anlage.with_user(self.user_manager).sudo().write(
            {'ansitel_api_url': 'https://fremd.example.invalid/api'})
        self._fehler()
        post.assert_not_called()

    def test_ungueltiges_json(self):
        self._post(antwort(json_fehler=True))
        self._kein_geheimnis(self._fehler())

    def test_antwort_kein_objekt(self):
        for roh in ([1, 2], 'text', None, 5):
            with self.subTest(roh=roh):
                self._post(antwort(roh))
                self._kein_geheimnis(self._fehler())

    def test_fehlerstatus_im_body(self):
        self._post(antwort({'status': 401, 'message': f'Token {TOKEN} ungültig'}))
        fehler = self._fehler()
        self._kein_geheimnis(fehler)

    def test_status_fehlt_gilt_http(self):
        self._post(antwort({'data': []}))
        self.assertEqual(self.api._aufruf(self.anlage, 'getpeer'), {'data': []})

    def test_status_als_text_200(self):
        self._post(antwort({'status': '200'}))
        self.assertEqual(self.api._aufruf(self.anlage, 'x')['status'], '200')

    def test_token_unlesbar_nach_schluesselwechsel(self):
        from cryptography.fernet import Fernet
        post = self._post(antwort({'status': 200}))
        with patch.dict(os.environ, {'WB_VOIP_FERNET_KEY': Fernet.generate_key().decode()}):
            fehler = self._fehler()
        post.assert_not_called()
        self._kein_geheimnis(fehler)

    def test_ohne_schluessel(self):
        post = self._post(antwort({'status': 200}))
        umgebung = {k: v for k, v in os.environ.items() if k != 'WB_VOIP_FERNET_KEY'}
        with patch.dict(os.environ, umgebung, clear=True):
            with self.assertRaises(UserError):
                self.api._aufruf(self.anlage, 'getpeer')
        post.assert_not_called()


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestPeerId(AnsitelCase):
    """``_peer_id``: lookup by extension, cached per worker without plain token."""

    def test_treffer_ueber_nebenstelle_und_name(self):
        self._post(antwort(PEERS), antwort(PEERS))
        self.assertEqual(self.api._peer_id(self.anlage, '100'), 7)
        self.assertEqual(self.api._peer_id(self.anlage, ' Tel101 '), 8)

    def test_cache_zweiter_aufruf_ohne_netz(self):
        post = self._post(antwort(PEERS))
        self.assertEqual(self.api._peer_id(self.anlage, '100'), 7)
        self.assertEqual(self.api._peer_id(self.anlage, '100'), 7)
        self.assertEqual(post.call_count, 1)

    def test_eine_liste_fuer_alle_nebenstellen(self):
        post = self._post(antwort(PEERS))
        self.assertEqual(self.api._peer_id(self.anlage, '100'), 7)
        self.assertEqual(self.api._peer_id(self.anlage, 'Tel101'), 8)
        self.assertEqual(self.api._peer_intern(self.anlage, '101'), 8)
        self.assertEqual(post.call_count, 1)

    def test_intern_nur_ueber_interne_nummer(self):
        self._post(antwort(PEERS))
        self.assertIsNone(self.api._peer_intern(self.anlage, 'Tel101'))
        self.assertIsNone(self.api._peer_intern(self.anlage, ''))
        self.assertEqual(self.api._peer_intern(self.anlage, ' 100 '), 7)
        self.assertEqual(self.api._peer_interne_nummer(self.anlage, 8), '101')
        self.assertIsNone(self.api._peer_interne_nummer(self.anlage, 99))

    def test_unbekannt_wird_auch_gemerkt(self):
        post = self._post(antwort(PEERS))
        self.assertIsNone(self.api._peer_id(self.anlage, '999'))
        self.assertIsNone(self.api._peer_id(self.anlage, '999'))
        self.assertEqual(post.call_count, 1)

    def test_leere_nebenstelle(self):
        post = self._post(antwort(PEERS))
        self.assertIsNone(self.api._peer_id(self.anlage, ''))
        self.assertIsNone(self.api._peer_id(self.anlage, False))
        post.assert_not_called()

    def test_cache_leeren(self):
        post = self._post(antwort(PEERS), antwort(PEERS))
        self.api._peer_id(self.anlage, '100')
        ansitel_api.cache_leeren()
        self.api._peer_id(self.anlage, '100')
        self.assertEqual(post.call_count, 2)

    def test_tokenwechsel_macht_cache_ungueltig(self):
        post = self._post(antwort(PEERS), antwort(PEERS))
        self.api._peer_id(self.anlage, '100')
        self.anlage.write({'ansitel_token': 'anderer-token'})
        self.api._peer_id(self.anlage, '100')
        self.assertEqual(post.call_count, 2)

    def test_schluessel_ohne_klartext(self):
        self._post(antwort(PEERS))
        self.api._peer_id(self.anlage, '100')
        schluessel = repr(list(ansitel_api._PEER_CACHE))
        self.assertTrue(ansitel_api._PEER_CACHE)
        self.assertNotIn(TOKEN, schluessel)
        self.assertNotIn(self.anlage.sudo().ansitel_token_enc, schluessel)
        self.assertNotIn(TOKEN, repr(list(ansitel_api._PEER_CACHE.values())))

    def test_cache_je_anlage(self):
        post = self._post(antwort(PEERS), antwort(PEERS))
        zweite = self.anlage.copy({'name': 'Zweite', 'sip_domain': 'zweite.test'})
        zweite.write({'ansitel_aktiv': True, 'ansitel_api_url': URL, 'ansitel_token': TOKEN})
        self.api._peer_id(self.anlage, '100')
        self.api._peer_id(zweite, '100')
        self.assertEqual(post.call_count, 2)

    def test_schluessel_enthaelt_datenbank(self):
        self._post(antwort(PEERS))
        self.api._peer_id(self.anlage, '100')
        self.assertTrue(all(k[0] == self.env.cr.dbname for k in ansitel_api._PEER_CACHE))

    def test_unerwartete_antwort(self):
        for daten in ({'status': 200, 'data': 'x'}, {'status': 200, 'data': {'peerName': '100'}}):
            with self.subTest(daten=daten):
                ansitel_api.cache_leeren()
                self._post(antwort(daten))
                with self.assertRaises(AnsitelFehler):
                    self.api._peer_id(self.anlage, '100')

    def test_kaputte_eintraege_werden_uebersprungen(self):
        for kaputt in ({'peerName': '100'}, {'pk_peerId': 'abc', 'peerName': '100'}, 'text',
                       {'pk_peerId': None, 'peerInternalCallerId': '100'}, {'pk_peerId': True, 'peerName': '100'}):
            with self.subTest(kaputt=kaputt):
                ansitel_api.cache_leeren()
                self._post(antwort({'status': 200, 'data': [kaputt] + PEERS['data']}))
                with self.assertLogs('odoo.addons.wb_voip_ansitel.models.ansitel_api', 'WARNING') as log:
                    self.assertEqual(self.api._peer_id(self.anlage, '100'), 7)
                self.assertEqual(self.api._peer_intern(self.anlage, '101'), 8)
                self.assertEqual(len(log.output), 1)
                self.assertNotIn('100', log.output[0])

    def test_fehler_wird_nicht_gecacht(self):
        post = self._post(requests.ConnectionError('x'), antwort(PEERS))
        with self.assertRaises(AnsitelFehler):
            self.api._peer_id(self.anlage, '100')
        self.assertEqual(self.api._peer_id(self.anlage, '100'), 7)
        self.assertEqual(post.call_count, 2)


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestVerbindungTesten(AnsitelCase):
    """Test button: manager only, notification without secrets."""

    def _testen(self, user=None):
        return self.anlage.with_user(user or self.user_manager).action_ansitel_testen()

    def test_erfolg(self):
        post = self._post(antwort(PEERS))
        aktion = self._testen()
        self.assertEqual(aktion['tag'], 'display_notification')
        self.assertEqual(aktion['params']['type'], 'success')
        self.assertEqual(post.call_args.kwargs['json']['action'], 'getpeer')

    def test_fehler_als_hinweis(self):
        self._post(side_effect=requests.ConnectionError(f'{URL} {TOKEN}'))
        aktion = self._testen()
        self.assertEqual(aktion['params']['type'], 'danger')
        text = repr(aktion)
        for geheim in (TOKEN, 'pbx.example.invalid'):
            self.assertNotIn(geheim, text)

    def test_nur_manager(self):
        post = self._post(antwort(PEERS))
        with self.assertRaises(AccessError):
            self.anlage.with_user(self.user_a).action_ansitel_testen()
        post.assert_not_called()

    def test_ohne_schluessel_als_hinweis(self):
        post = self._post(antwort(PEERS))
        umgebung = {k: v for k, v in os.environ.items() if k != 'WB_VOIP_FERNET_KEY'}
        with patch.dict(os.environ, umgebung, clear=True):
            aktion = self._testen()
        self.assertEqual(aktion['params']['type'], 'danger')
        post.assert_not_called()

    def test_ohne_einrichtung(self):
        self.anlage.write({'ansitel_aktiv': False})
        post = self._post(antwort(PEERS))
        aktion = self._testen()
        self.assertEqual(aktion['params']['type'], 'danger')
        post.assert_not_called()
