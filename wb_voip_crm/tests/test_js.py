import logging
import re

from odoo.tests import HttpCase, tagged

# Mindestzahl der HOOT-Tests in @wb_voip_crm; schützt vor einem Lauf, der nichts findet
MIN_TESTS = 11


def _hoot_hash(text):
    """Return the HOOT id hash of a suite/test name (same as web/tests/test_js.py)."""
    wert = 0
    for zeichen in text:
        wert = (wert << 5) - wert + ord(zeichen)
        wert = wert & 0xFFFFFFFF
    return f'{wert:08x}'


def _hoot_fehler(message):
    # Wie Odoo: Meldungen einzelner HOOT-Tests stoppen den Lauf nicht sofort
    return '[HOOT]' not in message


class _Sammler(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.meldungen = []

    def emit(self, record):
        self.meldungen.append(record.getMessage())


@tagged('wb_voip_crm', 'post_install', '-at_install')
class TestJs(HttpCase):
    """Run the HOOT unit tests of wb_voip_crm (suite @wb_voip_crm) in Chrome."""

    def test_hoot_wb_voip_crm(self):
        self.assertEqual(_hoot_hash('@web/core'), 'e39ce9ba')
        sammler = _Sammler()
        logger = logging.getLogger(__name__)
        logger.addHandler(sammler)
        try:
            self.browser_js(
                f'/web/tests?headless&loglevel=2&preset=desktop&timeout=15000'
                f'&id={_hoot_hash("@wb_voip_crm")}',
                "", "", login='admin', timeout=600,
                success_signal="[HOOT] Test suite succeeded",
                error_checker=_hoot_fehler,
            )
        finally:
            logger.removeHandler(sammler)
        anzahl = [int(m.group(1)) for m in (
            re.search(r'\[HOOT\] Passed (\d+) tests', text) for text in sammler.meldungen) if m]
        self.assertTrue(anzahl, "HOOT hat keine Zusammenfassung gemeldet")
        self.assertGreaterEqual(anzahl[-1], MIN_TESTS)
