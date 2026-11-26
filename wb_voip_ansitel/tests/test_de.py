from odoo.tests import TransactionCase, tagged


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestDeutsch(TransactionCase):
    """German comes from i18n/de.po at runtime (source language is English)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('de_DE')
        cls.env['ir.module.module']._load_module_terms(['wb_voip', 'wb_voip_ansitel'], ['de_DE'],
                                                        overwrite=True)
        cls.de = cls.env(context=dict(cls.env.context, lang='de_DE'))

    def test_feldbezeichnung_und_auswahl(self):
        self.assertEqual(self.de['res.users'].fields_get(['wb_voip_handy'])['wb_voip_handy']['string'],
                         'Mein Handy')
        self.assertEqual(self.env['res.users'].fields_get(['wb_voip_handy'])['wb_voip_handy']['string'],
                         'My mobile phone')
        art = dict(self.de['wb.voip.taste'].fields_get(['art'])['art']['selection'])
        self.assertEqual(art['anrufschutz'], 'Anrufschutz (Nicht stören)')

    def test_fehlermeldung(self):
        self.assertEqual(self.de['wb.voip.taste']._ziel_pruefen('112', 'DE'),
                         'Notruf-, Kurzwahl- und Auskunftsnummern sind als Weiterleitungsziel nicht erlaubt.')
        self.assertEqual(self.env['wb.voip.taste']._ziel_pruefen('112', 'DE'),
                         'Emergency, short-code and directory-enquiry numbers are not allowed as a call '
                         'forwarding target.')
        texte = self.de['res.users']._wb_voip_tasten_texte()
        self.assertEqual(texte['handy'], 'Bitte unter Meine Präferenzen › Telefonie Ihr Handy eintragen.')

    def test_ansicht_menue(self):
        ansicht = self.env.ref('wb_voip_ansitel.wb_voip_ansitel_res_users_view_form_simple')
        self.assertIn('string="Schnelltasten"', ansicht.with_context(lang='de_DE').arch_db)
        self.assertIn('string="Quick keys"', ansicht.with_context(lang='en_US').arch_db)
        self.assertEqual(self.env.ref('wb_voip_ansitel.wb_voip_menu_taste').with_context(lang='de_DE').name,
                         'Softphone-Tasten')

    def test_auslieferungsdaten(self):
        for xmlid, deutsch, englisch in (('taste_ab', 'AB', 'VM'), ('taste_handy', '→ Handy', '→ Mobile'),
                                         ('taste_dnd', 'Nicht stören', 'Do not disturb')):
            taste = self.env.ref(f'wb_voip_ansitel.{xmlid}')
            with self.subTest(xmlid=xmlid):
                self.assertEqual(taste.with_context(lang='de_DE').name, deutsch)
                self.assertEqual(taste.with_context(lang='en_US').name, englisch)
