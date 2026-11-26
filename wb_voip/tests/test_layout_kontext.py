from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tools.misc import format_amount, format_date, format_datetime, formatLang

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestLayoutKontext(WbVoipCase):
    """Layout and formatted field values in the caller context and the log popup."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Layout = cls.env['wb.voip.layout']
        cls.Block = cls.env['wb.voip.layout.block']
        cls.std_fenster = cls.env.ref('wb_voip.wb_voip_layout_anrufer_fenster_standard')
        cls.std_popup = cls.env.ref('wb_voip.wb_voip_layout_protokoll_popup_standard')
        cls.user_a.tz = 'Europe/Berlin'
        cls.user_ohne = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Ohne VoIP', 'login': 'wb_voip_layout_ohne',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.firma = cls.env['res.partner'].create({'name': 'Firma L', 'is_company': True})
        cls.kontakt = cls.env['res.partner'].create({
            'name': 'Lena Layout', 'parent_id': cls.firma.id, 'ref': 'K-4711',
            'phone': '+49 170 7654321', 'email': 'lena@example.com',
            'country_id': cls.env.ref('base.de').id, 'type': 'contact', 'color': 3,
            'partner_latitude': 52.5, 'is_company': False,
        })
        cls.fremdfirma = cls.env['res.company'].create({'name': 'Fremdfirma L'})
        cls.fremder_kontakt = cls.env['res.partner'].create({
            'name': 'Geheim L', 'company_id': cls.fremdfirma.id})

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()

    # --- Hilfen ---

    def _feld(self, name, modell='res.partner'):
        return self.env['ir.model.fields']._get(modell, name)

    def _layout(self, bloecke, ort='anrufer_fenster', **werte):
        vals = {'name': 'Test-Layout', 'ort': ort, 'sequence': 1, 'breite': 'breit'}
        vals.update(werte)
        layout = self.Layout.create(vals)
        for nr, block in enumerate(bloecke, start=1):
            self.Block.create({'layout_id': layout.id, 'sequence': nr, **block})
        return layout

    def _felder_layout(self, *namen, **werte):
        return self._layout([{
            'art': 'felder', 'titel': 'Daten', 'spalte': '2',
            'feld_ids': [(6, 0, [self._feld(name).id for name in namen])]}], **werte)

    def _anruf(self, benutzer=None, **werte):
        vals = {'user_id': (benutzer or self.user_a).id, 'richtung': 'eingehend',
                'zustand': 'klingelt', 'nummer': '+49 170 7654321',
                'nummer_e164': '+491707654321', 'partner_id': self.kontakt.id}
        vals.update(werte)
        return self.env['wb.voip.anruf'].sudo().create(vals)

    def _protokoll(self, benutzer=None, **werte):
        vals = {'user_id': (benutzer or self.user_a).id, 'partner_id': self.kontakt.id,
                'richtung': 'eingehend', 'ergebnis': 'erreicht', 'dauer_sek': 30}
        vals.update(werte)
        return self.env['wb.voip.protokoll'].sudo().create(vals)

    def _aktivitaet(self, benutzer=None):
        return self.env['mail.activity'].sudo().create({
            'res_model_id': self.env['ir.model']._get_id('res.partner'),
            'res_id': self.kontakt.id, 'user_id': (benutzer or self.user_a).id,
            'summary': 'Rückruf', 'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
        })

    def _kontext(self, anruf=None, benutzer=None):
        return (anruf or self._anruf(benutzer)).with_user(benutzer or self.user_a).wb_voip_kontext()

    def _werte(self, daten):
        """Return {field name: value} of all field blocks of a context."""
        return {feld['name']: feld['wert']
                for block in daten['layout']['bloecke'] for feld in block['felder']}

    # --- Aufbau ---

    def test_kontext_alte_schluessel_und_layout(self):
        daten = self._kontext()
        self.assertEqual(set(daten), {'anruf', 'kontakt', 'protokolle', 'aufgaben', 'aktionen',
                                      'layout'})
        layout = daten['layout']
        self.assertEqual(layout['id'], self.std_fenster.id)
        self.assertEqual(layout['breite'], self.std_fenster.breite)
        self.assertEqual([b['art'] for b in layout['bloecke']],
                         ['kontaktkopf', 'buttons', 'protokolle', 'aufgaben', 'notiz'])
        for block in layout['bloecke']:
            self.assertEqual(set(block), {'art', 'titel', 'spalte', 'anzahl', 'text', 'felder'})
            self.assertEqual(block['felder'], [])
        anzahl = {b['art']: b['anzahl'] for b in layout['bloecke']}
        self.assertEqual(anzahl['protokolle'], 5)
        self.assertEqual(anzahl['aufgaben'], 10)
        self.assertFalse(anzahl['kontaktkopf'])

    def test_standard_fehlt_ersatz_ohne_datensatz(self):
        # Standard-Layouts per SQL gelöscht (Blöcke per Fremdschlüssel mit): kein Absturz
        protokoll = self._protokoll()
        self._protokoll()
        self.env.flush_all()
        self.env.cr.execute("DELETE FROM wb_voip_layout WHERE ist_standard IS TRUE")
        self.env.invalidate_all()
        self.assertFalse(self.std_fenster.exists())
        daten = self._kontext()
        layout = daten['layout']
        self.assertFalse(layout['id'])
        self.assertEqual(layout['breite'], 'normal')
        self.assertEqual([b['art'] for b in layout['bloecke']],
                         ['kontaktkopf', 'buttons', 'protokolle', 'aufgaben', 'notiz'])
        self.assertEqual({b['art']: b['anzahl'] for b in layout['bloecke']}['aufgaben'], 10)
        self.assertEqual(len(daten['protokolle']), 2)
        popup = protokoll.with_user(self.user_a).wb_voip_popup_layout()
        self.assertEqual([b['art'] for b in popup['layout']['bloecke']], ['kontaktkopf', 'protokolle'])
        self.assertEqual(len(popup['protokolle']), 1)
        # es wurde nichts angelegt
        self.assertFalse(self.Layout.with_context(active_test=False).search(
            [('ist_standard', '=', True)]))

    def test_text_block_klartext(self):
        layout = self._layout([{'art': 'text', 'titel': 'Hinweis', 'text': '<b>Bitte</b> fragen'}])
        block = self._kontext()['layout']['bloecke'][0]
        self.assertEqual(layout.block_ids.art, 'text')
        self.assertEqual(block['text'], '<b>Bitte</b> fragen')
        self.assertEqual(block['titel'], 'Hinweis')

    def test_gruppen_layout_wirkt(self):
        gruppe = self.env['res.groups'].create({'name': 'Layout-Gruppe K'})
        eigenes = self._felder_layout('ref', group_ids=[(6, 0, [gruppe.id])])
        self.user_a.write({'group_ids': [(4, gruppe.id)]})
        daten = self._kontext()
        self.assertEqual(daten['layout']['id'], eigenes.id)
        self.assertEqual(daten['layout']['breite'], 'breit')
        block = daten['layout']['bloecke'][0]
        self.assertEqual((block['art'], block['titel'], block['spalte']), ('felder', 'Daten', '2'))
        self.assertEqual(self._werte(daten), {'ref': 'K-4711'})
        # B ist nicht in der Gruppe: Standard
        self.assertEqual(self._kontext(benutzer=self.user_b)['layout']['id'], self.std_fenster.id)

    # --- Feldrechte (Review Focus 1) ---

    def test_feld_hinter_gruppe(self):
        self._felder_layout('ref', 'email')
        felder = type(self.env['res.partner'])._fields
        with patch.object(felder['ref'], 'groups', 'wb_voip.group_wb_voip_manager'):
            ohne = self._werte(self._kontext())
            self.user_manager.write({'group_ids': [(4, self.env.ref('base.group_partner_manager').id)]})
            mit = self._werte(self._kontext(benutzer=self.user_manager))
        self.assertNotIn('ref', ohne)
        self.assertEqual(ohne['email'], 'lena@example.com')
        self.assertEqual(mit['ref'], 'K-4711')

    def test_kontakt_unlesbar_keine_felder(self):
        self._felder_layout('ref', 'email')
        daten = self._kontext(self._anruf(partner_id=self.fremder_kontakt.id))
        self.assertIs(daten['kontakt'], False)
        self.assertEqual(daten['layout']['bloecke'][0]['felder'], [])

    def test_many2one_ziel_unlesbar_weggelassen(self):
        self._felder_layout('parent_id', 'country_id')
        self.kontakt.parent_id = self.fremder_kontakt
        werte = self._werte(self._kontext())
        self.assertNotIn('parent_id', werte)
        self.assertEqual(werte['country_id'], self.env.ref('base.de').with_user(self.user_a).display_name)

    def test_many2many_nur_lesbare_ziele(self):
        self._felder_layout('category_id')
        kategorien = self.env['res.partner.category'].create(
            [{'name': f'Kat {i:02d}'} for i in range(12)])
        self.kontakt.category_id = kategorien
        wert = self._werte(self._kontext())['category_id']
        namen = wert.split(', ')
        self.assertEqual(len(namen), 11)
        self.assertEqual(namen[-1], '…')
        self.assertTrue(all(name.startswith('Kat ') for name in namen[:10]))
        # Kategorien ohne Leserecht: Feld fehlt
        self.env['ir.rule'].create({
            'name': 'keine Kategorien', 'model_id': self.env['ir.model']._get_id('res.partner.category'),
            'domain_force': "[('id', '=', 0)]",
        })
        self.assertNotIn('category_id', self._werte(self._kontext()))

    # --- Formatierung ---

    def test_formatierung_je_typ(self):
        self._felder_layout('ref', 'type', 'is_company', 'color', 'partner_latitude',
                            'country_id', 'create_date', 'phone')
        daten = self._kontext()
        block = daten['layout']['bloecke'][0]
        label = {f['name']: f['label'] for f in block['felder']}
        werte = self._werte(daten)
        env_a = self.env(user=self.user_a)
        partner = self.kontakt.with_user(self.user_a)
        self.assertEqual(label['ref'], partner._fields['ref']._description_string(env_a))
        self.assertEqual(werte['ref'], 'K-4711')
        typen = dict(partner._fields['type']._description_selection(env_a))
        self.assertEqual(werte['type'], typen['contact'])
        self.assertEqual(werte['is_company'], 'No')
        self.assertEqual(werte['color'], '3')
        self.assertEqual(werte['partner_latitude'], formatLang(env_a, 52.5, digits=7))
        self.assertEqual(werte['create_date'],
                         format_datetime(env_a, self.kontakt.create_date, tz='Europe/Berlin'))
        # alle Werte sind Text
        self.assertTrue(all(isinstance(w, str) for w in werte.values()))

    def test_leere_werte_weggelassen(self):
        self._felder_layout('ref', 'country_id', 'category_id', 'email')
        self.kontakt.write({'ref': False, 'country_id': False, 'email': False})
        self.assertEqual(self._werte(self._kontext()), {})

    def test_formatierung_text_datum_waehrung(self):
        # res.partner hat hier keine Text-, Datums- oder Betragsfelder: Formatierer direkt prüfen
        Layout = self.Layout.with_user(self.user_a)
        env_a = Layout.env
        gruppe = self.env['res.groups'].create({'name': 'G', 'comment': 'x' * 800})
        text = Layout._wb_voip_feldwert(gruppe.with_user(self.user_a), gruppe._fields['comment'])
        self.assertEqual(len(text), 500)
        self.assertTrue(text.endswith('…'))
        aktivitaet = self._aktivitaet()
        datum = Layout._wb_voip_feldwert(aktivitaet.with_user(self.user_a),
                                        aktivitaet._fields['date_deadline'])
        self.assertEqual(datum, format_date(env_a, aktivitaet.date_deadline))
        # Betrag mit der Währung des Datensatzes (Feld currency_id der Firma)
        firma = self.env.company.with_user(self.user_a)
        wert = Layout._wb_voip_wert_text(firma, fields.Monetary(string='Betrag'), 1234.5)
        self.assertEqual(wert, format_amount(env_a, 1234.5, firma.currency_id))
        self.assertIn(firma.currency_id.symbol, wert)

    def test_char_gekuerzt(self):
        self._felder_layout('ref')
        self.kontakt.write({'ref': 'y' * 800})
        wert = self._werte(self._kontext())['ref']
        self.assertEqual(len(wert), 500)
        self.assertTrue(wert.endswith('…'))

    def test_feldwerte_unlesbarer_kontakt_leer(self):
        felder = self._feld('ref') | self._feld('email')
        Layout = self.Layout.with_user(self.user_a)
        self.assertFalse(self.fremder_kontakt.with_user(self.user_a).has_access('read'))
        self.assertEqual(Layout._wb_voip_feldwerte(self.fremder_kontakt.with_user(self.user_a), felder), [])
        self.assertEqual(Layout._wb_voip_feldwerte(self.env['res.partner'].with_user(self.user_a), felder), [])

    # --- Anzahl ---

    def test_anzahl_steuert_protokolle_und_aufgaben(self):
        self._layout([{'art': 'protokolle', 'anzahl': 2}, {'art': 'aufgaben', 'anzahl': 3}])
        for i in range(6):
            self._protokoll(datum=fields.Datetime.now() - timedelta(hours=i))
            self._aktivitaet()
        daten = self._kontext()
        self.assertEqual(len(daten['protokolle']), 2)
        self.assertEqual(len(daten['aufgaben']), 3)

    def test_anzahl_hoechstens_20(self):
        self._layout([{'art': 'protokolle', 'anzahl': 20}, {'art': 'aufgaben', 'anzahl': 20}])
        for i in range(23):
            self._protokoll(datum=fields.Datetime.now() - timedelta(minutes=i))
            self._aktivitaet()
        daten = self._kontext()
        self.assertEqual(len(daten['protokolle']), 20)
        self.assertEqual(len(daten['aufgaben']), 20)

    def test_ohne_block_nicht_geladen(self):
        self._layout([{'art': 'kontaktkopf'}, {'art': 'notiz'}])
        self._protokoll()
        self._aktivitaet()
        daten = self._kontext()
        self.assertEqual(daten['protokolle'], [])
        self.assertEqual(daten['aufgaben'], [])
        self.assertTrue(daten['kontakt'])

    # --- Popup ---

    def test_popup_layout(self):
        layout = self._felder_layout('ref', ort='protokoll_popup')
        self.Block.create({'layout_id': layout.id, 'sequence': 9, 'art': 'protokolle', 'anzahl': 3})
        frueher = [self._protokoll(datum=datetime(2026, 11, tag, 9, 0)) for tag in range(1, 6)]
        fremd = self._protokoll(benutzer=self.user_b, datum=datetime(2026, 11, 7, 9, 0))
        aktuell = self._protokoll(datum=datetime(2026, 11, 8, 9, 0))
        daten = aktuell.with_user(self.user_a).wb_voip_popup_layout()
        self.assertEqual(set(daten), {'layout', 'kontakt', 'protokolle'})
        self.assertEqual(daten['layout']['id'], layout.id)
        self.assertEqual(self._werte(daten), {'ref': 'K-4711'})
        self.assertEqual(daten['kontakt']['id'], self.kontakt.id)
        ids = [p['id'] for p in daten['protokolle']]
        self.assertEqual(ids, [p.id for p in reversed(frueher[2:])])
        self.assertNotIn(aktuell.id, ids)
        self.assertNotIn(fremd.id, ids)

    def test_popup_layout_standard(self):
        daten = self._protokoll().with_user(self.user_a).wb_voip_popup_layout()
        self.assertEqual(daten['layout']['id'], self.std_popup.id)

    def test_popup_layout_fremdes_protokoll(self):
        protokoll = self._protokoll(benutzer=self.user_b)
        with self.assertRaises(AccessError):
            protokoll.with_user(self.user_a).wb_voip_popup_layout()

    def test_popup_layout_nur_voip_benutzer(self):
        protokoll = self._protokoll(benutzer=self.user_ohne)
        with self.assertRaises(AccessError):
            protokoll.with_user(self.user_ohne).wb_voip_popup_layout()

    def test_popup_layout_kontakt_unlesbar(self):
        self._felder_layout('ref', ort='protokoll_popup')
        protokoll = self._protokoll(partner_id=self.fremder_kontakt.id)
        self._protokoll(partner_id=self.fremder_kontakt.id)
        daten = protokoll.with_user(self.user_a).wb_voip_popup_layout()
        self.assertIs(daten['kontakt'], False)
        self.assertEqual(daten['protokolle'], [])
        self.assertEqual(self._werte(daten), {})
