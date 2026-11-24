from datetime import datetime

from odoo.exceptions import AccessError, UserError

from .common import WbVoipCase
from odoo.tests import tagged


@tagged('wb_voip', 'post_install', '-at_install')
class TestPopup(WbVoipCase):
    """Server side of the call popup: contact data, last logs, follow-up activity."""

    def setUp(self):
        super().setUp()
        self.kontakt = self.env['res.partner'].create({
            'name': 'Popup Kontakt', 'phone': '+49 170 1234567', 'email': 'popup@example.com'})
        self.Protokoll = self.env['wb.voip.protokoll']

    def _protokoll(self, user=None, tag=1, partner=None, **werte):
        daten = {
            'user_id': (user or self.user_a).id,
            'partner_id': (partner or self.kontakt).id,
            'datum': datetime(2026, 11, tag, 10, 0, 0),
            'richtung': 'eingehend',
        }
        daten.update(werte)
        return self.Protokoll.sudo().create(daten)

    # --- rechte Spalte -----------------------------------------------------------------

    def test_letzte_protokolle_fuenf_ohne_aktuelles(self):
        protokolle = [self._protokoll(tag=tag) for tag in range(1, 8)]
        aktuell = protokolle[-1]
        letzte = aktuell.with_user(self.user_a).wb_voip_letzte_protokoll_ids
        self.assertEqual(len(letzte), 5)
        self.assertNotIn(aktuell, letzte)
        # neueste zuerst: 6, 5, 4, 3, 2
        self.assertEqual(letzte.ids, [p.id for p in reversed(protokolle[1:6])])

    def test_letzte_protokolle_nur_lesbare(self):
        eigenes = self._protokoll(tag=1)
        fremd = self._protokoll(user=self.user_b, tag=2)
        aktuell = self._protokoll(tag=3)
        letzte = aktuell.with_user(self.user_a).wb_voip_letzte_protokoll_ids
        self.assertEqual(letzte, eigenes)
        self.assertNotIn(fremd, letzte)

    def test_letzte_protokolle_ohne_kontakt_leer(self):
        ohne = self.Protokoll.sudo().create({'user_id': self.user_a.id})
        self.assertFalse(ohne.with_user(self.user_a).wb_voip_letzte_protokoll_ids)

    def test_kontaktdaten(self):
        protokoll = self._protokoll()
        als_a = protokoll.with_user(self.user_a)
        self.assertEqual(als_a.wb_voip_partner_telefon, '+49 170 1234567')
        self.assertEqual(als_a.wb_voip_partner_email, 'popup@example.com')

    def test_kontaktdaten_ohne_leserecht_leer(self):
        protokoll = self._protokoll()
        self.env['ir.rule'].create({
            'name': 'Test: Kontakt gesperrt', 'model_id': self.env.ref('base.model_res_partner').id,
            'domain_force': "[('id', '!=', %d)]" % self.kontakt.id,
        })
        als_a = protokoll.with_user(self.user_a)
        self.assertFalse(als_a.wb_voip_partner_telefon)
        self.assertFalse(als_a.wb_voip_partner_email)

    # --- Folgeaktivität ----------------------------------------------------------------

    def _pruefe_aktion(self, aktion, ziel):
        self.assertEqual(aktion['type'], 'ir.actions.act_window')
        self.assertEqual(aktion['res_model'], 'mail.activity.schedule')
        self.assertEqual(aktion['target'], 'new')
        self.assertEqual(aktion['context']['active_model'], ziel._name)
        self.assertEqual(aktion['context']['active_ids'], [ziel.id])
        self.assertEqual(aktion['context']['active_id'], ziel.id)

    def test_folgeaktivitaet_am_schreibbaren_ziel(self):
        protokoll = self._protokoll(res_model='res.partner', res_id=self.kontakt.id)
        aktion = protokoll.with_user(self.user_a).action_wb_voip_folgeaktivitaet()
        self._pruefe_aktion(aktion, self.kontakt)

    def test_folgeaktivitaet_am_protokoll_wenn_ziel_nicht_schreibbar(self):
        # lesbar, aber nicht schreibbar
        self.env['ir.rule'].create({
            'name': 'Test: Kontakt nur lesbar', 'model_id': self.env.ref('base.model_res_partner').id,
            'domain_force': "[('id', '!=', %d)]" % self.kontakt.id,
            'perm_read': False, 'perm_write': True, 'perm_create': False, 'perm_unlink': False,
        })
        protokoll = self._protokoll(res_model='res.partner', res_id=self.kontakt.id)
        als_a = protokoll.with_user(self.user_a)
        self.assertTrue(self.kontakt.with_user(self.user_a).has_access('read'))
        self.assertFalse(self.kontakt.with_user(self.user_a).has_access('write'))
        self._pruefe_aktion(als_a.action_wb_voip_folgeaktivitaet(), protokoll)

    def test_folgeaktivitaet_am_protokoll_ohne_ziel(self):
        protokoll = self._protokoll()
        self._pruefe_aktion(protokoll.with_user(self.user_a).action_wb_voip_folgeaktivitaet(),
                            protokoll)

    def test_folgeaktivitaet_fremdes_protokoll(self):
        protokoll = self._protokoll(user=self.user_a)
        with self.assertRaises(AccessError):
            protokoll.with_user(self.user_b).action_wb_voip_folgeaktivitaet()

    def test_folgeaktivitaet_nur_voip_benutzer(self):
        protokoll = self._protokoll()
        ohne_gruppe = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Ohne Gruppe', 'login': 'wb_voip_ohne', 'email': 'ohne@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            protokoll.with_user(ohne_gruppe).action_wb_voip_folgeaktivitaet()

    def test_popup_ansicht_enthaelt_platzhalter(self):
        ansicht = self.env.ref('wb_voip.wb_voip_protokoll_view_form_popup')
        self.assertIn('o_wb_voip_popup_aktionen', ansicht.arch_db)
        self.assertIn('action_wb_voip_folgeaktivitaet', ansicht.arch_db)
        # rechte Spalte nach Layout, linke Spalte fest
        self.assertIn('wb_voip_popup_layout', ansicht.arch_db)
        for feld in ('ergebnis', 'beschreibung'):
            self.assertIn(f'<field name="{feld}"', ansicht.arch_db)
        self.assertNotIn('wb_voip_letzte_protokoll_ids', ansicht.arch_db)

    # --- Anruf-Buttons im Popup --------------------------------------------------------

    def _anruf_mit_protokoll(self, user=None, zustand='beendet'):
        user = user or self.user_a
        anruf = self.env['wb.voip.anruf'].sudo().create({
            'user_id': user.id, 'richtung': 'eingehend', 'zustand': zustand,
            'nummer': '+49 170 1234567', 'nummer_e164': '+491701234567',
            'partner_id': self.kontakt.id})
        protokoll = self._protokoll(user=user, anruf_id=anruf.id)
        return anruf, protokoll

    def _taste(self, **werte):
        # „Neuer Kontakt“ entfällt bei Anrufen mit Kontakt: hier eine Webadresse
        vals = {'name': 'Taste', 'art': 'url', 'url_vorlage': 'https://x.example/{nummer}',
                'ort_protokoll_popup': True, 'zustand': 'beendet'}
        vals.update(werte)
        return self.env['wb.voip.aktion'].create(vals)

    def test_popup_aktionen_nur_popup_buttons(self):
        self._lizenz_gueltig()
        _anruf, protokoll = self._anruf_mit_protokoll()
        popup = self._taste(name='Im Popup')
        self._taste(name='Nur Fenster', ort_protokoll_popup=False, ort_anrufer_fenster=True)
        self._taste(name='Nur Softphone', ort_protokoll_popup=False, ort_softphone=True)
        self._taste(name='Falsche Richtung', richtung='ausgehend')
        self._taste(name='Läuft', zustand='laeuft')
        aktionen = protokoll.with_user(self.user_a).wb_voip_popup_aktionen()
        self.assertEqual([a['id'] for a in aktionen if a['name'] in (
            'Im Popup', 'Nur Fenster', 'Nur Softphone', 'Falsche Richtung', 'Läuft')], [popup.id])
        self.assertIn('protokoll_popup', aktionen[0]['orte'])
        self.assertNotIn('url_vorlage', aktionen[0])

    def test_popup_aktionen_fremdes_protokoll(self):
        _anruf, protokoll = self._anruf_mit_protokoll()
        self._taste()
        with self.assertRaises(AccessError):
            protokoll.with_user(self.user_b).wb_voip_popup_aktionen()

    def test_popup_aktionen_nur_voip_benutzer(self):
        _anruf, protokoll = self._anruf_mit_protokoll()
        ohne_gruppe = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Ohne Gruppe', 'login': 'wb_voip_ohne2', 'email': 'ohne2@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            protokoll.with_user(ohne_gruppe).wb_voip_popup_aktionen()

    def test_popup_aktionen_ohne_anruf(self):
        protokoll = self._protokoll()
        self._taste()
        self.assertEqual(protokoll.with_user(self.user_a).wb_voip_popup_aktionen(), [])

    def test_popup_aktion_ausfuehren_ueber_den_anruf(self):
        self._lizenz_gueltig()
        anruf, protokoll = self._anruf_mit_protokoll()
        taste = self._taste()
        ergebnis = anruf.with_user(self.user_a).wb_voip_aktion_ausfuehren(taste.id)
        self.assertEqual(ergebnis['type'], 'ir.actions.act_url')
        # Zustand „beendet“ muss der Button zulassen
        zu_frueh = self._taste(zustand='laeuft')
        with self.assertRaises(UserError):
            anruf.with_user(self.user_a).wb_voip_aktion_ausfuehren(zu_frueh.id)
        self.assertEqual(protokoll.anruf_id, anruf)
