import logging
import re

from odoo.tests import HttpCase, tagged

# Mindestzahl der HOOT-Tests in @wb_voip; schützt vor einem Lauf, der nichts findet
MIN_TESTS = 226


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


@tagged('wb_voip', 'post_install', '-at_install')
class TestJs(HttpCase):
    """Run the HOOT unit tests of wb_voip (suite @wb_voip) in Chrome."""

    def test_hoot_wb_voip(self):
        self.assertEqual(_hoot_hash('@web/core'), 'e39ce9ba')
        sammler = _Sammler()
        logger = logging.getLogger(__name__)
        logger.addHandler(sammler)
        try:
            self.browser_js(
                f'/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&id={_hoot_hash("@wb_voip")}',
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

    def test_sip_buendel_lazy(self):
        """SIP.js lives only in wb_voip.sip_lib and defines the SIP global when loaded."""
        dateien = lambda buendel: [  # noqa: E731
            a['filename'] or a['url'] for a in self.env['ir.qweb']._get_asset_content(buendel)[0]]
        self.assertTrue(any('sip-0.21.2.min.js' in d for d in dateien('wb_voip.sip_lib')))
        self.assertFalse(any('sip-0.21.2.min.js' in d for d in dateien('web.assets_backend')))
        self.assertIn('wb_voip/static/src/dienste/telefon_dienst.js',
                      ' '.join(dateien('web.assets_backend')))
        code = """
            const { loadBundle } = odoo.loader.modules.get("@web/core/assets");
            if (window.SIP) {
                console.error("SIP darf vor loadBundle nicht geladen sein");
            }
            loadBundle("wb_voip.sip_lib", { css: false }).then(() => {
                const SIP = window.SIP;
                if (SIP && SIP.UserAgent && SIP.Registerer && SIP.Inviter && SIP.Web
                        && SIP.Web.holdModifier) {
                    console.log("test successful");
                } else {
                    console.error("SIP.js nach loadBundle unvollständig");
                }
            }).catch((e) => console.error("loadBundle fehlgeschlagen: " + e));
        """
        self.browser_js('/odoo', code, "odoo.isReady",
                        login='admin', timeout=120)
