from odoo import api, fields, models
from odoo.exceptions import ValidationError


class WbVoipGeraet(models.Model):
    _name = 'wb.voip.geraet'
    _description = 'WB VoIP: PBX device'
    _order = 'anlage_id, name, id'

    name = fields.Char(string='Name', required=True)
    peer_name = fields.Char(
        string='Device (peer name)', required=True,
        help="Name of the device on the PBX, e.g. Tel51. It appears in the destination channel "
             "of the call list.")
    nebenstelle = fields.Char(string='Extension', help="Internal phone number according to the PBX.")
    anlage_id = fields.Many2one(
        'wb.voip.anlage', string='PBX', required=True, index=True, ondelete='cascade')
    user_id = fields.Many2one(
        'res.users', string='User', index='btree_not_null', ondelete='set null',
        help="Who owns the device; empty for devices without a fixed owner (e.g. reception).")
    active = fields.Boolean(string='Active', default=True)

    _peer_je_anlage = models.Constraint(
        'unique(anlage_id, peer_name)', 'This device already exists on the PBX.')

    @staticmethod
    def _bereinigen(vals):
        if isinstance(vals.get('peer_name'), str):
            vals['peer_name'] = vals['peer_name'].strip()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._bereinigen(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._bereinigen(vals)
        return super().write(vals)

    @api.constrains('peer_name')
    def _check_peer_name(self):
        for geraet in self:
            if not (geraet.peer_name or '').strip():
                raise ValidationError(self.env._("The peer name of the device must not be empty."))

    @api.model
    def _fuer(self, anlage, peer_name):
        """Return (sudo) the active device of ``anlage`` with this peer name, else empty."""
        peer_name = (peer_name or '').strip()
        if not peer_name:
            return self.sudo().browse()
        return self.sudo().search(
            [('anlage_id', '=', anlage.id), ('peer_name', '=', peer_name)], limit=1)
