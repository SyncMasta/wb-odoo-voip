from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestRechte(WbVoipCase):

    def test_passwort_fuer_fremde_unsichtbar(self):
        try:
            wert = self.user_a.with_user(self.user_b).read(['wb_voip_sip_passwort'])
        except AccessError:
            return
        self.assertFalse(wert[0]['wb_voip_sip_passwort'])

    def test_passwort_selbst_nur_schreibbar(self):
        a = self.user_a.with_user(self.user_a)
        self.assertFalse(a.read(['wb_voip_sip_passwort'])[0]['wb_voip_sip_passwort'])
        a.write({'wb_voip_sip_passwort': 'geheim2'})
        self.assertFalse(a.read(['wb_voip_sip_passwort'])[0]['wb_voip_sip_passwort'])
        self.assertEqual(a._wb_voip_zugang()['sip_passwort'], 'geheim2')

    def test_fremdes_passwort_nicht_schreibbar(self):
        with self.assertRaises(AccessError):
            self.user_a.with_user(self.user_b).write({'wb_voip_sip_passwort': 'x'})

    def test_manager_liest_kein_passwort(self):
        wert = self.user_a.with_user(self.user_manager).read(['wb_voip_sip_passwort'])
        self.assertFalse(wert[0]['wb_voip_sip_passwort'])

    def test_zugang_nur_selbst(self):
        with self.assertRaises(AccessError):
            self.user_a.with_user(self.user_b)._wb_voip_zugang()
        zugang = self.user_a.with_user(self.user_a)._wb_voip_zugang()
        self.assertEqual(zugang['ws_url'], 'wss://anlage.test/ws')
        self.assertEqual(zugang['sip_benutzer'], '100')
        self.assertEqual(zugang['land'], 'DE')

    def test_anlage_nur_admin_schreibbar(self):
        anlage = self.anlage.with_user(self.user_a)
        self.assertEqual(anlage.name, 'Testanlage')
        with self.assertRaises(AccessError):
            anlage.write({'name': 'x'})
        with self.assertRaises(AccessError):
            self.env['wb.voip.anlage'].with_user(self.user_a).create({
                'name': 'n', 'ws_url': 'wss://x', 'sip_domain': 'x'})
        self.anlage.with_user(self.user_manager).write({'name': 'Neu'})

    def test_zugang_ohne_lizenz(self):
        self._lizenz_fehlt()
        with self.assertRaises(UserError):
            self.env['res.users'].with_user(self.user_a).wb_voip_zugang_lesen()

    def test_zugang_mit_lizenz(self):
        self._lizenz_gueltig()
        zugang = self.env['res.users'].with_user(self.user_a).wb_voip_zugang_lesen()
        self.assertEqual(zugang['ws_url'], 'wss://anlage.test/ws')

    def test_profilformular_enthaelt_sip_felder(self):
        view = self.env.ref('base.view_users_form_simple_modif')
        res = self.env['res.users'].with_user(self.user_a).get_views([(view.id, 'form')])
        arch = res['views']['form']['arch']
        felder = res['models']['res.users']['fields']
        for name in ('wb_voip_sip_benutzer', 'wb_voip_sip_passwort'):
            self.assertIn('name="%s"' % name, arch)
            self.assertIn(name, felder)

    def test_passwort_nicht_suchbar_fuer_fremde(self):
        users = self.env['res.users'].with_user(self.user_b)
        try:
            treffer = users.search([('wb_voip_sip_passwort', '!=', False)])
        except (AccessError, ValueError):
            # nicht gespeichertes Feld: nicht suchbar
            return
        self.assertNotIn(self.user_a, treffer)

    def test_passwort_nicht_exportierbar_fuer_fremde(self):
        # Odoo 19 verweigert Export ohne Exportgruppe mit UserError; Passwort kommt nie zurück
        with self.assertRaises(UserError):
            self.user_a.with_user(self.user_b).export_data(['wb_voip_sip_passwort'])
