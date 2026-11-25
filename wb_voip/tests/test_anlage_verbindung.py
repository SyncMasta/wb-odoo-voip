from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import WbVoipCase

MELDUNG = "Verbindungsdaten der Anlage darf nur die Systemverwaltung ändern."


@tagged('wb_voip', 'post_install', '-at_install')
class TestAnlageVerbindung(WbVoipCase):
    """Connection data of a PBX may only be changed by the system administration."""

    def _system(self):
        return self.env.ref('base.user_admin')

    def test_manager_darf_verbindungsdaten_nicht_aendern(self):
        manager = self.anlage.with_user(self.user_manager)
        for feld, wert in (('ws_url', 'wss://fremd.example.invalid/ws'),
                           ('sip_domain', 'fremd.example.invalid'),
                           ('ice_server_urls', 'stun:fremd.example.invalid:3478')):
            with self.subTest(feld=feld), self.assertRaises(AccessError) as fehler:
                manager.write({feld: wert})
            self.assertEqual(str(fehler.exception), MELDUNG)
        self.assertEqual(self.anlage.ws_url, 'wss://anlage.test/ws')

    def test_manager_darf_keine_anlage_mit_verbindung_anlegen(self):
        with self.assertRaises(AccessError):
            self.env['wb.voip.anlage'].with_user(self.user_manager).create({
                'name': 'X', 'ws_url': 'wss://x.test/ws', 'sip_domain': 'x.test'})

    def test_manager_darf_uebrige_felder_aendern(self):
        self.anlage.with_user(self.user_manager).write({'name': 'Umbenannt', 'heranholen_praefix': '**'})
        self.assertEqual(self.anlage.name, 'Umbenannt')

    def test_system_darf(self):
        self.anlage.with_user(self._system()).write({'ws_url': 'wss://neu.test/ws'})
        self.assertEqual(self.anlage.ws_url, 'wss://neu.test/ws')

    def test_voip_benutzer_liest_weiter(self):
        daten = self.anlage.with_user(self.user_a).read(['ws_url', 'sip_domain', 'ice_server_urls'])[0]
        self.assertEqual(daten['ws_url'], 'wss://anlage.test/ws')
        self.assertEqual(daten['sip_domain'], 'anlage.test')
        with self.assertRaises(AccessError):
            self.anlage.with_user(self.user_a).write({'ws_url': 'wss://fremd.example.invalid/ws'})

    def test_sperre_fuer_ansicht(self):
        self.assertTrue(self.anlage.with_user(self.user_manager).verbindung_gesperrt)
        self.assertFalse(self.anlage.with_user(self._system()).verbindung_gesperrt)
