from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestLoeschfrist(WbVoipCase):
    """Retention period for call data (system parameter, daily cron)."""

    def _anruf(self, monate, zustand='beendet', sip=None):
        beginn = fields.Datetime.now() - relativedelta(months=monate)
        return self.env['wb.voip.anruf'].sudo().create({
            'user_id': self.user_a.id, 'richtung': 'eingehend', 'nummer': '0170 1',
            'sip_call_id': sip or f'frist-{monate}-{zustand}', 'zustand': zustand,
            'beginn': beginn, 'zuletzt_gesehen': beginn,
        })

    def _frist(self, wert):
        self.env['ir.config_parameter'].sudo().set_param('wb_voip.aufbewahrung_monate', wert)

    def test_standard_zwoelf_monate(self):
        self.assertEqual(
            self.env['ir.config_parameter'].sudo().get_param('wb_voip.aufbewahrung_monate'), '12')
        cron = self.env.ref('wb_voip.ir_cron_voip_loeschfrist')
        self.assertEqual((cron.interval_number, cron.interval_type), (1, 'days'))

    def test_alte_finale_anrufe_geloescht(self):
        self._frist('12')
        alt = self._anruf(13)
        alt_abgelehnt = self._anruf(13, 'abgelehnt')
        alt_besetzt = self._anruf(13, 'besetzt')
        alt_fehl = self._anruf(13, 'fehlgeschlagen')
        jung = self._anruf(11)
        self.env['wb.voip.anruf']._wb_voip_loeschfrist()
        self.assertFalse((alt | alt_abgelehnt | alt_besetzt | alt_fehl).exists())
        self.assertTrue(jung.exists())

    def test_offener_alter_anruf_bleibt(self):
        self._frist('12')
        laeuft = self._anruf(13, 'laeuft')
        klingelt = self._anruf(13, 'klingelt')
        self.env['wb.voip.anruf']._wb_voip_loeschfrist()
        self.assertTrue(laeuft.exists())
        self.assertTrue(klingelt.exists())

    def test_frist_null_loescht_nichts(self):
        self._frist('0')
        alt = self._anruf(60)
        self.env['wb.voip.anruf']._wb_voip_loeschfrist()
        self.assertTrue(alt.exists())

    def test_ungueltige_frist_loescht_nichts(self):
        self._frist('abc')
        alt = self._anruf(60)
        self.env['wb.voip.anruf']._wb_voip_loeschfrist()
        self.assertTrue(alt.exists())

    def test_protokoll_bleibt(self):
        self._frist('12')
        alt = self._anruf(13)
        protokoll = self.env['wb.voip.protokoll'].sudo().create({
            'anruf_id': alt.id, 'user_id': self.user_a.id, 'ergebnis': 'erreicht'})
        alt.protokoll_id = protokoll
        self.env['wb.voip.anruf']._wb_voip_loeschfrist()
        self.assertFalse(alt.exists())
        self.assertTrue(protokoll.exists())
        self.assertFalse(protokoll.anruf_id)

    def test_einstellung(self):
        einstellungen = self.env['res.config.settings'].create(
            {'wb_voip_aufbewahrung_monate': 6})
        einstellungen.execute()
        self.assertEqual(
            self.env['ir.config_parameter'].sudo().get_param('wb_voip.aufbewahrung_monate'), '6')
        self.assertEqual(
            self.env['res.config.settings']._fields['wb_voip_aufbewahrung_monate'].groups,
            'wb_voip.group_wb_voip_manager')

    def test_negative_frist_loescht_nichts(self):
        self._frist('-3')
        alt = self._anruf(60)
        with self.assertLogs('odoo.addons.wb_voip.models.wb_voip_anruf', level='WARNING'):
            self.env['wb.voip.anruf']._wb_voip_loeschfrist()
        self.assertTrue(alt.exists())
