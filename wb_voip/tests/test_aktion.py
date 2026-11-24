from unittest.mock import patch

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestAktion(WbVoipCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.firma = cls.env['res.partner'].create({'name': 'Firma X', 'is_company': True})
        cls.person = cls.env['res.partner'].create({'name': 'Person Y', 'is_company': False})
        cls.fenster = cls.env['ir.actions.act_window'].create({
            'name': 'Test', 'res_model': 'res.partner'})
        cls.server = cls.env['ir.actions.server'].create({
            'name': 'Test', 'model_id': cls.env.ref('base.model_res_partner').id,
            'state': 'code', 'code': 'pass'})

    def _neu(self, **werte):
        vals = {'name': 'Taste', 'art': 'neuer_kontakt', 'ort_softphone': True}
        vals.update(werte)
        return self.env['wb.voip.aktion'].create(vals)

    def _neu_als(self, benutzer, **werte):
        vals = {'name': 'Taste', 'art': 'neuer_kontakt', 'ort_softphone': True}
        vals.update(werte)
        return self.env['wb.voip.aktion'].with_user(benutzer).create(vals)

    def _anruf(self, **werte):
        vals = {'user_id': self.user_a.id, 'richtung': 'eingehend', 'zustand': 'klingelt'}
        vals.update(werte)
        return self.env['wb.voip.anruf'].sudo().create(vals)

    def test_pflichtfelder_je_art(self):
        with self.assertRaises(ValidationError):
            self._neu(art='fenster')
        with self.assertRaises(ValidationError):
            self._neu(art='serveraktion')
        with self.assertRaises(ValidationError):
            self._neu(art='url')
        self._neu(art='fenster', act_window_id=self.fenster.id)
        self._neu(art='serveraktion', server_action_id=self.server.id)
        self._neu(art='url', url_vorlage='https://x.example/{nummer}')

    def test_icon(self):
        self._neu(icon='fa-user')
        for schlecht in ('javascript:alert(1)', 'fa-', 'FA-user', 'user', 'fa-a b', 'fa-user\n'):
            with self.assertRaises(ValidationError, msg=schlecht):
                self._neu(icon=schlecht)

    def test_url_vorlage(self):
        for schlecht in ('javascript:alert(1)', 'http://x.example/{nummer}',
                         'https://x.example/{unbekannt}', 'https://x.example/{0}',
                         'https://x.example/' + 'a' * 500, 'https://{nummer}.example',
                         'https://x@{nummer}/a', 'https:///pfad', 'https://x.example/a b',
                         'https://x.example/a\tb'):
            with self.assertRaises(ValidationError, msg=schlecht):
                self._neu(art='url', url_vorlage=schlecht)
        self._neu(art='url', url_vorlage='https://x.example/{nummer}/{nummer_e164}/{partner_id}')

    def test_partner_domain(self):
        self._neu(partner_domain="[('is_company', '=', True)]")
        for schlecht in ("[('gibtsnicht', '=', 1)]", "__import__('os')", "('is_company', '=', True)",
                         "[('is_company', 'ungueltig', True)]", "[('name.x', '=', 1)]", "{}"):
            with self.assertRaises(ValidationError, msg=schlecht):
                self._neu(partner_domain=schlecht)

    def test_kein_ort(self):
        with self.assertRaises(ValidationError):
            self._neu(ort_softphone=False)

    def test_benutzer_kann_nicht_schreiben(self):
        aktion = self._neu()
        aktion.with_user(self.user_a).read(['name'])
        with self.assertRaises(AccessError):
            aktion.with_user(self.user_a).write({'name': 'x'})
        with self.assertRaises(AccessError):
            self.env['wb.voip.aktion'].with_user(self.user_a).create(
                {'name': 'x', 'art': 'neuer_kontakt', 'ort_softphone': True})
        aktion.with_user(self.user_manager).write({'name': 'y'})

    def test_passend_richtung_zustand(self):
        aktion = self._neu(richtung='eingehend', zustand='klingelt').with_user(self.user_a)
        self.assertTrue(aktion._wb_voip_passend(self._anruf()))
        self.assertFalse(aktion._wb_voip_passend(self._anruf(richtung='ausgehend')))
        self.assertFalse(aktion._wb_voip_passend(self._anruf(zustand='laeuft')))
        beendet = self._neu(zustand='beendet').with_user(self.user_a)
        self.assertTrue(beendet._wb_voip_passend(self._anruf(zustand='beendet')))
        self.assertTrue(beendet._wb_voip_passend(self._anruf(zustand='besetzt')))
        self.assertFalse(beendet._wb_voip_passend(self._anruf()))
        immer = self._neu().with_user(self.user_a)
        self.assertTrue(immer._wb_voip_passend(self._anruf(richtung='ausgehend', zustand='laeuft')))

    def test_passend_gruppe(self):
        aktion = self._neu(group_ids=[(6, 0, [self.env.ref('wb_voip.group_wb_voip_manager').id])])
        anruf_a = self._anruf()
        self.assertFalse(aktion.with_user(self.user_a)._wb_voip_passend(anruf_a))
        anruf_m = self._anruf(user_id=self.user_manager.id)
        self.assertTrue(aktion.with_user(self.user_manager)._wb_voip_passend(anruf_m))
        self.assertTrue(self._neu().with_user(self.user_a)._wb_voip_passend(anruf_a))

    def test_passend_gruppe_implizit(self):
        # Manager hat die Benutzergruppe nur über implied_ids
        aktion = self._neu(group_ids=[(6, 0, [self.env.ref('wb_voip.group_wb_voip_user').id])])
        anruf_m = self._anruf(user_id=self.user_manager.id)
        self.assertTrue(aktion.with_user(self.user_manager)._wb_voip_passend(anruf_m))

    def test_passend_nur_eigener_anruf(self):
        aktion = self._neu()
        self.assertFalse(aktion.with_user(self.user_b)._wb_voip_passend(self._anruf()))

    def test_domain_feldgruppe_und_rechte(self):
        # Autor ohne Recht auf das Feld/Modell: abgelehnt
        with self.assertRaises(ValidationError):
            self._neu_als(self.user_manager, partner_domain="[('user_ids.password', '!=', False)]")
        with self.assertRaises(ValidationError):
            self._neu_als(self.user_manager, partner_domain="[('user_ids', 'any!', [])]")
        modell = self.env['res.partner'].with_user(self.user_manager)
        gesperrt = [n for n, f in modell._fields.items()
                    if f.store and not modell._has_field_access(f, 'read')]
        self.assertTrue(gesperrt, "Test braucht ein Kontaktfeld mit Gruppe")
        with self.assertRaises(ValidationError):
            self._neu_als(self.user_manager, partner_domain=f"[('{gesperrt[0]}', '!=', False)]")

    def test_domain_pfade_und_any(self):
        self._neu(partner_domain="[('parent_id.is_company', '=', True)]")
        self._neu(partner_domain="[('child_ids', 'any', [('is_company', '=', False)])]")
        with self.assertRaises(ValidationError):
            self._neu(partner_domain="[('child_ids', 'any', [('gibtsnicht', '=', 1)])]")
        firma = self.env['res.partner'].create({'name': 'F2', 'is_company': True})
        kind = self.env['res.partner'].create({'name': 'K2', 'parent_id': firma.id})
        aktion = self._neu(partner_domain="[('parent_id.is_company', '=', True)]")
        self.assertTrue(aktion.with_user(self.user_a)._wb_voip_passend(
            self._anruf(partner_id=kind.id)))
        self.assertFalse(aktion.with_user(self.user_a)._wb_voip_passend(
            self._anruf(partner_id=self.person.id)))

    def test_domain_unlesbarer_kontakt(self):
        aktion = self._neu(partner_domain="[('is_company', '=', True)]")
        anruf = self._anruf(partner_id=self.firma.id)
        modell = type(self.env['res.partner'])
        with patch.object(modell, 'has_access', return_value=False):
            self.assertFalse(aktion.with_user(self.user_a)._wb_voip_passend(anruf))

    def test_felder_nur_manager(self):
        aktion = self._neu(art='url', url_vorlage='https://x.example/{nummer}',
                           partner_domain="[('is_company', '=', True)]")
        for feld in ('url_vorlage', 'partner_domain'):
            with self.assertRaises(AccessError):
                aktion.with_user(self.user_a).read([feld])
            aktion.with_user(self.user_manager).read([feld])
        self.assertNotIn('url_vorlage', aktion.with_user(self.user_a)._wb_voip_fuer_client())

    def test_passend_domain(self):
        aktion = self._neu(partner_domain="[('is_company', '=', True)]").with_user(self.user_a)
        self.assertTrue(aktion._wb_voip_passend(self._anruf(partner_id=self.firma.id)))
        self.assertFalse(aktion._wb_voip_passend(self._anruf(partner_id=self.person.id)))
        self.assertFalse(aktion._wb_voip_passend(self._anruf()))

    def test_fuer_client(self):
        aktion = self._neu(icon='fa-user', farbe='danger', ort_anrufer_fenster=True)
        daten = aktion._wb_voip_fuer_client()
        self.assertEqual(daten, {
            'id': aktion.id, 'name': 'Taste', 'icon': 'fa-user', 'farbe': 'danger',
            'orte': ['anrufer_fenster', 'softphone']})

    def test_standard_neuer_kontakt(self):
        aktion = self.env.ref('wb_voip.wb_voip_aktion_neuer_kontakt')
        self.assertEqual(aktion.art, 'neuer_kontakt')
        self.assertTrue(aktion.ort_anrufer_fenster)
        self.assertFalse(aktion.partner_domain)
