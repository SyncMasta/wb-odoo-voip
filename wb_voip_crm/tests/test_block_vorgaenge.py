from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools.misc import format_amount

from .common import WbVoipCrmCase


@tagged('wb_voip_crm', 'post_install', '-at_install')
class TestBlockVorgaenge(WbVoipCrmCase):
    """Layout block "Vorgänge": open leads of the contact, only what the user may read."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Layout = cls.env['wb.voip.layout']
        cls.standard = cls.Layout.search(
            [('ort', '=', 'anrufer_fenster'), ('ist_standard', '=', True)], limit=1)

    def _block(self, benutzer, anruf=None, partner=None, anzahl=5):
        """Return the block data hook result as ``benutzer`` (never sudo)."""
        Layout = self.Layout.with_user(benutzer)
        anruf = anruf if anruf is not None else self.env['wb.voip.anruf'].with_user(benutzer)
        partner = (partner or self.kontakt).with_user(benutzer)
        return Layout._wb_voip_block_daten_vorgaenge(
            anruf, {'art': 'vorgaenge', 'anzahl': anzahl}, partner)

    def _anruf_von(self, benutzer):
        daten = self._beginnen(benutzer)
        return self.env['wb.voip.anruf'].with_user(benutzer).browse(daten['id'])

    # --- Blockart ---

    def test_art_im_anrufer_fenster_gezaehlt(self):
        arten = self.Layout._wb_voip_erlaubte_arten()
        self.assertIn('vorgaenge', arten['anrufer_fenster'])
        self.assertIn('vorgaenge', self.Layout._wb_voip_gezaehlte_arten())
        # das Popup hat keinen Zuordnen-Knopf: dort gibt es den Block nicht
        self.assertNotIn('vorgaenge', arten['protokoll_popup'])
        # der Kern bleibt unverändert
        self.assertEqual(arten['anrufer_fenster'][:2], ('kontaktkopf', 'felder'))

    def test_block_in_layout_anlegen(self):
        block = self.env['wb.voip.layout.block'].create({
            'layout_id': self.standard.id, 'art': 'vorgaenge', 'titel': 'Vorgänge'})
        self.assertEqual(block.anzahl, 5)
        with self.assertRaises(ValidationError):
            block.anzahl = 21
        with self.assertRaises(ValidationError):
            block.anzahl = 0
        popup = self.Layout.search([('ort', '=', 'protokoll_popup'), ('ist_standard', '=', True)])
        with self.assertRaises(ValidationError):
            self.env['wb.voip.layout.block'].create({'layout_id': popup.id, 'art': 'vorgaenge'})

    def test_designer_kennt_die_art(self):
        daten = self.Layout.with_user(self.user_manager).wb_voip_designer_laden(False)
        arten = {a['art']: a for a in daten['arten']['anrufer_fenster']}
        self.assertEqual(arten['vorgaenge'], {'art': 'vorgaenge', 'label': 'Vorgänge',
                                             'gezaehlt': True})
        self.assertNotIn('vorgaenge', [a['art'] for a in daten['arten']['protokoll_popup']])

    # --- Daten ---

    def test_nur_offene_lesbare_vorgaenge(self):
        eigener = self._lead(name='Eigener')
        self._lead(name='Fremder', user_id=self.vertrieb_alle.id)  # V1: Regel „eigene“
        gewonnen = self._lead(name='Gewonnen')
        gewonnen.action_set_won()
        archiviert = self._lead(name='Archiviert')
        archiviert.action_archive()
        self._lead(name='Anderer Kontakt', partner=self.firma)
        fremdfirma = self._lead(name='Andere Firma')
        fremdfirma.company_id = self.fremdfirma
        daten = self._block(self.vertrieb_eigene)
        self.assertEqual([v['name'] for v in daten], ['Eigener'])
        self.assertEqual(daten[0]['id'], eigener.id)
        self.assertEqual(set(daten[0]),
                         {'id', 'name', 'stufe', 'erwarteter_umsatz', 'zugeordnet'})
        # V2 sieht alle offenen Vorgänge des Kontakts
        namen = {v['name'] for v in self._block(self.vertrieb_alle)}
        self.assertEqual(namen, {'Eigener', 'Fremder'})

    def test_firma_des_kontakts(self):
        lead = self._lead(name='Firmenvorgang', partner=self.firma)
        daten = self._block(self.vertrieb_eigene, partner=self.ansprechpartner)
        self.assertEqual([v['id'] for v in daten], [lead.id])

    def test_ohne_crm_recht_leer(self):
        self._lead(name='Geheim')
        self.assertEqual(self._block(self.ohne_crm), [])

    def test_ohne_kontakt_leer(self):
        self._lead()
        leer = self.env['res.partner']
        Layout = self.Layout.with_user(self.vertrieb_eigene)
        self.assertEqual(Layout._wb_voip_block_daten_vorgaenge(
            self.env['wb.voip.anruf'].with_user(self.vertrieb_eigene), {'anzahl': 5}, leer), [])

    def test_anzahl_und_reihenfolge(self):
        for nr in range(4):
            self._lead(name=f'V{nr}')
        namen = [v['name'] for v in self._block(self.vertrieb_eigene, anzahl=3)]
        self.assertEqual(namen, ['V3', 'V2', 'V1'])  # zuletzt geändert zuerst
        # ungültige Anzahl wird begrenzt, nicht ausgeführt
        self.assertEqual(len(self._block(self.vertrieb_eigene, anzahl=0)), 1)
        self.assertEqual(len(self._block(self.vertrieb_eigene, anzahl=10 ** 9)), 4)
        self.assertEqual(len(self._block(self.vertrieb_eigene, anzahl='x')), 4)

    def test_stufe_und_umsatz(self):
        lead = self._lead(name='Mit Umsatz', expected_revenue=1500)
        daten = self._block(self.vertrieb_eigene)[0]
        self.assertEqual(daten['stufe'], lead.stage_id.name)
        self.assertEqual(daten['erwarteter_umsatz'], format_amount(
            self.env(user=self.vertrieb_eigene), 1500, lead.company_currency))
        ohne = self._lead(name='Ohne Umsatz', expected_revenue=0)
        daten = {v['id']: v for v in self._block(self.vertrieb_eigene)}
        self.assertFalse(daten[ohne.id]['erwarteter_umsatz'])

    def test_umsatz_nur_wenn_feld_lesbar(self):
        self._lead(name='Mit Umsatz', expected_revenue=1500)
        Lead = type(self.env['crm.lead'])
        original = Lead._has_field_access

        def ohne_umsatz(modell, feld, operation):
            if feld.name == 'expected_revenue':
                return False
            return original(modell, feld, operation)

        self.patch(Lead, '_has_field_access', ohne_umsatz)
        daten = self._block(self.vertrieb_eigene)[0]
        self.assertFalse(daten['erwarteter_umsatz'])
        self.assertEqual(daten['name'], 'Mit Umsatz')

    def test_zugeordnet_markiert(self):
        erster = self._lead(name='Erster')
        zweiter = self._lead(name='Zweiter')
        anruf = self._anruf_von(self.vertrieb_eigene)
        # zwei offene Vorgänge, beide eigene: der zuletzt geänderte wurde zugeordnet
        daten = {v['id']: v for v in self._block(self.vertrieb_eigene, anruf=anruf)}
        self.assertEqual(sum(v['zugeordnet'] for v in daten.values()), 1)
        anruf.wb_voip_vorgang_setzen(erster.id)
        daten = {v['id']: v for v in self._block(self.vertrieb_eigene, anruf=anruf)}
        self.assertTrue(daten[erster.id]['zugeordnet'])
        self.assertFalse(daten[zweiter.id]['zugeordnet'])
        anruf.wb_voip_vorgang_setzen(False)
        daten = self._block(self.vertrieb_eigene, anruf=anruf)
        self.assertFalse(any(v['zugeordnet'] for v in daten))

    def test_vorschau_ohne_anruf_nie_zugeordnet(self):
        self._lead(name='Vorschau')
        daten = self._block(self.vertrieb_eigene)
        self.assertEqual([v['zugeordnet'] for v in daten], [False])

    # --- im Kontext des Fensters ---

    def test_im_fensterkontext(self):
        self.env['wb.voip.layout.block'].create({
            'layout_id': self.standard.id, 'art': 'vorgaenge', 'sequence': 99})
        lead = self._lead(name='Im Fenster')
        anruf = self._anruf_von(self.vertrieb_eigene)
        kontext = anruf.wb_voip_kontext()
        bloecke = [b for b in kontext['layout']['bloecke'] if b['art'] == 'vorgaenge']
        self.assertEqual(len(bloecke), 1)
        self.assertEqual(bloecke[0]['anzahl'], 5)
        self.assertEqual([v['id'] for v in bloecke[0]['daten']], [lead.id])
        self.assertTrue(bloecke[0]['daten'][0]['zugeordnet'])

    def test_designer_vorschau(self):
        self.env['wb.voip.layout.block'].create({
            'layout_id': self.standard.id, 'art': 'vorgaenge', 'sequence': 99})
        self._lead(name='Nur sichtbar mit Recht')
        Layout = self.Layout.with_user(self.user_manager)
        vorschau = Layout.wb_voip_designer_vorschau(self.standard.id, self.kontakt.id)
        bloecke = [b for b in vorschau['layout']['bloecke'] if b['art'] == 'vorgaenge']
        # Verwalter ohne CRM-Recht: leer, ohne Fehler
        self.assertEqual([b['daten'] for b in bloecke], [[]])
