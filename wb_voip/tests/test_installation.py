from unittest.mock import patch

from odoo.addons.wb_voip import _wb_voip_post_init
from odoo.modules.module import get_manifest
from odoo.tests import tagged

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestInstallation(WbVoipCase):

    def test_gruppen_vorhanden(self):
        manager = self.env.ref('wb_voip.group_wb_voip_manager')
        benutzer = self.env.ref('wb_voip.group_wb_voip_user')
        self.assertIn(benutzer, manager.implied_ids)

    def test_abhaengigkeiten_und_lizenz(self):
        modul = self.env['ir.module.module'].search([('name', '=', 'wb_voip')])
        namen = set(modul.dependencies_id.mapped('name'))
        self.assertFalse(namen & {'voip_oca', 'crm_phonecall', 'partner_phone_secondary'})
        self.assertIn('wb_license_client', namen)
        self.assertEqual(modul.license, 'OPL-1')
        self.assertIn('voip', get_manifest('wb_voip')['excludes'])

    def test_post_init_meldet_installation(self):
        with patch.object(type(self.env['wb.license.client']), 'register_install') as reg:
            _wb_voip_post_init(self.env)
        reg.assert_called_once_with('VOIP')

    def test_post_init_fehler_bricht_nicht_ab(self):
        with patch.object(type(self.env['wb.license.client']), 'register_install',
                          side_effect=ConnectionError('aus')):
            _wb_voip_post_init(self.env)  # keine Exception

    def test_testbasis_benutzer_und_lizenz(self):
        self.assertTrue(self.user_a.has_group('wb_voip.group_wb_voip_user'))
        self.assertTrue(self.user_b.has_group('wb_voip.group_wb_voip_user'))
        self.assertTrue(self.user_manager.has_group('wb_voip.group_wb_voip_manager'))
        self._lizenz_gueltig()
        self.assertTrue(self.env['wb.license.client'].check_license('VOIP').is_valid)
        self._lizenz_fehlt()
        self.assertFalse(self.env['wb.license.client'].check_license('VOIP').is_valid)
