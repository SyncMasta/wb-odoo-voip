import json

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestKontext(WbVoipCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        # C darf Kontakte nur lesen, nicht ändern
        cls.user_c = Users.create({
            'name': 'VoIP Benutzer C', 'login': 'wb_voip_c',
            'email': 'wb_voip_c@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('wb_voip.group_wb_voip_user').id])],
        })
        cls.user_ohne = Users.create({
            'name': 'Ohne VoIP', 'login': 'wb_voip_ohne',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.firma = cls.env['res.partner'].create({'name': 'Firma X', 'is_company': True})
        cls.kontakt = cls.env['res.partner'].create({
            'name': 'Erika Muster', 'parent_id': cls.firma.id,
            'phone': '+49 170 1234567', 'email': 'erika@example.com'})
        # Kontakt einer fremden Firma: für A nicht lesbar (Mehrfirmen-Regel)
        cls.fremdfirma = cls.env['res.company'].create({'name': 'Fremdfirma'})
        cls.fremder_kontakt = cls.env['res.partner'].create({
            'name': 'Geheim', 'company_id': cls.fremdfirma.id})
        # Standardtaste „Neuer Kontakt“ aus dem Weg räumen, damit die Auswahl eindeutig ist
        cls.env.ref('wb_voip.wb_voip_aktion_neuer_kontakt').active = False

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()

    # --- Hilfen ---

    def _anruf(self, benutzer=None, **werte):
        vals = {'user_id': (benutzer or self.user_a).id, 'richtung': 'eingehend',
                'zustand': 'klingelt', 'nummer': '+49 170 1234567',
                'nummer_e164': '+491701234567', 'partner_id': self.kontakt.id}
        vals.update(werte)
        return self.env['wb.voip.anruf'].sudo().create(vals)

    def _aktion(self, **werte):
        vals = {'name': 'Taste', 'art': 'neuer_kontakt', 'ort_anrufer_fenster': True}
        vals.update(werte)
        return self.env['wb.voip.aktion'].create(vals)

    def _protokoll(self, benutzer=None, **werte):
        vals = {'user_id': (benutzer or self.user_a).id, 'partner_id': self.kontakt.id,
                'richtung': 'eingehend', 'ergebnis': 'erreicht', 'dauer_sek': 30}
        vals.update(werte)
        return self.env['wb.voip.protokoll'].sudo().create(vals)

    def _aktivitaet(self, benutzer, res_model, res_id, **werte):
        vals = {
            'res_model_id': self.env['ir.model']._get_id(res_model), 'res_id': res_id,
            'user_id': benutzer.id, 'summary': 'Rückruf',
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
        }
        vals.update(werte)
        return self.env['mail.activity'].sudo().create(vals)

    def _kontext(self, anruf, benutzer=None):
        return anruf.with_user(benutzer or self.user_a).wb_voip_kontext()

    def _ausfuehren(self, anruf, aktion_id, benutzer=None):
        return anruf.with_user(benutzer or self.user_a).wb_voip_aktion_ausfuehren(aktion_id)

    # --- Kontext ---

    def test_kontext_fremder_anruf(self):
        anruf = self._anruf()
        with self.assertRaises(AccessError):
            self._kontext(anruf, self.user_b)

    def test_kontext_ohne_gruppe(self):
        anruf = self._anruf()
        with self.assertRaises(AccessError):
            self._kontext(anruf, self.user_ohne)

    def test_kontext_kontakt(self):
        anruf = self._anruf()
        daten = self._kontext(anruf)
        self.assertEqual(daten['anruf']['id'], anruf.id)
        kontakt = daten['kontakt']
        self.assertEqual(kontakt['id'], self.kontakt.id)
        self.assertEqual(kontakt['name'], 'Erika Muster')
        self.assertEqual(kontakt['firma'], 'Firma X')
        self.assertEqual(kontakt['telefon'], '+49 170 1234567')
        self.assertEqual(kontakt['email'], 'erika@example.com')
        self.assertEqual(kontakt['bild_url'], f'/web/image/res.partner/{self.kontakt.id}/avatar_128')

    def test_kontext_ohne_kontakt(self):
        daten = self._kontext(self._anruf(partner_id=False))
        self.assertIs(daten['kontakt'], False)
        self.assertEqual(daten['protokolle'], [])

    def test_kontext_kontakt_unlesbar(self):
        self._protokoll(partner_id=self.fremder_kontakt.id)
        anruf = self._anruf(partner_id=self.fremder_kontakt.id)
        self.assertFalse(self.fremder_kontakt.with_user(self.user_a).has_access('read'))
        daten = self._kontext(anruf)
        self.assertIs(daten['kontakt'], False)
        self.assertEqual(daten['protokolle'], [])
        self.assertEqual(daten['aufgaben'], [])

    def test_kontext_protokolle(self):
        for i in range(7):
            self._protokoll(beschreibung=f'<p>Gespräch <b>{i}</b></p>' + 'x' * 400)
        fremd = self._protokoll(benutzer=self.user_b)
        anderer = self._protokoll(partner_id=self.firma.id)
        daten = self._kontext(self._anruf())
        protokolle = daten['protokolle']
        self.assertEqual(len(protokolle), 5)
        ids = {p['id'] for p in protokolle}
        self.assertNotIn(fremd.id, ids)
        self.assertNotIn(anderer.id, ids)
        eintrag = protokolle[0]
        self.assertEqual(set(eintrag), {'id', 'datum', 'ergebnis_label', 'dauer_sek',
                                        'beschreibung_text'})
        self.assertEqual(eintrag['ergebnis_label'], 'Erreicht')
        self.assertEqual(eintrag['dauer_sek'], 30)
        self.assertLessEqual(len(eintrag['beschreibung_text']), 300)
        self.assertNotIn('<', eintrag['beschreibung_text'])
        self.assertTrue(eintrag['beschreibung_text'].startswith('Gespräch'))

    def test_kontext_aufgaben(self):
        for _i in range(12):
            self._aktivitaet(self.user_a, 'res.partner', self.kontakt.id)
        fremd = self._aktivitaet(self.user_b, 'res.partner', self.kontakt.id)
        andere = self._aktivitaet(self.user_a, 'res.partner', self.firma.id)
        daten = self._kontext(self._anruf())
        aufgaben = daten['aufgaben']
        self.assertEqual(len(aufgaben), 10)
        ids = {a['id'] for a in aufgaben}
        self.assertNotIn(fremd.id, ids)
        self.assertNotIn(andere.id, ids)
        self.assertEqual(set(aufgaben[0]), {'id', 'titel', 'faellig', 'res_model', 'res_id'})
        self.assertEqual(aufgaben[0]['titel'], 'Rückruf')
        self.assertEqual(aufgaben[0]['res_model'], 'res.partner')

    def test_kontext_aufgaben_am_datensatz(self):
        # verknüpfter Datensatz des Anrufs: hier die Firma
        am_datensatz = self._aktivitaet(self.user_a, 'res.partner', self.firma.id)
        anruf = self._anruf(partner_id=False, res_model='res.partner', res_id=self.firma.id)
        ids = {a['id'] for a in self._kontext(anruf)['aufgaben']}
        self.assertIn(am_datensatz.id, ids)

    def test_kontext_aktionen(self):
        passt = self._aktion(name='Passt', ort_softphone=True)
        self._aktion(name='Falscher Zustand', zustand='laeuft')
        self._aktion(name='Falsche Richtung', richtung='ausgehend')
        self._aktion(name='Gruppe', group_ids=[
            (6, 0, [self.env.ref('wb_voip.group_wb_voip_manager').id])])
        self._aktion(name='Domain', partner_domain="[('is_company', '=', True)]")
        self._aktion(name='Inaktiv', active=False)
        aktionen = self._kontext(self._anruf())['aktionen']
        self.assertEqual([a['name'] for a in aktionen], ['Passt'])
        self.assertEqual(aktionen[0]['id'], passt.id)
        self.assertEqual(aktionen[0]['orte'], ['anrufer_fenster', 'softphone'])
        self.assertNotIn('url_vorlage', aktionen[0])

    # --- Ausführen: Eingaben und Rechte ---

    def test_ausfuehren_eingabe(self):
        anruf = self._anruf()
        aktion = self._aktion()
        for schlecht in ('1', None, 1.0, True, [aktion.id], -1, 0):
            with self.assertRaises(UserError, msg=repr(schlecht)):
                self._ausfuehren(anruf, schlecht)
        with self.assertRaises(UserError):
            self._ausfuehren(anruf, aktion.id + 10_000)
        inaktiv = self._aktion(active=False)
        with self.assertRaises(UserError):
            self._ausfuehren(anruf, inaktiv.id)

    def test_ausfuehren_passt_nicht(self):
        aktion = self._aktion(zustand='laeuft')
        with self.assertRaises(UserError):
            self._ausfuehren(self._anruf(), aktion.id)

    def test_ausfuehren_ohne_lizenz(self):
        self._lizenz_fehlt()
        aktion = self._aktion()
        with self.assertRaises(UserError):
            self._ausfuehren(self._anruf(), aktion.id)

    def test_ausfuehren_ohne_gruppe(self):
        aktion = self._aktion()
        with self.assertRaises(AccessError):
            self._ausfuehren(self._anruf(), aktion.id, self.user_ohne)

    def test_ausfuehren_fremder_anruf(self):
        aktion = self._aktion()
        with self.assertRaises(AccessError):
            self._ausfuehren(self._anruf(), aktion.id, self.user_b)

    # --- Ausführen je Art ---

    def test_neuer_kontakt(self):
        aktion = self._aktion()
        ergebnis = self._ausfuehren(self._anruf(partner_id=False), aktion.id)
        self.assertEqual(ergebnis['type'], 'ir.actions.act_window')
        self.assertEqual(ergebnis['res_model'], 'res.partner')
        self.assertEqual(ergebnis['views'], [[False, 'form']])
        self.assertEqual(ergebnis['context']['default_phone'], '+49 170 1234567')

    def test_fenster(self):
        fenster = self.env['ir.actions.act_window'].create({
            'name': 'Kontakte', 'res_model': 'res.partner', 'view_mode': 'list,form',
            'context': "{'search_default_x': 1}", 'domain': "[('is_company', '=', True)]",
            'help': '<p>geheim</p>'})
        aktion = self._aktion(art='fenster', act_window_id=fenster.id)
        anruf = self._anruf()
        ergebnis = self._ausfuehren(anruf, aktion.id)
        self.assertEqual(ergebnis['type'], 'ir.actions.act_window')
        self.assertEqual(ergebnis['res_model'], 'res.partner')
        self.assertEqual(ergebnis['name'], 'Kontakte')
        self.assertEqual(ergebnis['domain'], "[('is_company', '=', True)]")
        self.assertEqual(ergebnis['context'], {
            'search_default_x': 1, 'default_partner_id': self.kontakt.id,
            'wb_voip_anruf_id': anruf.id})
        self.assertLessEqual(set(ergebnis), {
            'type', 'res_model', 'view_mode', 'views', 'target', 'name', 'domain', 'context'})

    def test_fenster_ohne_leserecht(self):
        fenster = self.env['ir.actions.act_window'].create({
            'name': 'Parameter', 'res_model': 'ir.config_parameter'})
        aktion = self._aktion(art='fenster', act_window_id=fenster.id)
        with self.assertRaises(AccessError):
            self._ausfuehren(self._anruf(), aktion.id)

    def test_fenster_gruppe_fehlt(self):
        fenster = self.env['ir.actions.act_window'].create({
            'name': 'Kontakte', 'res_model': 'res.partner',
            'group_ids': [(6, 0, [self.env.ref('base.group_system').id])]})
        aktion = self._aktion(art='fenster', act_window_id=fenster.id)
        with self.assertRaises(AccessError):
            self._ausfuehren(self._anruf(), aktion.id)

    def _serveraktion_schreiben(self, **werte):
        vals = {
            'name': 'Kontakt ändern', 'model_id': self.env.ref('base.model_res_partner').id,
            'state': 'object_write',
            'update_path': 'function',
            'evaluation_type': 'value', 'value': 'Geändert'}
        vals.update(werte)
        return self.env['ir.actions.server'].create(vals)

    def test_serveraktion_schreibt(self):
        server = self._serveraktion_schreiben()
        aktion = self._aktion(art='serveraktion', server_action_id=server.id)
        self._ausfuehren(self._anruf(), aktion.id)
        self.assertEqual(self.kontakt.function, 'Geändert')

    def test_serveraktion_ohne_schreibrecht(self):
        # Review Focus 1: C darf den Kontakt nicht schreiben -> kein Durchgriff
        server = self._serveraktion_schreiben()
        aktion = self._aktion(art='serveraktion', server_action_id=server.id)
        anruf = self._anruf(benutzer=self.user_c)
        self.assertFalse(self.kontakt.with_user(self.user_c).has_access('write'))
        with self.assertRaises(AccessError):
            self._ausfuehren(anruf, aktion.id, self.user_c)
        self.assertFalse(self.kontakt.function)

    def test_serveraktion_mit_gruppe_ohne_schreibrecht(self):
        # Mit Gruppen prüft Odoo selbst keine Datensatzrechte und schreibt als sudo
        server = self._serveraktion_schreiben(
            group_ids=[(6, 0, [self.env.ref('base.group_user').id])])
        aktion = self._aktion(art='serveraktion', server_action_id=server.id)
        anruf = self._anruf(benutzer=self.user_c)
        with self.assertRaises(AccessError):
            self._ausfuehren(anruf, aktion.id, self.user_c)
        self.assertFalse(self.kontakt.function)

    def test_serveraktion_gruppe_fehlt(self):
        server = self._serveraktion_schreiben(
            group_ids=[(6, 0, [self.env.ref('base.group_system').id])])
        aktion = self._aktion(art='serveraktion', server_action_id=server.id)
        with self.assertRaises(AccessError):
            self._ausfuehren(self._anruf(), aktion.id)
        self.assertFalse(self.kontakt.function)

    def test_serveraktion_am_anruf(self):
        server = self.env['ir.actions.server'].create({
            'name': 'Notiz', 'model_id': self.env.ref('wb_voip.model_wb_voip_anruf').id,
            'state': 'code',
            'code': "record.write({'notiz': 'Kontakt %s' % env.context.get('wb_voip_partner_id')})"})
        aktion = self._aktion(art='serveraktion', server_action_id=server.id)
        anruf = self._anruf()
        self._ausfuehren(anruf, aktion.id)
        self.assertEqual(anruf.notiz, f'Kontakt {self.kontakt.id}')

    def test_serveraktion_fremdes_modell(self):
        server = self.env['ir.actions.server'].create({
            'name': 'Benutzer', 'model_id': self.env.ref('base.model_res_users').id,
            'state': 'code', 'code': 'pass'})
        aktion = self._aktion(art='serveraktion', server_action_id=server.id)
        with self.assertRaises(UserError):
            self._ausfuehren(self._anruf(), aktion.id)

    def test_url_platzhalter_kodiert(self):
        aktion = self._aktion(
            art='url', url_vorlage='https://x.example/suche?q={nummer}&e={nummer_e164}&p={partner_id}')
        ergebnis = self._ausfuehren(self._anruf(), aktion.id)
        self.assertEqual(ergebnis, {
            'type': 'ir.actions.act_url', 'target': 'new',
            'url': 'https://x.example/suche?q=%2B49%20170%201234567&e=%2B491701234567'
                   f'&p={self.kontakt.id}'})

    def test_url_ohne_kontakt_und_boese_nummer(self):
        aktion = self._aktion(art='url', url_vorlage='https://x.example/{nummer}?p={partner_id}')
        ergebnis = self._ausfuehren(
            self._anruf(partner_id=False, nummer='a/../@b?#'), aktion.id)
        self.assertEqual(ergebnis['url'], 'https://x.example/a%2F..%2F%40b%3F%23?p=')

    def test_kontext_melden(self):
        anruf = self._anruf()
        Bus = self.env['bus.bus'].sudo()
        self.env.cr.precommit.run()
        vorher = Bus.search([]).ids
        anruf.with_user(self.user_a)._wb_voip_kontext_melden()
        self.env.cr.precommit.run()
        neu = Bus.search([('id', 'not in', vorher)])
        meldungen = [json.loads(n.message) for n in neu]
        meldungen = [m for m in meldungen if m['type'] == 'wb_voip/kontext']
        self.assertEqual(meldungen, [{'type': 'wb_voip/kontext', 'payload': {'anruf_id': anruf.id}}])
        kanal = json.loads(neu.filtered(lambda n: 'wb_voip/kontext' in n.message).channel)
        self.assertEqual(kanal[1:], ['res.partner', self.user_a.partner_id.id])
