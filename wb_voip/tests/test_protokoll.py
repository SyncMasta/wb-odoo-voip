from datetime import timedelta

from odoo.exceptions import AccessError, UserError
from odoo.tests import freeze_time, tagged

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestProtokoll(WbVoipCase):

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()
        self.kontakt = self.env['res.partner'].create({
            'name': 'Anna Anrufer', 'phone': '+49 170 1234567'})

    def _anruf(self, user=None, sip_call_id='abc', res_model=False, res_id=False,
               richtung='eingehend'):
        Anruf = self.env['wb.voip.anruf'].with_user(user or self.user_a)
        daten = Anruf.wb_voip_beginnen(
            richtung, '+49 170 1234567', sip_call_id, res_model, res_id)
        return Anruf.browse(daten['id'])

    def _fuehren(self, anruf, sekunden=65, notiz=False):
        """Answer, let time pass, end."""
        with freeze_time('2026-11-24 10:00:00') as zeit:
            anruf.wb_voip_angenommen()
            zeit.tick(timedelta(seconds=sekunden))
            return anruf.wb_voip_beendet(notiz, False)

    def test_ein_protokoll_je_anruf(self):
        anruf = self._anruf()
        erst = self._fuehren(anruf)
        zweit = anruf.wb_voip_beendet(False, False)
        self.assertTrue(erst['protokoll_id'])
        self.assertEqual(erst['protokoll_id'], zweit['protokoll_id'])
        self.assertEqual(self.env['wb.voip.protokoll'].search_count(
            [('anruf_id', '=', anruf.id)]), 1)

    def test_ergebnis_aus_zustand(self):
        erreicht = self._fuehren(self._anruf())
        besetzt = self._anruf(sip_call_id='b').wb_voip_beendet(False, '486')
        nie = self._anruf(sip_call_id='c').wb_voip_beendet(False, False)
        Protokoll = self.env['wb.voip.protokoll']
        self.assertEqual(Protokoll.browse(erreicht['protokoll_id']).ergebnis, 'erreicht')
        self.assertEqual(Protokoll.browse(besetzt['protokoll_id']).ergebnis, 'besetzt')
        self.assertEqual(Protokoll.browse(nie['protokoll_id']).ergebnis, 'nicht_erreicht')

    def test_felder_uebernommen(self):
        anruf = self._anruf()
        daten = self._fuehren(anruf)
        protokoll = self.env['wb.voip.protokoll'].browse(daten['protokoll_id'])
        self.assertEqual(protokoll.anruf_id, anruf)
        self.assertEqual(protokoll.user_id, self.user_a)
        self.assertEqual(protokoll.richtung, 'eingehend')
        self.assertEqual(protokoll.dauer_sek, 65)
        self.assertEqual(protokoll.partner_id, self.kontakt)

    def test_notiz_wird_beschreibung(self):
        daten = self._fuehren(self._anruf(), notiz='Rückruf Montag')
        protokoll = self.env['wb.voip.protokoll'].browse(daten['protokoll_id'])
        self.assertIn('Rückruf Montag', protokoll.beschreibung)

    def test_chatter_am_kontakt(self):
        self._fuehren(self._anruf())
        nachricht = self.kontakt.message_ids[:1]
        self.assertIn('Call incoming · reached · 1:05', nachricht.body)
        self.assertEqual(nachricht.subtype_id, self.env.ref('mail.mt_note'))
        self.assertEqual(nachricht.author_id, self.user_a.partner_id)

    def test_chatter_am_verknuepften_datensatz(self):
        anderer = self.env['res.partner'].create({'name': 'Genannt', 'phone': '089 111111'})
        self._fuehren(self._anruf(res_model='res.partner', res_id=anderer.id))
        self.assertIn('Call incoming', anderer.message_ids[:1].body)
        self.assertFalse(self.kontakt.message_ids.filtered(
            lambda m: 'Call incoming' in (m.body or '')))

    def test_kein_chatter_ohne_leserecht_am_kontakt(self):
        # Globale Regel: user_a darf den Kontakt nicht lesen
        self.env['ir.rule'].create({
            'name': 'Test: Kontakt gesperrt', 'model_id': self.env.ref('base.model_res_partner').id,
            'domain_force': "[('id', '!=', %d)]" % self.kontakt.id,
        })
        anruf = self._anruf()
        self.assertFalse(anruf.sudo().partner_id)
        daten = self._fuehren(anruf)
        self.assertTrue(daten['protokoll_id'])
        self.assertFalse(self.kontakt.message_ids.filtered(
            lambda m: 'Call incoming' in (m.body or '')))

    def test_kein_chatter_ohne_leserecht_am_datensatz(self):
        fremd = self.env['wb.voip.anruf'].with_user(self.user_b)
        fremd_anruf = fremd.browse(fremd.wb_voip_beginnen(
            'eingehend', '+49 170 1234567', 'fremd', False, False)['id'])
        anruf = self._anruf(res_model='wb.voip.anruf', res_id=fremd_anruf.id)
        # fremder Anruf nicht lesbar: kein Link darüber, Kontakt nur über die Nummer
        self.assertEqual(anruf.sudo().partner_id, self.kontakt)
        daten = self._fuehren(anruf)
        self.assertTrue(daten['protokoll_id'])
        self.assertFalse(self.env['mail.message'].search_count(
            [('model', '=', 'wb.voip.anruf'), ('res_id', '=', fremd_anruf.id)]))

    def test_ziel_ungueltige_angaben(self):
        Anruf = self.env['wb.voip.anruf'].with_user(self.user_a)
        # erfundener Modellname, Text als ID, flüchtiges Modell, abstraktes Modell, negative ID
        self.assertIsNone(Anruf._wb_voip_ziel('gibt.es.nicht', 1))
        self.assertIsNone(Anruf._wb_voip_ziel('res.partner', '1'))
        self.assertIsNone(Anruf._wb_voip_ziel('res.partner', True))
        self.assertIsNone(Anruf._wb_voip_ziel('res.partner', -1))
        self.assertIsNone(Anruf._wb_voip_ziel('res.partner', 0))
        self.assertIsNone(Anruf._wb_voip_ziel('res.partner', 99999999))
        self.assertIsNone(Anruf._wb_voip_ziel('res.config.settings', 1))
        self.assertIsNone(Anruf._wb_voip_ziel('mail.thread', 1))
        self.assertIsNone(Anruf._wb_voip_ziel(False, False))
        self.assertIsNone(Anruf._wb_voip_ziel(['x'], 1))
        self.assertEqual(Anruf._wb_voip_ziel('res.partner', self.kontakt.id), self.kontakt)

    def test_beginnen_mit_ungueltigem_ziel_ohne_fehler(self):
        for modell, rid in (('gibt.es.nicht', 1), ('res.partner', 99999999),
                            ('res.config.settings', 1), ('ir.config_parameter', 1)):
            daten = self.env['wb.voip.anruf'].with_user(self.user_a).wb_voip_beginnen(
                'eingehend', '+49 170 1234567', f'x-{modell}', modell, rid)
            self.assertEqual(daten['partner'][0], self.kontakt.id)  # nur Nummernsuche

    def test_fremdes_protokoll_unsichtbar(self):
        daten = self._fuehren(self._anruf())
        protokoll = self.env['wb.voip.protokoll'].browse(daten['protokoll_id'])
        self.assertTrue(protokoll.with_user(self.user_a).exists())
        self.assertFalse(self.env['wb.voip.protokoll'].with_user(self.user_b).search(
            [('id', '=', protokoll.id)]))
        with self.assertRaises(AccessError):
            protokoll.with_user(self.user_b).read(['ergebnis'])
        self.assertTrue(self.env['wb.voip.protokoll'].with_user(
            self.user_manager).search([('id', '=', protokoll.id)]))

    def test_serverfelder_nicht_schreibbar_ergebnis_schon(self):
        daten = self._fuehren(self._anruf())
        protokoll = self.env['wb.voip.protokoll'].browse(daten['protokoll_id']).with_user(self.user_a)
        protokoll.write({'ergebnis': 'mailbox', 'beschreibung': '<p>Neu</p>'})
        self.assertEqual(protokoll.ergebnis, 'mailbox')
        for feld, wert in (('user_id', self.user_b.id), ('dauer_sek', 1),
                           ('richtung', 'ausgehend'), ('anruf_id', False),
                           ('datum', '2026-01-01 00:00:00')):
            with self.assertRaises(UserError, msg=feld):
                protokoll.write({feld: wert})

    def test_kein_loeschen_fuer_benutzer(self):
        daten = self._fuehren(self._anruf())
        protokoll = self.env['wb.voip.protokoll'].browse(daten['protokoll_id'])
        with self.assertRaises(AccessError):
            protokoll.with_user(self.user_a).unlink()
        protokoll.with_user(self.user_manager).unlink()

    def test_smart_button_partner(self):
        self._fuehren(self._anruf())
        kontakt = self.kontakt.with_user(self.user_a)
        self.assertEqual(kontakt.wb_voip_protokoll_anzahl, 1)
        aktion = kontakt.action_wb_voip_protokolle()
        self.assertEqual(aktion['res_model'], 'wb.voip.protokoll')
        self.assertEqual(
            self.env['wb.voip.protokoll'].with_user(self.user_a).search_count(aktion['domain']), 1)
        self.assertEqual(self.kontakt.with_user(self.user_b).wb_voip_protokoll_anzahl, 0)

    def test_popup_ansicht_vorhanden(self):
        ansicht = self.env.ref('wb_voip.wb_voip_protokoll_view_form_popup')
        self.assertEqual(ansicht.model, 'wb.voip.protokoll')

    def test_fremde_anruf_id_nicht_unterschiebbar(self):
        anruf = self._anruf()
        Protokoll = self.env['wb.voip.protokoll'].with_user(self.user_b)
        with self.assertRaises(UserError):
            Protokoll.create({'anruf_id': anruf.id})
        gefaelscht = Protokoll.with_context(default_anruf_id=anruf.id).create({})
        self.assertFalse(gefaelscht.anruf_id)
        daten = self._fuehren(anruf)
        self.assertTrue(daten['protokoll_id'])
        self.assertEqual(self.env['wb.voip.protokoll'].search_count(
            [('anruf_id', '=', anruf.id)]), 1)

    def test_create_default_kontext_faelscht_nicht(self):
        protokoll = self.env['wb.voip.protokoll'].with_user(self.user_a).with_context(
            default_richtung='ausgehend', default_dauer_sek=9999,
            default_user_id=self.user_b.id).create({'ergebnis': 'mailbox'})
        self.assertFalse(protokoll.richtung)
        self.assertEqual(protokoll.dauer_sek, 0)
        self.assertEqual(protokoll.user_id, self.user_a)

    def test_vorhandenes_protokoll_wird_wiederverwendet(self):
        anruf = self._anruf()
        vorab = self.env['wb.voip.protokoll'].sudo().create(
            {'anruf_id': anruf.id, 'user_id': self.user_a.id})
        daten = self._fuehren(anruf)
        self.assertEqual(daten['protokoll_id'], vorab.id)
