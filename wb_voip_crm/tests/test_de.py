from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('wb_voip_crm', 'post_install', '-at_install')
class TestDeutsch(TransactionCase):
    """German comes from i18n/de.po at runtime (source language is English)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('de_DE')
        cls.env['ir.module.module']._load_module_terms(['wb_voip', 'wb_voip_crm'], ['de_DE'], overwrite=True)
        cls.de = cls.env(context=dict(cls.env.context, lang='de_DE'))

    def test_feldbezeichnung_und_auswahl(self):
        felder = self.de['wb.voip.anruf'].fields_get(['wb_voip_lead_lesbar_id'])
        self.assertEqual(felder['wb_voip_lead_lesbar_id']['string'], 'Vorgang')
        self.assertEqual(self.env['wb.voip.anruf'].fields_get(['wb_voip_lead_lesbar_id'])
                         ['wb_voip_lead_lesbar_id']['string'], 'Opportunity')
        art = dict(self.de['wb.voip.aktion'].fields_get(['art'])['art']['selection'])
        self.assertEqual(art['vorgang'], 'Vorgang anlegen')
        block = dict(self.de['wb.voip.layout.block'].fields_get(['art'])['art']['selection'])
        self.assertEqual(block['vorgaenge'], 'Vorgänge')

    def test_fehlermeldung(self):
        for env, text in ((self.de, 'Der Titel darf höchstens 200 Zeichen lang sein.'),
                          (self.env, 'The title may be at most 200 characters long.')):
            with self.subTest(lang=env.lang), self.assertRaises(ValidationError) as fehler:
                env['wb.voip.aktion'].create({'name': 'X', 'art': 'vorgang', 'titel_vorlage': 'x' * 201})
            self.assertEqual(str(fehler.exception), text)

    def test_ansicht(self):
        ansicht = self.env.ref('wb_voip_crm.wb_voip_crm_lead_view_form')
        self.assertIn('string="Anrufprotokolle"', ansicht.with_context(lang='de_DE').arch_db)
        self.assertIn('string="Call logs"', ansicht.with_context(lang='en_US').arch_db)

    def test_auslieferungsdaten(self):
        button = self.env.ref('wb_voip_crm.wb_voip_aktion_vorgang')
        self.assertEqual(button.with_context(lang='de_DE').name, 'Vorgang anlegen')
        self.assertEqual(button.with_context(lang='en_US').name, 'Create opportunity')

    def test_titelvorlage_uebersetzt(self):
        button = self.env.ref('wb_voip_crm.wb_voip_aktion_vorgang')
        self.assertEqual(button.with_context(lang='de_DE').titel_vorlage, 'Anruf von {kontakt} ({datum})')
        self.assertEqual(button.with_context(lang='en_US').titel_vorlage, 'Call from {kontakt} ({datum})')
