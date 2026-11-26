"""Input checks shared by the list RPC methods of the softphone."""
from odoo.exceptions import UserError

MAX_LIMIT = 100
MAX_SUCHTEXT = 100


def limit_pruefen(env, limit):
    """Raise ``UserError`` unless ``limit`` is an int from 1 to ``MAX_LIMIT``."""
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise UserError(env._("Invalid value: %s.", env._("Count")))


def suchtext_pruefen(env, suchtext):
    """Raise ``UserError`` unless ``suchtext`` is a str of at most ``MAX_SUCHTEXT`` characters."""
    if not isinstance(suchtext, str) or len(suchtext) > MAX_SUCHTEXT:
        raise UserError(env._("Invalid value: %s.", env._("Search text")))
