import os
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet

from odoo.tests import HttpCase, tagged


@tagged('wb_voip', 'post_install', '-at_install')
class TestTourSoftphone(HttpCase):
    """Dial from a contact's phone field, write a note, hang up, see the call listed."""

    def setUp(self):
        super().setUp()
        schluessel = patch.dict(os.environ, {'WB_VOIP_FERNET_KEY': Fernet.generate_key().decode()})
        schluessel.start()
        self.addCleanup(schluessel.stop)
        lizenz = patch.object(
            type(self.env['wb.license.client']), 'check_license',
            return_value=SimpleNamespace(is_valid=True, user_message=''))
        lizenz.start()
        self.addCleanup(lizenz.stop)

        anlage = self.env['wb.voip.anlage'].create({
            'name': 'Touranlage', 'modus': 'test',
            'ws_url': 'wss://tour.test/ws', 'sip_domain': 'tour.test',
        })
        self.admin = self.env.ref('base.user_admin')
        self.admin.write({
            'wb_voip_anlage_id': anlage.id,
            'wb_voip_sip_benutzer': '900',
            'wb_voip_sip_passwort': 'tour',
        })
        self.kontakt = self.env['res.partner'].create({
            'name': 'Tour Kontakt', 'phone': '+49 170 2223334'})

    def test_tour_softphone(self):
        self.start_tour(f'/odoo/res.partner/{self.kontakt.id}', 'wb_voip_softphone_tour',
                        login='admin', timeout=120)
        anruf = self.env['wb.voip.anruf'].search([
            ('user_id', '=', self.admin.id), ('partner_id', '=', self.kontakt.id)])
        self.assertEqual(len(anruf), 1)
        self.assertEqual(anruf.richtung, 'ausgehend')
        self.assertEqual(anruf.nummer, '+49 170 2223334')
        self.assertEqual((anruf.res_model, anruf.res_id), ('res.partner', self.kontakt.id))
        self.assertEqual(anruf.zustand, 'beendet')
        self.assertEqual(anruf.notiz, 'Tourtest')
        self.assertTrue(anruf.protokoll_id)
        # im Popup auf „Mailbox“ geändert und gespeichert
        self.assertEqual(anruf.protokoll_id.ergebnis, 'mailbox')
