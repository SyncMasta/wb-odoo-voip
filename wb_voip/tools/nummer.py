import re

from odoo.exceptions import UserError


def e164(nummer, land_code='DE'):
    """Return the E.164 form of a phone number, or ``False`` if it is not valid.

    ``land_code`` (ISO-2) is used for numbers without a country prefix.
    """
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
