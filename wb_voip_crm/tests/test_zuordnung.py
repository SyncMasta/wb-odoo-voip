from odoo.tests import tagged

from .common import WbVoipCrmCase


@tagged('wb_voip_crm', 'post_install', '-at_install')
class TestZuordnung(WbVoipCrmCase):

    def _vorschlag(self, benutzer, partner=None):
        Anruf = self.env['wb.voip.anruf'].with_user(benutzer)
        partner = (partner or self.kontakt).with_user(benutzer)
        return Anruf._wb_voip_zuordnung(partner, None)

    def test_kein_vorgang(self):
        self.assertFalse(self._vorschlag(self.vertrieb_eigene))

    def test_ohne_kontakt_keine_zuordnung(self):
        self._lead()
        Anruf = self.env['wb.voip.anruf'].with_user(self.vertrieb_eigene)
        self.assertIsNone(Anruf._wb_voip_zuordnung(self.env['res.partner'], None))

    def test_ein_offener_vorgang(self):
        lead = self._lead()
        self.assertEqual(self._vorschlag(self.vertrieb_eigene), lead)

    def test_ein_vorgang_eines_kollegen_mit_leserecht(self):
        # genau ein offener Vorgang, der Benutzer sieht alle: wird gesetzt, auch wenn fremd
        lead = self._lead(user_id=self.vertrieb_eigene.id)
        self.assertEqual(self._vorschlag(self.vertrieb_alle), lead)

    def test_mehrere_neuester_eigener(self):
        alt = self._lead(name='Alt')
        neu = self._lead(name='Neu')
        fremd = self._lead(name='Fremd', user_id=self.vertrieb_alle.id)
        self.env.cr.execute(
            "UPDATE crm_lead SET write_date = write_date - interval '2 days' WHERE id = %s",
            (alt.id,))
        self.env.cr.execute(
            "UPDATE crm_lead SET write_date = write_date + interval '1 day' WHERE id = %s",
            (fremd.id,))
        self.env.invalidate_all()
        # „Alle“ ist nur bei „Fremd“ verantwortlich; „Eigene“ sieht nur Alt und Neu, Neu gewinnt
        self.assertEqual(self._vorschlag(self.vertrieb_alle, self.kontakt), fremd)
        self.assertEqual(self._vorschlag(self.vertrieb_eigene), neu)

    def test_mehrere_keiner_eigener(self):
        self._lead(name='A', user_id=self.vertrieb_eigene.id)
        self._lead(name='B', user_id=self.vertrieb_eigene.id)
        # V-Alle sieht beide, ist aber bei keinem verantwortlich: keine Zuordnung
        self.assertFalse(self._vorschlag(self.vertrieb_alle))

    def test_gewonnener_vorgang_zaehlt_nicht(self):
        lead = self._lead()
        lead.action_set_won()
        self.assertEqual(lead.probability, 100)
        self.assertFalse(self._vorschlag(self.vertrieb_eigene))

    def test_verlorener_und_archivierter_vorgang_zaehlt_nicht(self):
        lost = self._lead(name='Verloren')
        lost.action_set_lost()
        archiv = self._lead(name='Archiv')
        archiv.active = False
        self.assertFalse(self._vorschlag(self.vertrieb_eigene))

    def test_firma_des_kontakts(self):
        lead = self._lead(partner=self.firma)
        self.assertEqual(self._vorschlag(self.vertrieb_eigene, self.ansprechpartner), lead)

    def test_andere_firma_nicht_zugeordnet(self):
        # Benutzer darf beide Firmen, der Vorgang gehört der anderen Firma
        self._lead(company_id=self.fremdfirma.id)
        self.assertEqual(self.vertrieb_eigene.company_id, self.env.company)
        self.assertFalse(self._vorschlag(self.vertrieb_eigene))

    def test_vorgang_ohne_firma_zaehlt(self):
        lead = self._lead(company_id=False)
        self.assertEqual(self._vorschlag(self.vertrieb_eigene), lead)

    def test_andere_firma_neben_eigener(self):
        eigener = self._lead(name='Eigene Firma', company_id=self.env.company.id)
        self._lead(name='Andere Firma', company_id=self.fremdfirma.id)
        self.assertEqual(self._vorschlag(self.vertrieb_eigene), eigener)

    # --- Review Focus 1: Leserecht ---

    def test_regel_eigene_fremder_vorgang_nicht_zugeordnet(self):
        self._lead(user_id=self.vertrieb_alle.id)
        self.assertFalse(self._vorschlag(self.vertrieb_eigene))

    def test_regel_eigene_vorgang_ohne_verantwortlichen_sichtbar(self):
        lead = self._lead(user_id=False)
        self.assertEqual(self._vorschlag(self.vertrieb_eigene), lead)

    def test_regel_alle_sieht_fremden_vorgang(self):
        lead = self._lead(user_id=self.vertrieb_eigene.id)
        self.assertEqual(self._vorschlag(self.vertrieb_alle), lead)

    def test_ohne_crm_recht_still_keine_zuordnung(self):
        self._lead()
        self.assertIsNone(self._vorschlag(self.ohne_crm))

    # --- über die öffentliche Methode ---

    def test_beginnen_setzt_vorgang(self):
        lead = self._lead()
        daten = self._beginnen(self.vertrieb_eigene)
        self.assertEqual((daten['res_model'], daten['res_id']), ('crm.lead', lead.id))
        anruf = self.env['wb.voip.anruf'].browse(daten['id'])
        self.assertEqual(anruf.lead_id, lead)

    def test_beginnen_ohne_leserecht_keine_zuordnung_und_kein_name(self):
        self._lead(user_id=self.vertrieb_alle.id, name='Geheimer Vorgang')
        daten = self._beginnen(self.vertrieb_eigene)
        self.assertFalse(daten['res_model'])
        self.assertFalse(daten['res_id'])
        self.assertFalse(daten['res_name'])
        self.assertNotIn('Geheimer Vorgang', str(daten))
        self.assertFalse(self.env['wb.voip.anruf'].browse(daten['id']).lead_id)

    def test_beginnen_uebergebener_vorgang_hat_vorrang(self):
        self._lead(name='Kandidat')
        gewaehlt = self._lead(name='Gewählt', partner=self.firma)
        daten = self._beginnen(self.vertrieb_eigene, 'crm.lead', gewaehlt.id)
        self.assertEqual(daten['res_id'], gewaehlt.id)
        self.assertEqual(self.env['wb.voip.anruf'].browse(daten['id']).lead_id, gewaehlt)

    def test_beginnen_am_kontakt_ordnet_vorgang_zu(self):
        lead = self._lead()
        daten = self._beginnen(self.vertrieb_eigene, 'res.partner', self.kontakt.id)
        self.assertEqual((daten['res_model'], daten['res_id']), ('crm.lead', lead.id))

    def test_abgewiesen_ordnet_vorgang_zu(self):
        lead = self._lead()
        daten = self.env['wb.voip.anruf'].with_user(self.vertrieb_eigene) \
            .wb_voip_abgewiesen_melden('+491701234567', 'crm-480')
        self.assertEqual(daten['res_id'], lead.id)
        self.assertEqual(self.env['wb.voip.anruf'].browse(daten['id']).lead_id, lead)

    def test_beginnen_gewonnener_vorgang_nicht(self):
        lead = self._lead()
        lead.action_set_won()
        daten = self._beginnen(self.vertrieb_eigene)
        self.assertFalse(daten['res_id'])
