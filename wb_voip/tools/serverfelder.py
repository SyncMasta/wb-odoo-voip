"""Shared guard for server-only fields (used by wb.voip.anruf and wb.voip.protokoll)."""
from odoo.exceptions import UserError


def gesperrte_felder(vals, nur_server):
    """Return the sorted server-only keys contained in ``vals``."""
    return sorted(set(vals) & set(nur_server))


def pruefen(env, vals, nur_server):
    """Raise if a non-server caller writes server-only fields."""
    gesperrt = gesperrte_felder(vals, nur_server)
    if gesperrt:
        raise UserError(env._("Only the server sets these fields: %s", ', '.join(gesperrt)))


def kontext_ohne_defaults(env, nur_server):
    """Return the context without ``default_<server field>`` keys.

    Clients send arbitrary context by RPC; such defaults would be applied inside
    ``create`` after the key check and bypass it.
    """
    gesperrt = set(nur_server)
    return {k: v for k, v in env.context.items()
            if not (k.startswith('default_') and k[8:] in gesperrt)}
