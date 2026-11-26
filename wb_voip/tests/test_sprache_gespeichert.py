from datetime import timedelta

from odoo.tests import freeze_time, tagged

from .common import WbVoipCase

NUMMER = '+49 170 7654321'
START = '2026-11-25 09:00:00'


@tagged('wb_voip', 'post_install', '-at_install')
class TestSpracheGespeichert(WbVoipCase):
    """Stored texts of server jobs (no ``lang`` in the context) use the language of the owner."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('de_DE')
        cls.env['ir.module.module']._load_module_terms(['wb_voip'], ['de_DE'], overwrite=True)
        cls.kontakt = cls.env['res.partner'].create({'name': 'Sven Sprache', 'phone': NUMMER})
        cls.user_a.lang = 'de_DE'
        cls.user_b.lang = 'en_US'

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()
        self._zaehler = 0

    def _anruf(self, user, richtung='eingehend'):
        self._zaehler += 1
        Anruf = self.env['wb.voip.anruf'].with_user(user)
        daten = Anruf.wb_voip_beginnen(richtung, NUMMER, f'sprache-{self._zaehler}', False, False)
        return Anruf.browse(daten['id'])

    def _verpasst(self, user):
        anruf = self._anruf(user)
        anruf.wb_voip_beendet(False, '487')
        return anruf

    def _cron(self, anruf):
        """The call as a server job sees it: superuser, empty context."""
        return anruf.sudo().with_env(self.env(context={}))

    def test_erledigt_text_je_besitzer(self):
        with freeze_time(START) as zeit:
            alt_a = self._verpasst(self.user_a)
            alt_b = self._verpasst(self.user_b)
            zeit.tick(timedelta(minutes=5))
            neu = self._anruf(self.user_b)
            neu.wb_voip_angenommen()
            self._cron(neu)._wb_voip_nummer_erreicht()
        self.assertEqual(alt_a.sudo().erledigt_text, 'Angenommen von VoIP Benutzer B')
        self.assertEqual(alt_b.sudo().erledigt_text, 'Answered by VoIP Benutzer B')

    def test_rueckruf_text_je_besitzer(self):
        with freeze_time(START) as zeit:
            alt_a = self._verpasst(self.user_a)
            alt_b = self._verpasst(self.user_b)
            zeit.tick(timedelta(minutes=5))
            raus = self._anruf(self.user_b, richtung='ausgehend')
            raus.wb_voip_angenommen()
            daten = raus.wb_voip_beendet(False, False)
            protokoll = self.env['wb.voip.protokoll'].sudo().browse(daten['protokoll_id'])
            self._cron(raus)._wb_voip_nummer_erreicht(protokoll)
        self.assertEqual(alt_a.sudo().erledigt_text, 'Zurückgerufen von VoIP Benutzer B')
        self.assertEqual(alt_b.sudo().erledigt_text, 'Called back by VoIP Benutzer B')

    def test_chatter_text_des_protokolls_in_benutzersprache(self):
        with freeze_time(START):
            anruf = self._anruf(self.user_a)
            anruf.wb_voip_angenommen()
            # Beenden durch einen Serverlauf (z. B. Aufräumen) ohne Sprache im Kontext
            self._cron(anruf)._wb_voip_protokollieren()
        texte = ' '.join(self.kontakt.message_ids.mapped('body'))
        self.assertIn('Anruf eingehend', texte)
