import os
from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import WbVoipCase

MELDUNG_SCHLUESSEL = "WB VoIP: The key WB_VOIP_FERNET_KEY is missing on the server."
MELDUNG_UNLESBAR = "SIP password unreadable, please enter it again."


@tagged('wb_voip', 'post_install', '-at_install')
class TestSipPasswort(WbVoipCase):
    """SIP password: stored encrypted (Fernet), write-only for users."""

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()

    def _ohne_schluessel(self):
        umgebung = {k: v for k, v in os.environ.items() if k != 'WB_VOIP_FERNET_KEY'}
        return patch.dict(os.environ, umgebung, clear=True)

    def test_chiffrat_statt_klartext(self):
        chiffrat = self.user_a.sudo().wb_voip_sip_passwort_enc
        self.assertTrue(chiffrat)
        self.assertNotEqual(chiffrat, 'geheim')
        self.assertNotIn('geheim', chiffrat)
        self.assertEqual(
            Fernet(self.voip_schluessel.encode()).decrypt(chiffrat.encode()).decode(), 'geheim')

    def test_selbst_liest_leer(self):
        a = self.user_a.with_user(self.user_a)
        werte = a.read(['wb_voip_sip_passwort', 'wb_voip_sip_passwort_gesetzt'])[0]
        self.assertFalse(werte['wb_voip_sip_passwort'])
        self.assertTrue(werte['wb_voip_sip_passwort_gesetzt'])
        self.assertFalse(self.user_b.with_user(self.user_b).wb_voip_sip_passwort_gesetzt)

    def test_selbst_schreiben_dann_leer_lesen(self):
        a = self.user_a.with_user(self.user_a)
        a.write({'wb_voip_sip_passwort': 'geheim2'})
        self.assertFalse(a.read(['wb_voip_sip_passwort'])[0]['wb_voip_sip_passwort'])
        self.assertFalse(a.wb_voip_sip_passwort)
        self.assertEqual(a._wb_voip_zugang()['sip_passwort'], 'geheim2')

    def test_zugang_liefert_klartext(self):
        zugang = self.env['res.users'].with_user(self.user_a).wb_voip_zugang_lesen()
        self.assertEqual(zugang['sip_passwort'], 'geheim')

    def test_leeren_loescht_chiffrat(self):
        self.user_a.with_user(self.user_a).write({'wb_voip_sip_passwort': False})
        self.assertFalse(self.user_a.sudo().wb_voip_sip_passwort_enc)
        self.assertFalse(self.user_a.with_user(self.user_a)._wb_voip_zugang()['sip_passwort'])

    def test_fremde_lesen_suchen_exportieren_nichts(self):
        b_sicht = self.user_a.with_user(self.user_b)
        try:
            wert = b_sicht.read(['wb_voip_sip_passwort'])[0]['wb_voip_sip_passwort']
        except AccessError:
            wert = False
        self.assertFalse(wert)
        Users = self.env['res.users'].with_user(self.user_b)
        for feld in ('wb_voip_sip_passwort', 'wb_voip_sip_passwort_enc'):
            try:
                treffer = Users.search([(feld, '!=', False)])
            except (AccessError, UserError, ValueError):
                continue
            self.assertNotIn(self.user_a, treffer, feld)
        try:
            export = b_sicht.export_data(['wb_voip_sip_passwort'])
        except (AccessError, UserError):
            export = {}
        self.assertNotIn('geheim', str(export))

    def test_chiffrat_fuer_benutzer_und_manager_unlesbar(self):
        for user in (self.user_a, self.user_b, self.user_manager):
            with self.assertRaises(AccessError, msg=user.login):
                self.user_a.with_user(user).read(['wb_voip_sip_passwort_enc'])

    def test_ohne_schluessel_speichern(self):
        with self._ohne_schluessel():
            with self.assertRaises(UserError) as fehler:
                self.user_a.with_user(self.user_a).write({'wb_voip_sip_passwort': 'neu'})
        self.assertEqual(str(fehler.exception), MELDUNG_SCHLUESSEL)

    def test_ohne_schluessel_zugang_lesen(self):
        with self._ohne_schluessel():
            with self.assertRaises(UserError) as fehler:
                self.env['res.users'].with_user(self.user_a).wb_voip_zugang_lesen()
        self.assertEqual(str(fehler.exception), MELDUNG_SCHLUESSEL)

    def test_ohne_schluessel_status(self):
        with self._ohne_schluessel():
            status = self.env['res.users'].with_user(self.user_a).wb_voip_status()
        self.assertIs(status['konfiguriert'], False)
        self.assertEqual(status['meldung'], MELDUNG_SCHLUESSEL)
        self.assertIn('lizenz_gueltig', status)
        status = self.env['res.users'].with_user(self.user_a).wb_voip_status()
        self.assertIs(status['konfiguriert'], True)

    def test_falscher_schluessel(self):
        anderer = Fernet.generate_key().decode()
        with patch.dict(os.environ, {'WB_VOIP_FERNET_KEY': anderer}):
            with self.assertRaises(UserError) as fehler:
                self.user_a.with_user(self.user_a)._wb_voip_zugang()
        self.assertEqual(str(fehler.exception), MELDUNG_UNLESBAR)

    def test_fremdes_passwort_nur_voip_administrator(self):
        Users = self.env['res.users'].with_context(no_reset_password=True)
        verwaltung = self.env.ref('base.group_erp_manager')
        nur_odoo = Users.create({
            'name': 'Odoo-Verwaltung', 'login': 'wb_voip_erp', 'email': 'erp@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, verwaltung.id])],
        })
        with self.assertRaises(AccessError):
            self.user_a.with_user(nur_odoo).write({'wb_voip_sip_passwort': 'x'})
        voip_admin = Users.create({
            'name': 'VoIP-Verwaltung', 'login': 'wb_voip_erp2', 'email': 'erp2@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id, verwaltung.id,
                                  self.env.ref('wb_voip.group_wb_voip_manager').id])],
        })
        self.user_a.with_user(voip_admin).write({'wb_voip_sip_passwort': 'vom_admin'})
        self.assertEqual(self.user_a.with_user(self.user_a)._wb_voip_zugang()['sip_passwort'],
                         'vom_admin')

    def test_ungueltiger_schluessel_warnt_ohne_wert(self):
        with patch.dict(os.environ, {'WB_VOIP_FERNET_KEY': 'kaputt-geheim'}), \
                self.assertLogs('odoo.addons.wb_voip.tools.geheimnis', level='WARNING') as logs:
            with self.assertRaises(UserError) as fehler:
                self.user_a.with_user(self.user_a)._wb_voip_zugang()
        self.assertEqual(str(fehler.exception), MELDUNG_SCHLUESSEL)
        self.assertIn('[wb_voip] WB_VOIP_FERNET_KEY ist ungültig', logs.output[0])
        self.assertNotIn('kaputt-geheim', ' '.join(logs.output))
