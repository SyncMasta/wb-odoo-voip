"""REST client for the Ansitel PBX.

``_aufruf`` is the only place that talks to the network. The token and the
URL stay on the server; error messages and logs never contain them.
"""
import functools
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


class AnsitelNichtErreichbar(AnsitelFehler):
    """PBX not reachable (network error, time-out, server error): further calls would only wait."""


def _robust(methode):
    """Turn unexpected answer shapes (wrong types, missing keys) into ``AnsitelFehler``."""
    @functools.wraps(methode)
    def huelle(self, *args, **kwargs):
        try:
            return methode(self, *args, **kwargs)
        except (KeyError, ValueError, TypeError, AttributeError):
            _logger.warning("[wb_voip_ansitel] %s: unerwartete Antwort", methode.__name__)
        # außerhalb des except-Blocks: keine verkettete Ausnahme mit Antwortinhalt
        raise AnsitelFehler(self.env._("Ansitel hat unverständlich geantwortet."))
    return huelle


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
        return self._entschluesseln(anlage.sudo().ansitel_token_enc)

    def _entschluesseln(self, chiffrat):
        """Return the plain text of a stored token ciphertext or ``None`` if empty."""
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
        nicht_erreichbar = self.env._("Ansitel nicht erreichbar.")
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
            fehler = nicht_erreichbar
        if fehler:
            raise (AnsitelNichtErreichbar if fehler == nicht_erreichbar else AnsitelFehler)(fehler)
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

    # --- Weiterleitung ---

    @_robust
    def _weiterleitung_lesen(self, anlage, peer_id):
        """Return the forwarding target of the peer as text, ``None`` if there is none."""
        daten = self._aufruf(anlage, 'getcallfordwardingonpeer', pk_peerId=peer_id).get('data')
        if isinstance(daten, dict):
            daten = daten.get('destination')
        if not isinstance(daten, (str, int, type(None))) or isinstance(daten, bool):
            raise TypeError('getcallfordwardingonpeer')
        ziel = str(daten).strip() if daten is not None else ''
        return ziel if ziel and ziel != '0' else None

    def _weiterleitung_setzen(self, anlage, peer_id, ziel):
        """Set the forwarding target (dialling form); empty or ``None`` switches forwarding off."""
        self._aufruf(anlage, 'setcallfordwardingonpeer', pk_peerId=peer_id, destination=(ziel or '0'))

    # --- Anrufschutz (Nicht stören) ---

    @_robust
    def _dnd_lesen(self, anlage, peer_id):
        """Return True if do-not-disturb is on for the peer."""
        daten = self._aufruf(anlage, 'getdndonpeer', pk_peerId=peer_id).get('data')
        if isinstance(daten, dict):
            daten = daten.get('dnd')
        if not isinstance(daten, (str, int, type(None))):
            raise TypeError('getdndonpeer')
        return daten is not None and str(daten) == '1'

    def _dnd_setzen(self, anlage, peer_id, an):
        """Switch do-not-disturb of the peer on or off."""
        self._aufruf(anlage, 'setdndonpeer', pk_peerId=peer_id, dnd='1' if an else '0')

    # --- Anrufbeantworter (hängt am Ansitel-Benutzer des Tokens) ---

    def _ab_token(self, benutzer):
        """Return the token for the answering machine of ``benutzer`` or ``None``.

        Order: 1. personal token; 2. central token if the administrator allowed it
        (``wb_voip_ansitel_ab_zentral``) and the extension belongs to the central Ansitel user;
        3. central token if its Ansitel user owns exactly one extension and that is the
        user's; otherwise ``None``. Only "fits / does not fit" is cached, never a token.
        """
        benutzer = benutzer.sudo()
        persoenlich = self._entschluesseln(benutzer.wb_voip_ansitel_token_enc)
        if persoenlich:
            return persoenlich
        anlage = benutzer.wb_voip_anlage_id
        nebenstelle = (benutzer.wb_voip_ansitel_nebenstelle or '').strip()
        if not (anlage and nebenstelle and anlage.ansitel_token_enc):
            return None
        genau_eine = not benutzer.wb_voip_ansitel_ab_zentral
        schluessel = (self.env.cr.dbname, 'ab', anlage.id, anlage.ansitel_api_url, _anlage_hash(anlage),
                      nebenstelle, genau_eine)
        treffer = _PEER_CACHE.get(schluessel)
        if treffer and time.monotonic() - treffer[0] < PEER_CACHE_SEKUNDEN:
            passt = treffer[1]
        else:
            passt = self._zentraler_token_passt(anlage, nebenstelle, genau_eine)
            _cache_setzen(schluessel, passt)
        return self._token_lesen(anlage) if passt else None

    @_robust
    def _zentraler_token_passt(self, anlage, nebenstelle, genau_eine=True):
        """Return True if the extension belongs to the Ansitel user of the central token.

        With ``genau_eine`` only if it is that user's only extension.
        """
        peer_id = self._peer_id(anlage, nebenstelle)
        info = self._aufruf(anlage, 'getinfooncuserbytoken').get('data') or {}
        if not isinstance(info, dict):
            raise TypeError('getinfooncuserbytoken')
        peers = info.get('peer') or {}
        if not isinstance(peers, dict):
            raise TypeError('getinfooncuserbytoken')
        if genau_eine and len(peers) != 1:
            return False
        return any(
            (peer_id is not None and str(peer.get('peerId')) == str(peer_id))
            or str(peer.get('peerName')) == nebenstelle
            for peer in peers.values())

    @_robust
    def _ab_lesen(self, anlage, token):
        """Return the voicemail settings (dict) of the Ansitel user of ``token``."""
        daten = self._aufruf(anlage, 'getvoicemailoncuserbytoken', token=token).get('data') or {}
        if not isinstance(daten, dict):
            raise TypeError('getvoicemailoncuserbytoken')
        return daten

    @_robust
    def _ab_setzen(self, anlage, token, an, modus='sofort', sekunden=20, daten=None):
        """Switch the answering machine on or off.

        Only the known fields (``active``, ``option_*``) of ``daten`` (else read fresh) go back,
        so the other options stay as they are and ids like ``id``/``user`` stay out.
        """
        quelle = daten if daten is not None else self._ab_lesen(anlage, token)
        werte = {k: v for k, v in quelle.items() if k == 'active' or str(k).startswith('option_')}
        werte['active'] = '1' if an else '0'
        if an:
            sofort = modus != 'nach_rufzeit'
            werte['option_immediately_check'] = '1' if sofort else '0'
            werte['option_ringtime_check'] = '0' if sofort else '1'
            if not sofort:
                werte['option_ringtime_seconds'] = str(int(sekunden))
        self._aufruf(anlage, 'setvoicemailoncuserbytoken', token=token, **werte)
