import re
import unicodedata

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import clean_context
from odoo.tools.misc import format_date

LEAD_MODELL = 'crm.lead'
TITEL_MAX = 200
# Only these three placeholders exist; they are replaced in one pass, as plain text
PLATZHALTER = ('kontakt', 'nummer', 'datum')
PLATZHALTER_MUSTER = re.compile(r'\{(%s)\}' % '|'.join(PLATZHALTER))
STEUERZEICHEN_MUSTER = re.compile(r'[\x00-\x1f\x7f-\x9f]')


class WbVoipAktion(models.Model):
    _inherit = 'wb.voip.aktion'

    # Ohne dieses Modul sind solche Buttons sinnlos (kein Vorgang): sie werden mit gelöscht,
    # statt auf eine Art ohne Ziel zurückzufallen.
    art = fields.Selection(
        selection_add=[('vorgang', 'Create opportunity')], ondelete={'vorgang': 'cascade'})
    crm_team_id = fields.Many2one(
        'crm.team', string='Sales team', ondelete='set null',
        groups='wb_voip.group_wb_voip_manager',
        help="Team of the new opportunity. Empty = the user's default team.")
    titel_vorlage = fields.Char(
        string='Opportunity title', translate=True, groups='wb_voip.group_wb_voip_manager',
        help='Plain text, at most 200 characters. Placeholders: {kontakt}, {nummer}, {datum}. '
             'Empty = "Call from {kontakt}".')

    @api.constrains('titel_vorlage')
    def _check_titel_vorlage(self):
        for aktion in self:
            if aktion.titel_vorlage and len(aktion.titel_vorlage) > TITEL_MAX:
                raise ValidationError(self.env._(
                    "The title may be at most %(max)s characters long.", max=TITEL_MAX))

    def _wb_voip_titel(self, anruf, partner):
        """Return the lead title: the template with its three placeholders replaced.

        Plain text only: one ``re.sub`` pass over ``{kontakt}``, ``{nummer}`` and ``{datum}``
        (a replaced value is never scanned again), no ``format``, no evaluation, no markup.
        Control characters and line breaks become blanks, invisible format characters
        (category Cf, e.g. zero-width, bidi) are dropped, the result has at most 200 characters.
        """
        self.ensure_one()
        vorlage = self.sudo().titel_vorlage or self.env._("Call from {kontakt}")
        nummer = anruf.sudo().nummer or ''
        werte = {
            'kontakt': (partner.display_name if partner else '') or nummer,
            'nummer': nummer,
            'datum': format_date(self.env, fields.Date.context_today(self)),
        }
        titel = PLATZHALTER_MUSTER.sub(lambda treffer: werte[treffer.group(1)], vorlage)
        # unsichtbare Formatzeichen (Kategorie Cf: Zero-Width, Bidi) entfallen ganz
        titel = ''.join(z for z in titel if unicodedata.category(z) != 'Cf')
        titel = ' '.join(STEUERZEICHEN_MUSTER.sub(' ', titel).split())
        return titel[:TITEL_MAX] or self.env._("Call")

    def _wb_voip_aktion_vorgang(self, anruf, partner):
        """Create a lead for the call as the caller and open its form.

        Created with the caller's own rights (no sudo): without the right to create leads
        ``AccessError`` is raised. Contact, team and salesperson (the caller) are set; the
        call and its log then point to the new lead.
        """
        self.ensure_one()
        Lead = self.env[LEAD_MODELL]
        Lead.check_access('create')
        werte = {
            'name': self._wb_voip_titel(anruf, partner),
            'type': 'opportunity',
            'user_id': self.env.user.id,
        }
        if partner:
            werte['partner_id'] = partner.id
        else:
            werte['phone'] = anruf.sudo().nummer or False
        team = self.sudo().crm_team_id
        if team:
            # nur ein Team, das der Benutzer lesen darf: nie still ein anderes nehmen
            if not team.with_user(self.env.user).has_access('read'):
                raise AccessError(self.env._("You are not allowed to read the sales team of this button."))
            if team.company_id and team.company_id != self.env.company:
                raise UserError(self.env._("The team belongs to a different company."))
            werte['team_id'] = team.id
        # ohne default_* aus dem Client-Kontext (RPC): nur die Werte oben gelten
        lead = Lead.with_context(clean_context(self.env.context)).create(werte)
        anruf.wb_voip_vorgang_setzen(lead.id)
        return {
            'type': 'ir.actions.act_window',
            'name': lead.display_name,
            'res_model': LEAD_MODELL,
            'res_id': lead.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }
