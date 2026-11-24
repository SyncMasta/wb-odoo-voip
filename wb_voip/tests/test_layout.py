import importlib
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestLayout(WbVoipCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Layout = cls.env['wb.voip.layout']
        cls.Block = cls.env['wb.voip.layout.block']
        cls.std_fenster = cls.env.ref('wb_voip.wb_voip_layout_anrufer_fenster_standard')
        cls.std_popup = cls.env.ref('wb_voip.wb_voip_layout_protokoll_popup_standard')
        cls.gruppe_a = cls.env['res.groups'].create({'name': 'Layout-Gruppe A'})
        cls.gruppe_b = cls.env['res.groups'].create({'name': 'Layout-Gruppe B'})

    def _layout(self, **werte):
        vals = {'name': 'Eigenes', 'ort': 'anrufer_fenster', 'sequence': 20}
        vals.update(werte)
        return self.Layout.create(vals)

    def _block(self, layout, **werte):
        vals = {'layout_id': layout.id, 'art': 'notiz'}
        vals.update(werte)
        return self.Block.create(vals)

    def _feld(self, name, modell='res.partner'):
        feld = self.env['ir.model.fields']._get(modell, name)
        if not feld:
            self.skipTest(f"Feld {modell}.{name} existiert hier nicht")
        return feld

    def _bloecke(self, layout):
        return [(b.art, b.anzahl if b.art in ('protokolle', 'aufgaben') else 0)
                for b in layout.block_ids.sorted('sequence')]

    # --- Standard-Layouts ---

    def test_standard_layouts_ausgeliefert(self):
        self.assertTrue(self.std_fenster.ist_standard)
        self.assertEqual(self.std_fenster.ort, 'anrufer_fenster')
        self.assertEqual(
            self._bloecke(self.std_fenster),
            [('kontaktkopf', 0), ('buttons', 0), ('protokolle', 5), ('aufgaben', 10), ('notiz', 0)])
        self.assertEqual(self._bloecke(self.std_popup), [('kontaktkopf', 0), ('protokolle', 5)])
        self.assertTrue(self.std_popup.ist_standard)

    def test_standard_nicht_loeschbar_nicht_archivierbar(self):
        with self.assertRaises(UserError):
            self.std_fenster.unlink()
        with self.assertRaises(UserError):
            self.std_fenster.write({'active': False})
        self.assertTrue(self.std_fenster.active)
        # bearbeitbar
        self.std_fenster.write({'name': 'Geändert', 'breite': 'breit'})
        self.assertEqual(self.std_fenster.breite, 'breit')

    def test_eigenes_loeschbar_mit_bloecken(self):
        layout = self._layout()
        block = self._block(layout)
        layout.unlink()
        self.assertFalse(block.exists())

    def test_ist_standard_je_ort_einmalig(self):
        with mute_logger('odoo.sql_db'), self.assertRaises(Exception):
            self._layout(ist_standard=True)
        # anderer Ort hat seinen eigenen Standard, mit Standard je Ort eindeutig
        with mute_logger('odoo.sql_db'), self.assertRaises(Exception):
            self._layout(ort='protokoll_popup', ist_standard=True)

    # --- Auswahl je Benutzer ---

    def test_auswahl_ohne_eigenes_ist_standard(self):
        self.assertEqual(self.user_a._wb_voip_layout('anrufer_fenster'), self.std_fenster)
        self.assertEqual(self.user_a._wb_voip_layout('protokoll_popup'), self.std_popup)

    def test_auswahl_gruppe_passt_nicht_faellt_auf_standard(self):
        self._layout(group_ids=[(6, 0, [self.gruppe_a.id])])
        self.assertEqual(self.user_a._wb_voip_layout('anrufer_fenster'), self.std_fenster)

    def test_auswahl_gruppe_passt(self):
        eigenes = self._layout(group_ids=[(6, 0, [self.gruppe_a.id])])
        self.user_a.write({'group_ids': [(4, self.gruppe_a.id)]})
        self.assertEqual(self.user_a._wb_voip_layout('anrufer_fenster'), eigenes)
        # B ist nicht in der Gruppe
        self.assertEqual(self.user_b._wb_voip_layout('anrufer_fenster'), self.std_fenster)
        # anderer Ort bleibt unberührt
        self.assertEqual(self.user_a._wb_voip_layout('protokoll_popup'), self.std_popup)

    def test_auswahl_kleinste_sequence_gewinnt(self):
        spaet = self._layout(name='Spät', sequence=30, group_ids=[(6, 0, [self.gruppe_a.id])])
        frueh = self._layout(name='Früh', sequence=5, group_ids=[(6, 0, [self.gruppe_b.id])])
        self.user_a.write({'group_ids': [(4, self.gruppe_a.id), (4, self.gruppe_b.id)]})
        self.assertEqual(self.user_a._wb_voip_layout('anrufer_fenster'), frueh)
        frueh.sequence = 50
        self.assertEqual(self.user_a._wb_voip_layout('anrufer_fenster'), spaet)

    def test_auswahl_ohne_gruppen_gilt_fuer_alle_und_schlaegt_standard(self):
        offen = self._layout(sequence=50)
        self.assertEqual(self.user_a._wb_voip_layout('anrufer_fenster'), offen)

    def test_auswahl_archiviert_zaehlt_nicht(self):
        eigenes = self._layout()
        eigenes.active = False
        self.assertEqual(self.user_a._wb_voip_layout('anrufer_fenster'), self.std_fenster)

    def test_auswahl_ungueltiger_ort(self):
        with self.assertRaises(ValidationError):
            self.user_a._wb_voip_layout('gibtsnicht')

    # --- Rechte ---

    def test_benutzer_ohne_direkten_zugriff(self):
        with self.assertRaises(AccessError):
            self.Layout.with_user(self.user_a).search([])
        with self.assertRaises(AccessError):
            self.Block.with_user(self.user_a).search([])
        with self.assertRaises(AccessError):
            self.Layout.with_user(self.user_a).create({'name': 'X', 'ort': 'anrufer_fenster'})

    def test_manager_darf_alles(self):
        layout = self.Layout.with_user(self.user_manager).create(
            {'name': 'M', 'ort': 'protokoll_popup', 'group_ids': [(6, 0, [self.gruppe_a.id])]})
        self.Block.with_user(self.user_manager).create({'layout_id': layout.id, 'art': 'notiz'})
        layout.with_user(self.user_manager).unlink()

    # --- Validierungen ---

    def test_felder_block_braucht_felder(self):
        layout = self._layout()
        with self.assertRaises(ValidationError):
            self._block(layout, art='felder')
        self._block(layout, art='felder', feld_ids=[(6, 0, [self._feld('email').id])])

    def test_feld_anderes_modell(self):
        layout = self._layout()
        with self.assertRaises(ValidationError):
            self._block(layout, art='felder', feld_ids=[(6, 0, [self._feld('login', 'res.users').id])])

    def test_feld_gesperrte_namen(self):
        modul = 'odoo.addons.wb_voip.models.wb_voip_layout'
        sperr = importlib.import_module(modul)._WB_VOIP_LAYOUT_SPERRFELDER
        for name in ('signup_token', 'signup_type', 'signup_expiration', 'user_ids',
                     'message_ids', 'activity_ids', 'website_message_ids'):
            self.assertIn(name, sperr)
        layout = self._layout()
        # a harmless char field becomes forbidden through the list (enforced by the constraint)
        feld = self._feld('city')
        self._block(layout, art='felder', feld_ids=[(6, 0, [feld.id])])
        with patch(modul + '._WB_VOIP_LAYOUT_SPERRFELDER', ('city',)):
            with self.assertRaises(ValidationError):
                self._block(layout, art='felder', feld_ids=[(6, 0, [feld.id])])
        # real forbidden partner fields never pass (type or list)
        for name in ('signup_token', 'user_ids', 'message_ids', 'activity_ids'):
            if self.env['ir.model.fields']._get('res.partner', name):
                with self.assertRaises(ValidationError, msg=name):
                    self._block(layout, art='felder',
                                feld_ids=[(6, 0, [self._feld(name).id])])

    def test_feld_namensmuster_gesperrt(self):
        layout = self._layout()
        for teil in ('password', 'token', 'secret', 'key'):
            feld = self.env['ir.model.fields'].sudo().create({
                'name': f'x_test_{teil}_wert', 'model_id': self.env.ref('base.model_res_partner').id,
                'field_description': 'Test', 'ttype': 'char'})
            with self.assertRaises(ValidationError, msg=teil):
                self._block(layout, art='felder', feld_ids=[(6, 0, [feld.id])])

    def test_feld_typ_und_sensibles_ziel(self):
        layout = self._layout()
        # Typ nicht erlaubt (One2many, Binary)
        with self.assertRaises(ValidationError):
            self._block(layout, art='felder', feld_ids=[(6, 0, [self._feld('child_ids').id])])
        with self.assertRaises(ValidationError):
            self._block(layout, art='felder', feld_ids=[(6, 0, [self._feld('image_1920').id])])
        # Many2one auf harmlose Modelle ist ok, auf sensible nicht
        self._block(layout, art='felder', feld_ids=[(6, 0, [self._feld('country_id').id])])
        with self.assertRaises(ValidationError):
            self._block(layout, art='felder', feld_ids=[(6, 0, [self._feld('user_id').id])])

    def test_feld_nachtraeglich_geaendert(self):
        layout = self._layout()
        block = self._block(layout, art='felder', feld_ids=[(6, 0, [self._feld('email').id])])
        with self.assertRaises(ValidationError):
            block.write({'feld_ids': [(4, self._feld('user_ids').id)]})

    def test_anzahl_grenzen(self):
        layout = self._layout()
        self.assertEqual(self._block(layout, art='protokolle').anzahl, 5)
        for schlecht in (0, -1, 21):
            with self.assertRaises(ValidationError, msg=schlecht):
                self._block(layout, art='protokolle', anzahl=schlecht)
            with self.assertRaises(ValidationError, msg=schlecht):
                self._block(layout, art='aufgaben', anzahl=schlecht)
        self._block(layout, art='aufgaben', anzahl=1)
        self._block(layout, art='aufgaben', anzahl=20)

    def test_text_laenge(self):
        layout = self._layout()
        self._block(layout, art='text', text='x' * 2000)
        with self.assertRaises(ValidationError):
            self._block(layout, art='text', text='x' * 2001)

    def test_spalte_und_breite_werte(self):
        layout = self._layout(breite='sehr_breit')
        self.assertEqual(self._block(layout).spalte, '1')
        self._block(layout, spalte='2')
        with self.assertRaises(ValueError):
            self._block(layout, spalte='3')

    # --- Zurücksetzen ---

    def test_zuruecksetzen(self):
        vorher = self._bloecke(self.std_fenster)
        self.std_fenster.block_ids.unlink()
        self._block(self.std_fenster, art='text', text='Hallo')
        self.std_fenster.with_user(self.user_manager).action_auf_standard_zuruecksetzen()
        self.assertEqual(self._bloecke(self.std_fenster), vorher)

    def test_zuruecksetzen_popup(self):
        vorher = self._bloecke(self.std_popup)
        self._block(self.std_popup, art='notiz')
        self.std_popup.action_auf_standard_zuruecksetzen()
        self.assertEqual(self._bloecke(self.std_popup), vorher)

    def test_zuruecksetzen_nur_manager(self):
        with self.assertRaises(AccessError):
            self.std_fenster.with_user(self.user_a).action_auf_standard_zuruecksetzen()

    def test_zuruecksetzen_nur_standard(self):
        with self.assertRaises(UserError):
            self._layout().action_auf_standard_zuruecksetzen()

    # --- Fix round 1 ---

    def test_standard_kennzeichen_geschuetzt(self):
        with self.assertRaises(UserError):
            self.std_fenster.write({'ist_standard': False})
        with self.assertRaises(UserError):
            self.std_fenster.write({'ort': 'protokoll_popup'})
        with self.assertRaises(UserError):
            self._layout(ist_standard=True)
        eigenes = self._layout()
        with self.assertRaises(UserError):
            eigenes.write({'ist_standard': True})
        # unchanged values are fine
        self.std_fenster.write({'ort': 'anrufer_fenster', 'ist_standard': True})

    def test_standard_bloecke_helfer_idempotent(self):
        vorher = self._bloecke(self.std_fenster)
        self.Layout._wb_voip_standard_bloecke_sicherstellen()
        self.assertEqual(self._bloecke(self.std_fenster), vorher)
        self.assertEqual(len(self.std_fenster.block_ids), len(vorher))
        # reset then a simulated update: block count stays
        self.std_fenster.action_auf_standard_zuruecksetzen()
        self.Layout._wb_voip_standard_bloecke_sicherstellen()
        self.assertEqual(self._bloecke(self.std_fenster), vorher)
        self.assertFalse(self.env['ir.model.data'].search([
            ('model', '=', 'wb.voip.layout.block'), ('res_id', 'in', self.std_fenster.block_ids.ids)]))
        # empty standard layout gets its blocks back
        self.std_popup.block_ids.unlink()
        self.Layout._wb_voip_standard_bloecke_sicherstellen()
        self.assertEqual(self._bloecke(self.std_popup), [('kontaktkopf', 0), ('protokolle', 5)])

    def test_ziele_erlaubliste(self):
        layout = self._layout()
        for modell, name in (('res.partner', 'bank_ids'), ('res.partner', 'company_id'),
                             ('res.partner', 'user_id')):
            with self.assertRaises(ValidationError, msg=name):
                self._block(layout, art='felder', feld_ids=[(6, 0, [self._feld(name, modell).id])])
        for name in ('country_id', 'state_id', 'title', 'parent_id', 'lang', 'industry_id',
                     'category_id'):
            self._block(layout, art='felder', feld_ids=[(6, 0, [self._feld(name).id])])

    def test_nur_gespeicherte_felder(self):
        layout = self._layout()
        # display_name is not stored on res.partner
        nicht_gespeichert = self.env['ir.model.fields'].search([
            ('model', '=', 'res.partner'), ('store', '=', False), ('ttype', '=', 'char'),
            ('name', 'not ilike', 'key')], limit=1)
        self.assertTrue(nicht_gespeichert)
        with self.assertRaises(ValidationError):
            self._block(layout, art='felder', feld_ids=[(6, 0, [nicht_gespeichert.id])])
