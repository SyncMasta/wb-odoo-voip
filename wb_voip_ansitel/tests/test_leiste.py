from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import AnsitelCase


@tagged('post_install', '-at_install')
class TestTastenleiste(AnsitelCase):
    """Display setting of the quick-key bar and the cache carry-over of the token flag."""

    def test_standard_softphone(self):
        self.assertEqual(self.user_a.wb_voip_ansitel_tastenleiste, 'softphone')

    def test_benutzer_stellt_eigene_anzeige_ein(self):
        for wert in ('rand', 'beide', 'softphone'):
            self.user_a.with_user(self.user_a).write({'wb_voip_ansitel_tastenleiste': wert})
            self.assertEqual(self.user_a.with_user(self.user_a).wb_voip_ansitel_tastenleiste, wert)

    def test_ungueltiger_wert_abgelehnt(self):
        with self.assertRaises(ValueError):
            self.user_a.with_user(self.user_a).write({'wb_voip_ansitel_tastenleiste': 'links'})

    def test_fremde_anzeige_nicht_schreibbar(self):
        with self.assertRaises(AccessError):
            self.user_b.with_user(self.user_a).write({'wb_voip_ansitel_tastenleiste': 'rand'})

    def test_session_wert_nur_fuer_aktive_benutzer(self):
        self.user_a.write({'wb_voip_ansitel_tastenleiste': 'beide'})
        self.assertEqual(self.user_a.with_user(self.user_a)._wb_voip_ansitel_session_wert(), 'beide')
        # Benutzer ohne Telefon-Einrichtung: kein Wert (die Leiste gibt es dort nicht)
        self.user_a.write({'wb_voip_anlage_id': False})
        self.assertFalse(self.user_a.with_user(self.user_a)._wb_voip_ansitel_session_wert())

    def test_verwerfen_setzt_token_flag_zurueck(self):
        self.user_a.write({'wb_voip_ansitel_token': 'persoenlich-test-token'})
        self.assertTrue(self.user_a.wb_voip_ansitel_token_gesetzt)
        self.env['res.users']._wb_voip_ansitel_loesen(self.user_a)
        self.assertFalse(self.user_a.wb_voip_ansitel_token_gesetzt)
