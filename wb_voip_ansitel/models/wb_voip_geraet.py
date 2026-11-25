from odoo import api, fields, models
from odoo.exceptions import ValidationError


class WbVoipGeraet(models.Model):
    _name = 'wb.voip.geraet'
    _description = 'WB VoIP: Gerät der Telefonanlage'
    _order = 'anlage_id, name, id'

    name = fields.Char(string='Bezeichnung', required=True)
    peer_name = fields.Char(
        string='Gerät (Peer-Name)', required=True,
        help="Name des Geräts in der Anlage, z. B. Tel51. Er steht im Zielkanal der Anrufliste.")
    nebenstelle = fields.Char(string='Nebenstelle', help="Interne Rufnummer laut Anlage.")
    anlage_id = fields.Many2one(
        'wb.voip.anlage', string='Telefonanlage', required=True, index=True, ondelete='cascade')
    user_id = fields.Many2one(
        'res.users', string='Benutzer', index='btree_not_null', ondelete='set null',
        help="Wem das Gerät gehört; leer bei Geräten ohne festen Besitzer (z. B. Empfang).")
    active = fields.Boolean(string='Aktiv', default=True)

    _peer_je_anlage = models.Constraint(
        'unique(anlage_id, peer_name)', 'Dieses Gerät gibt es an der Telefonanlage schon.')

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
                raise ValidationError(self.env._("Der Peer-Name des Geräts darf nicht leer sein."))

    @api.model
    def _fuer(self, anlage, peer_name):
        """Return (sudo) the active device of ``anlage`` with this peer name, else empty."""
        peer_name = (peer_name or '').strip()
        if not peer_name:
            return self.sudo().browse()
        return self.sudo().search(
            [('anlage_id', '=', anlage.id), ('peer_name', '=', peer_name)], limit=1)
