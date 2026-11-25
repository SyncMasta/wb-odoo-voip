from types import SimpleNamespace
from unittest.mock import patch

from odoo.addons.wb_voip.tests.common import WbVoipCase
from odoo.addons.wb_voip_ansitel.models import ansitel_api

# Erfundene Werte: kein echter Host, kein echter Token
URL = 'https://pbx.example.invalid/awi/api/rest'
TOKEN = 'geheimer-test-token-123'
PEERS = {'status': 200, 'data': [
    {'pk_peerId': 7, 'peerInternalCallerId': '100', 'peerName': 'Tel100'},
    {'pk_peerId': 8, 'peerInternalCallerId': '101', 'peerName': 'Tel101'},
]}


def antwort(daten=None, status_code=200, json_fehler=False):
    """Build a fake ``requests`` response."""
    def json():
        if json_fehler:
            raise ValueError('kein JSON')
        return daten
    return SimpleNamespace(status_code=status_code, json=json)


class AnsitelCase(WbVoipCase):
    """Base class: PBX with Ansitel access; ``requests.post`` is always faked."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.anlage.write({
            'ansitel_aktiv': True, 'ansitel_api_url': URL, 'ansitel_token': TOKEN})
        cls.api = cls.env['wb.voip.ansitel']

    def setUp(self):
        super().setUp()
        ansitel_api.cache_leeren()
        self.addCleanup(ansitel_api.cache_leeren)

    def _post(self, *antworten, **kwargs):
        """Patch ``requests.Session.post`` of the API module (never real network)."""
        if antworten:
            kwargs.setdefault('side_effect', list(antworten))
        alt = getattr(self, '_post_patcher', None)
        if alt:
            alt.stop()  # mehrfach im selben Test (Teiltests): erst den alten Fake lösen
        patcher = self._post_patcher = patch.object(ansitel_api.requests.Session, 'post', autospec=True, **kwargs)
        mock = patcher.start()
        self.addCleanup(lambda: patcher.stop() if patcher is self._post_patcher else None)
        return mock
