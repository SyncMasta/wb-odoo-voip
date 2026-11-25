from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.wb_voip.tools import serverfelder

from .common import WbVoipCrmCase


@tagged('wb_voip_crm', 'post_install', '-at_install')
class TestVorgang(WbVoipCrmCase):

    def setUp(self):
        super().setUp()
        self.lead = self._lead()
        self.Anruf = self.env['wb.voip.anruf']

    # --- lead_id ---

    def test_lead_id_aus_res_model(self):
        anruf = self._anruf(res_model='crm.lead', res_id=self.lead.id)
        self.assertEqual(anruf.lead_id, self.lead)

    def test_lead_id_nur_bei_lead(self):
        anruf = self._anruf(res_model='res.partner', res_id=self.kontakt.id)
        self.assertFalse(anruf.lead_id)
        anruf.sudo().write({'res_model': 'crm.lead', 'res_id': self.lead.id})
        self.assertEqual(anruf.lead_id, self.lead)
        anruf.sudo().write({'res_model': 'res.partner', 'res_id': self.kontakt.id})
        self.assertFalse(anruf.lead_id)

    def test_lead_id_nicht_vorhandener_vorgang(self):
        anruf = self._anruf(res_model='crm.lead', res_id=999999999)
        self.assertFalse(anruf.lead_id)

    def test_lead_id_archivierter_vorgang_bleibt(self):
        anruf = self._anruf(res_model='crm.lead', res_id=self.lead.id)
        self.lead.active = False
        self.assertEqual(anruf.lead_id, self.lead)

    def test_lead_id_geloeschter_vorgang(self):
        anruf = self._anruf(res_model='crm.lead', res_id=self.lead.id)
        self.lead.unlink()
        anruf.invalidate_recordset()
        self.assertFalse(anruf.lead_id)

    def test_lead_id_nur_server(self):
        anruf = self._anruf()
        with self.assertRaises(UserError):
            anruf.with_user(self.vertrieb_eigene).write({'lead_id': self.lead.id})
        with self.assertRaises(UserError):
            self.Anruf.with_user(self.vertrieb_eigene).create({
                'user_id': self.vertrieb_eigene.id, 'richtung': 'eingehend',
                'lead_id': self.lead.id})
        self.assertIn('lead_id', self.Anruf._WB_VOIP_NUR_SERVER)
        # der Kern bleibt unverändert erhalten
        self.assertIn('res_model', self.Anruf._WB_VOIP_NUR_SERVER)

    def test_serverfelder_methode_erweiterbar(self):
        """A second extension adds a field through ``super()``; both stay protected."""
        modell = type(self.Anruf)
        vorher = modell._wb_voip_nur_server

        def zweite(modell_self):
            return vorher(modell_self) + ('zweites_feld',)

        with patch.object(modell, '_wb_voip_nur_server', zweite):
            nur_server = self.Anruf._wb_voip_nur_server()
            self.assertIn('lead_id', nur_server)
            self.assertIn('zweites_feld', nur_server)
            self.assertIn('res_model', nur_server)
            anruf = self._anruf()
            for feld in ('lead_id', 'zweites_feld'):
                with self.assertRaises(UserError, msg=feld):
                    anruf.with_user(self.vertrieb_eigene).write({feld: 1})
                with self.assertRaises(UserError, msg=feld):
                    self.Anruf.with_user(self.vertrieb_eigene).create({
                        'user_id': self.vertrieb_eigene.id, 'richtung': 'eingehend', feld: 1})

    def test_lead_id_default_im_kontext_wirkungslos(self):
        env = self.Anruf.with_user(self.vertrieb_eigene).with_context(
            default_lead_id=self.lead.id, default_res_id=5).env
        kontext = serverfelder.kontext_ohne_defaults(env, self.Anruf._WB_VOIP_NUR_SERVER)
        self.assertNotIn('default_lead_id', kontext)
        self.assertNotIn('default_res_id', kontext)

    # --- wb_voip_vorgang_setzen ---

    def test_setzen_anruf_und_protokoll(self):
        anruf = self._anruf()
        protokoll = self._protokoll(anruf)
        meldungen = self._bus_meldungen(
            lambda: anruf.with_user(self.vertrieb_eigene).wb_voip_vorgang_setzen(self.lead.id))
        self.assertEqual((anruf.res_model, anruf.res_id), ('crm.lead', self.lead.id))
        self.assertEqual(anruf.lead_id, self.lead)
        self.assertEqual((protokoll.res_model, protokoll.res_id), ('crm.lead', self.lead.id))
        self.assertEqual([m['payload'] for m in meldungen], [{'anruf_id': anruf.id}])

    def test_setzen_ohne_protokoll(self):
        anruf = self._anruf()
        anruf.with_user(self.vertrieb_eigene).wb_voip_vorgang_setzen(self.lead.id)
        self.assertEqual(anruf.lead_id, self.lead)

    def test_setzen_loesen(self):
        anruf = self._anruf(res_model='crm.lead', res_id=self.lead.id)
        protokoll = self._protokoll(anruf)
        protokoll.sudo().write({'res_model': 'crm.lead', 'res_id': self.lead.id})
        anruf.with_user(self.vertrieb_eigene).wb_voip_vorgang_setzen(False)
        # ohne Kontakt am Anruf bleibt nichts übrig
        self.assertFalse(anruf.res_model)
        self.assertFalse(anruf.res_id)
        self.assertFalse(anruf.lead_id)
        self.assertFalse(protokoll.res_id)

    def test_loesen_faellt_auf_kontakt_zurueck(self):
        anruf = self._anruf(res_model='crm.lead', res_id=self.lead.id,
                            partner_id=self.kontakt.id)
        protokoll = self._protokoll(anruf)
        protokoll.sudo().write({'res_model': 'crm.lead', 'res_id': self.lead.id})
        anruf.with_user(self.vertrieb_eigene).wb_voip_vorgang_setzen(False)
        self.assertEqual((anruf.res_model, anruf.res_id), ('res.partner', self.kontakt.id))
        self.assertEqual((protokoll.res_model, protokoll.res_id),
                         ('res.partner', self.kontakt.id))
        self.assertFalse(anruf.lead_id)

    def test_zuordnung_ohne_suche_bei_uebergebenem_vorgang(self):
        anruf_modell = self.Anruf.with_user(self.vertrieb_eigene)
        self.assertIsNone(anruf_modell._wb_voip_zuordnung(
            self.kontakt.with_user(self.vertrieb_eigene), self.lead.with_user(self.vertrieb_eigene)))

    # --- Vorgangsname nur mit Leserecht ---

    def _name_per_web_read(self, anruf, benutzer):
        spez = {'wb_voip_lead_lesbar_id': {'fields': {'display_name': {}}}}
        return anruf.with_user(benutzer).web_read(spez)[0]['wb_voip_lead_lesbar_id']

    def test_vorgangsname_nur_mit_leserecht(self):
        self.lead.name = 'Streng geheimer Vorgang'
        anruf = self._anruf(res_model='crm.lead', res_id=self.lead.id)
        self.assertEqual(self._name_per_web_read(anruf, self.vertrieb_eigene)['display_name'],
                         'Streng geheimer Vorgang')
        # Vorgang wird einem anderen Verantwortlichen übergeben (Regel „eigene“)
        self.lead.user_id = self.vertrieb_alle
        self.env.invalidate_all()
        self.assertFalse(self._name_per_web_read(anruf, self.vertrieb_eigene))

    def test_vorgangsname_manager_mit_eigenen_rechten(self):
        self.lead.name = 'Streng geheimer Vorgang'
        manager = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'VoIP Manager CRM', 'login': 'wb_voip_crm_mgr',
            'group_ids': [(6, 0, [self.env.ref('wb_voip.group_wb_voip_manager').id,
                                  self.env.ref('sales_team.group_sale_salesman').id])]})
        anruf = self._anruf(res_model='crm.lead', res_id=self.lead.id)
        self.assertEqual(len(self.Anruf.with_user(manager).search([('id', '=', anruf.id)])), 1)
        self.assertFalse(self._name_per_web_read(anruf, manager))
        ohne_crm = self.Anruf.with_user(self.user_manager).browse(anruf.id)
        self.assertFalse(self._name_per_web_read(ohne_crm, self.user_manager))

    def test_ansicht_zeigt_nur_lesbares_feld(self):
        ansicht = self.Anruf.with_user(self.vertrieb_eigene).get_view(view_type='form')
        self.assertIn('wb_voip_lead_lesbar_id', ansicht['arch'])
        self.assertNotIn('"lead_id"', ansicht['arch'])

    def test_loesen_laesst_anderes_ziel_stehen(self):
        anruf = self._anruf(res_model='res.partner', res_id=self.kontakt.id)
        anruf.with_user(self.vertrieb_eigene).wb_voip_vorgang_setzen(False)
        self.assertEqual(anruf.res_model, 'res.partner')

    def test_setzen_fremder_anruf(self):
        anruf = self._anruf(benutzer=self.vertrieb_alle)
        with self.assertRaises(UserError):
            anruf.with_user(self.vertrieb_eigene).wb_voip_vorgang_setzen(self.lead.id)
        self.assertFalse(anruf.lead_id)

    def test_setzen_unlesbarer_vorgang(self):
        fremd = self._lead(user_id=self.vertrieb_alle.id)
        anruf = self._anruf()
        with self.assertRaises(AccessError):
            anruf.with_user(self.vertrieb_eigene).wb_voip_vorgang_setzen(fremd.id)
        self.assertFalse(anruf.lead_id)

    def test_setzen_ohne_crm_recht(self):
        anruf = self._anruf(benutzer=self.ohne_crm)
        with self.assertRaises(AccessError):
            anruf.with_user(self.ohne_crm).wb_voip_vorgang_setzen(self.lead.id)

    def test_setzen_ohne_voip_recht(self):
        anruf = self._anruf()
        ohne = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Ohne VoIP', 'login': 'wb_voip_crm_ohnevoip',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('sales_team.group_sale_salesman').id])]})
        with self.assertRaises(AccessError):
            anruf.with_user(ohne).wb_voip_vorgang_setzen(self.lead.id)

    def test_setzen_ungueltige_eingaben(self):
        anruf = self._anruf()
        for wert in (0, -1, True, None, '5', 1.5, [self.lead.id]):
            with self.assertRaises(UserError, msg=repr(wert)):
                anruf.with_user(self.vertrieb_eigene).wb_voip_vorgang_setzen(wert)
        with self.assertRaises(AccessError):
            anruf.with_user(self.vertrieb_eigene).wb_voip_vorgang_setzen(999999999)

    def test_setzen_ohne_lizenz(self):
        self._lizenz_fehlt()
        anruf = self._anruf()
        with self.assertRaises(UserError):
            anruf.with_user(self.vertrieb_eigene).wb_voip_vorgang_setzen(self.lead.id)

    # --- Smart-Button, Protokolle ---

    def test_anzahl_eigene_sichtbare(self):
        self._anruf(res_model='crm.lead', res_id=self.lead.id)
        self._anruf(res_model='crm.lead', res_id=self.lead.id)
        self._anruf(benutzer=self.vertrieb_alle, res_model='crm.lead', res_id=self.lead.id)
        lead_eigene = self.lead.with_user(self.vertrieb_eigene)
        lead_alle = self.lead.with_user(self.vertrieb_alle)
        self.assertEqual(lead_eigene.wb_voip_anruf_anzahl, 2)
        self.assertEqual(lead_alle.wb_voip_anruf_anzahl, 1)
        # Datensatzregel gilt auch über sudo() des Aufrufers
        self.assertEqual(lead_eigene.sudo().wb_voip_anruf_anzahl, 2)
        # Manager sieht alle
        self.assertEqual(self.lead.with_user(self.user_manager).sudo(False).wb_voip_anruf_anzahl, 3)

    def test_anzahl_ohne_anrufe_und_neuer_datensatz(self):
        self.assertEqual(self.lead.with_user(self.vertrieb_eigene).wb_voip_anruf_anzahl, 0)
        neu = self.env['crm.lead'].with_user(self.vertrieb_eigene).new({'name': 'Neu'})
        self.assertEqual(neu.wb_voip_anruf_anzahl, 0)

    def test_felder_nur_fuer_voip_benutzer(self):
        ohne = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Nur CRM', 'login': 'wb_voip_crm_nurcrm',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('sales_team.group_sale_salesman_all_leads').id])]})
        lead = self.lead.with_user(ohne)
        with self.assertRaises(AccessError):
            lead.wb_voip_anruf_anzahl
        # Formular lädt ohne Fehler und ohne VoIP-Teile
        ansicht = lead.get_view(view_type='form')
        self.assertNotIn('wb_voip_anruf_anzahl', ansicht['arch'])

    def test_totes_feld_anruf_ids_entfernt(self):
        self.assertNotIn('wb_voip_anruf_ids', self.env['crm.lead']._fields)

    def test_formular_fuer_voip_benutzer(self):
        ansicht = self.lead.with_user(self.vertrieb_eigene).get_view(view_type='form')
        self.assertIn('wb_voip_anruf_anzahl', ansicht['arch'])
        self.assertIn('wb_voip_protokoll_ids', ansicht['arch'])

    def test_aktion_anrufe(self):
        aktion = self.lead.with_user(self.vertrieb_eigene).action_wb_voip_anrufe()
        self.assertEqual(aktion['res_model'], 'wb.voip.anruf')
        self.assertEqual(aktion['domain'], [('res_model', '=', 'crm.lead'), ('res_id', '=', self.lead.id)])
        a1 = self._anruf(res_model='crm.lead', res_id=self.lead.id)
        self._anruf(benutzer=self.vertrieb_alle, res_model='crm.lead', res_id=self.lead.id)
        gefunden = self.Anruf.with_user(self.vertrieb_eigene).search(aktion['domain'])
        self.assertEqual(gefunden, a1)

    def test_aktion_anrufe_ohne_voip(self):
        lead = self.lead.with_user(self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Nur CRM 2', 'login': 'wb_voip_crm_nurcrm2',
                'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                      self.env.ref('sales_team.group_sale_salesman_all_leads').id])]}))
        with self.assertRaisesRegex(AccessError, 'Nur für Benutzer von WB VoIP'):
            lead.action_wb_voip_anrufe()

    def test_protokolle_am_vorgang(self):
        a1 = self._anruf(res_model='crm.lead', res_id=self.lead.id)
        p1 = self._protokoll(a1)
        p1.sudo().write({'res_model': 'crm.lead', 'res_id': self.lead.id})
        a2 = self._anruf(benutzer=self.vertrieb_alle, res_model='crm.lead', res_id=self.lead.id)
        p2 = self._protokoll(a2)
        p2.sudo().write({'res_model': 'crm.lead', 'res_id': self.lead.id})
        anderer = self._lead(name='Anderer')
        a3 = self._anruf(res_model='crm.lead', res_id=anderer.id)
        self._protokoll(a3).sudo().write({'res_model': 'crm.lead', 'res_id': anderer.id})
        # Datensatzregel der Protokolle: nur eigene
        self.assertEqual(self.lead.with_user(self.vertrieb_eigene).wb_voip_protokoll_ids, p1)
        self.assertEqual(self.lead.with_user(self.vertrieb_alle).wb_voip_protokoll_ids, p2)
        self.assertEqual(self.lead.with_user(self.vertrieb_eigene).sudo().wb_voip_protokoll_ids, p1)

    # --- lead_id nur für Systemverwaltung ---

    def _manager_crm(self):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'VoIP Manager CRM 2', 'login': 'wb_voip_crm_mgr2',
            'group_ids': [(6, 0, [self.env.ref('wb_voip.group_wb_voip_manager').id,
                                  self.env.ref('sales_team.group_sale_salesman').id])]})

    def test_lead_id_fuer_benutzer_gesperrt(self):
        anruf = self._anruf(res_model='crm.lead', res_id=self.lead.id)
        for benutzer in (self.vertrieb_eigene, self._manager_crm()):
            modell = self.Anruf.with_user(benutzer)
            self.assertNotIn('lead_id', modell.fields_get())
            with self.assertRaises(AccessError):
                anruf.with_user(benutzer).read(['lead_id'])
            with self.assertRaises(AccessError):
                modell.search_read([], ['lead_id'])
            with self.assertRaises(AccessError):
                anruf.with_user(benutzer).web_read({'lead_id': {'fields': {'display_name': {}}}})
            with self.assertRaises(AccessError):
                modell._read_group([], ['lead_id'], ['__count'])
            with self.assertRaises(AccessError):
                modell.search([('lead_id', '=', self.lead.id)])
        # Systemverwaltung darf
        self.assertIn('lead_id', self.Anruf.with_user(self.env.ref('base.user_admin')).fields_get())

    def test_zaehler_und_aktion_ohne_lead_id(self):
        manager = self._manager_crm()
        self._anruf(res_model='crm.lead', res_id=self.lead.id)
        self._anruf(benutzer=self.vertrieb_alle, res_model='crm.lead', res_id=self.lead.id)
        eigen = self.lead.with_user(self.vertrieb_eigene)
        self.assertEqual(len(self.Anruf.with_user(self.vertrieb_eigene).search(
            eigen.action_wb_voip_anrufe()['domain'])), 1)
        self.assertEqual(eigen.wb_voip_anruf_anzahl, 1)
        self.lead.user_id = manager  # Manager darf den Vorgang lesen (Regel „eigene“)
        self.env.invalidate_all()
        lead = self.lead.with_user(manager)
        self.assertEqual(lead.wb_voip_anruf_anzahl, 2)
        self.assertEqual(len(self.Anruf.with_user(manager).search(
            lead.action_wb_voip_anrufe()['domain'])), 2)
