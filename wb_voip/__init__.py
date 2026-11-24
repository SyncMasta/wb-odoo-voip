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
