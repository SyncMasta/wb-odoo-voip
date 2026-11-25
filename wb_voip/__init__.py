import logging

from . import models
from . import tools
from . import wizards

_logger = logging.getLogger(__name__)


def _wb_voip_post_init(env):
    """Report this installation to wissen-beratung.de (lead registry).

    Best effort: a server outage must never abort the module install.
    """
    try:
        env['wb.license.client'].register_install(models.PRODUKT_CODE)
    except Exception as e:
        _logger.warning("[wb_voip] register_install failed: %s", e)
    env['wb.voip.layout']._wb_voip_standard_bloecke_sicherstellen()


def _wb_voip_uninstall(env):
    """Delete the server actions of the button wizard before the module data goes.

    Without the technical group (removed with the module data) Odoo would let anybody run
    them from the action menu. Deleted are all server actions that carry exactly that group
    or that a wizard-made button uses.
    """
    from .models.wb_voip_aktion import GRUPPE_BUTTON_AKTION, assistenten_aktion

    Server = env['ir.actions.server'].sudo().with_context(active_test=False)
    server = Server.browse()
    if env.ref(GRUPPE_BUTTON_AKTION, raise_if_not_found=False):
        server = Server.search([('group_ids', 'in', env.ref(GRUPPE_BUTTON_AKTION).ids)])
        server = server.filtered(assistenten_aktion)
    buttons = env['wb.voip.aktion'].sudo().with_context(active_test=False).search(
        [('wb_voip_assistent', '=', True)])
    server |= buttons.server_action_id
    anzahl = len(server)
    server.unlink()
    _logger.info("[wb_voip] Deinstallation: %s Serveraktionen des Button-Assistenten gelöscht", anzahl)
