from contextlib import contextmanager

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import WbVoipCase

FEHLER = (AccessError, UserError, ValidationError, ValueError)


@tagged('wb_voip', 'post_install', '-at_install')
class TestAktionAssistent(WbVoipCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        gruppe_manager = cls.env.ref('wb_voip.group_wb_voip_manager')
        # VoIP-Manager, der Kontakte und Kategorien ändern darf (kein Systemadministrator)
        cls.manager = Users.create({
            'name': 'VoIP Manager Kontakte', 'login': 'wb_voip_assistent_manager',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, gruppe_manager.id,
                                  cls.env.ref('base.group_partner_manager').id])],
        })
        cls.systemadmin = Users.create({
            'name': 'VoIP Systemadmin', 'login': 'wb_voip_assistent_system',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, gruppe_manager.id,
                                  cls.env.ref('base.group_system').id,
                                  cls.env.ref('base.group_partner_manager').id])],
        })
        # C darf Kontakte nur lesen
        cls.user_c = Users.create({
            'name': 'VoIP Benutzer C', 'login': 'wb_voip_assistent_c',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('wb_voip.group_wb_voip_user').id])],
        })
        cls.kontakt = cls.env['res.partner'].create({'name': 'Erika Muster'})
        cls.kategorie_alt = cls.env['res.partner.category'].create({'name': 'Bestand'})
        cls.kategorie = cls.env['res.partner.category'].create({'name': 'Rückruf gewünscht'})
        cls.kontakt.category_id = cls.kategorie_alt
        cls.technisch = cls.env.ref('wb_voip.group_wb_voip_button_aktion')
        cls.env.ref('wb_voip.wb_voip_aktion_neuer_kontakt').active = False

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()

    # --- Hilfen ---

    @contextmanager
    def _abgelehnt(self, msg=None):
        # assertRaises von Odoo nimmt kein Tupel: allgemein fangen, Art danach prüfen
        with self.assertRaises(Exception, msg=msg) as fang:
            yield
        self.assertIsInstance(fang.exception, FEHLER, msg)

    def _feld(self, modell, name):
        return self.env['ir.model.fields']._get(modell, name)

    def _modell(self, name):
        return self.env['ir.model']._get(name)

    def _assistent(self, benutzer=None, **werte):
        vals = {'name': 'Funktion setzen', 'icon': 'fa-pencil', 'farbe': 'primary',
                'ort_anrufer_fenster': True, 'art': 'feld', 'ziel': 'kontakt',
                'feld_name': 'function', 'wert_char': 'Geändert'}
        vals.update(werte)
        return self.env['wb.voip.aktion.assistent'].with_user(benutzer or self.manager).create(vals)

    def _anlegen(self, benutzer=None, **werte):
        assistent = self._assistent(benutzer, **werte)
        ergebnis = assistent.action_anlegen()
        return self.env['wb.voip.aktion'].browse(ergebnis['res_id'])

    def _anruf(self, benutzer=None, **werte):
        vals = {'user_id': (benutzer or self.user_a).id, 'richtung': 'eingehend',
                'zustand': 'klingelt', 'nummer': '+49 170 1234567',
                'nummer_e164': '+491701234567', 'partner_id': self.kontakt.id}
        vals.update(werte)
        return self.env['wb.voip.anruf'].sudo().create(vals)

    def _ausfuehren(self, aktion, benutzer=None, anruf=None):
        benutzer = benutzer or self.user_a
        anruf = anruf or self._anruf(benutzer)
        return anruf.with_user(benutzer).wb_voip_aktion_ausfuehren(aktion.id)

    # --- Technische Gruppe ---

    def test_technische_gruppe_ohne_benutzer(self):
        self.assertFalse(self.technisch.sudo().with_context(active_test=False).user_ids)
        self.assertFalse(self.technisch.implied_ids)
        erbende = self.env['res.groups'].sudo().search([('implied_ids', 'in', self.technisch.ids)])
        self.assertFalse(erbende)
        admin = self.env.ref('base.user_admin')
        self.assertNotIn(self.technisch, admin.all_group_ids)

    # --- Zugriff ---

    def test_nicht_manager_kein_assistent(self):
        with self.assertRaises(AccessError):
            self._assistent(self.user_a)
        assistent = self._assistent()
        with self.assertRaises(AccessError):
            assistent.with_user(self.user_a).action_anlegen()

    def test_code_fuer_manager_nicht_waehlbar(self):
        Assistent = self.env['wb.voip.aktion.assistent']
        arten = [wert for wert, _ in Assistent.with_user(self.manager)._selection_art()]
        self.assertIn('feld', arten)
        self.assertIn('vorlage', arten)
        self.assertNotIn('code', arten)
        arten = [wert for wert, _ in Assistent.with_user(self.systemadmin)._selection_art()]
        self.assertIn('code', arten)

    def test_code_per_rpc_abgelehnt(self):
        with self._abgelehnt():
            self._assistent(art='code', code="record.write({'function': 'x'})")
        with self._abgelehnt():
            self._anlegen(art='code')
        assistent = self._assistent()
        with self._abgelehnt():
            assistent.write({'code': "record.write({'function': 'x'})"})
        # Selbst wenn die Art im Speicher steht: Anlegen prüft die Systemgruppe erneut
        assistent = self._assistent()
        assistent.sudo().write({'art': 'code', 'code': "record.write({'function': 'x'})"})
        with self.assertRaises(AccessError):
            assistent.action_anlegen()
        self.assertFalse(self.env['wb.voip.aktion'].search([('name', '=', 'Funktion setzen')]))

    # --- Feld setzen ---

    def test_feld_setzen_erzeugt_erlaubte_aktion(self):
        aktion = self._anlegen()
        self.assertEqual(aktion.art, 'serveraktion')
        self.assertTrue(aktion.wb_voip_assistent)
        self.assertEqual(aktion.name, 'Funktion setzen')
        self.assertEqual(aktion.icon, 'fa-pencil')
        server = aktion.sudo().server_action_id
        self.assertEqual(server.state, 'object_write')
        self.assertEqual(server.update_path, 'function')
        self.assertNotIn('.', server.update_path)
        self.assertEqual(server.update_field_id, self._feld('res.partner', 'function'))
        self.assertEqual(server.evaluation_type, 'value')
        self.assertEqual(server.model_id.model, 'res.partner')
        self.assertEqual(server.group_ids, self.technisch)
        self.assertFalse(server.binding_model_id)
        self.assertFalse(server.warning)

    def test_ausfuehrung_als_benutzer(self):
        aktion = self._anlegen()
        self._ausfuehren(aktion)
        self.assertEqual(self.kontakt.function, 'Geändert')
        self.assertEqual(self.kontakt.write_uid, self.user_a)

    def test_ausfuehrung_ohne_schreibrecht(self):
        aktion = self._anlegen()
        with self.assertRaises(AccessError):
            self._ausfuehren(aktion, self.user_c)
        self.assertFalse(self.kontakt.function)

    def test_ausfuehrung_ausserhalb_verweigert(self):
        aktion = self._anlegen()
        server = aktion.sudo().server_action_id
        for benutzer in (self.user_a, self.manager, self.systemadmin,
                         self.env.ref('base.user_admin')):
            with self.assertRaises(AccessError, msg=benutzer.login):
                server.with_user(benutzer).with_context(
                    active_model='res.partner', active_id=self.kontakt.id,
                    active_ids=[self.kontakt.id]).run()
        self.assertFalse(self.kontakt.function)

    def test_hand_button_mit_assistenten_aktion_verweigert(self):
        # ohne Kennzeichen am Button gilt die Gruppenprüfung (die technische Gruppe hat niemand)
        aktion = self._anlegen()
        hand = self.env['wb.voip.aktion'].create({
            'name': 'Hand', 'art': 'serveraktion', 'ort_anrufer_fenster': True,
            'server_action_id': aktion.sudo().server_action_id.id})
        self.assertFalse(hand.wb_voip_assistent)
        with self.assertRaises(AccessError):
            self._ausfuehren(hand)
        self.assertFalse(self.kontakt.function)

    def test_datensatz_ziel(self):
        aktion = self._anlegen(
            ziel='datensatz', modell='res.partner.category',
            feld_name='name', wert_char='Neu')
        anruf = self._anruf(res_model='res.partner.category', res_id=self.kategorie.id)
        self._ausfuehren(aktion, anruf=anruf)
        self.assertEqual(self.kategorie.name, 'Neu')

    def test_typen(self):
        faelle = [
            ('is_company', {'wert_boolean': True}, True),
            ('color', {'wert_integer': 3}, 3),
            ('partner_latitude', {'wert_float': 1.5}, 1.5),
        ]
        for feld, werte, erwartet in faelle:
            aktion = self._anlegen(feld_name=feld, wert_char=False,
                                   **werte)
            self._ausfuehren(aktion)
            self.assertEqual(self.kontakt[feld], erwartet, feld)
        # HTML ist kein erlaubter Typ; Many2one ohne oder mit nicht vorhandenem Datensatz
        with self._abgelehnt():
            self._anlegen(feld_name='comment', wert_char=False, wert_text='Notiz')
        with self._abgelehnt():
            self._anlegen(feld_name='parent_id', wert_char=False)
        with self._abgelehnt():
            self._anlegen(feld_name='parent_id', wert_char=False, wert_res_id=999_999_999)

    def test_many2one_wert(self):
        firma = self.env['res.partner'].create({'name': 'Firma', 'is_company': True})
        aktion = self._anlegen(feld_name='parent_id',
                               wert_res_id=firma.id)
        self._ausfuehren(aktion)
        self.assertEqual(self.kontakt.parent_id, firma)

    def test_many2one_wert_unlesbar(self):
        fremdfirma = self.env['res.company'].create({'name': 'Fremdfirma'})
        geheim = self.env['res.partner'].create({'name': 'Geheim', 'company_id': fremdfirma.id})
        self.assertFalse(geheim.with_user(self.manager).has_access('read'))
        with self._abgelehnt():
            self._anlegen(feld_name='parent_id',
                          wert_res_id=geheim.id)

    def test_auswahl_wert(self):
        aktion = self._anlegen(feld_name='type', wert_auswahl='invoice')
        server = aktion.sudo().server_action_id
        self.assertEqual(server.value, 'invoice')
        self._ausfuehren(aktion)
        self.assertEqual(self.kontakt.type, 'invoice')
        with self._abgelehnt():
            self._anlegen(feld_name='type', wert_auswahl='gibt_es_nicht')
        with self._abgelehnt():
            self._anlegen(feld_name='type')

    def test_feldauswahl_abgelehnt(self):
        # berechnet/readonly, Relation über Feldpfad, fremdes Modell, falscher Typ
        for modell, feld in (('res.partner', 'commercial_partner_id'),
                             ('res.partner', 'display_name'),
                             ('res.partner', 'create_uid'),
                             ('res.partner', 'child_ids'),
                             ('res.partner', 'user_ids'),
                             ('res.users', 'login'),
                             ('res.partner', 'gibt_es_nicht')):
            with self._abgelehnt(msg=f'{modell}.{feld}'):
                self._anlegen(feld_name=feld, wert_char='x')
        Assistent = self.env['wb.voip.aktion.assistent'].with_user(self.manager)
        erlaubt = [field.name for field in Assistent._wb_voip_erlaubte_felder('res.partner')]
        self.assertIn('function', erlaubt)
        self.assertIn('category_id', erlaubt)
        for name in ('commercial_partner_id', 'display_name', 'create_uid', 'child_ids',
                     'user_ids', 'message_ids', 'comment'):
            self.assertNotIn(name, erlaubt)

    def test_feld_ohne_schreibrecht_des_managers(self):
        # der Standard-Manager ohne Kontaktrechte darf Kontakte nicht ändern
        with self._abgelehnt():
            self._anlegen(self.user_manager)

    def test_modellauswahl_abgelehnt(self):
        for name, feld in (('ir.config_parameter', 'value'), ('res.users', 'signature'),
                           ('res.groups', 'name'), ('wb.voip.anruf', 'notiz'),
                           ('wb.voip.protokoll', 'beschreibung'), ('mail.message', 'subject'),
                           ('bus.presence', 'status')):
            if name not in self.env or feld not in self.env[name]._fields:
                continue
            with self._abgelehnt(msg=name):
                self._anlegen(ziel='datensatz', modell=name,
                              feld_name=feld, wert_char='x')
        Assistent = self.env['wb.voip.aktion.assistent'].with_user(self.manager)
        modelle = [name for name, _label in Assistent._selection_modell()]
        self.assertIn('res.partner.category', modelle)
        for name in modelle:
            self.assertFalse(name.startswith(('ir.', 'base.', 'bus.', 'mail.', 'wb.voip.',
                                              'wb.license.', 'res.users', 'res.groups')), name)
            self.assertFalse(self.env[name]._transient or self.env[name]._abstract, name)
            self.assertTrue(self.env[name].with_user(self.manager).has_access('write'), name)

    # --- Vorlagen ---

    def test_vorlage_kategorie(self):
        aktion = self._anlegen(art='vorlage', vorlage='kategorie_hinzufuegen',
                               feld_name=False, wert_char=False, wert_kategorie_id=self.kategorie.id)
        server = aktion.sudo().server_action_id
        self.assertEqual(server.state, 'object_write')
        self.assertEqual(server.update_path, 'category_id')
        self.assertEqual(server.update_m2m_operation, 'add')
        self.assertEqual(server.group_ids, self.technisch)
        self._ausfuehren(aktion)
        self.assertEqual(self.kontakt.category_id, self.kategorie_alt | self.kategorie)

    def test_vorlage_kategorie_ohne_wahl(self):
        with self.assertRaises(UserError):
            self._anlegen(art='vorlage', vorlage='kategorie_hinzufuegen', feld_name=False)

    def test_vorlage_sprache(self):
        sprache = self.env['res.lang'].get_installed()[0][0]
        aktion = self._anlegen(art='vorlage', vorlage='sprache_setzen', feld_name=False,
                               wert_sprache=sprache)
        self.assertEqual(aktion.sudo().server_action_id.value, sprache)
        self.kontakt.lang = False
        self._ausfuehren(aktion)
        self.assertEqual(self.kontakt.lang, sprache)
        with self._abgelehnt():
            self._anlegen(art='vorlage', vorlage='sprache_setzen', feld_name=False,
                          wert_sprache='xx_XX')

    # --- Python-Code (Systemadministrator) ---

    def test_code_vom_systemadmin_laeuft_als_benutzer(self):
        aktion = self._anlegen(
            self.systemadmin, art='code', feld_name=False,
            code="record.write({'function': '%s/%s' % (env.uid, env.su)})")
        server = aktion.sudo().server_action_id
        self.assertEqual(server.state, 'code')
        self.assertEqual(server.group_ids, self.technisch)
        self.assertFalse(server.binding_model_id)
        self._ausfuehren(aktion)
        self.assertEqual(self.kontakt.function, f'{self.user_a.id}/False')
        with self.assertRaises(AccessError):
            server.with_user(self.user_a).with_context(
                active_model='res.partner', active_id=self.kontakt.id).run()

    def test_code_ungueltig(self):
        with self.assertRaises(UserError):
            self._anlegen(self.systemadmin, art='code', feld_name=False, code='def (')
        with self.assertRaises(UserError):
            self._anlegen(self.systemadmin, art='code', feld_name=False, code='')

    def test_code_ergebnis_bereinigt(self):
        aktion = self._anlegen(
            self.systemadmin, art='code', feld_name=False,
            code="action = {'type': 'ir.actions.act_window', 'res_model': 'res.partner', "
                 "'view_mode': 'form', 'views': [[False, 'form']], 'res_id': record.id}")
        ergebnis = self._ausfuehren(aktion)
        self.assertEqual(ergebnis['res_model'], 'res.partner')
        self.assertEqual(ergebnis['res_id'], self.kontakt.id)

    # --- Schutz der erzeugten Daten ---

    def test_manager_kann_kennzeichen_und_aktion_nicht_aendern(self):
        aktion = self._anlegen()
        server = aktion.sudo().server_action_id
        als_manager = aktion.with_user(self.manager)
        with self.assertRaises(UserError):
            als_manager.write({'wb_voip_assistent': False})
        with self.assertRaises(UserError):
            als_manager.write({'server_action_id': server.id})
        with self.assertRaises(UserError):
            als_manager.write({'art': 'url', 'url_vorlage': 'https://beispiel.example'})
        # Darstellung bleibt änderbar
        als_manager.write({'name': 'Neu', 'icon': 'fa-star'})
        with self.assertRaises(UserError):
            self.env['wb.voip.aktion'].with_user(self.manager).create({
                'name': 'X', 'art': 'serveraktion', 'ort_softphone': True,
                'server_action_id': server.id, 'wb_voip_assistent': True})
        neu = self.env['wb.voip.aktion'].with_user(self.manager).with_context(
            default_wb_voip_assistent=True).create({
                'name': 'X', 'art': 'neuer_kontakt', 'ort_softphone': True})
        self.assertFalse(neu.wb_voip_assistent)
        Server = server.with_user(self.manager)
        for werte in ({'group_ids': [(5, 0, 0)]}, {'value': 'Anders'},
                      {'state': 'code'}, {'binding_model_id': self._modell('res.partner').id}):
            with self.assertRaises(AccessError, msg=str(werte)):
                Server.write(werte)
        self.assertEqual(server.group_ids, self.technisch)

    def test_kennzeichen_ohne_passende_gruppe_greift_nicht(self):
        # wurde die Gruppe der Aktion verändert, gilt wieder die normale Gruppenprüfung
        aktion = self._anlegen()
        server = aktion.sudo().server_action_id
        server.group_ids = self.env.ref('base.group_system')
        with self.assertRaises(AccessError):
            self._ausfuehren(aktion)
        self.assertFalse(self.kontakt.function)

    # --- Löschen ---

    def test_loeschen_raeumt_auf(self):
        aktion = self._anlegen()
        server = aktion.sudo().server_action_id
        aktion.with_user(self.manager).unlink()
        self.assertFalse(server.exists())

    def test_loeschen_geteilte_aktion_bleibt(self):
        aktion = self._anlegen()
        server = aktion.sudo().server_action_id
        zweiter = aktion.copy()
        self.assertEqual(zweiter.sudo().server_action_id, server)
        self.assertFalse(zweiter.wb_voip_assistent)
        aktion.with_user(self.manager).unlink()
        self.assertTrue(server.exists())

    def test_loeschen_hand_aktion_bleibt(self):
        hand_server = self.env['ir.actions.server'].create({
            'name': 'Hand', 'model_id': self._modell('res.partner').id,
            'state': 'code', 'code': 'pass'})
        hand = self.env['wb.voip.aktion'].create({
            'name': 'Hand', 'art': 'serveraktion', 'ort_softphone': True,
            'server_action_id': hand_server.id})
        hand.with_user(self.manager).unlink()
        self.assertTrue(hand_server.exists())

    def test_loeschen_nur_mit_recht(self):
        aktion = self._anlegen()
        server = aktion.sudo().server_action_id
        with self.assertRaises(AccessError):
            aktion.with_user(self.user_a).unlink()
        self.assertTrue(server.exists())

    # --- Bearbeiten mit Assistent ---

    def test_bearbeiten_mit_assistent(self):
        aktion = self._anlegen()
        alt = aktion.sudo().server_action_id
        ergebnis = aktion.with_user(self.manager).action_wb_voip_assistent_bearbeiten()
        self.assertEqual(ergebnis['res_model'], 'wb.voip.aktion.assistent')
        assistent = self.env['wb.voip.aktion.assistent'].with_user(self.manager).with_context(
            ergebnis['context']).create({})
        self.assertEqual(assistent.aktion_id, aktion)
        self.assertEqual(assistent.name, 'Funktion setzen')
        self.assertEqual(assistent.art, 'feld')
        self.assertEqual(assistent.feld_name, 'function')
        self.assertEqual(assistent.wert_char, 'Geändert')
        assistent.write({'wert_char': 'Zweiter Wert', 'name': 'Umbenannt'})
        assistent.action_anlegen()
        self.assertEqual(aktion.name, 'Umbenannt')
        self.assertTrue(aktion.wb_voip_assistent)
        neu = aktion.sudo().server_action_id
        self.assertNotEqual(neu, alt)
        self.assertFalse(alt.exists())
        self.assertEqual(neu.group_ids, self.technisch)
        self._ausfuehren(aktion)
        self.assertEqual(self.kontakt.function, 'Zweiter Wert')

    def test_bearbeiten_vorlage(self):
        aktion = self._anlegen(art='vorlage', vorlage='kategorie_hinzufuegen', feld_name=False,
                               wert_kategorie_id=self.kategorie.id)
        ergebnis = aktion.with_user(self.manager).action_wb_voip_assistent_bearbeiten()
        assistent = self.env['wb.voip.aktion.assistent'].with_user(self.manager).with_context(
            ergebnis['context']).create({})
        self.assertEqual(assistent.art, 'vorlage')
        self.assertEqual(assistent.vorlage, 'kategorie_hinzufuegen')
        self.assertEqual(assistent.wert_kategorie_id, self.kategorie)

    def test_bearbeiten_code_nur_systemadmin(self):
        aktion = self._anlegen(self.systemadmin, art='code', feld_name=False, code='pass')
        with self.assertRaises(AccessError):
            aktion.with_user(self.manager).action_wb_voip_assistent_bearbeiten()
        with self.assertRaises(AccessError):
            self._anlegen(aktion_id=aktion.id)
        ergebnis = aktion.with_user(self.systemadmin).action_wb_voip_assistent_bearbeiten()
        assistent = self.env['wb.voip.aktion.assistent'].with_user(self.systemadmin).with_context(
            ergebnis['context']).create({})
        self.assertEqual(assistent.code, 'pass')

    def test_bearbeiten_nur_assistenten_buttons(self):
        hand = self.env['wb.voip.aktion'].create({
            'name': 'Hand', 'art': 'neuer_kontakt', 'ort_softphone': True})
        with self.assertRaises(UserError):
            hand.with_user(self.manager).action_wb_voip_assistent_bearbeiten()
        with self.assertRaises(UserError):
            self._anlegen(aktion_id=hand.id)

    # --- Schritte ---

    def test_schritte(self):
        assistent = self._assistent(schritt='button')
        ergebnis = assistent.action_weiter()
        self.assertEqual(assistent.schritt, 'aktion')
        self.assertEqual(ergebnis['res_id'], assistent.id)
        assistent.action_weiter()
        self.assertEqual(assistent.schritt, 'feld')
        optionen = assistent.option_ids
        felder = optionen.filtered(lambda o: o.art == 'feld').mapped('name')
        self.assertIn('function', felder)
        self.assertNotIn('commercial_partner_id', felder)
        self.assertEqual(assistent.feld_option_id.name, 'function')
        self.assertIn('invoice', optionen.filtered(
            lambda o: o.art == 'auswahl' and o.feld_name == 'type').mapped('name'))
        assistent.action_zurueck()
        self.assertEqual(assistent.schritt, 'aktion')
        assistent.action_zurueck()
        self.assertEqual(assistent.schritt, 'button')
        assistent.write({'ort_anrufer_fenster': False})
        with self.assertRaises(UserError):
            assistent.action_weiter()
        assistent.write({'ort_softphone': True, 'icon': 'kein icon'})
        with self.assertRaises(UserError):
            assistent.action_weiter()
