from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        """Add ``wb_voip_aktiv`` (only then the browser starts the phone service) and
        ``wb_voip_manager`` (shows setup hints to VoIP administrators)."""
        info = super().session_info()
        info['wb_voip_aktiv'] = self.env.user._wb_voip_session_aktiv()
        info['wb_voip_manager'] = bool(
            info['wb_voip_aktiv'] and self.env.user.has_group('wb_voip.group_wb_voip_manager'))
        return info
