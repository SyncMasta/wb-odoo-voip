import os
from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import TOKEN, URL, AnsitelCase


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestAnlageFelder(AnsitelCase):
    """PBX fields: https only, token only as Fernet ciphertext."""

    def _neu(self, **werte):
        basis = {'name': 'Neu', 'ws_url': 'wss://neu.test/ws', 'sip_domain': 'neu.test'}
        return self.env['wb.voip.anlage'].create({**basis, **werte})

    def test_standardwerte(self):
        anlage = self._neu()
        self.assertFalse(anlage.ansitel_aktiv)
        self.assertEqual(anlage.ansitel_tz, 'Europe/Berlin')
        self.assertFalse(anlage.ansitel_token_gesetzt)

    def test_nur_https(self):
        for url in ('http://pbx.example.invalid/api', 'ftp://pbx.example.invalid/api',
                    'javascript:alert(1)', 'https://', 'https:///api', 'pbx.example.invalid/api',
                    'https://benutzer:pw@pbx.example.invalid/api', 'https://pbx example.invalid/api'):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                self.anlage.write({'ansitel_api_url': url})
        self.anlage.write({'ansitel_api_url': '  HTTPS://pbx.example.invalid/api  '})
        self.assertEqual(self.anlage.ansitel_api_url, 'HTTPS://pbx.example.invalid/api')

    def test_url_pflicht_wenn_aktiv(self):
        with self.assertRaises(ValidationError):
            self._neu(ansitel_aktiv=True)
        anlage = self._neu(ansitel_aktiv=True, ansitel_api_url=URL)
        self.assertTrue(anlage.ansitel_aktiv)
        with self.assertRaises(ValidationError):
            anlage.write({'ansitel_api_url': False})
        anlage.write({'ansitel_aktiv': False, 'ansitel_api_url': False})

    def test_token_nur_als_chiffrat(self):
        enc = self.anlage.sudo().ansitel_token_enc
        self.assertTrue(enc)
        self.assertNotIn(TOKEN, enc)
        self.assertEqual(Fernet(self.voip_schluessel.encode()).decrypt(enc.encode()).decode(), TOKEN)
        self.assertTrue(self.anlage.ansitel_token_gesetzt)

    def test_token_lesen_leer(self):
        self.assertFalse(self.anlage.ansitel_token)
        self.assertFalse(self.anlage.read(['ansitel_token'])[0]['ansitel_token'])

    def test_token_leeren(self):
        self.anlage.write({'ansitel_token': False})
        self.assertFalse(self.anlage.sudo().ansitel_token_enc)
        self.assertFalse(self.anlage.ansitel_token_gesetzt)

    def test_token_felder_nur_system(self):
        manager = self.anlage.with_user(self.user_manager)
        for feld in ('ansitel_token', 'ansitel_token_enc'):
            with self.assertRaises(AccessError, msg=feld):
                manager.read([feld])
        with self.assertRaises(AccessError):
            manager.write({'ansitel_token': 'neu'})
        with self.assertRaises(AccessError):
            self.anlage.with_user(self.user_a).read(['ansitel_token_enc'])

    def test_token_gesetzt_fuer_manager_lesbar(self):
        self.assertTrue(self.anlage.with_user(self.user_manager).ansitel_token_gesetzt)

    def test_ohne_schluessel_speichern(self):
        umgebung = {k: v for k, v in os.environ.items() if k != 'WB_VOIP_FERNET_KEY'}
        with patch.dict(os.environ, umgebung, clear=True):
            with self.assertRaises(UserError) as fehler:
                self.anlage.write({'ansitel_token': 'neu'})
        self.assertNotIn('neu', str(fehler.exception))
        self.assertTrue(self.anlage.sudo().ansitel_token_enc)

    def test_nicht_kopierbar(self):
        kopie = self.anlage.copy({'name': 'Kopie'})
        self.assertFalse(kopie.sudo().ansitel_token_enc)
