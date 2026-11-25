from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.wb_license_client.models.wb_license_client import license_required
from odoo.addons.wb_voip.models import PRODUKT_CODE
from odoo.addons.wb_voip.models.wb_voip_anruf import WbVoipAnruf as KernAnruf
from odoo.addons.wb_voip.tools.rechte import nur_voip_benutzer

LEAD_MODELL = 'crm.lead'


class WbVoipAnruf(models.Model):
    _inherit = 'wb.voip.anruf'

    # lead_id wird nur aus res_model/res_id berechnet und ist für Clients nie schreibbar.
    # Die Konstante bleibt für ältere Aufrufer; geprüft wird über ``_wb_voip_nur_server()``.
    _WB_VOIP_NUR_SERVER = KernAnruf._WB_VOIP_NUR_SERVER + ('lead_id',)

    @api.model
    def _wb_voip_nur_server(self):
        return super()._wb_voip_nur_server() + ('lead_id',)

    # Nur Systemverwaltung: Many2one-Anzeigenamen werden per sudo gelesen und würden den
    # Vorgangsnamen ohne Leserecht preisgeben; Benutzer nutzen res_model/res_id und das
    # Feld wb_voip_lead_lesbar_id.
    lead_id = fields.Many2one(
        'crm.lead', string='Vorgang', compute='_compute_lead_id', store=True, readonly=True,
        copy=False, index='btree_not_null', ondelete='set null',
        groups='base.group_system')

    wb_voip_lead_lesbar_id = fields.Many2one(
        'crm.lead', string='Vorgang', compute='_compute_wb_voip_lead_lesbar_id')

    @api.depends_context('uid')
    @api.depends('lead_id')
    def _compute_wb_voip_lead_lesbar_id(self):
        """Show the lead only if the current user may read it (display names bypass rules)."""
        lesbar = self.sudo().lead_id.with_user(self.env.user)._filtered_access('read')
        for anruf in self:
            anruf.wb_voip_lead_lesbar_id = anruf.sudo().lead_id if anruf.sudo().lead_id in lesbar \
                else False

    @api.depends('res_model', 'res_id')
    def _compute_lead_id(self):
        """Derive the lead from ``res_model``/``res_id`` (only if it still exists)."""
        ids = {a.res_id for a in self if a.res_model == LEAD_MODELL and a.res_id}
        vorhanden = set(self.env[LEAD_MODELL].sudo().with_context(active_test=False)
                        .browse(ids).exists().ids)
        for anruf in self:
            ist_lead = anruf.res_model == LEAD_MODELL and anruf.res_id in vorhanden
            anruf.lead_id = anruf.res_id if ist_lead else False

    @api.model
    def _wb_voip_zuordnung(self, partner, ziel):
        """Propose the open lead of the contact (or its company) for a new call.

        Searched as the caller, so the record rules of ``crm`` apply. Exactly one open
        lead wins; with several, the most recently changed one the caller is responsible
        for; otherwise none.
        """
        vorschlag = super()._wb_voip_zuordnung(partner, ziel)
        if vorschlag is not None or not partner:
            return vorschlag
        if ziel is not None and ziel._name == LEAD_MODELL:
            return None  # übergebener Vorgang hat Vorrang, keine Suche nötig
        Lead = self.env[LEAD_MODELL]
        if not Lead.has_access('read'):
            return None
        kontakte = partner | partner.commercial_partner_id
        domain = [
            ('active', '=', True),
            ('partner_id', 'in', kontakte.ids),
            ('probability', '<', 100),
            ('company_id', 'in', [self.env.company.id, False]),
        ]
        reihenfolge = 'write_date desc, id desc'
        # Zwei Treffer genügen, um „genau einer“ von „mehrere“ zu trennen
        leads = Lead.search(domain, order=reihenfolge, limit=2)
        if len(leads) == 1:
            return leads
        if not leads:
            return None
        eigene = Lead.search(domain + [('user_id', '=', self.env.user.id)],
                             order=reihenfolge, limit=1)
        return eigene or None

    @nur_voip_benutzer
    @license_required(PRODUKT_CODE)
    def wb_voip_vorgang_setzen(self, lead_id):
        """Assign the caller's own call (and its log) to a lead; ``False`` detaches it.

        The lead must be readable by the caller. Values are written with sudo only after
        the call was checked to be the caller's own; the log is written as the caller.
        """
        anruf = self._wb_voip_eigener()
        if lead_id is False:
            if anruf.res_model != LEAD_MODELL:
                return True
            # zurück auf den Kontakt des Anrufs, falls vorhanden
            partner = anruf.partner_id
            werte = {'res_model': 'res.partner' if partner else False,
                     'res_id': partner.id if partner else False}
        else:
            if type(lead_id) is not int or lead_id <= 0:
                raise UserError(self.env._("Ungültige Angabe: %s.", self.env._("Vorgang")))
            lead = self.env[LEAD_MODELL].with_user(self.env.user).browse(lead_id).exists()
            if not lead or not lead.has_access('read'):
                raise AccessError(self.env._("Sie dürfen diesen Vorgang nicht lesen."))
            werte = {'res_model': LEAD_MODELL, 'res_id': lead.id}
        anruf.write(werte)
        if anruf.protokoll_id:
            anruf.protokoll_id.with_user(self.env.user).write(werte)
        anruf._wb_voip_kontext_melden()
        return True

    def _wb_voip_rueckruf_ziel(self):
        """Return the lead of the call as target of the callback activity, else the contact.

        The lead counts only while it is active and not won at creation time; the read check
        for the call's user (and the fall back to the contact) is done by the core. A lead
        read here as sudo is only returned, never its data.
        """
        self.ensure_one()
        anruf = self.sudo()
        if anruf.res_model == LEAD_MODELL and anruf.res_id:
            lead = self.env[LEAD_MODELL].sudo().browse(anruf.res_id).exists()
            if lead and lead.active and lead.probability < 100:
                return lead
        return super()._wb_voip_rueckruf_ziel()
