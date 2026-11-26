"""Encryption of SIP passwords with Fernet.

The key comes only from the environment variable ``WB_VOIP_FERNET_KEY`` of the
Odoo process; it is never stored in the database, in system parameters, in
logs or in a cache.
"""
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

from odoo.exceptions import UserError

SCHLUESSEL_VARIABLE = 'WB_VOIP_FERNET_KEY'

_logger = logging.getLogger(__name__)


def _fernet():
    """Return a Fernet instance for the configured key, or None if it is missing/invalid."""
    schluessel = os.environ.get(SCHLUESSEL_VARIABLE)
    if not schluessel:
        return None
    try:
        return Fernet(schluessel.encode())
    except (ValueError, TypeError):
        # Nie den Wert ausgeben
        _logger.warning("[wb_voip] WB_VOIP_FERNET_KEY ist ungültig")
        return None


def schluessel_vorhanden():
    """Return True if a usable key is configured."""
    return _fernet() is not None


def schluessel_meldung(env):
    """Return the user message for a missing (or unusable) key."""
    return env._("WB VoIP: The key WB_VOIP_FERNET_KEY is missing on the server.")


def pruefen(env):
    """Return the Fernet instance or raise ``UserError`` if no usable key is configured."""
    fernet = _fernet()
    if fernet is None:
        raise UserError(schluessel_meldung(env))
    return fernet


def verschluesseln(env, klartext):
    """Return the Fernet token (str) for ``klartext``."""
    return pruefen(env).encrypt(klartext.encode()).decode()


def entschluesseln(env, chiffrat):
    """Return the plain text of ``chiffrat``; raise ``UserError`` if it cannot be read."""
    fernet = pruefen(env)
    try:
        return fernet.decrypt(chiffrat.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        # Schlüssel gewechselt oder Wert beschädigt: Inhalt nie ausgeben
        raise UserError(env._("SIP password unreadable, please enter it again.")) from None
