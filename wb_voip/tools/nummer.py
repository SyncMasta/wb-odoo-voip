import re

from odoo.exceptions import UserError

try:
    import phonenumbers
except ImportError:  # pragma: no cover - fail closed in e164()
    phonenumbers = None


def e164(nummer, land_code='DE'):
    """Return the E.164 form of a phone number, or ``False`` if it is not valid.

    ``land_code`` (ISO-2) is used for numbers without a country prefix.
    """
    if phonenumbers is None:
        # Without phonenumbers Odoo's phone_format returns the raw input unchecked,
        # so no number may count as valid (toll-fraud validator relies on this).
        return False
    roh = (nummer or '').strip()
    if not re.search(r'\d', roh):
        return False
    from odoo.addons.phone_validation.tools.phone_validation import phone_format
    try:
        ergebnis = phone_format(
            roh, (land_code or 'DE').upper(), None, force_format='E164', raise_exception=True)
    except UserError:
        return False
    return ergebnis or False
