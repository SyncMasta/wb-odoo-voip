import importlib
import importlib.util
import os
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
        vals = {'layout_id': layout.id, 'art': 'text'}
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

    def test_standard_ohne_gruppen(self):
        for std in (self.std_fenster, self.std_popup):
            with self.assertRaises(UserError) as fang:
                std.with_user(self.user_manager).write({'group_ids': [(6, 0, [self.gruppe_a.id])]})
            self.assertIn("groups only on custom layouts", str(fang.exception))
            with self.assertRaises(UserError):
                std.write({'group_ids': [(4, self.gruppe_a.id)]})
            self.assertFalse(std.group_ids)
            # leere Gruppen sind erlaubt (Designer schickt sie immer mit)
            std.write({'group_ids': [(6, 0, [])], 'name': 'Standard neu'})
        # eigene Layouts dürfen Gruppen haben
        self._layout(group_ids=[(6, 0, [self.gruppe_a.id])])

    def test_formular_gruppen_und_felder(self):
        from lxml import etree

        def ansicht(benutzer):
            arch = self.Layout.with_user(benutzer).get_views([(False, 'form')])['views']['form']['arch']
            return etree.fromstring(arch)

        erp = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Layout ERP', 'login': 'wb_voip_layout_erp',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('wb_voip.group_wb_voip_manager').id,
                                  self.env.ref('base.group_erp_manager').id])]})
        for benutzer in (self.user_manager, erp):
            baum = ansicht(benutzer)
            gruppen = baum.xpath("//field[@name='group_ids']")
            self.assertTrue(gruppen)
            self.assertIn('ist_standard', gruppen[0].get('invisible', ''))
        baum = ansicht(self.user_manager)
        self.assertFalse(baum.xpath("//field[@name='block_ids']//field[@name='feld_ids']"))
        self.assertIn('Choose fields in the designer', etree.tostring(baum, encoding='unicode'))
        baum = ansicht(erp)
        self.assertTrue(baum.xpath("//field[@name='block_ids']//field[@name='feld_ids']"))
        self.assertNotIn('Choose fields in the designer', etree.tostring(baum, encoding='unicode'))

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
        self.Block.with_user(self.user_manager).create({'layout_id': layout.id, 'art': 'text'})
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
        self._block(self.std_popup, art='text', text='Hallo')
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

    def test_install_mode_vom_client_reicht_nicht(self):
        manager = self.Layout.with_user(self.user_manager).with_context(install_mode=True)
        with self.assertRaises(UserError):
            manager.create({'name': 'X', 'ort': 'anrufer_fenster', 'ist_standard': True})
        eigenes = self._layout()
        with self.assertRaises(UserError):
            eigenes.with_user(self.user_manager).with_context(install_mode=True).write(
                {'ist_standard': True})

    # --- Erlaubte Blockarten je Fenster ---

    def test_popup_nur_erlaubte_arten(self):
        layout = self._layout(ort='protokoll_popup')
        for art in ('kontaktkopf', 'felder', 'protokolle', 'text'):
            werte = {'feld_ids': [(6, 0, [self._feld('email').id])]} if art == 'felder' else {}
            self._block(layout, art=art, **werte)
        for art in ('buttons', 'notiz', 'aufgaben'):
            with self.assertRaises(ValidationError, msg=art):
                self._block(layout, art=art)

    def test_fenster_alle_arten(self):
        layout = self._layout()
        for art in ('kontaktkopf', 'protokolle', 'aufgaben', 'notiz', 'buttons', 'text'):
            self._block(layout, art=art)
        self._block(layout, art='felder', feld_ids=[(6, 0, [self._feld('email').id])])

    def test_notiz_hoechstens_einmal(self):
        layout = self._layout()
        self._block(layout, art='notiz')
        with self.assertRaises(ValidationError):
            self._block(layout, art='notiz')
        # ein anderes Layout darf seine eigene Notiz haben
        self._block(self._layout(name='Zweites'), art='notiz')

    def test_ort_wechsel_prueft_bloecke(self):
        layout = self._layout()
        self._block(layout, art='buttons')
        with self.assertRaises(ValidationError):
            layout.write({'ort': 'protokoll_popup'})
        layout.block_ids.unlink()
        self._block(layout, art='text', text='x')
        layout.write({'ort': 'protokoll_popup'})


@tagged('wb_voip', 'post_install', '-at_install')
class TestLayoutDesigner(WbVoipCase):
    """Server side of the layout designer (managers only, strict input)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Layout = cls.env['wb.voip.layout']
        cls.std_fenster = cls.env.ref('wb_voip.wb_voip_layout_anrufer_fenster_standard')
        cls.std_popup = cls.env.ref('wb_voip.wb_voip_layout_protokoll_popup_standard')
        cls.eigenes = cls.Layout.create({'name': 'Eigenes', 'ort': 'anrufer_fenster', 'sequence': 90})
        cls.env['wb.voip.layout.block'].create(
            {'layout_id': cls.eigenes.id, 'art': 'text', 'text': 'Vorher'})
        cls.gruppe = cls.env['res.groups'].create({'name': 'Designer-Gruppe'})
        cls.erika = cls.env['res.partner'].create({
            'name': 'Erika Designer', 'email': 'erika.designer@example.com',
            'phone': '+49 170 1112223', 'city': 'Musterstadt', 'ref': 'K-77'})
        cls.feld_email = cls.env['ir.model.fields']._get('res.partner', 'email')
        cls.feld_city = cls.env['ir.model.fields']._get('res.partner', 'city')
        cls.feld_ref = cls.env['ir.model.fields']._get('res.partner', 'ref')

    def _als_manager(self):
        return self.Layout.with_user(self.user_manager)

    def _daten(self, **werte):
        daten = {
            'name': 'Neu', 'ort': 'anrufer_fenster', 'breite': 'breit',
            'group_ids': [self.gruppe.id],
            'bloecke': [
                {'art': 'kontaktkopf', 'titel': False, 'spalte': '1'},
                {'art': 'felder', 'titel': 'Daten', 'spalte': '2',
                 'feld_ids': [self.feld_city.id, self.feld_email.id]},
                {'art': 'protokolle', 'titel': False, 'spalte': '1', 'anzahl': 3},
                {'art': 'text', 'titel': 'Hinweis', 'spalte': '2', 'text': 'Zeile 1\nZeile 2'},
                {'art': 'notiz', 'spalte': '1'},
            ],
        }
        daten.update(werte)
        return daten

    def _bloecke(self, layout):
        return [(b.art, b.titel or False, b.spalte, b.anzahl if b.art in ('protokolle', 'aufgaben') else 0,
                 b.text or False, b.feld_ids.ids)
                for b in layout.block_ids.sorted(lambda b: (b.sequence, b.id))]

    # --- Rechte ---

    def test_nur_manager(self):
        for benutzer in (self.user_a, self.env['res.users'].create({
                'name': 'Ohne VoIP', 'login': 'wb_voip_designer_ohne',
                'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})):
            Layout = self.Layout.with_user(benutzer)
            with self.assertRaises(AccessError):
                Layout.wb_voip_designer_laden(self.eigenes.id)
            with self.assertRaises(AccessError):
                Layout.wb_voip_designer_speichern(self.eigenes.id, self._daten())
            with self.assertRaises(AccessError):
                Layout.wb_voip_designer_vorschau(self.eigenes.id, self.erika.id)
        with self.assertRaises(AccessError):
            self.eigenes.with_user(self.user_a).action_wb_voip_designer()
        self.assertEqual(self._bloecke(self.eigenes)[0][4], 'Vorher')

    def test_designer_aktion(self):
        aktion = self.eigenes.with_user(self.user_manager).action_wb_voip_designer()
        self.assertEqual(aktion['type'], 'ir.actions.client')
        self.assertEqual(aktion['tag'], 'wb_voip.layout_designer')
        self.assertEqual(aktion['params'], {'layout_id': self.eigenes.id})
        menue = self.env.ref('wb_voip.wb_voip_menu_layout_designer')
        self.assertEqual(menue.group_ids, self.env.ref('wb_voip.group_wb_voip_manager'))

    # --- Laden ---

    def test_laden(self):
        daten = self._als_manager().wb_voip_designer_laden(self.std_fenster.id)
        self.assertEqual(set(daten), {'layout', 'arten', 'felder', 'orte', 'breiten', 'layouts',
                                      'beispielkontakt_id'})
        layout = daten['layout']
        self.assertEqual(layout['id'], self.std_fenster.id)
        self.assertTrue(layout['ist_standard'])
        self.assertEqual([b['art'] for b in layout['bloecke']],
                         ['kontaktkopf', 'buttons', 'protokolle', 'aufgaben', 'notiz'])
        self.assertEqual(set(layout['bloecke'][0]),
                         {'art', 'titel', 'spalte', 'anzahl', 'text', 'feld_ids'})
        self.assertEqual([a['art'] for a in daten['arten']['protokoll_popup']],
                         ['kontaktkopf', 'felder', 'protokolle', 'text'])
        # die sieben Arten des Kerns, Brückenmodule dürfen weitere ergänzen
        self.assertEqual([a['art'] for a in daten['arten']['anrufer_fenster']][:7],
                         ['kontaktkopf', 'felder', 'protokolle', 'aufgaben', 'notiz', 'buttons', 'text'])
        namen = {f['name'] for f in daten['felder']}
        self.assertIn('email', namen)
        self.assertIn('country_id', namen)
        for gesperrt in ('user_ids', 'signup_token', 'image_1920', 'child_ids', 'user_id', 'company_id'):
            self.assertNotIn(gesperrt, namen)
        self.assertEqual(daten['beispielkontakt_id'], self.user_manager.partner_id.id)
        self.assertIn(self.eigenes.id, [l['id'] for l in daten['layouts']])
        # ohne Angabe: Standard des Anrufer-Fensters
        self.assertEqual(self._als_manager().wb_voip_designer_laden(False)['layout']['id'],
                         self.std_fenster.id)

    def test_laden_ungueltig(self):
        for schlecht in ('1', 1.5, True, -3, 999999, [1]):
            with self.assertRaises(UserError, msg=repr(schlecht)):
                self._als_manager().wb_voip_designer_laden(schlecht)

    # --- Speichern ---

    def test_speichern_ersetzt_bloecke(self):
        antwort = self._als_manager().wb_voip_designer_speichern(self.eigenes.id, self._daten())
        self.assertEqual(antwort['layout']['id'], self.eigenes.id)
        self.assertEqual(self.eigenes.name, 'Neu')
        self.assertEqual(self.eigenes.breite, 'breit')
        self.assertEqual(self.eigenes.group_ids, self.gruppe)
        self.assertEqual(self._bloecke(self.eigenes), [
            ('kontaktkopf', False, '1', 0, False, []),
            ('felder', 'Daten', '2', 0, False, [self.feld_city.id, self.feld_email.id]),
            ('protokolle', False, '1', 3, False, []),
            ('text', 'Hinweis', '2', 0, 'Zeile 1\nZeile 2', []),
            ('notiz', False, '1', 0, False, []),
        ])
        # leere Liste entfernt alle Blöcke
        self._als_manager().wb_voip_designer_speichern(self.eigenes.id, self._daten(bloecke=[]))
        self.assertFalse(self.eigenes.block_ids)

    def test_speichern_ungueltig_aendert_nichts(self):
        feld_login = self.env['ir.model.fields']._get('res.users', 'login')
        feld_user_ids = self.env['ir.model.fields']._get('res.partner', 'user_ids')
        gut = self._daten()['bloecke']
        schlechte = {
            'kein dict': [1, 2],
            'fremder Schlüssel': self._daten(sequence=1),
            'bloecke kein list': self._daten(bloecke={'art': 'text'}),
            'zu viele': self._daten(bloecke=[{'art': 'text', 'text': str(i)} for i in range(31)]),
            'Block kein dict': self._daten(bloecke=['text']),
            'Block fremder Schlüssel': self._daten(bloecke=[{'art': 'text', 'sequence': 3}]),
            'Art unbekannt': self._daten(bloecke=[{'art': 'html'}]),
            'Art im Popup nicht erlaubt': self._daten(ort='protokoll_popup', bloecke=[{'art': 'buttons'}]),
            'zwei Notizen': self._daten(bloecke=[{'art': 'notiz'}, {'art': 'notiz'}]),
            'Feld anderes Modell': self._daten(bloecke=[{'art': 'felder', 'feld_ids': [feld_login.id]}]),
            'Feld gesperrt': self._daten(bloecke=[{'art': 'felder', 'feld_ids': [feld_user_ids.id]}]),
            'Feld-ID kein int': self._daten(bloecke=[{'art': 'felder', 'feld_ids': [str(self.feld_email.id)]}]),
            'Feld-ID unbekannt': self._daten(bloecke=[{'art': 'felder', 'feld_ids': [99999999]}]),
            'Felder ohne Feld': self._daten(bloecke=[{'art': 'felder', 'feld_ids': []}]),
            'Felder an anderer Art': self._daten(bloecke=[{'art': 'text', 'feld_ids': [self.feld_email.id]}]),
            'Text zu lang': self._daten(bloecke=[{'art': 'text', 'text': 'x' * 2001}]),
            'Text kein str': self._daten(bloecke=[{'art': 'text', 'text': ['x']}]),
            'Titel zu lang': self._daten(bloecke=[{'art': 'text', 'titel': 'x' * 201}]),
            'Anzahl 0': self._daten(bloecke=[{'art': 'protokolle', 'anzahl': 0}]),
            'Anzahl 21': self._daten(bloecke=[{'art': 'aufgaben', 'anzahl': 21}]),
            'Anzahl Text': self._daten(bloecke=[{'art': 'protokolle', 'anzahl': '5'}]),
            'Anzahl bool': self._daten(bloecke=[{'art': 'protokolle', 'anzahl': True}]),
            'Spalte 3': self._daten(bloecke=[{'art': 'text', 'spalte': '3'}]),
            'Breite falsch': self._daten(breite='riesig'),
            'Ort falsch': self._daten(ort='softphone'),
            'Name leer': self._daten(name='  '),
            'Name zu lang': self._daten(name='x' * 201),
            'Gruppe unbekannt': self._daten(group_ids=[99999999]),
            'Gruppe kein int': self._daten(group_ids=['1']),
            'gültig, dann ungültig': self._daten(bloecke=gut + [{'art': 'html'}]),
        }
        vorher = self._bloecke(self.eigenes)
        for name, daten in schlechte.items():
            with self.assertRaises(UserError, msg=name):  # ValidationError ist ein UserError
                self._als_manager().wb_voip_designer_speichern(self.eigenes.id, daten)
            self.assertEqual(self._bloecke(self.eigenes), vorher, name)
            self.assertEqual(self.eigenes.name, 'Eigenes', name)
            self.assertFalse(self.eigenes.group_ids, name)

    def test_speichern_atomar_bei_constraint(self):
        # ein Fehler erst beim Anlegen der Blöcke (hier: erzwungen) lässt Layout und Blöcke unverändert
        vorher = self._bloecke(self.eigenes)
        Block = type(self.env['wb.voip.layout.block'])
        anlegen = Block.create

        def kaputt(modell, vals_list):
            anlegen(modell, vals_list)
            raise ValidationError('kaputt')

        with patch.object(Block, 'create', kaputt):
            with self.assertRaises(ValidationError):
                self._als_manager().wb_voip_designer_speichern(self.eigenes.id, self._daten())
        self.assertEqual(self._bloecke(self.eigenes), vorher)
        self.assertEqual(self.eigenes.name, 'Eigenes')

    def test_speichern_standard_ort_fest(self):
        with self.assertRaises(UserError):
            self._als_manager().wb_voip_designer_speichern(
                self.std_fenster.id, self._daten(ort='protokoll_popup', bloecke=[]))
        self.assertEqual(self.std_fenster.ort, 'anrufer_fenster')
        self.assertTrue(self.std_fenster.block_ids)
        # Popup-Standard mit erlaubten Arten speicherbar
        self._als_manager().wb_voip_designer_speichern(self.std_popup.id, {
            'name': 'Popup', 'ort': 'protokoll_popup', 'breite': 'normal', 'group_ids': [],
            'bloecke': [{'art': 'text', 'text': 'Hallo'}]})
        self.assertEqual([b.art for b in self.std_popup.block_ids], ['text'])

    def test_speichern_standard_ohne_gruppen(self):
        vorher = self._bloecke(self.std_fenster)
        with self.assertRaises(UserError) as fang:
            self._als_manager().wb_voip_designer_speichern(self.std_fenster.id, self._daten())
        self.assertIn("groups only on custom layouts", str(fang.exception))
        self.assertFalse(self.std_fenster.group_ids)
        self.assertEqual(self._bloecke(self.std_fenster), vorher)
        self._als_manager().wb_voip_designer_speichern(
            self.std_fenster.id, self._daten(group_ids=[]))
        self.assertEqual(self.std_fenster.name, 'Neu')

    # --- Vorschau ---

    def test_vorschau_format_wie_kontext(self):
        self.env['wb.voip.layout.block'].create({
            'layout_id': self.eigenes.id, 'art': 'felder',
            'feld_ids': [(6, 0, [self.feld_city.id, self.feld_ref.id])]})
        daten = self._als_manager().wb_voip_designer_vorschau(self.eigenes.id, self.erika.id)
        self.assertEqual(set(daten), {'layout', 'kontakt', 'protokolle', 'aufgaben'})
        erwartet = self.eigenes.with_user(self.user_manager)._wb_voip_fuer_client(
            self.erika.with_user(self.user_manager))
        self.assertEqual(daten['layout'], erwartet)
        self.assertEqual(daten['kontakt']['name'], 'Erika Designer')
        felder = daten['layout']['bloecke'][1]['felder']
        self.assertEqual([(f['name'], f['wert']) for f in felder], [('city', 'Musterstadt'), ('ref', 'K-77')])

    def test_vorschau_entwurf(self):
        entwurf = self._daten(bloecke=[
            {'art': 'felder', 'feld_ids': [self.feld_email.id]}, {'art': 'text', 'text': 'Entwurf'}])
        daten = self._als_manager().wb_voip_designer_vorschau(self.eigenes.id, self.erika.id, entwurf)
        self.assertEqual(daten['layout']['breite'], 'breit')
        self.assertEqual([b['art'] for b in daten['layout']['bloecke']], ['felder', 'text'])
        self.assertEqual(daten['layout']['bloecke'][0]['felder'][0]['wert'], 'erika.designer@example.com')
        self.assertEqual(daten['layout']['bloecke'][1]['text'], 'Entwurf')
        # nichts gespeichert
        self.assertEqual([b.art for b in self.eigenes.block_ids], ['text'])
        # Entwurf wird genauso streng geprüft
        with self.assertRaises(UserError):
            self._als_manager().wb_voip_designer_vorschau(
                self.eigenes.id, self.erika.id, self._daten(bloecke=[{'art': 'html'}]))

    def test_vorschau_nur_lesbarer_kontakt(self):
        firma_b = self.env['res.company'].create({'name': 'Fremdfirma Designer'})
        fremd = self.env['res.partner'].create({'name': 'Fremd', 'company_id': firma_b.id})
        if fremd.with_user(self.user_manager).has_access('read'):
            self.skipTest("Kontakte anderer Firmen sind hier lesbar")
        with self.assertRaises(AccessError):
            self._als_manager().wb_voip_designer_vorschau(self.eigenes.id, fremd.id)
        for schlecht in (False, '3', 0, -1, 99999999, 2.0):
            with self.assertRaises(UserError, msg=repr(schlecht)):
                self._als_manager().wb_voip_designer_vorschau(self.eigenes.id, schlecht)

    def test_titel_und_text_nur_leerzeichen_sind_leer(self):
        daten = self._daten(bloecke=[{'art': 'text', 'titel': '   ', 'text': ' \n '},
                                     {'art': 'kontaktkopf', 'titel': '  Kunde  '}])
        self._als_manager().wb_voip_designer_speichern(self.eigenes.id, daten)
        text, kopf = self.eigenes.block_ids.sorted('sequence')
        self.assertFalse(text.titel)
        self.assertFalse(text.text)
        self.assertEqual(kopf.titel, 'Kunde')


@tagged('wb_voip', 'post_install', '-at_install')
class TestLayoutAltdaten(WbVoipCase):
    """Blocks a place no longer allows (data from before the constraint)."""

    def _verbotene_bloecke(self):
        layout = self.env['wb.voip.layout'].create(
            {'name': 'Alt-Popup', 'ort': 'protokoll_popup', 'sequence': 1})
        Block = self.env['wb.voip.layout.block']
        bloecke = Block.browse([Block.create({'layout_id': layout.id, 'sequence': nr, 'art': 'text',
                                              'text': str(nr)}).id for nr in (1, 2, 3)])
        fenster = self.env['wb.voip.layout'].create(
            {'name': 'Alt-Fenster', 'ort': 'anrufer_fenster', 'sequence': 1})
        notiz1 = Block.create({'layout_id': fenster.id, 'sequence': 1, 'art': 'notiz'})
        notiz2 = Block.create({'layout_id': fenster.id, 'sequence': 2, 'art': 'text', 'text': 'x'})
        # wie Altdaten: an den Constraints vorbei
        self.env.cr.execute("UPDATE wb_voip_layout_block SET art = 'buttons' WHERE id = %s", [bloecke[0].id])
        self.env.cr.execute("UPDATE wb_voip_layout_block SET art = 'notiz' WHERE id IN %s",
                            [(bloecke[2].id, notiz2.id)])
        self.env.invalidate_all()
        return layout, bloecke, fenster, notiz1, notiz2

    def test_laufzeit_ueberspringt_verbotene(self):
        layout, bloecke, fenster, notiz1, _notiz2 = self._verbotene_bloecke()
        daten = layout._wb_voip_fuer_client(self.env['res.partner'])
        self.assertEqual([(b['art'], b['text']) for b in daten['bloecke']], [('text', '2')])
        daten = fenster._wb_voip_fuer_client(self.env['res.partner'])
        self.assertEqual([b['art'] for b in daten['bloecke']], ['notiz'])

    def test_migration_entfernt_verbotene(self):
        layout, bloecke, fenster, notiz1, notiz2 = self._verbotene_bloecke()
        pfad = os.path.join(os.path.dirname(__file__), '..', 'migrations', '19.0.2.4.1',
                            'post-migration.py')
        spec = importlib.util.spec_from_file_location('wb_voip_mig_19_0_2_4_1', pfad)
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
        modul.migrate(self.env.cr, '19.0.2.4.0')
        self.env.invalidate_all()
        self.assertEqual(layout.block_ids, bloecke[1])
        self.assertEqual(fenster.block_ids, notiz1)
        self.assertFalse(notiz2.exists())
        # danach wieder bearbeitbar, zweiter Lauf ändert nichts
        layout.write({'name': 'Neu'})
        modul.migrate(self.env.cr, '19.0.2.4.0')
        self.assertEqual(layout.block_ids, bloecke[1])


@tagged('wb_voip', 'post_install', '-at_install')
class TestNeuerKontaktKopf(WbVoipCase):
    """Fixed "new contact" button of the caller window header."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.user_lesen = Users.create({
            'name': 'VoIP nur lesen', 'login': 'wb_voip_kopf_lesen',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('wb_voip.group_wb_voip_user').id])]})
        cls.user_ohne = Users.create({
            'name': 'Ohne VoIP', 'login': 'wb_voip_kopf_ohne',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])]})
        # auch ohne konfigurierten Button
        cls.env.ref('wb_voip.wb_voip_aktion_neuer_kontakt').active = False

    def _anruf(self, benutzer):
        return self.env['wb.voip.anruf'].sudo().create({
            'user_id': benutzer.id, 'richtung': 'eingehend', 'zustand': 'klingelt',
            'nummer': '0170 9998887', 'nummer_e164': '+491709998887'})

    def test_eigener_anruf(self):
        anruf = self._anruf(self.user_a)
        aktion = anruf.with_user(self.user_a).action_wb_voip_neuer_kontakt()
        self.assertEqual(aktion['type'], 'ir.actions.act_window')
        self.assertEqual(aktion['res_model'], 'res.partner')
        self.assertEqual(aktion['views'], [[False, 'form']])
        self.assertEqual(aktion['context'], {'default_phone': '0170 9998887', 'wb_voip_anruf_id': anruf.id})
        # dieselbe Aktion wie die Button-Art „Neuer Kontakt“
        self.assertEqual(aktion, anruf.with_user(self.user_a)._wb_voip_neuer_kontakt())

    def test_fremder_anruf(self):
        anruf = self._anruf(self.user_b)
        with self.assertRaises(AccessError):
            anruf.with_user(self.user_a).action_wb_voip_neuer_kontakt()

    def test_ohne_anlegerecht(self):
        if self.env['res.partner'].with_user(self.user_lesen).has_access('create'):
            self.skipTest("Benutzer dürfen hier Kontakte anlegen")
        anruf = self._anruf(self.user_lesen)
        with self.assertRaises(AccessError):
            anruf.with_user(self.user_lesen).action_wb_voip_neuer_kontakt()

    def test_ohne_voip(self):
        anruf = self._anruf(self.user_a)
        with self.assertRaises(AccessError):
            anruf.with_user(self.user_ohne).action_wb_voip_neuer_kontakt()
