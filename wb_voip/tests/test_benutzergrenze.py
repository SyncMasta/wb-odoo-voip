from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import WbVoipCase

LIZENZ = 'odoo.addons.wb_license_client.models.wb_license_client.WbLicenseClient._wb_license_max_users'


@tagged('wb_voip', 'post_install', '-at_install')
class TestBenutzergrenze(WbVoipCase):
    """The user limit of the licence package blocks only an increase of the VoIP users."""

    def setUp(self):
        super().setUp()
        self.gruppe = self.env.ref('wb_voip.group_wb_voip_user')
        self.gruppe_manager = self.env.ref('wb_voip.group_wb_voip_manager')
        self.intern = self.env.ref('base.group_user')
        # Ausgangslage: user_a hat einen SIP-Benutzer, B keinen; die Dev-DB kann weitere haben
        self.basis = self.env['res.users']._wb_voip_benutzer_zaehlen()
        self.assertGreaterEqual(self.basis, 1)

    def _zahl(self, plus=0):
        return self.basis + plus

    def _grenze(self, wert):
        """Patch the limit; a positive ``wert`` counts seats on top of ``basis`` minus one (1 = full)."""
        if wert:
            wert = self.basis + wert - 1
        self.grenze = wert
        patcher = patch(LIZENZ, return_value=wert)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _neu(self, login, gruppen=None, **extra):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'email': f'{login}@example.com',
            'group_ids': [(6, 0, [self.intern.id] + [g.id for g in (gruppen or [])])], **extra})

    def test_gruppe_unter_und_an_der_grenze(self):
        self._grenze(2)
        # B hat nur noch keine Nebenstelle: Gruppe allein braucht keinen Platz, Nebenstelle schon
        self.user_b.write({'wb_voip_sip_benutzer': '101'})
        self.assertEqual(self.env['res.users']._wb_voip_benutzer_zaehlen(), self._zahl(1))

    def test_gruppe_ueber_der_grenze(self):
        self._grenze(1)
        with self.assertRaises(UserError) as fehler:
            self.user_b.write({'wb_voip_sip_benutzer': '101'})
        self.assertIn(f'allows {self.grenze} users', str(fehler.exception))
        c = self._neu('wb_voip_c')
        with self.assertRaises(UserError):
            c.write({'group_ids': [(4, self.gruppe.id)], 'wb_voip_sip_benutzer': '102'})

    def test_gruppe_hinzufuegen_zaehlt_mit_sip_benutzer(self):
        self._grenze(1)
        c = self._neu('wb_voip_c', wb_voip_sip_benutzer='102')
        self.assertEqual(self.env['res.users']._wb_voip_benutzer_zaehlen(), self._zahl())  # ohne Gruppe kein Platz
        with self.assertRaises(UserError):
            c.write({'group_ids': [(4, self.gruppe.id)]})

    def test_implizierte_gruppe_ueber_manager(self):
        self._grenze(1)
        c = self._neu('wb_voip_c', wb_voip_sip_benutzer='102')
        with self.assertRaises(UserError):
            c.write({'group_ids': [(4, self.gruppe_manager.id)]})
        with self.assertRaises(UserError):
            self._neu('wb_voip_d', [self.gruppe_manager], wb_voip_sip_benutzer='103')

    def test_gruppenseite(self):
        self._grenze(1)
        c = self._neu('wb_voip_c', wb_voip_sip_benutzer='102')
        with self.assertRaises(UserError):
            self.gruppe.write({'user_ids': [(4, c.id)]})

    def test_reaktivieren_zaehlt(self):
        self._grenze(1)
        c = self._neu('wb_voip_c', [self.gruppe], wb_voip_sip_benutzer='102', active=False)
        with self.assertRaises(UserError):
            c.write({'active': True})

    def test_verkleinern_laesst_bestehende_bleiben(self):
        self._grenze(0)
        c = self._neu('wb_voip_c', [self.gruppe], wb_voip_sip_benutzer='102')
        self.assertEqual(self.env['res.users']._wb_voip_benutzer_zaehlen(), self._zahl(1))
        self._grenze(1)
        # Bestehende bleiben änderbar, Name etc. unberührt, Nebenstelle ändern ist keine Erhöhung
        c.write({'wb_voip_sip_benutzer': '202', 'name': 'Neu'})
        self.user_a.write({'wb_voip_sip_benutzer': '200'})
        # Entfernen ist erlaubt, danach darf wieder bis zur Grenze aufgefüllt werden
        c.write({'wb_voip_sip_benutzer': False})
        self.assertEqual(self.env['res.users']._wb_voip_benutzer_zaehlen(), self._zahl())
        with self.assertRaises(UserError):
            c.write({'wb_voip_sip_benutzer': '203'})

    def test_unbegrenzt(self):
        self._grenze(0)
        for nr in range(5):
            self._neu(f'wb_voip_u{nr}', [self.gruppe], wb_voip_sip_benutzer=f'3{nr}')
        self.assertEqual(self.env['res.users']._wb_voip_benutzer_zaehlen(), self._zahl(5))

    def test_keine_lizenz_sperrt_nicht(self):
        self._grenze(None)
        for nr in range(3):
            self._neu(f'wb_voip_n{nr}', [self.gruppe], wb_voip_sip_benutzer=f'4{nr}')
        self.assertEqual(self.env['res.users']._wb_voip_benutzer_zaehlen(), self._zahl(3))

    def test_lizenzabfrage_fehler_sperrt_nicht(self):
        with patch(LIZENZ, side_effect=RuntimeError('x')):
            self._neu('wb_voip_e', [self.gruppe], wb_voip_sip_benutzer='500')

    def test_stapel_anlegen(self):
        self._grenze(3)  # zwei freie Plätze
        vals = [{
            'name': f'wb_voip_s{nr}', 'login': f'wb_voip_s{nr}', 'email': f's{nr}@example.com',
            'wb_voip_sip_benutzer': f'60{nr}',
            'group_ids': [(6, 0, [self.intern.id, self.gruppe.id])]} for nr in range(3)]
        with self.assertRaises(UserError):
            self.env['res.users'].with_context(no_reset_password=True).create(vals)
        # Ablehnung nimmt den ganzen Stapel zurück
        self.assertFalse(self.env['res.users'].search([('login', 'like', 'wb_voip_s%')]))
        self.env['res.users'].with_context(no_reset_password=True).create(vals[:2])

    def test_stapel_schreiben(self):
        self._grenze(2)
        b = self.user_b
        c = self._neu('wb_voip_c', [self.gruppe])
        with self.assertRaises(UserError):
            (b | c).write({'wb_voip_sip_benutzer': '700'})  # Nebenstelle doppelt, aber Grenze zuerst
        self.assertEqual(self.env['res.users']._wb_voip_benutzer_zaehlen(), self._zahl())

    def test_installmodus_nicht_gesperrt(self):
        self._grenze(1)
        self.env['res.users'].with_context(install_mode=True, no_reset_password=True).create({
            'name': 'wb_voip_i', 'login': 'wb_voip_i', 'wb_voip_sip_benutzer': '800',
            'group_ids': [(6, 0, [self.intern.id, self.gruppe.id])]})

    def test_installmodus_per_kontext_nicht_abschaltbar(self):
        # Ein Client kann jeden Kontext schicken: nur der Superuser darf install_mode nutzen
        self._grenze(1)
        verwalter = self._neu('wb_voip_verwalter', [self.env.ref('base.group_erp_manager'), self.env.ref('base.group_partner_manager'), self.gruppe_manager])
        env = self.env(user=verwalter, context=dict(self.env.context, install_mode=True, no_reset_password=True))
        self.assertFalse(env.su)
        with self.assertRaisesRegex(UserError, 'package allows'):
            env['res.users'].create({
                'name': 'wb_voip_k', 'login': 'wb_voip_k', 'wb_voip_sip_benutzer': '900',
                'group_ids': [(6, 0, [self.intern.id, self.gruppe.id])]})

    def test_einstellungen_anzeige(self):
        self._grenze(5)
        einstellung = self.env['res.config.settings'].create({})
        self.assertEqual(einstellung.wb_voip_benutzer_info, f'VoIP users: {self.basis} of {self.basis + 4}')
        self._grenze(0)
        einstellung.invalidate_recordset()
        self.assertEqual(einstellung.wb_voip_benutzer_info, f'VoIP users: {self.basis} (unlimited)')

    def test_deutsch(self):
        self.env['res.lang']._activate_lang('de_DE')
        self.env['ir.module.module']._load_module_terms(['wb_voip'], ['de_DE'], overwrite=True)
        self._grenze(1)
        de = self.env(context=dict(self.env.context, lang='de_DE'))
        with self.assertRaises(UserError) as fehler:
            self.user_b.with_env(de).write({'wb_voip_sip_benutzer': '101'})
        self.assertEqual(str(fehler.exception), f'Ihr WB-VoIP-Paket erlaubt {self.grenze} Benutzer. Bitte Lizenz erweitern.')
