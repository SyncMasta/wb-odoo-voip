from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import WbVoipCrmCase


@tagged('wb_voip_crm', 'post_install', '-at_install')
class TestAktionVorgang(WbVoipCrmCase):
    """Button type "Vorgang anlegen": lead created as the user, title template as plain text."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team = cls.env['crm.team'].create({'name': 'Team Vorgangsbutton'})

    def _button(self, **werte):
        vals = {'name': 'Vorgang anlegen', 'art': 'vorgang', 'ort_anrufer_fenster': True,
                'titel_vorlage': 'Rückruf {kontakt} ({nummer}) {datum}'}
        vals.update(werte)
        return self.env['wb.voip.aktion'].sudo().create(vals)

    def _ausfuehren(self, benutzer, button, **beginnen):
        daten = self._beginnen(benutzer, **beginnen)
        anruf = self.env['wb.voip.anruf'].with_user(benutzer).browse(daten['id'])
        return anruf, anruf.wb_voip_aktion_ausfuehren(button.id)

    # --- Art und Konfiguration ---

    def test_art_und_ondelete(self):
        feld = self.env['wb.voip.aktion']._fields['art']
        self.assertIn('vorgang', dict(feld.selection))
        # Ohne das Modul wären solche Buttons sinnlos: sie werden mit gelöscht
        self.assertEqual(feld.ondelete['vorgang'], 'cascade')

    def test_felder_nur_fuer_verwalter(self):
        for name in ('crm_team_id', 'titel_vorlage'):
            self.assertEqual(self.env['wb.voip.aktion']._fields[name].groups,
                             'wb_voip.group_wb_voip_manager')

    def test_assistent_bietet_art_nicht_an(self):
        arten = dict(self.env['wb.voip.aktion.assistent']._fields['art']
                     ._description_selection(self.env))
        self.assertNotIn('vorgang', arten)

    def test_vorlage_hoechstens_200_zeichen(self):
        self._button(titel_vorlage='x' * 200)
        with self.assertRaises(ValidationError):
            self._button(titel_vorlage='x' * 201)

    def test_button_im_anruf_kontext(self):
        button = self._button()
        daten = self._beginnen(self.vertrieb_eigene)
        anruf = self.env['wb.voip.anruf'].with_user(self.vertrieb_eigene).browse(daten['id'])
        aktionen = anruf.wb_voip_kontext()['aktionen']
        gefunden = [a for a in aktionen if a['id'] == button.id]
        self.assertEqual([a['art'] for a in gefunden], ['vorgang'])
        # keine Vorlage, kein Team an den Browser
        self.assertEqual(set(gefunden[0]), {'id', 'name', 'icon', 'farbe', 'art', 'orte'})

    # --- Ausführung ---

    def test_legt_vorgang_als_benutzer_an(self):
        button = self._button(crm_team_id=self.team.id)
        daten = self._beginnen(self.vertrieb_eigene)
        anruf = self.env['wb.voip.anruf'].with_user(self.vertrieb_eigene).browse(daten['id'])
        self._protokoll(anruf.sudo())
        ergebnis = anruf.wb_voip_aktion_ausfuehren(button.id)
        lead = self.env['crm.lead'].sudo().search([('partner_id', '=', self.kontakt.id)])
        self.assertEqual(len(lead), 1)
        self.assertEqual(lead.type, 'opportunity')
        self.assertEqual(lead.user_id, self.vertrieb_eigene)
        self.assertEqual(lead.create_uid, self.vertrieb_eigene)
        self.assertEqual(lead.team_id, self.team)
        self.assertEqual(lead.partner_id, self.kontakt)
        self.assertTrue(lead.name.startswith(f'Rückruf Erika Vorgang ({anruf.sudo().nummer}) '))
        # Formular des Vorgangs
        self.assertEqual(ergebnis['type'], 'ir.actions.act_window')
        self.assertEqual((ergebnis['res_model'], ergebnis['res_id']), ('crm.lead', lead.id))
        self.assertEqual(ergebnis['views'], [[False, 'form']])
        self.assertEqual(ergebnis['target'], 'current')
        # Anruf und Protokoll zeigen auf den Vorgang
        self.assertEqual((anruf.sudo().res_model, anruf.sudo().res_id), ('crm.lead', lead.id))
        protokoll = anruf.sudo().protokoll_id
        self.assertTrue(protokoll)
        self.assertEqual((protokoll.res_model, protokoll.res_id), ('crm.lead', lead.id))

    def test_ohne_team_und_vorlage(self):
        button = self._button(titel_vorlage=False)
        _anruf, ergebnis = self._ausfuehren(self.vertrieb_eigene, button)
        lead = self.env['crm.lead'].sudo().browse(ergebnis['res_id'])
        self.assertEqual(lead.name, 'Anruf von Erika Vorgang')
        self.assertEqual(lead.user_id, self.vertrieb_eigene)

    def test_ohne_kontakt(self):
        button = self._button()
        anruf, ergebnis = self._ausfuehren(
            self.vertrieb_eigene, button, nummer='+49 30 7770001')
        lead = self.env['crm.lead'].sudo().browse(ergebnis['res_id'])
        self.assertFalse(lead.partner_id)
        self.assertTrue(anruf.sudo().nummer)
        self.assertIn(anruf.sudo().nummer, lead.name)
        self.assertEqual(lead.type, 'opportunity')

    def test_ohne_crm_recht(self):
        button = self._button()
        vorher = self.env['crm.lead'].sudo().search_count([])
        daten = self._beginnen(self.ohne_crm)
        anruf = self.env['wb.voip.anruf'].with_user(self.ohne_crm).browse(daten['id'])
        with self.assertRaises(AccessError):
            anruf.wb_voip_aktion_ausfuehren(button.id)
        self.assertEqual(self.env['crm.lead'].sudo().search_count([]), vorher)
        self.assertFalse(anruf.sudo().res_model == 'crm.lead')

    def test_team_ohne_leserecht(self):
        # Unlesbares Team wird nicht stillschweigend übergangen
        self.env['ir.rule'].sudo().create({
            'name': 'wb_voip_crm Test: Team nicht lesbar',
            'model_id': self.env.ref('sales_team.model_crm_team').id,
            'domain_force': '[(0, "=", 1)]',
            'groups': [(4, self.env.ref('base.group_user').id)]})
        button = self._button(crm_team_id=self.team.id)
        vorher = self.env['crm.lead'].sudo().search_count([])
        daten = self._beginnen(self.vertrieb_eigene)
        anruf = self.env['wb.voip.anruf'].with_user(self.vertrieb_eigene).browse(daten['id'])
        with self.assertRaises(AccessError):
            anruf.wb_voip_aktion_ausfuehren(button.id)
        self.assertEqual(self.env['crm.lead'].sudo().search_count([]), vorher)

    # --- Review Focus 4: Vorlage ist Klartext ---

    def _titel(self, vorlage, kontaktname=None):
        kontakt = self.kontakt
        if kontaktname is not None:
            kontakt = self.env['res.partner'].create(
                {'name': kontaktname, 'phone': '+49 171 5550001'})
        button = self._button(titel_vorlage=vorlage)
        _anruf, ergebnis = self._ausfuehren(
            self.vertrieb_eigene, button, nummer='+491715550001' if kontaktname else '+491701234567')
        lead = self.env['crm.lead'].sudo().browse(ergebnis['res_id'])
        self.assertEqual(lead.partner_id, kontakt)
        return lead.name

    def test_html_bleibt_klartext(self):
        name = '<img src=x onerror=alert(1)> & "Q"'
        self.assertEqual(self._titel('<b>{kontakt}</b>', name), f'<b>{name}</b>')

    def test_kein_format_keine_auswertung(self):
        vorlage = '{kontakt.__class__} {0} {unbekannt} %(nummer)s ${0} __import__("os") {{kontakt}}'
        titel = self._titel(vorlage)
        # nur die drei Platzhalter werden ersetzt, alles andere bleibt wörtlich stehen
        self.assertEqual(
            titel, '{kontakt.__class__} {0} {unbekannt} %(nummer)s ${0} __import__("os") '
                   '{Erika Vorgang}')

    def test_ersetzte_werte_werden_nicht_erneut_ersetzt(self):
        # Ein Kontaktname mit Platzhalter wird nicht ein zweites Mal ersetzt
        titel = self._titel('{kontakt} {nummer}', '{nummer} {datum}')
        self.assertTrue(titel.startswith('{nummer} {datum} '))
        self.assertEqual(titel.count('{'), 2)

    def test_laenge_nach_ersetzung_begrenzt(self):
        titel = self._titel('{kontakt}', 'L' * 300)
        self.assertEqual(len(titel), 200)

    def test_steuerzeichen_werden_zu_leerzeichen(self):
        titel = self._titel('{kontakt}', 'Zeile1\nZeile2\t\x01Ende')
        self.assertEqual(titel, 'Zeile1 Zeile2 Ende')
