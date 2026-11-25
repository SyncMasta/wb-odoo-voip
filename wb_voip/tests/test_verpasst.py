from odoo.tests import tagged

from odoo.addons.wb_voip.models import wb_voip_anruf

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestVerpasst(WbVoipCase):
    """Final states ``verpasst`` and ``anderswo`` for unanswered incoming calls."""

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()
        self._zaehler = 0

    def _anruf(self, richtung='eingehend'):
        self._zaehler += 1
        daten = self.env['wb.voip.anruf'].with_user(self.user_a).wb_voip_beginnen(
            richtung, '+49 170 1234567', f'verpasst-{self._zaehler}', False, False)
        return self.env['wb.voip.anruf'].with_user(self.user_a).browse(daten['id'])

    def test_zustaende_final(self):
        werte = [w for w, _ in self.env['wb.voip.anruf']._fields['zustand'].selection]
        self.assertIn('verpasst', werte)
        self.assertIn('anderswo', werte)
        self.assertIn('verpasst', wb_voip_anruf.FINALE_ZUSTAENDE)
        self.assertIn('anderswo', wb_voip_anruf.FINALE_ZUSTAENDE)

    def test_eingehend_487_verpasst_mit_protokoll(self):
        daten = self._anruf().wb_voip_beendet(False, '487')
        self.assertEqual(daten['zustand'], 'verpasst')
        protokoll = self.env['wb.voip.protokoll'].browse(daten['protokoll_id'])
        self.assertEqual(protokoll.ergebnis, 'nicht_erreicht')

    def test_eingehend_480_verpasst(self):
        daten = self._anruf().wb_voip_beendet(False, '480')
        self.assertEqual(daten['zustand'], 'verpasst')
        self.assertTrue(daten['protokoll_id'])

    def test_eingehend_anderswo_ohne_protokoll(self):
        anruf = self._anruf()
        daten = anruf.wb_voip_beendet(False, 'anderswo')
        self.assertEqual(daten['zustand'], 'anderswo')
        self.assertFalse(daten['protokoll_id'])
        self.assertFalse(anruf.sudo().protokoll_id)
        self.assertFalse(self.env['wb.voip.protokoll'].sudo().search(
            [('anruf_id', '=', anruf.id)]))

    def test_eingehend_603_abgelehnt(self):
        daten = self._anruf().wb_voip_beendet(False, '603')
        self.assertEqual(daten['zustand'], 'abgelehnt')
        self.assertTrue(daten['protokoll_id'])

    def test_eingehend_486_besetzt(self):
        self.assertEqual(self._anruf().wb_voip_beendet(False, '486')['zustand'], 'besetzt')

    def test_ausgehend_487_bleibt_abgelehnt(self):
        daten = self._anruf('ausgehend').wb_voip_beendet(False, '487')
        self.assertEqual(daten['zustand'], 'abgelehnt')

    def test_ausgehend_anderswo_fehlgeschlagen(self):
        daten = self._anruf('ausgehend').wb_voip_beendet(False, 'anderswo')
        self.assertEqual(daten['zustand'], 'fehlgeschlagen')

    def test_angenommen_bleibt_beendet(self):
        anruf = self._anruf()
        anruf.wb_voip_angenommen()
        self.assertEqual(anruf.wb_voip_beendet(False, 'anderswo')['zustand'], 'beendet')

    def test_anderswo_ist_final_noop(self):
        anruf = self._anruf()
        anruf.wb_voip_beendet(False, 'anderswo')
        daten = anruf.wb_voip_beendet(False, '487')
        self.assertEqual(daten['zustand'], 'anderswo')
        self.assertFalse(daten['protokoll_id'])
