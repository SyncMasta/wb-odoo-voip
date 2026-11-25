import json

from odoo.addons.wb_voip.tests.common import WbVoipCase


class WbVoipCrmCase(WbVoipCase):
    """Base class for WB VoIP CRM tests: salespeople, contacts, a second company."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        basis = [cls.env.ref('base.group_user').id,
                 cls.env.ref('wb_voip.group_wb_voip_user').id]
        cls.fremdfirma = cls.env['res.company'].create({'name': 'CRM Fremdfirma'})
        # V1/V2: VoIP-Benutzer, nur eigene Vorgänge (Regel „eigene“) bzw. alle Vorgänge
        cls.vertrieb_eigene = Users.create({
            'name': 'Vertrieb Eigene', 'login': 'wb_voip_crm_eigene',
            'email': 'wb_voip_crm_eigene@example.com',
            'company_ids': [(6, 0, [cls.env.company.id, cls.fremdfirma.id])],
            'group_ids': [(6, 0, basis + [cls.env.ref('sales_team.group_sale_salesman').id])],
        })
        cls.vertrieb_alle = Users.create({
            'name': 'Vertrieb Alle', 'login': 'wb_voip_crm_alle',
            'email': 'wb_voip_crm_alle@example.com',
            'company_ids': [(6, 0, [cls.env.company.id, cls.fremdfirma.id])],
            'group_ids': [(6, 0, basis + [
                cls.env.ref('sales_team.group_sale_salesman_all_leads').id])],
        })
        # VoIP-Benutzer ohne CRM-Rechte
        cls.ohne_crm = Users.create({
            'name': 'Ohne CRM', 'login': 'wb_voip_crm_ohne',
            'email': 'wb_voip_crm_ohne@example.com',
            'group_ids': [(6, 0, basis)],
        })
        cls.kontakt = cls.env['res.partner'].create({'name': 'Erika Vorgang', 'phone': '+49 170 1234567'})
        cls.firma = cls.env['res.partner'].create({'name': 'Vorgang GmbH', 'is_company': True})
        cls.ansprechpartner = cls.env['res.partner'].create({
            'name': 'Max Ansprech', 'parent_id': cls.firma.id})
        cls._nr = 0

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()

    def _lead(self, partner=None, **werte):
        vals = {'name': 'Vorgang', 'type': 'opportunity',
                'partner_id': (partner or self.kontakt).id,
                'user_id': self.vertrieb_eigene.id}
        vals.update(werte)
        lead = self.env['crm.lead'].create(vals)
        self.assertLess(lead.probability, 100)
        return lead

    def _anruf(self, benutzer=None, **werte):
        vals = {'user_id': (benutzer or self.vertrieb_eigene).id, 'richtung': 'eingehend',
                'zustand': 'beendet', 'nummer': '0170 / 1234567',
                'nummer_e164': '+491701234567'}
        vals.update(werte)
        return self.env['wb.voip.anruf'].sudo().create(vals)

    def _protokoll(self, anruf):
        protokoll = self.env['wb.voip.protokoll'].sudo().create({
            'anruf_id': anruf.id, 'user_id': anruf.user_id.id, 'richtung': 'eingehend'})
        anruf.protokoll_id = protokoll
        return protokoll

    def _beginnen(self, benutzer, res_model=False, res_id=False, nummer='+491701234567'):
        """Start a call through the public RPC method, return the client data."""
        self._nr += 1
        return self.env['wb.voip.anruf'].with_user(benutzer).wb_voip_beginnen(
            'eingehend', nummer, f'crm-{self._nr}-{benutzer.id}', res_model, res_id)

    def _bus_meldungen(self, aktion):
        Bus = self.env['bus.bus'].sudo()
        self.env.cr.precommit.run()
        vorher = Bus.search([]).ids
        aktion()
        self.env.cr.precommit.run()
        neu = Bus.search([('id', 'not in', vorher)])
        return [json.loads(n.message) for n in neu
                if json.loads(n.message)['type'] == 'wb_voip/kontext']
