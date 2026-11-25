from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def session_info(self):
        """Add ``wb_voip_ansitel_tastenleiste`` (placement of the quick-key bar) for phone users."""
        info = super().session_info()
        info['wb_voip_ansitel_tastenleiste'] = self.env.user._wb_voip_ansitel_session_wert()
        return info
