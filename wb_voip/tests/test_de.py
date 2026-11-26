from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..tools import eingaben


@tagged('wb_voip', 'post_install', '-at_install')
class TestDeutsch(TransactionCase):
    """German comes from i18n/de.po at runtime (source language is English)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('de_DE')
        cls.env['ir.module.module']._load_module_terms(['wb_voip'], ['de_DE'], overwrite=True)
        cls.de = cls.env(context=dict(cls.env.context, lang='de_DE'))

    def test_feldbezeichnung(self):
        felder = self.de['wb.voip.protokoll'].fields_get(['ergebnis', 'beschreibung'])
        self.assertEqual(felder['ergebnis']['string'], 'Ergebnis')
        self.assertEqual(felder['beschreibung']['string'], 'Beschreibung')
        self.assertEqual(self.env['wb.voip.protokoll'].fields_get(['ergebnis'])['ergebnis']['string'],
                         'Result')

    def test_auswahl(self):
        ergebnis = dict(self.de['wb.voip.protokoll'].fields_get(['ergebnis'])['ergebnis']['selection'])
        self.assertEqual(ergebnis['mailbox'], 'Mailbox')
        self.assertEqual(ergebnis['nicht_erreicht'], 'Nicht erreicht')
        zustand = dict(self.de['wb.voip.anruf'].fields_get(['zustand'])['zustand']['selection'])
        self.assertEqual(zustand['verpasst'], 'Verpasst')

    def test_fehlermeldung(self):
        with self.assertRaises(UserError) as fehler:
            eingaben.limit_pruefen(self.de, 0)
        self.assertEqual(str(fehler.exception), 'Ungültige Angabe: Anzahl.')
        with self.assertRaises(UserError) as fehler:
            eingaben.limit_pruefen(self.env, 0)
        self.assertEqual(str(fehler.exception), 'Invalid value: Count.')

    def test_ansicht_menue_gruppe(self):
        ansicht = self.env.ref('wb_voip.wb_voip_res_users_view_form').with_context(lang='de_DE')
        self.assertIn('string="Telefonie"', ansicht.arch_db)
        self.assertIn('string="Telephony"', ansicht.with_context(lang='en_US').arch_db)
        self.assertEqual(self.env.ref('wb_voip.wb_voip_menu_anlage').with_context(lang='de_DE').name,
                         'Telefonanlagen')
        self.assertEqual(self.env.ref('wb_voip.group_wb_voip_user').with_context(lang='de_DE').name,
                         'WB VoIP: Benutzer')

    def test_auslieferungsdaten(self):
        typ = self.env.ref('wb_voip.wb_voip_activity_type_rueckruf')
        self.assertEqual(typ.with_context(lang='de_DE').name, 'Rückruf')
        self.assertEqual(typ.with_context(lang='en_US').name, 'Callback')
        button = self.env.ref('wb_voip.wb_voip_aktion_neuer_kontakt')
        self.assertEqual(button.with_context(lang='de_DE').name, 'Neuer Kontakt')
