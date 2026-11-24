"""Group check for the public RPC methods of WB VoIP."""
import functools

from odoo.exceptions import AccessError

GRUPPE_BENUTZER = 'wb_voip.group_wb_voip_user'
GRUPPE_MANAGER = 'wb_voip.group_wb_voip_manager'


def ist_voip_benutzer(env):
    """Return True if the current user belongs to the WB VoIP user group."""
    return env.user.has_group(GRUPPE_BENUTZER)


def nur_voip_benutzer(methode):
    """Decorator: raise ``AccessError`` unless the caller is a WB VoIP user.

    Put it outside ``license_required`` so that the group is checked first.
    """
    @functools.wraps(methode)
    def huelle(self, *args, **kwargs):
        if not ist_voip_benutzer(self.env):
            raise AccessError(self.env._("Nur für Benutzer von WB VoIP."))
        return methode(self, *args, **kwargs)
    return huelle
