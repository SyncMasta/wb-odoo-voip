import os
import re

import polib

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path

MODUL = 'wb_voip_crm'
PLATZHALTER = re.compile(r'%\([a-z_]+\)[sd]|%[sd]|\{[a-z_0-9]+\}')
# Begriffe, die auf Deutsch gleich lauten, und technische Werte (Feldname)
GLEICH_IN_DE = {'ID', 'Lead', 'res_id', 'res_model'}


def _platzhalter(text):
    return sorted(PLATZHALTER.findall(text))


@tagged('wb_voip_crm', 'post_install', '-at_install')
class TestI18n(TransactionCase):
    """Source language is English; German ships as de.po with identical placeholders."""

    def _pot(self):
        return polib.pofile(file_path(f'{MODUL}/i18n/{MODUL}.pot'))

    def _de(self):
        return polib.pofile(file_path(f'{MODUL}/i18n/de.po'))

    def test_vorlage_vorhanden(self):
        self.assertTrue(len(self._pot()))

    def test_keine_en_po(self):
        ordner = os.path.dirname(file_path(f'{MODUL}/i18n/{MODUL}.pot'))
        self.assertFalse(os.path.exists(os.path.join(ordner, 'en.po')))

    def test_de_po_platzhalter_gleich(self):
        for eintrag in self._de():
            with self.subTest(msgid=eintrag.msgid):
                self.assertTrue(eintrag.msgstr)
                self.assertEqual(_platzhalter(eintrag.msgid), _platzhalter(eintrag.msgstr))

    def test_de_po_vollstaendig(self):
        """Every msgid of the template (Python, XML, JS and QWeb) has a German translation."""
        deutsch = {eintrag.msgid: eintrag.msgstr for eintrag in self._de()}
        for eintrag in self._pot():
            with self.subTest(msgid=eintrag.msgid):
                self.assertTrue(deutsch.get(eintrag.msgid))

    def test_de_po_ohne_englische_reste(self):
        """German differs from the English source, except for terms that are the same in both."""
        for eintrag in self._de():
            if not re.search(r'[A-Za-z]', eintrag.msgid) or eintrag.msgid in GLEICH_IN_DE:
                continue
            with self.subTest(msgid=eintrag.msgid):
                self.assertNotEqual(eintrag.msgstr, eintrag.msgid)
