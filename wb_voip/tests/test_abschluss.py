"""Final fixes of phase 1: ICE servers, cron log, task limits, race on answer, search, noupdate."""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools.convert import convert_file

from odoo.addons.wb_voip.models import mail_activity

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestAbschluss(WbVoipCase):
    """Fixes from the final review of phase 1."""

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()

    def _anruf(self, sip_call_id='ab1', nummer='+49 170 7654321'):
        daten = self.env['wb.voip.anruf'].with_user(self.user_a).wb_voip_beginnen(
            'ausgehend', nummer, sip_call_id, False, False)
        return self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])

    # --- 1. Keine Daten an fremde STUN-Server ---

    def test_ice_server_standard_leer(self):
        self.assertFalse(self.anlage.ice_server_urls)
        zugang = self.user_a.with_user(self.user_a)._wb_voip_zugang()
        self.assertEqual(zugang['ice_server_urls'], [])

    def test_ice_server_aus_anlage(self):
        self.anlage.ice_server_urls = (
            " stun:stun.example.org:3478 \n\nstun:stun2.example.org\n")
        zugang = self.user_a.with_user(self.user_a)._wb_voip_zugang()
        self.assertEqual(zugang['ice_server_urls'], [
            'stun:stun.example.org:3478', 'stun:stun2.example.org'])

    def test_ice_server_nur_stun(self):
        for falsch in ('turn:turn.example.org:3478', 'turns:turn.example.org:5349',
                       'stun:stun.example.org\nturns:turn.example.org:5349',
                       'https://stun.example.org', 'javascript:alert(1)', 'stun:a b',
                       'stun:', 'stun.example.org:3478', 'stun:' + 'a' * 300,
                       '\n'.join(f'stun:s{i}.example.org' for i in range(11))):
            with self.assertRaises(ValidationError, msg=falsch[:30]), self.env.cr.savepoint():
                self.anlage.ice_server_urls = falsch
                self.anlage.flush_recordset()
        for richtig in ('STUN:stun.example.org', 'stun:stun.example.org:3478', False, ''):
            self.anlage.ice_server_urls = richtig
            self.anlage.flush_recordset()

    # --- 3. Aufräum-Cron protokolliert ---

    def test_aufraeumen_protokolliert(self):
        jetzt = fields.Datetime.now()
        Anruf = self.env['wb.voip.anruf'].sudo()
        ohne = Anruf.create({
            'user_id': self.user_a.id, 'richtung': 'eingehend', 'nummer': '+49 170 7654321',
            'sip_call_id': 'cron-ohne', 'zustand': 'klingelt',
            'beginn': jetzt - timedelta(hours=5),
            'zuletzt_gesehen': jetzt - timedelta(hours=5)})
        mit = Anruf.create({
            'user_id': self.user_a.id, 'richtung': 'ausgehend', 'nummer': '+49 170 7654321',
            'sip_call_id': 'cron-mit', 'zustand': 'laeuft',
            'beginn': jetzt - timedelta(hours=6),
            'annahme': jetzt - timedelta(hours=6) + timedelta(seconds=10),
            'zuletzt_gesehen': jetzt - timedelta(hours=5)})
        Anruf._wb_voip_aufraeumen()
        self.assertEqual(ohne.zustand, 'fehlgeschlagen')
        self.assertTrue(ohne.protokoll_id)
        self.assertEqual(ohne.protokoll_id.ergebnis, 'nicht_erreicht')
        self.assertEqual(mit.zustand, 'beendet')
        self.assertTrue(mit.protokoll_id)
        self.assertEqual(mit.protokoll_id.ergebnis, 'erreicht')
        # Dauer bis zum letzten Lebenszeichen, nicht bis zum Lauf des Crons
        self.assertEqual(mit.dauer_sek, 3600 - 10)
        # zweiter Lauf: nichts doppelt
        Anruf._wb_voip_aufraeumen()
        self.assertEqual(self.env['wb.voip.protokoll'].sudo().search_count(
            [('anruf_id', 'in', (ohne + mit).ids)]), 2)

    # --- 5. Obergrenze für wb_voip_aufgaben ---

    def test_aufgaben_obergrenze(self):
        ohne_nummer = self.env['res.partner'].create({'name': 'Grenze ohne Nummer'})
        mit_nummer = self.env['res.partner'].create({
            'name': 'Grenze mit Nummer', 'phone': '+49 170 3334445'})
        heute = fields.Date.today()
        for tage in range(3):
            ohne_nummer.activity_schedule(
                'mail.mail_activity_data_todo', user_id=self.user_a.id,
                date_deadline=heute + timedelta(days=tage), summary=f'leer {tage}')
        ziel = mit_nummer.activity_schedule(
            'mail.mail_activity_data_todo', user_id=self.user_a.id,
            date_deadline=heute + timedelta(days=10), summary='mit Nummer')
        Activity = self.env['mail.activity'].with_user(self.user_a)
        with patch.object(mail_activity, 'AUFGABEN_MIN_STAPEL', 2):
            self.assertEqual([a['id'] for a in Activity.wb_voip_aufgaben(limit=1)], [ziel.id])
            with patch.object(mail_activity, 'AUFGABEN_MAX_STAPEL', 1):
                self.assertEqual(Activity.wb_voip_aufgaben(limit=1), [])
            with patch.object(mail_activity, 'AUFGABEN_MAX_GELESEN', 3):
                self.assertEqual(Activity.wb_voip_aufgaben(limit=1), [])

    def test_aufgaben_obergrenzen_werte(self):
        self.assertEqual(mail_activity.AUFGABEN_MAX_STAPEL, 10)
        self.assertEqual(mail_activity.AUFGABEN_MAX_GELESEN, 2000)

    def test_aufgaben_nur_modelle_mit_kontakt(self):
        modelle = self.env['mail.activity'].with_user(self.user_a)._wb_voip_aufgaben_modelle()
        self.assertIn('res.partner', modelle)
        self.assertIn('res.users', modelle)  # partner_id -> res.partner
        self.assertNotIn('res.groups', modelle)
        self.assertNotIn('mail.activity', modelle)

    # --- 6. Angenommen beim Wettlauf ---

    def test_beendet_mit_angenommen_ohne_annahme(self):
        anruf = self._anruf('ab-wettlauf')
        daten = anruf.wb_voip_beendet(False, 'bye', angenommen=True)
        self.assertEqual(daten['zustand'], 'beendet')
        self.assertTrue(anruf.sudo().annahme)
        self.assertEqual(anruf.sudo().dauer_sek, 0)
        protokoll = self.env['wb.voip.protokoll'].browse(daten['protokoll_id'])
        self.assertEqual(protokoll.ergebnis, 'erreicht')

    def test_beendet_angenommen_pruefen(self):
        anruf = self._anruf('ab-pruefen')
        for falsch in ('ja', 1, None, [True]):
            with self.assertRaises(UserError, msg=repr(falsch)):
                anruf.wb_voip_beendet(False, False, angenommen=falsch)
        self.assertEqual(anruf.sudo().zustand, 'klingelt')
        daten = anruf.wb_voip_beendet(False, '603', angenommen=False)
        self.assertEqual(daten['zustand'], 'abgelehnt')

    def test_beendet_angenommen_vorhandene_annahme_bleibt(self):
        anruf = self._anruf('ab-vorhanden')
        anruf.wb_voip_angenommen()
        annahme = anruf.sudo().annahme
        anruf.wb_voip_beendet(False, 'bye', angenommen=True)
        self.assertEqual(anruf.sudo().annahme, annahme)
        self.assertEqual(anruf.sudo().zustand, 'beendet')

    # --- 12. Suche nach „000“ ---

    def test_suche_nur_nullen(self):
        partner = self.env['res.partner'].create({
            'name': 'Nullsuche Kontakt', 'phone': '+49 170 5556667'})
        Partner = self.env['res.partner'].with_user(self.user_a)
        for text in ('000', '0 0 0 0', '00012'):
            ids = {k['id'] for k in Partner.wb_voip_kontakte_suchen(text)}
            self.assertNotIn(partner.id, ids, text)
        ids = {k['id'] for k in Partner.wb_voip_kontakte_suchen('0170 5556667')}
        self.assertIn(partner.id, ids)
        ids = {k['id'] for k in Partner.wb_voip_kontakte_suchen('0555')}
        self.assertIn(partner.id, ids)

    # --- 9. Updates überschreiben keine Admin-Änderungen ---

    def test_cron_bei_update_nicht_ueberschrieben(self):
        cron = self.env.ref('wb_voip.ir_cron_voip_aufraeumen')
        cron.write({'interval_number': 3, 'active': False})
        convert_file(self.env, 'wb_voip', 'data/ir_cron.xml', {}, mode='update')
        self.assertEqual(cron.interval_number, 3)
        self.assertFalse(cron.active)

    def test_admin_gruppe_bei_update_nicht_ueberschrieben(self):
        gruppe = self.env.ref('wb_voip.group_wb_voip_manager')
        admin = self.env.ref('base.user_admin')
        admin.write({'group_ids': [(3, gruppe.id)]})
        self.assertNotIn(gruppe, admin.group_ids)
        convert_file(self.env, 'wb_voip', 'security/wb_voip_security.xml', {}, mode='update')
        self.assertNotIn(gruppe, admin.group_ids)
        # Name der Gruppe wird bei Updates weiter gepflegt
        self.assertFalse(self.env['ir.model.data'].search([
            ('module', '=', 'wb_voip'), ('name', '=', 'group_wb_voip_manager')]).noupdate)
