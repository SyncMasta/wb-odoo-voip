from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import freeze_time, tagged
from odoo.tools import mute_logger

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestAnruf(WbVoipCase):

    def _beginnen(self, user=None, sip_call_id='abc', richtung='eingehend'):
        Anruf = self.env['wb.voip.anruf'].with_user(user or self.user_a)
        return Anruf.wb_voip_beginnen(richtung, '+49 170 1234567', sip_call_id, False, False)

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()

    def test_beginnen_idempotent(self):
        erster = self._beginnen()
        zweiter = self._beginnen()
        self.assertEqual(erster['id'], zweiter['id'])
        self.assertEqual(erster['zustand'], 'klingelt')
        self.assertEqual(self.env['wb.voip.anruf'].search_count([('sip_call_id', '=', 'abc')]), 1)

    def test_ablauf_angenommen_beendet(self):
        with freeze_time('2026-11-24 10:00:00') as zeit:
            daten = self._beginnen()
            anruf = self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])
            self.assertEqual(anruf.wb_voip_angenommen()['zustand'], 'laeuft')
            zeit.tick(timedelta(seconds=65))
            ergebnis = anruf.wb_voip_beendet('Notiz', False)
        self.assertEqual(ergebnis['zustand'], 'beendet')
        self.assertEqual(ergebnis['dauer_sek'], 65)
        self.assertEqual(anruf.sudo().notiz, 'Notiz')

    def test_beendet_zweimal_noop(self):
        daten = self._beginnen()
        anruf = self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])
        erst = anruf.wb_voip_beendet(False, '603')
        zweit = anruf.wb_voip_beendet(False, '486')
        self.assertEqual(erst, zweit)
        self.assertEqual(zweit['zustand'], 'abgelehnt')

    def test_ablehnen_603(self):
        daten = self._beginnen()
        anruf = self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])
        self.assertEqual(anruf.wb_voip_beendet(False, '603')['zustand'], 'abgelehnt')
        daten = self._beginnen(sip_call_id='def')
        anruf = self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])
        self.assertEqual(anruf.wb_voip_beendet(False, '486')['zustand'], 'besetzt')

    def test_ablehnen_480(self):
        # Manche Telefonanlagen melden die Ablehnung des Gerufenen als 480 statt 603
        daten = self._beginnen()
        anruf = self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])
        ergebnis = anruf.wb_voip_beendet(False, '480')
        self.assertEqual(ergebnis['zustand'], 'abgelehnt')
        protokoll = self.env['wb.voip.protokoll'].browse(ergebnis['protokoll_id'])
        self.assertEqual(protokoll.ergebnis, 'nicht_erreicht')

    def test_ohne_grund_fehlgeschlagen(self):
        daten = self._beginnen()
        anruf = self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])
        self.assertEqual(anruf.wb_voip_beendet(False, False)['zustand'], 'fehlgeschlagen')

    def test_fremder_anruf_unsichtbar(self):
        daten = self._beginnen()
        Anruf_b = self.env['wb.voip.anruf'].with_user(self.user_b)
        self.assertFalse(Anruf_b.search([]))
        with self.assertRaises(AccessError):
            Anruf_b.browse(daten['id']).read(['zustand'])
        with self.assertRaises(AccessError):
            Anruf_b.browse(daten['id']).wb_voip_beendet(False, False)

    def test_gleiche_call_id_je_benutzer(self):
        a = self._beginnen(self.user_a)
        b = self._beginnen(self.user_b)
        self.assertNotEqual(a['id'], b['id'])

    def test_serverfeld_nicht_schreibbar(self):
        daten = self._beginnen()
        anruf = self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])
        with self.assertRaises(UserError):
            anruf.write({'zustand': 'beendet'})
        with self.assertRaises(UserError):
            anruf.write({'sip_call_id': 'x'})
        with self.assertRaises(UserError):
            self.env['wb.voip.anruf'].with_user(self.user_a).create({
                'user_id': self.user_b.id, 'richtung': 'eingehend', 'nummer': '1'})

    def test_beginnen_ohne_lizenz_beenden_geht(self):
        daten = self._beginnen()
        anruf = self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])
        self._lizenz_fehlt()
        with self.assertRaises(UserError):
            self._beginnen(sip_call_id='neu')
        self.assertEqual(anruf.wb_voip_beendet(False, False)['zustand'], 'fehlgeschlagen')

    def test_client_daten(self):
        partner = self.env['res.partner'].create({'name': 'Kunde'})
        anruf = self.env['wb.voip.anruf'].with_user(self.user_a).wb_voip_beginnen(
            'ausgehend', '0170 1234567', 'cd1', 'res.partner', partner.id)
        self.assertEqual(
            set(anruf), {'id', 'richtung', 'nummer', 'zustand', 'partner', 'res_model',
                         'res_id', 'beginn', 'dauer_sek', 'protokoll_id'})
        self.assertEqual(anruf['res_id'], partner.id)

    def test_aufraeumen_nach_4h(self):
        jetzt = fields.Datetime.now()
        alt = self.env['wb.voip.anruf'].sudo().create({
            'user_id': self.user_a.id, 'richtung': 'eingehend', 'nummer': '1',
            'sip_call_id': 'alt', 'zustand': 'laeuft',
            'zuletzt_gesehen': jetzt - timedelta(hours=4, minutes=1)})
        jung = self.env['wb.voip.anruf'].sudo().create({
            'user_id': self.user_a.id, 'richtung': 'eingehend', 'nummer': '1',
            'sip_call_id': 'jung', 'zustand': 'klingelt',
            'zuletzt_gesehen': jetzt - timedelta(hours=3, minutes=59)})
        self.env['wb.voip.anruf']._wb_voip_aufraeumen()
        self.assertEqual(alt.zustand, 'fehlgeschlagen')
        self.assertEqual(jung.zustand, 'klingelt')

    def test_lebenszeichen(self):
        with freeze_time('2026-11-24 10:00:00') as zeit:
            daten = self._beginnen()
            anruf = self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])
            zeit.tick(timedelta(minutes=10))
            anruf.wb_voip_lebenszeichen()
            self.assertEqual(anruf.sudo().zuletzt_gesehen, fields.Datetime.now())

    def test_default_kontext_umgeht_sperre_nicht(self):
        Anruf = self.env['wb.voip.anruf'].with_user(self.user_a).with_context(
            default_zustand='beendet', default_sip_call_id='fake', default_dauer_sek=99,
            default_richtung='ausgehend')
        # richtung (Pflicht) wird aus dem Default entfernt -> NOT NULL schlägt an;
        # gelänge das Anlegen, dürften die gefälschten Werte nicht gespeichert sein.
        try:
            with mute_logger('odoo.sql_db'), self.env.cr.savepoint():
                anruf = Anruf.create({'nummer': '1'})
        except Exception:  # noqa: BLE001
            return
        self.assertNotEqual(anruf.sudo().zustand, 'beendet')
        self.assertFalse(anruf.sudo().sip_call_id)
        self.assertEqual(anruf.sudo().dauer_sek, 0)

    def test_beginnen_pflichtwerte(self):
        Anruf = self.env['wb.voip.anruf'].with_user(self.user_a)
        with self.assertRaises(UserError):
            Anruf.wb_voip_beginnen('eingehend', '1', False, False, False)
        with self.assertRaises(UserError):
            Anruf.wb_voip_beginnen('eingehend', '1', '  ', False, False)
        with self.assertRaises(UserError):
            Anruf.wb_voip_beginnen('eingehend', '', 'x1', False, False)

    def test_partner_nicht_schreibbar(self):
        daten = self._beginnen()
        partner = self.env['res.partner'].create({'name': 'P'})
        anruf = self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])
        with self.assertRaises(UserError):
            anruf.write({'partner_id': partner.id})
        with self.assertRaises(UserError):
            anruf.write({'res_model': 'res.partner'})
