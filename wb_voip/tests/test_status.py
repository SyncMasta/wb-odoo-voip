from unittest.mock import patch

from odoo.tests import tagged

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestStatus(WbVoipCase):
    """Softphone status without raising (license, setup) and session flag."""

    def test_status_ohne_lizenz_wirft_nicht(self):
        self._lizenz_fehlt()
        status = self.env['res.users'].with_user(self.user_a).wb_voip_status()
        self.assertEqual(status, {
            'aktiv': True,
            'lizenz_gueltig': False,
            'meldung': 'Lizenz fehlt oder abgelaufen',
            'eingerichtet': True,
            'konfiguriert': True,
        })

    def test_status_mit_lizenz(self):
        self._lizenz_gueltig()
        status = self.env['res.users'].with_user(self.user_a).wb_voip_status()
        self.assertTrue(status['lizenz_gueltig'])
        self.assertEqual(status['meldung'], '')
        self.assertTrue(status['eingerichtet'])
        # keine Zugangsdaten in der Antwort
        self.assertNotIn('geheim', str(status))

    def test_status_nicht_eingerichtet(self):
        self._lizenz_gueltig()
        # Benutzer B hat weder Anlage noch SIP-Benutzer
        status = self.env['res.users'].with_user(self.user_b).wb_voip_status()
        self.assertFalse(status['eingerichtet'])

    def test_status_lizenzpruefung_kaputt(self):
        with patch.object(type(self.env['wb.license.client']), 'check_license',
                          side_effect=RuntimeError('kaputt')), \
                self.assertLogs('odoo.addons.wb_voip.models.res_users', level='ERROR'):
            status = self.env['res.users'].with_user(self.user_a).wb_voip_status()
        self.assertFalse(status['lizenz_gueltig'])
        self.assertTrue(status['meldung'])

    def test_session_kennzeichen(self):
        self.assertIs(self.user_a._wb_voip_session_aktiv(), True)
        # B ist VoIP-Benutzer, aber ohne Anlage: Dienst startet nicht
        self.assertIs(self.user_b._wb_voip_session_aktiv(), False)
        sonst = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Ohne VoIP', 'login': 'wb_voip_ohne', 'email': 'ohne@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        sonst.sudo().wb_voip_anlage_id = self.anlage
        self.assertIs(sonst._wb_voip_session_aktiv(), False)
