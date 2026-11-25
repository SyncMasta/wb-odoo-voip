import os

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path

MODUL = 'wb_voip_ansitel'


@tagged('wb_voip', 'post_install', '-at_install')
class TestI18n(TransactionCase):
    """Source language is German: the template (.pot) ships, but no en.po (it would mix the UI)."""

    def test_vorlage_vorhanden(self):
        pfad = file_path(f'{MODUL}/i18n/{MODUL}.pot')
        with open(pfad, encoding='utf-8') as datei:
            self.assertIn('msgid "', datei.read())

    def test_keine_en_po(self):
        ordner = os.path.dirname(file_path(f'{MODUL}/i18n/{MODUL}.pot'))
        self.assertFalse(os.path.exists(os.path.join(ordner, 'en.po')))
