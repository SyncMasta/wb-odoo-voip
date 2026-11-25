from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import WbVoipCase

NUMMER = '+49 170 2223334'


@tagged('wb_voip', 'post_install', '-at_install')
class TestHooks(WbVoipCase):
    """Extension points of wb_voip that other modules (e.g. a CRM bridge) override.

    Each hook is overridden here with ``patch.object`` on the registry class, the way a
    subclass in another module would; without the override behaviour stays unchanged.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Anruf = cls.env['wb.voip.anruf']
        cls.Layout = cls.env['wb.voip.layout']
        cls.Block = cls.env['wb.voip.layout.block']
        cls.firma = cls.env['res.partner'].create({'name': 'Hook GmbH', 'is_company': True})
        cls.kontakt = cls.env['res.partner'].create({
            'name': 'Hanna Hook', 'parent_id': cls.firma.id, 'phone': NUMMER})
        cls.anderer = cls.env['res.partner'].create({'name': 'Anderer Datensatz'})
        # Kontakt einer fremden Firma: für A nicht lesbar (Mehrfirmen-Regel)
        cls.fremdfirma = cls.env['res.company'].create({'name': 'Fremdfirma H'})
        cls.fremd = cls.env['res.partner'].create({
            'name': 'Geheim H', 'company_id': cls.fremdfirma.id})
        cls.typ = cls.env.ref('wb_voip.wb_voip_activity_type_rueckruf')
        cls.env.ref('wb_voip.wb_voip_aktion_neuer_kontakt').active = False

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()
        self._nr = 0

    # --- Hilfen ---

    def _ueberschreiben(self, modell, name, funktion, neu=False):
        """Override method ``name`` of ``modell`` for this test (like a subclass would)."""
        patcher = patch.object(type(self.env[modell]), name, funktion, create=neu)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _beginnen(self, res_model=False, res_id=False, richtung='eingehend', nummer=NUMMER):
        self._nr += 1
        Anruf = self.Anruf.with_user(self.user_a)
        daten = Anruf.wb_voip_beginnen(richtung, nummer, f'hook-{self._nr}', res_model, res_id)
        return Anruf.browse(daten['id']), daten

    def _verpasst(self):
        anruf, _daten = self._beginnen()
        self.assertEqual(anruf.wb_voip_beendet(False, '487')['zustand'], 'verpasst')
        return anruf.sudo()

    # --- Zuordnung beim Anruf ---

    def test_zuordnung_standard_keine(self):
        self.assertIsNone(self.Anruf.with_user(self.user_a)._wb_voip_zuordnung(self.kontakt, None))
        anruf, daten = self._beginnen()
        self.assertEqual(anruf.sudo().partner_id, self.kontakt)
        self.assertFalse(daten['res_model'])
        self.assertFalse(daten['res_id'])

    def test_zuordnung_setzt_datensatz(self):
        aufrufe = []

        def zuordnung(anruf_modell, partner, ziel):
            aufrufe.append((anruf_modell.env.uid, anruf_modell.env.su, partner, ziel))
            return self.anderer

        self._ueberschreiben('wb.voip.anruf', '_wb_voip_zuordnung', zuordnung)
        anruf, daten = self._beginnen()
        self.assertEqual((daten['res_model'], daten['res_id']), ('res.partner', self.anderer.id))
        self.assertEqual(daten['res_name'], 'Anderer Datensatz')
        self.assertEqual(anruf.sudo().partner_id, self.kontakt)
        # als Benutzer (nie sudo), mit gewähltem Kontakt und ohne übergebenes Ziel
        self.assertEqual(len(aufrufe), 1)
        uid, su, partner, ziel = aufrufe[0]
        self.assertEqual(uid, self.user_a.id)
        self.assertFalse(su)
        self.assertEqual(partner, self.kontakt)
        self.assertEqual(partner.env.uid, self.user_a.id)
        self.assertIsNone(ziel)

    def test_zuordnung_ohne_kontakt_leerer_partner(self):
        aufrufe = []

        def zuordnung(anruf_modell, partner, ziel):
            aufrufe.append(partner)

        self._ueberschreiben('wb.voip.anruf', '_wb_voip_zuordnung', zuordnung)
        self._beginnen(nummer='+49 30 999999')
        self.assertEqual(len(aufrufe), 1)
        self.assertFalse(aufrufe[0])
        self.assertEqual(aufrufe[0]._name, 'res.partner')

    def test_zuordnung_unlesbar_ignoriert(self):
        self._ueberschreiben('wb.voip.anruf', '_wb_voip_zuordnung',
                             lambda anruf_modell, partner, ziel: self.fremd)
        _anruf, daten = self._beginnen()
        self.assertFalse(daten['res_model'])
        self.assertFalse(daten['res_id'])

    def test_zuordnung_mehrere_ignoriert(self):
        self._ueberschreiben('wb.voip.anruf', '_wb_voip_zuordnung',
                             lambda anruf_modell, partner, ziel: self.anderer | self.firma)
        _anruf, daten = self._beginnen()
        self.assertFalse(daten['res_model'])

    def test_zuordnung_uebergebenes_ziel_hat_vorrang(self):
        land = self.env.ref('base.de')
        aufrufe = []

        def zuordnung(anruf_modell, partner, ziel):
            aufrufe.append(ziel)
            return self.anderer

        self._ueberschreiben('wb.voip.anruf', '_wb_voip_zuordnung', zuordnung)
        _anruf, daten = self._beginnen('res.country', land.id)
        self.assertEqual((daten['res_model'], daten['res_id']), ('res.country', land.id))
        self.assertEqual(aufrufe, [land])

    def test_zuordnung_ziel_ist_der_kontakt(self):
        # Klick am Kontakt: das Ziel ist nur der Kontakt selbst, die Zuordnung darf es ersetzen
        self._ueberschreiben('wb.voip.anruf', '_wb_voip_zuordnung',
                             lambda anruf_modell, partner, ziel: self.anderer)
        anruf, daten = self._beginnen('res.partner', self.kontakt.id)
        self.assertEqual((daten['res_model'], daten['res_id']), ('res.partner', self.anderer.id))
        self.assertEqual(anruf.sudo().partner_id, self.kontakt)

    def test_zuordnung_abgewiesen(self):
        self._ueberschreiben('wb.voip.anruf', '_wb_voip_zuordnung',
                             lambda anruf_modell, partner, ziel: self.anderer)
        daten = self.Anruf.with_user(self.user_a).wb_voip_abgewiesen_melden(NUMMER, 'hook-480')
        self.assertEqual(daten['zustand'], 'verpasst')
        self.assertEqual((daten['res_model'], daten['res_id']), ('res.partner', self.anderer.id))

    # --- Rückruf-Ziel ---

    def test_rueckruf_ziel_standard_kontakt(self):
        anruf = self._verpasst()
        self.assertEqual(anruf._wb_voip_rueckruf_ziel(), self.kontakt)
        aktivitaet = anruf.rueckruf_aktivitaet_id
        self.assertEqual((aktivitaet.res_model, aktivitaet.res_id), ('res.partner', self.kontakt.id))

    def test_rueckruf_ziel_ueberschrieben(self):
        self._ueberschreiben('wb.voip.anruf', '_wb_voip_rueckruf_ziel', lambda anruf: self.anderer)
        erster = self._verpasst()
        aktivitaet = erster.rueckruf_aktivitaet_id
        self.assertEqual((aktivitaet.res_model, aktivitaet.res_id), ('res.partner', self.anderer.id))
        self.assertEqual(aktivitaet.user_id, self.user_a)
        # offene Aktivität am Ziel wird wiederverwendet (Suche am Ziel, nicht am Kontakt)
        zweiter = self._verpasst()
        self.assertEqual(zweiter.rueckruf_aktivitaet_id, aktivitaet)
        am_kontakt = self.env['mail.activity'].sudo().search_count([
            ('activity_type_id', '=', self.typ.id), ('res_model', '=', 'res.partner'),
            ('res_id', '=', self.kontakt.id)])
        self.assertEqual(am_kontakt, 0)

    def test_rueckruf_ziel_unlesbar_faellt_auf_kontakt(self):
        self._ueberschreiben('wb.voip.anruf', '_wb_voip_rueckruf_ziel', lambda anruf: self.fremd)
        aktivitaet = self._verpasst().rueckruf_aktivitaet_id
        self.assertEqual((aktivitaet.res_model, aktivitaet.res_id), ('res.partner', self.kontakt.id))

    def test_rueckruf_ziel_ohne_aktivitaeten_faellt_auf_kontakt(self):
        land = self.env.ref('base.de')
        self._ueberschreiben('wb.voip.anruf', '_wb_voip_rueckruf_ziel', lambda anruf: land)
        aktivitaet = self._verpasst().rueckruf_aktivitaet_id
        self.assertEqual((aktivitaet.res_model, aktivitaet.res_id), ('res.partner', self.kontakt.id))

    def test_rueckruf_ziel_leer_faellt_auf_kontakt(self):
        self._ueberschreiben('wb.voip.anruf', '_wb_voip_rueckruf_ziel',
                             lambda anruf: self.env['res.partner'])
        aktivitaet = self._verpasst().rueckruf_aktivitaet_id
        self.assertEqual((aktivitaet.res_model, aktivitaet.res_id), ('res.partner', self.kontakt.id))

    def test_rueckruf_ziel_lesbar_fuer_besitzer_geprueft(self):
        # das Ziel wird mit den Rechten des Besitzers geprüft, nicht mit sudo
        gesehen = []

        def ziel(anruf):
            gesehen.append(anruf.env.su)
            return self.fremd

        self._ueberschreiben('wb.voip.anruf', '_wb_voip_rueckruf_ziel', ziel)
        aktivitaet = self._verpasst().rueckruf_aktivitaet_id
        self.assertTrue(gesehen)
        self.assertEqual(aktivitaet.res_id, self.kontakt.id)

    # --- Kontext: Datensatz nur bei Leserecht ---

    def test_client_daten_datensatzname(self):
        _anruf, daten = self._beginnen('res.partner', self.anderer.id)
        self.assertEqual(daten['res_name'], 'Anderer Datensatz')
        anruf, daten = self._beginnen()
        self.assertFalse(daten['res_name'])

    def test_client_daten_datensatz_unlesbar(self):
        anruf, _daten = self._beginnen()
        anruf.sudo().write({'res_model': 'res.partner', 'res_id': self.fremd.id})
        daten = anruf.wb_voip_kontext()['anruf']
        self.assertFalse(daten['res_model'])
        self.assertFalse(daten['res_id'])
        self.assertFalse(daten['res_name'])

    # --- Anruf-Buttons: Ausführung je Art ---

    def test_aktion_arten_haben_ausfuehrung(self):
        Aktion = type(self.env['wb.voip.aktion'])
        for art, _label in self.env['wb.voip.aktion']._fields['art'].selection:
            self.assertTrue(callable(getattr(Aktion, f'_wb_voip_aktion_{art}', None)), art)

    def test_aktion_ausfuehrung_ueber_methode(self):
        aufrufe = []

        def url(aktion, anruf, partner):
            aufrufe.append((aktion, anruf.env.uid, anruf.env.su, partner))
            return {'type': 'ir.actions.act_url', 'url': 'https://example.com/x', 'target': 'new'}

        self._ueberschreiben('wb.voip.aktion', '_wb_voip_aktion_url', url)
        aktion = self.env['wb.voip.aktion'].create({
            'name': 'Web', 'art': 'url', 'url_vorlage': 'https://example.com/{nummer}',
            'ort_anrufer_fenster': True})
        anruf, _daten = self._beginnen()
        ergebnis = anruf.wb_voip_aktion_ausfuehren(aktion.id)
        self.assertEqual(ergebnis['url'], 'https://example.com/x')
        self.assertEqual(len(aufrufe), 1)
        self.assertEqual(aufrufe[0][0], aktion)
        self.assertEqual(aufrufe[0][1:3], (self.user_a.id, False))
        self.assertEqual(aufrufe[0][3], self.kontakt)

    def test_aktion_unbekannte_art(self):
        aktion = self.env['wb.voip.aktion'].create({
            'name': 'Alt', 'art': 'url', 'url_vorlage': 'https://example.com/',
            'ort_anrufer_fenster': True})
        # Art eines deinstallierten Moduls, die die Auswahl nicht mehr kennt
        self.env.cr.execute("UPDATE wb_voip_aktion SET art = 'gibt_es_nicht' WHERE id = %s",
                            [aktion.id])
        aktion.invalidate_recordset(['art'])
        anruf, _daten = self._beginnen()
        with self.assertRaises(UserError):
            anruf.wb_voip_aktion_ausfuehren(aktion.id)

    # --- Layout: Blockarten und Block-Daten ---

    def test_erlaubte_arten_standard(self):
        arten = self.Layout._wb_voip_erlaubte_arten()
        self.assertEqual(arten['protokoll_popup'], ('kontaktkopf', 'felder', 'protokolle', 'text'))
        self.assertIn('buttons', arten['anrufer_fenster'])
        # Ergebnis ist eine Kopie: Ändern wirkt nicht auf den nächsten Aufruf
        arten['protokoll_popup'] += ('notiz',)
        self.assertNotIn('notiz', self.Layout._wb_voip_erlaubte_arten()['protokoll_popup'])
        # Brückenmodule (z. B. wb_voip_crm) dürfen weitere Arten ergänzen
        self.assertEqual(self.Layout._wb_voip_gezaehlte_arten()[:2], ('protokolle', 'aufgaben'))

    def _popup_mit_notiz_erlauben(self):
        original = type(self.Layout)._wb_voip_erlaubte_arten

        def erlaubte_arten(layout):
            arten = original(layout)
            arten['protokoll_popup'] += ('notiz',)
            return arten

        self._ueberschreiben('wb.voip.layout', '_wb_voip_erlaubte_arten', erlaubte_arten)

    def test_erlaubte_arten_erweitert(self):
        layout = self.Layout.create({'name': 'Popup', 'ort': 'protokoll_popup'})
        with self.assertRaises(ValidationError):
            self.Block.create({'layout_id': layout.id, 'art': 'notiz'})
        self._popup_mit_notiz_erlauben()
        self.Block.create({'layout_id': layout.id, 'art': 'notiz'})
        self.assertIn('notiz', layout._wb_voip_gueltige_bloecke().mapped('art'))
        daten = self.Layout.with_user(self.user_manager).wb_voip_designer_laden(layout.id)
        self.assertIn('notiz', [a['art'] for a in daten['arten']['protokoll_popup']])
        # der Designer prüft gegen dieselbe Liste
        entwurf = {k: daten['layout'][k] for k in ('name', 'ort', 'breite', 'group_ids')}
        entwurf['bloecke'] = [{'art': 'notiz', 'spalte': '1'}]
        self.Layout.with_user(self.user_manager).wb_voip_designer_speichern(layout.id, entwurf)

    def test_gezaehlte_arten_erweitert(self):
        layout = self.Layout.create({'name': 'Zaehlen', 'ort': 'anrufer_fenster', 'sequence': 1})
        block = self.Block.create({'layout_id': layout.id, 'art': 'text', 'text': 'x', 'anzahl': 50})
        daten = self.Layout.with_user(self.user_manager).wb_voip_designer_laden(layout.id)
        eintraege = {a['art']: a for a in daten['arten']['anrufer_fenster']}
        self.assertTrue(eintraege['protokolle']['gezaehlt'])
        self.assertFalse(eintraege['text']['gezaehlt'])
        self._ueberschreiben('wb.voip.layout', '_wb_voip_gezaehlte_arten',
                             lambda layout: ('protokolle', 'aufgaben', 'text'))
        with self.assertRaises(ValidationError):
            block.anzahl = 50
            block.flush_recordset()
        block.anzahl = 3
        self.assertEqual(layout._wb_voip_anzahl('text'), 3)
        bloecke = layout._wb_voip_fuer_client(self.env['res.partner'])['bloecke']
        self.assertEqual(bloecke[0]['anzahl'], 3)
        daten = self.Layout.with_user(self.user_manager).wb_voip_designer_laden(layout.id)
        self.assertEqual(daten['layout']['bloecke'][0]['anzahl'], 3)
        self.assertTrue({a['art']: a for a in daten['arten']['anrufer_fenster']}['text']['gezaehlt'])

    def test_block_daten_standard_ohne_schluessel(self):
        anruf, _daten = self._beginnen()
        for block in anruf.wb_voip_kontext()['layout']['bloecke']:
            self.assertNotIn('daten', block)

    def test_block_daten_hook(self):
        layout = self.Layout.create({'name': 'Mit Daten', 'ort': 'anrufer_fenster', 'sequence': 1})
        self.Block.create({'layout_id': layout.id, 'art': 'text', 'text': 'Hallo', 'titel': 'T'})
        aufrufe = []

        def block_daten(layout_modell, anruf, block, partner):
            aufrufe.append((layout_modell.env.uid, layout_modell.env.su, anruf, dict(block), partner))
            return {'zeilen': [partner.display_name if partner else '']}

        self._ueberschreiben('wb.voip.layout', '_wb_voip_block_daten_text', block_daten, neu=True)
        anruf, _daten = self._beginnen()
        block = anruf.wb_voip_kontext()['layout']['bloecke'][0]
        self.assertEqual(block['daten'], {'zeilen': ['Hook GmbH, Hanna Hook']})
        uid, su, anruf_arg, block_arg, partner = aufrufe[0]
        self.assertEqual((uid, su), (self.user_a.id, False))
        self.assertEqual(anruf_arg, anruf)
        self.assertEqual(anruf_arg.env.uid, self.user_a.id)
        self.assertEqual(block_arg['art'], 'text')
        self.assertEqual(block_arg['titel'], 'T')
        self.assertEqual(partner, self.kontakt)
        # Vorschau im Designer: ohne Anruf (leer), mit dem Beispielkontakt
        aufrufe.clear()
        self.Layout.with_user(self.user_manager).wb_voip_designer_vorschau(
            layout.id, self.kontakt.id)
        self.assertEqual(len(aufrufe), 1)
        self.assertFalse(aufrufe[0][2])
        self.assertEqual(aufrufe[0][2]._name, 'wb.voip.anruf')
        self.assertEqual(aufrufe[0][4], self.kontakt)

    def test_block_daten_popup(self):
        popup = self.Layout.create({'name': 'Popup D', 'ort': 'protokoll_popup', 'sequence': 1})
        self.Block.create({'layout_id': popup.id, 'art': 'text', 'text': 'P'})
        gesehen = []

        def block_daten(layout_modell, anruf, block, partner):
            gesehen.append(anruf)
            return 7

        self._ueberschreiben('wb.voip.layout', '_wb_voip_block_daten_text', block_daten, neu=True)
        anruf, _daten = self._beginnen()
        anruf.wb_voip_beendet(False, '487')
        protokoll = anruf.sudo().protokoll_id.with_user(self.user_a)
        daten = protokoll.wb_voip_popup_layout()
        self.assertEqual(daten['layout']['bloecke'][0]['daten'], 7)
        self.assertEqual(gesehen, [anruf])

    def test_block_daten_popup_nur_eigener_anruf(self):
        popup = self.Layout.create({'name': 'Popup E', 'ort': 'protokoll_popup', 'sequence': 1})
        self.Block.create({'layout_id': popup.id, 'art': 'text', 'text': 'P'})
        gesehen = []

        def block_daten(layout_modell, anruf, block, partner):
            gesehen.append(anruf)
            return 1

        self._ueberschreiben('wb.voip.layout', '_wb_voip_block_daten_text', block_daten, neu=True)
        anruf, _daten = self._beginnen()
        anruf.wb_voip_beendet(False, '487')
        protokoll = anruf.sudo().protokoll_id
        # Das Protokoll gehört user_a, der verknüpfte Anruf einem anderen Benutzer
        fremder = self.Anruf.sudo().create({
            'user_id': self.user_b.id, 'richtung': 'eingehend', 'zustand': 'beendet',
            'nummer': '+49 30 99', 'sip_call_id': 'popup-fremd'})
        protokoll.anruf_id = fremder
        protokoll.with_user(self.user_a).wb_voip_popup_layout()
        self.assertEqual(len(gesehen), 1)
        self.assertFalse(gesehen[0])
        self.assertEqual(gesehen[0]._name, 'wb.voip.anruf')

    # --- Nachbesserung: Last, Robustheit, kein sudo ---

    def _anrufe_mit_datensatz(self, anzahl):
        datensaetze = self.env['res.partner'].create(
            [{'name': f'Datensatz {i}'} for i in range(anzahl)])
        return self.Anruf.sudo().create([{
            'user_id': self.user_a.id, 'richtung': 'ausgehend', 'zustand': 'beendet',
            'nummer': f'+49 30 {i:06d}', 'sip_call_id': f'last-{anzahl}-{i}',
            'res_model': 'res.partner', 'res_id': d.id,
        } for i, d in enumerate(datensaetze)])

    def _abfragen_liste(self):
        Anruf = self.Anruf.with_user(self.user_a)
        self.env.invalidate_all()
        vorher = self.env.cr.sql_log_count
        liste = Anruf.wb_voip_liste(limit=50)
        return self.env.cr.sql_log_count - vorher, liste

    def test_liste_datensaetze_gebuendelt(self):
        self._anrufe_mit_datensatz(2)
        wenige, liste = self._abfragen_liste()
        self.assertEqual(len(liste), 2)
        self._anrufe_mit_datensatz(20)
        viele, liste = self._abfragen_liste()
        self.assertEqual(len(liste), 22)
        self.assertTrue(all(e['res_name'].startswith('Datensatz') for e in liste))
        # Datensätze je Modell auf einmal geprüft, nicht je Anruf
        self.assertLessEqual(viele - wenige, 3)

    def test_liste_unlesbarer_datensatz(self):
        anrufe = self._anrufe_mit_datensatz(1)
        anrufe[0].res_id = self.fremd.id
        _abfragen, liste = self._abfragen_liste()
        self.assertEqual((liste[0]['res_model'], liste[0]['res_id'], liste[0]['res_name']),
                         (False, False, False))

    def test_datensatzname_fehler_bricht_nichts(self):
        # Anruf ohne Kontakt, aber mit Datensatz (sonst wäre der Datensatz der Kontakt)
        anruf = self.Anruf.sudo().create({
            'user_id': self.user_a.id, 'richtung': 'ausgehend', 'zustand': 'klingelt',
            'nummer': '+49 30 4711', 'sip_call_id': 'name-kaputt',
            'res_model': 'res.partner', 'res_id': self.anderer.id,
        }).with_user(self.user_a)

        def kaputt(datensaetze):
            raise AccessError('kein Zugriff auf verknüpfte Daten')

        self._ueberschreiben('res.partner', '_compute_display_name', kaputt)
        self.env.invalidate_all()
        daten = anruf.wb_voip_angenommen()
        self.assertEqual((daten['res_model'], daten['res_id']), ('res.partner', self.anderer.id))
        self.assertFalse(daten['res_name'])
        self.assertFalse(self.Anruf.with_user(self.user_a).wb_voip_liste()[0]['res_name'])

    def test_block_daten_nie_als_superuser(self):
        layout = self.Layout.create({'name': 'Su', 'ort': 'anrufer_fenster', 'sequence': 1})
        self.Block.create({'layout_id': layout.id, 'art': 'text', 'text': 'x'})
        aufrufe = []

        def block_daten(layout_modell, anruf, block, partner):
            aufrufe.append(layout_modell.env.su)
            return 1

        self._ueberschreiben('wb.voip.layout', '_wb_voip_block_daten_text', block_daten, neu=True)
        bloecke = layout.sudo()._wb_voip_fuer_client(self.kontakt.sudo())['bloecke']
        self.assertNotIn('daten', bloecke[0])
        self.assertEqual(aufrufe, [])
        bloecke = layout._wb_voip_fuer_client(self.kontakt.with_user(self.user_a))['bloecke']
        self.assertEqual(bloecke[0]['daten'], 1)
        self.assertEqual(aufrufe, [False])

    def test_hook_hilfe_ausserhalb_des_praefixes(self):
        Layout = type(self.Layout)
        # Hooks gibt es nur in anderen Modulen (z. B. wb_voip_crm), nie im Kern selbst
        for name in dir(Layout):
            if name.startswith('_wb_voip_block_daten_'):
                modul = getattr(Layout, name).__module__
                self.assertFalse(modul.startswith('odoo.addons.wb_voip.'), name)
        self.assertTrue(callable(getattr(Layout, '_wb_voip_block_hook', None)))
