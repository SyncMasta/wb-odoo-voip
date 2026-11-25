import ast
import re

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path

MODUL = 'wb_voip_crm'
# Platzhalter: %s, %d, %(name)s, {name}
PLATZHALTER = re.compile(r'%\([^)]*\)[sd]|%[sd]|\{[^{}]*\}')


def _lese_po(pfad):
    """Return {msgid: msgstr} of a .po/.pot file (without the header entry)."""
    eintraege = {}
    feld = None
    aktuell = {'msgid': '', 'msgstr': ''}

    def abschliessen():
        if aktuell['msgid'] != '':
            eintraege[aktuell['msgid']] = aktuell['msgstr']

    with open(pfad, encoding='utf-8') as datei:
        for zeile in datei:
            zeile = zeile.rstrip('\n')
            if zeile.startswith('msgid '):
                abschliessen()
                aktuell = {'msgid': ast.literal_eval(zeile[6:]), 'msgstr': ''}
                feld = 'msgid'
            elif zeile.startswith('msgstr '):
                aktuell['msgstr'] = ast.literal_eval(zeile[7:])
                feld = 'msgstr'
            elif zeile.startswith('"') and feld:
                aktuell[feld] += ast.literal_eval(zeile)
        abschliessen()
    return eintraege


@tagged('wb_voip_crm', 'post_install', '-at_install')
class TestI18n(TransactionCase):
    """English translation (i18n/en.po) is complete, current and placeholder-safe."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pot = _lese_po(file_path(f'{MODUL}/i18n/{MODUL}.pot'))
        cls.en = _lese_po(file_path(f'{MODUL}/i18n/en.po'))

    def test_header_sprache_en(self):
        with open(file_path(f'{MODUL}/i18n/en.po'), encoding='utf-8') as datei:
            self.assertIn('"Language: en\\n"', datei.read())

    def test_vollstaendig(self):
        self.assertTrue(self.pot)
        self.assertEqual(sorted(set(self.pot) - set(self.en)), [])

    def test_keine_veralteten_eintraege(self):
        self.assertEqual(sorted(set(self.en) - set(self.pot)), [])

    def test_keine_leeren_uebersetzungen(self):
        leer = [msgid for msgid, msgstr in self.en.items() if not msgstr.strip()]
        self.assertEqual(leer, [])

    def test_platzhalter_identisch(self):
        falsch = [
            msgid for msgid, msgstr in self.en.items()
            if sorted(PLATZHALTER.findall(msgid)) != sorted(PLATZHALTER.findall(msgstr))
        ]
        self.assertEqual(falsch, [])
