from datetime import timedelta

from odoo.tests import freeze_time, tagged

from .common import WbVoipCase

NUMMER = '+49 170 7654321'
START = '2026-11-25 09:00:00'


@tagged('wb_voip', 'post_install', '-at_install')
class TestSpracheFirma(WbVoipCase):
    """Stored texts of a user without language use the language of the call's company.

    A server job runs in the main company (``env.company``); a call of a second company must
    still get the texts in the language of that company.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('de_DE')
        cls.env['ir.module.module']._load_module_terms(['wb_voip'], ['de_DE'], overwrite=True)
        Firma = cls.env['res.company']
        cls.firma_de = Firma.create({'name': 'Firma Deutsch'})
        cls.firma_de.partner_id.lang = 'de_DE'
        cls.firma_en = Firma.create({'name': 'Firma Englisch'})
        cls.firma_en.partner_id.lang = 'en_US'
        cls.kontakt = cls.env['res.partner'].create({'name': 'Frida Firma', 'phone': NUMMER})
        # Benutzer ohne eigene Sprache, Mitglied beider Firmen
        cls.user_a.write({
            'company_ids': [(4, cls.firma_de.id), (4, cls.firma_en.id)],
        })
        cls.user_a.partner_id.lang = False

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()
        self._zaehler = 0

    def _anruf(self, firma):
        self._zaehler += 1
        Anruf = self.env['wb.voip.anruf'].with_user(self.user_a).with_company(firma)
        daten = Anruf.wb_voip_beginnen('eingehend', NUMMER, f'firma-{self._zaehler}', False, False)
        return Anruf.browse(daten['id'])

    def _cron(self, anruf):
        """The call as a server job sees it: superuser, main company, empty context."""
        return anruf.sudo().with_env(self.env(context={}))

    def test_benutzer_ohne_sprache(self):
        self.assertFalse(self.user_a.lang)

    def test_sprache_der_firma_des_anrufs(self):
        # Hauptfirma Englisch, Anruf der deutschen Firma → Deutsch
        self.env.company.partner_id.lang = 'en_US'
        anruf = self._anruf(self.firma_de)
        self.assertEqual(anruf.sudo().company_id, self.firma_de)
        self.assertEqual(self._cron(anruf)._wb_voip_sprache(self.user_a), 'de_DE')

    def test_sprache_der_firma_des_anrufs_englisch(self):
        # Hauptfirma Deutsch, Anruf der englischen Firma → Englisch
        self.env.company.partner_id.lang = 'de_DE'
        anruf = self._anruf(self.firma_en)
        self.assertEqual(self._cron(anruf)._wb_voip_sprache(self.user_a), 'en_US')

    def test_sprache_des_benutzers_vor_firma(self):
        self.user_a.partner_id.lang = 'en_US'
        anruf = self._anruf(self.firma_de)
        self.assertEqual(self._cron(anruf)._wb_voip_sprache(self.user_a), 'en_US')

    def test_ohne_datensatz_sprache_der_aktuellen_firma(self):
        self.env.company.partner_id.lang = 'de_DE'
        Anruf = self.env['wb.voip.anruf'].sudo().with_env(self.env(context={}))
        self.assertEqual(Anruf._wb_voip_sprache(), 'de_DE')

    def test_mehrere_eintraege_einer_firma(self):
        # mehrere Einträge derselben (zweiten) Firma: deren Sprache, nicht die der Hauptfirma
        self.env.company.partner_id.lang = 'en_US'
        eintraege = self._anruf(self.firma_de) | self._anruf(self.firma_de)
        self.assertEqual(self._cron(eintraege)._wb_voip_sprache(self.user_a), 'de_DE')
        # Einträge verschiedener Firmen: aktuelle Firma
        gemischt = eintraege | self._anruf(self.firma_en)
        self.assertEqual(self._cron(gemischt)._wb_voip_sprache(self.user_a), 'en_US')

    def test_geteilte_rueckruf_aktivitaet_in_sprache_der_firma(self):
        # zwei verpasste Anrufe derselben Nummer an einer Rückruf-Aktivität, erledigt im Serverlauf
        self.env.company.partner_id.lang = 'en_US'
        with freeze_time(START) as zeit:
            e1 = self._anruf(self.firma_de)
            e1.wb_voip_beendet(False, '487')
            zeit.tick(timedelta(minutes=1))
            e2 = self._anruf(self.firma_de)
            e2.wb_voip_beendet(False, '487')
            aktivitaet = e1.sudo().rueckruf_aktivitaet_id
            self.assertTrue(aktivitaet)
            self.assertEqual(aktivitaet, e2.sudo().rueckruf_aktivitaet_id)
            aktivitaet.with_env(self.env(context={})).action_feedback()
        self.assertEqual(e1.sudo().erledigt_text, 'Aufgabe erledigt')
        self.assertEqual(e2.sudo().erledigt_text, 'Aufgabe erledigt')

    def test_chatter_text_in_sprache_der_firma(self):
        self.env.company.partner_id.lang = 'en_US'
        with freeze_time(START):
            anruf = self._anruf(self.firma_de)
            anruf.wb_voip_angenommen()
            # Beenden durch einen Serverlauf (Hauptfirma, ohne Sprache im Kontext)
            self._cron(anruf)._wb_voip_protokollieren()
        texte = ' '.join(self.kontakt.message_ids.mapped('body'))
        self.assertIn('Anruf eingehend', texte)
