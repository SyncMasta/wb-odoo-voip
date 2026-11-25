from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.wb_voip.tools import nummer as nummer_modul
from odoo.addons.wb_voip.tools.nummer import e164

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestNummer(WbVoipCase):

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()

    def _anruf(self, nummer, res_model=False, res_id=False, sip='sip-1'):
        daten = self.env['wb.voip.anruf'].with_user(self.user_a).wb_voip_beginnen(
            'eingehend', nummer, sip, res_model, res_id)
        return self.env['wb.voip.anruf'].browse(daten['id'])

    def test_e164_varianten(self):
        for roh in ('0170 1234567', '+49 (170) 123-4567', '0049 170 1234567'):
            self.assertEqual(e164(roh), '+491701234567', roh)
        self.assertIs(e164('abc'), False)
        self.assertIs(e164(''), False)
        self.assertIs(e164('100'), False)

    def test_ohne_phonenumbers_keine_nummer(self):
        """Without phonenumbers, Odoo returns the raw input: fail closed instead."""
        self.assertEqual(e164('0170 1234567'), '+491701234567')
        with patch.object(nummer_modul, 'phonenumbers', None):
            self.assertIs(e164('0170 1234567'), False)
            self.assertIs(e164('+49 170 1234567'), False)

    def test_kontakt_eindeutig(self):
        kontakt = self.env['res.partner'].create({
            'name': 'Eindeutig', 'phone': '0170 1234567'})
        self.assertEqual(kontakt.wb_voip_nummern_e164, '+491701234567')
        anruf = self._anruf('+491701234567')
        self.assertEqual(anruf.partner_id, kontakt)

    def test_kontakt_doppelt_keine_zuordnung(self):
        self.env['res.partner'].create({'name': 'Doppelt 1', 'phone': '0170 1234567'})
        self.env['res.partner'].create({'name': 'Doppelt 2', 'phone': '+49 170 1234567'})
        anruf = self._anruf('+491701234567')
        self.assertFalse(anruf.partner_id)

    def test_neuberechnung_bei_aenderung(self):
        kontakt = self.env['res.partner'].create({
            'name': 'Wechsel', 'phone': '0170 1234567'})
        kontakt.phone = '0421 123456'
        self.assertEqual(kontakt.wb_voip_nummern_e164, '+49421123456')
        anruf = self._anruf('+491701234567')
        self.assertFalse(anruf.partner_id)

    def test_exakter_abgleich_kein_teilstueck(self):
        self.env['res.partner'].create({'name': 'Lang', 'phone': '0170 12345678'})
        self.assertFalse(self.env['res.partner']._wb_voip_finden('+491701234567'))

    def test_ungueltige_nummer_ohne_e164(self):
        anruf = self._anruf('100')
        self.assertFalse(anruf.nummer_e164)
        self.assertFalse(anruf.partner_id)

    def test_vorrang_res_model_partner(self):
        genannt = self.env['res.partner'].create({'name': 'Genannt', 'phone': '089 111111'})
        self.env['res.partner'].create({'name': 'Nummer', 'phone': '0170 1234567'})
        anruf = self._anruf('+491701234567', 'res.partner', genannt.id)
        self.assertEqual(anruf.partner_id, genannt)

    def test_vorrang_datensatz_mit_partner(self):
        genannt = self.env['res.partner'].create({'name': 'Am Benutzer'})
        self.env['res.partner'].create({'name': 'Nummer', 'phone': '0170 1234567'})
        anruf = self._anruf('+491701234567', 'res.users', self.user_b.id)
        self.assertEqual(anruf.partner_id, self.user_b.partner_id)
        self.assertNotEqual(anruf.partner_id, genannt)

    def test_archivierter_kontakt_ignoriert(self):
        kontakt = self.env['res.partner'].create({'name': 'Alt', 'phone': '0170 1234567'})
        kontakt.active = False
        self.assertFalse(self._anruf('+491701234567').partner_id)
