"""REST client for the Ansitel PBX.

``_aufruf`` is the only place that talks to the network. The token and the
URL stay on the server; error messages and logs never contain them.
"""
import hashlib
import logging
import time

import requests
from cryptography.fernet import InvalidToken

from odoo import models
from odoo.exceptions import UserError

from odoo.addons.wb_voip.tools import geheimnis

from ..tools.adresse import url_ist_https

_logger = logging.getLogger(__name__)

TIMEOUT = 10  # seconds
PEER_CACHE_SEKUNDEN = 600
# Per worker process. Every key carries a hash of the encrypted token and the
# write date of the PBX, so a token change invalidates entries in all workers.
# Neither keys nor values ever hold the token.
# (database, PBX id, URL, hash, extension) -> (monotonic time, peer id or None)
_PEER_CACHE = {}


class AnsitelFehler(UserError):
    """PBX not reachable or refuses; the message is safe to show (no token, no URL)."""


def cache_leeren():
    """Drop all cached peer ids of this worker."""
    _PEER_CACHE.clear()


def _cache_setzen(schluessel, wert):
    """Store an entry and drop expired ones (the cache would grow without bound)."""
    jetzt = time.monotonic()
    for alt in [k for k, (zeit, _wert) in _PEER_CACHE.items() if jetzt - zeit >= PEER_CACHE_SEKUNDEN]:
        del _PEER_CACHE[alt]
    _PEER_CACHE[schluessel] = (jetzt, wert)


def _anlage_hash(anlage):
    """Short hash of the token ciphertext and the write date (never the plain token)."""
    anlage = anlage.sudo()
    roh = f'{anlage.ansitel_token_enc or ""}|{anlage.write_date or ""}'
    return hashlib.sha256(roh.encode()).hexdigest()[:16]


class WbVoipAnsitel(models.AbstractModel):
    _name = 'wb.voip.ansitel'
    _description = 'Ansitel-REST-Client'

    def _token_lesen(self, anlage):
        """Return the central token of ``anlage`` in plain text or ``None`` if none is stored."""
        chiffrat = anlage.sudo().ansitel_token_enc
        if not chiffrat:
            return None
        fernet = geheimnis.pruefen(self.env)
        try:
            return fernet.decrypt(chiffrat.encode()).decode()
        except (InvalidToken, ValueError, TypeError):
            pass  # Inhalt nie ausgeben
        raise AnsitelFehler(self.env._("Ansitel-Token nicht lesbar, bitte neu eingeben."))

    def _aufruf(self, anlage, aktion, token=None, **werte):
        """POST ``{token, action, ...}`` to the REST URL of ``anlage``; return the JSON answer (dict).

        ``token`` overrides the central token (personal token). Raises ``AnsitelFehler``
        for every failure; the message contains neither token nor URL.
        """
        anlage = anlage.sudo()
        url = (anlage.ansitel_api_url or '').strip()
        if not (anlage.ansitel_aktiv and url):
            raise AnsitelFehler(self.env._("Ansitel ist nicht eingerichtet."))
        # Nur https: auch Bestandsdaten, die am Feld vorbei geschrieben wurden
        if not url_ist_https(url):
            raise AnsitelFehler(self.env._("Ansitel ist nicht eingerichtet."))
        token = token or self._token_lesen(anlage)
        if not token:
            raise AnsitelFehler(self.env._("Ansitel ist nicht eingerichtet."))

        nutzdaten = {**werte, 'token': token, 'action': aktion}
        fehler = None
        daten = None
        try:
            # Keine Umgebungs-Proxys, -CA-Pakete oder .netrc; Zertifikat wird geprüft; keine Weiterleitung,
            # sonst ginge der Token an eine fremde Adresse
            with requests.Session() as sitzung:
                sitzung.trust_env = False
                antwort = sitzung.post(
                    url, json=nutzdaten, timeout=TIMEOUT, allow_redirects=False, verify=True)
            fehler, daten = self._antwort_pruefen(aktion, antwort)
        except (requests.RequestException, ValueError, UnicodeError) as exc:
            # Nur der Typ ins Log: Text und URL der Ausnahme können Token/Query enthalten
            _logger.warning("[wb_voip_ansitel] %s: %s", aktion, type(exc).__name__)
            fehler = self.env._("Ansitel nicht erreichbar.")
        if fehler:
            raise AnsitelFehler(fehler)
        return daten

    def _antwort_pruefen(self, aktion, antwort):
        """Return ``(fehlertext, daten)``: the user message if ``antwort`` is unusable, else the parsed dict."""
        code = antwort.status_code
        if code != 200:
            _logger.warning("[wb_voip_ansitel] %s: HTTP %s", aktion, code)
            if code in (401, 403):
                return self.env._("Ansitel lehnt den Token ab."), None
            if code == 400:
                return self.env._("Ansitel lehnt die Anfrage ab."), None
            return self.env._("Ansitel nicht erreichbar."), None
        try:
            daten = antwort.json()
        except ValueError:
            daten = None
        if not isinstance(daten, dict):
            _logger.warning("[wb_voip_ansitel] %s: Antwort unverständlich", aktion)
            return self.env._("Ansitel hat unverständlich geantwortet."), None
        # Fehlt `status` im Body, gilt allein der HTTP-Status
        status = daten.get('status')
        if status is not None and str(status) != '200':
            _logger.warning("[wb_voip_ansitel] %s: Status %s im Antworttext", aktion, str(status)[:20])
            return self.env._("Ansitel lehnt die Anfrage ab."), None
        return None, daten

    def _peer_id(self, anlage, nebenstelle):
        """Return the peer id of an extension (number or name) or ``None``; cached per worker."""
        nebenstelle = (nebenstelle or '').strip()
        if not nebenstelle:
            return None
        anlage = anlage.sudo()
        schluessel = (self.env.cr.dbname, anlage.id, anlage.ansitel_api_url, _anlage_hash(anlage), nebenstelle)
        treffer = _PEER_CACHE.get(schluessel)
        if treffer and time.monotonic() - treffer[0] < PEER_CACHE_SEKUNDEN:
            return treffer[1]
        try:
            liste = self._aufruf(anlage, 'getpeer').get('data') or []
            if not isinstance(liste, list):
                raise TypeError('getpeer')
            peer_id = None
            for peer in liste:
                if nebenstelle in (str(peer.get('peerInternalCallerId') or ''), str(peer.get('peerName') or '')):
                    peer_id = int(peer['pk_peerId'])
                    break
        except (KeyError, ValueError, TypeError, AttributeError):
            _logger.warning("[wb_voip_ansitel] getpeer: unerwartete Antwort")
            raise AnsitelFehler(self.env._("Ansitel hat unverständlich geantwortet.")) from None
        _cache_setzen(schluessel, peer_id)
        return peer_id
