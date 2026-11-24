import json

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestZuordnen(WbVoipCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        # C darf Kontakte nur lesen, nicht ändern
        cls.user_c = Users.create({
            'name': 'VoIP Benutzer C', 'login': 'wb_voip_zuordnen_c',
            'email': 'wb_voip_zuordnen_c@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('wb_voip.group_wb_voip_user').id])],
        })
        cls.user_ohne = Users.create({
            'name': 'Ohne VoIP', 'login': 'wb_voip_zuordnen_ohne',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.kontakt = cls.env['res.partner'].create({'name': 'Erika Muster'})
        cls.fremdfirma = cls.env['res.company'].create({'name': 'Fremdfirma'})
        cls.fremder_kontakt = cls.env['res.partner'].create({
            'name': 'Geheim', 'company_id': cls.fremdfirma.id})

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()

    # --- Hilfen ---

    def _anruf(self, benutzer=None, **werte):
        vals = {'user_id': (benutzer or self.user_a).id, 'richtung': 'eingehend',
                'zustand': 'beendet', 'nummer': '0170 / 1234567',
                'nummer_e164': '+491701234567'}
        vals.update(werte)
        return self.env['wb.voip.anruf'].sudo().create(vals)

    def _protokoll(self, anruf):
        protokoll = self.env['wb.voip.protokoll'].sudo().create({
            'anruf_id': anruf.id, 'user_id': anruf.user_id.id, 'richtung': 'eingehend'})
        anruf.protokoll_id = protokoll
        return protokoll

    def _assistent(self, anruf, partner, benutzer=None, **werte):
        vals = {'anruf_id': anruf.id, 'partner_id': partner.id, 'feld': 'phone'}
        vals.update(werte)
        return self.env['wb.voip.zuordnen'].with_user(benutzer or self.user_a).create(vals)

    def _bus_meldungen(self, aktion):
        Bus = self.env['bus.bus'].sudo()
        self.env.cr.precommit.run()
        vorher = Bus.search([]).ids
        aktion()
        self.env.cr.precommit.run()
        neu = Bus.search([('id', 'not in', vorher)])
        return [json.loads(n.message) for n in neu
                if json.loads(n.message)['type'] == 'wb_voip/kontext']

    # --- Assistent öffnen ---

    def test_aktion_oeffnet_assistent(self):
        anruf = self._anruf()
        aktion = anruf.with_user(self.user_a).action_wb_voip_zuordnen()
        self.assertEqual(aktion['res_model'], 'wb.voip.zuordnen')
        self.assertEqual(aktion['target'], 'new')
        self.assertEqual(aktion['context']['default_anruf_id'], anruf.id)

    def test_aktion_fremder_anruf(self):
        with self.assertRaises(UserError):
            self._anruf(self.user_b).with_user(self.user_a).action_wb_voip_zuordnen()

    def test_aktion_ohne_voip_gruppe(self):
        with self.assertRaises(AccessError):
            self._anruf().with_user(self.user_ohne).action_wb_voip_zuordnen()

    def test_assistent_vorbelegung_und_nummer(self):
        anruf = self._anruf()
        Assistent = self.env['wb.voip.zuordnen'].with_user(self.user_a)
        vorgabe = Assistent.with_context(default_anruf_id=anruf.id).default_get(['anruf_id'])
        self.assertEqual(vorgabe['anruf_id'], anruf.id)
        self.assertEqual(self._assistent(anruf, self.kontakt).nummer, '0170 / 1234567')

    # --- Eigentümerprüfung ---

    def test_anlegen_fremder_anruf(self):
        fremd = self._anruf(self.user_b)
        with self.assertRaises(UserError):
            self._assistent(fremd, self.kontakt)

    def test_default_fremder_anruf(self):
        fremd = self._anruf(self.user_b)
        Assistent = self.env['wb.voip.zuordnen'].with_user(self.user_a)
        with self.assertRaises(UserError):
            Assistent.with_context(default_anruf_id=fremd.id).default_get(['anruf_id'])

    def test_zuordnen_fremder_anruf_nachtraeglich(self):
        """Wizard owned by A, anruf_id rewritten to a foreign call: still refused."""
        eigener = self._anruf()
        fremd = self._anruf(self.user_b)
        assistent = self._assistent(eigener, self.kontakt)
        assistent.sudo().anruf_id = fremd
        with self.assertRaises(UserError):
            assistent.action_zuordnen()
        self.assertFalse(fremd.partner_id)
        self.assertFalse(self.kontakt.phone)

    def test_zuordnen_ohne_voip_gruppe(self):
        assistent = self._assistent(self._anruf(), self.kontakt)
        assistent.sudo().create_uid = self.user_ohne
        with self.assertRaises(UserError):
            assistent.with_user(self.user_ohne).action_zuordnen()

    def test_zuordnen_ohne_lizenz(self):
        self._lizenz_fehlt()
        assistent = self._assistent(self._anruf(), self.kontakt)
        with self.assertRaises(UserError):
            assistent.action_zuordnen()

    # --- Zuordnung und Nummer ---

    def test_leeres_feld_wird_gespeichert_wie_eingegeben(self):
        anruf = self._anruf()
        protokoll = self._protokoll(anruf)
        ergebnis = self._assistent(anruf, self.kontakt).action_zuordnen()
        self.assertEqual(ergebnis, {'type': 'ir.actions.act_window_close'})
        self.assertEqual(anruf.partner_id, self.kontakt)
        self.assertEqual(protokoll.partner_id, self.kontakt)
        self.assertEqual(self.kontakt.phone, '0170 / 1234567')
        # die normalisierte Form wird nachgeführt
        self.assertIn('+491701234567', self.kontakt.wb_voip_nummern_e164)

    def test_zuordnen_ohne_protokoll(self):
        anruf = self._anruf()
        self._assistent(anruf, self.kontakt).action_zuordnen()
        self.assertEqual(anruf.partner_id, self.kontakt)

    def test_belegtes_feld_ohne_ersetzen(self):
        self.kontakt.phone = '+49 30 111'
        anruf = self._anruf()
        assistent = self._assistent(anruf, self.kontakt)
        with self.assertRaises(UserError):
            assistent.action_zuordnen()
        self.assertEqual(self.kontakt.phone, '+49 30 111')
        self.assertFalse(anruf.partner_id)

    def test_belegtes_feld_mit_ersetzen(self):
        self.kontakt.phone = '+49 30 111'
        anruf = self._anruf()
        self._assistent(anruf, self.kontakt, ersetzen=True).action_zuordnen()
        self.assertEqual(self.kontakt.phone, '0170 / 1234567')
        self.assertEqual(anruf.partner_id, self.kontakt)

    def test_nicht_speichern(self):
        self.kontakt.phone = '+49 30 111'
        anruf = self._anruf()
        protokoll = self._protokoll(anruf)
        self._assistent(anruf, self.kontakt, feld='nicht_speichern').action_zuordnen()
        self.assertEqual(self.kontakt.phone, '+49 30 111')
        self.assertEqual(anruf.partner_id, self.kontakt)
        self.assertEqual(protokoll.partner_id, self.kontakt)

    def test_felder_auswahl(self):
        schluessel = [k for k, _ in self.env['wb.voip.zuordnen']._selection_feld()]
        self.assertEqual(schluessel, ['phone', 'nicht_speichern'])

    # --- Kontakt nur lesbar / nicht lesbar ---

    def test_kontakt_nur_lesbar(self):
        anruf = self._anruf(self.user_c)
        protokoll = self._protokoll(anruf)
        ergebnis = self._assistent(anruf, self.kontakt, self.user_c).action_zuordnen()
        self.assertEqual(anruf.partner_id, self.kontakt)
        self.assertEqual(protokoll.partner_id, self.kontakt)
        self.assertFalse(self.kontakt.phone)
        self.assertEqual(ergebnis['type'], 'ir.actions.client')
        self.assertEqual(ergebnis['tag'], 'display_notification')
        self.assertEqual(ergebnis['params']['type'], 'warning')
        self.assertEqual(
            ergebnis['params']['message'],
            'Zugeordnet. Die Nummer wurde nicht am Kontakt gespeichert (keine Schreibrechte).')

    def test_kontakt_nur_lesbar_nicht_speichern(self):
        anruf = self._anruf(self.user_c)
        ergebnis = self._assistent(
            anruf, self.kontakt, self.user_c, feld='nicht_speichern').action_zuordnen()
        self.assertEqual(ergebnis, {'type': 'ir.actions.act_window_close'})
        self.assertEqual(anruf.partner_id, self.kontakt)

    def test_kontakt_nicht_lesbar(self):
        anruf = self._anruf()
        with self.assertRaises(AccessError):
            self._assistent(anruf, self.fremder_kontakt).action_zuordnen()
        self.assertFalse(anruf.partner_id)

    def test_ohne_kontakt(self):
        anruf = self._anruf()
        assistent = self._assistent(anruf, self.kontakt)
        assistent.partner_id = False
        with self.assertRaises(UserError):
            assistent.action_zuordnen()

    # --- Server-Methode ---

    def test_partner_setzen_fremder_anruf(self):
        fremd = self._anruf(self.user_b)
        with self.assertRaises(UserError):
            fremd.with_user(self.user_a)._wb_voip_partner_setzen(self.kontakt)
        self.assertFalse(fremd.partner_id)

    def test_partner_setzen_nicht_lesbar(self):
        anruf = self._anruf()
        with self.assertRaises(AccessError):
            anruf.with_user(self.user_a)._wb_voip_partner_setzen(self.fremder_kontakt)

    # --- Bus ---

    def test_bus_nachricht(self):
        anruf = self._anruf()
        assistent = self._assistent(anruf, self.kontakt)
        meldungen = self._bus_meldungen(assistent.action_zuordnen)
        self.assertEqual(
            meldungen, [{'type': 'wb_voip/kontext', 'payload': {'anruf_id': anruf.id}}])
