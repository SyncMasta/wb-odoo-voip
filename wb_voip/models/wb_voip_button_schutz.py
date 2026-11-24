"""Second line of defence for the server actions of the button wizard.

The technical group keeps them from running anywhere else. Should somebody still end up
in that group (user form, implied groups, SQL), Odoo would run ``object_write`` with
superuser rights; therefore ``run()`` refuses these actions for everyone (WB VoIP itself
never calls ``run()`` for them), and the group cannot get members.
"""
from odoo import api, models
from odoo.exceptions import AccessError, ValidationError

from .wb_voip_aktion import GRUPPE_BUTTON_AKTION, assistenten_aktion


def _verweigern(env):
    raise AccessError(env._(
        "Diese Serveraktion gehört zu einem Anruf-Button von WB VoIP und läuft nur über den Button."))


class IrActionsServer(models.Model):
    _inherit = 'ir.actions.server'

    def run(self):
        for action in self:
            if assistenten_aktion(action):
                _verweigern(self.env)
        return super().run()

    def _can_execute_action_on_records(self, records):
        if assistenten_aktion(self):
            _verweigern(self.env)
        return super()._can_execute_action_on_records(records)


def _technische_gruppe_pruefen(env):
    """Raise ``ValidationError`` if the technical group has members or implying groups."""
    gruppe = env.ref(GRUPPE_BUTTON_AKTION, raise_if_not_found=False)
    if not gruppe:
        return
    gruppe = gruppe.sudo()
    if gruppe.implied_by_ids or gruppe.all_user_ids:
        raise ValidationError(env._(
            "Die Gruppe „%s“ ist technisch und darf weder Benutzer noch übergeordnete Gruppen "
            "haben.", gruppe.name))


class ResGroups(models.Model):
    _inherit = 'res.groups'

    @api.constrains('user_ids', 'implied_ids', 'implied_by_ids')
    def _check_wb_voip_button_gruppe(self):
        _technische_gruppe_pruefen(self.env)


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.constrains('group_ids')
    def _check_wb_voip_button_gruppe(self):
        gruppe = self.env.ref(GRUPPE_BUTTON_AKTION, raise_if_not_found=False)
        if gruppe and any(gruppe in benutzer.sudo().all_group_ids for benutzer in self):
            _technische_gruppe_pruefen(self.env)
            raise ValidationError(self.env._(
                "Die Gruppe „%s“ ist technisch und darf keinem Benutzer zugewiesen werden.",
                gruppe.name))
