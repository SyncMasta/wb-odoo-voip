from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.tools.misc import format_amount

LEAD_MODELL = 'crm.lead'
ANZAHL_STANDARD = 5
ANZAHL_MAX = 20


class WbVoipLayoutBlock(models.Model):
    _inherit = 'wb.voip.layout.block'

    # Ohne dieses Modul gibt es die Blockart nicht mehr: ihre Blöcke werden mit gelöscht
    art = fields.Selection(
        selection_add=[('vorgaenge', 'Vorgänge')], ondelete={'vorgaenge': 'cascade'})


class WbVoipLayout(models.Model):
    _inherit = 'wb.voip.layout'

    @api.model
    def _wb_voip_erlaubte_arten(self):
        """Allow the block "Vorgänge" in the caller window (the log popup has no assign button)."""
        arten = super()._wb_voip_erlaubte_arten()
        arten['anrufer_fenster'] += ('vorgaenge',)
        return arten

    @api.model
    def _wb_voip_gezaehlte_arten(self):
        return super()._wb_voip_gezaehlte_arten() + ('vorgaenge',)

    @api.model
    def _wb_voip_block_daten_vorgaenge(self, anruf, block, partner):
        """Return the open leads of the contact (or its company) the caller may read.

        Runs as the caller (record rules of ``crm`` apply, never sudo). ``anruf`` is empty in
        the designer preview, so nothing is marked as assigned there. Each entry:
        ``{id, name, stufe, erwarteter_umsatz, zugeordnet}``; ``erwarteter_umsatz`` is False
        unless the caller may read the field and the value is not zero.
        """
        Lead = self.env[LEAD_MODELL]
        if not partner or self.env.su or not Lead.has_access('read'):
            return []
        anzahl = block.get('anzahl')
        if type(anzahl) is not int:
            anzahl = ANZAHL_STANDARD
        anzahl = min(max(anzahl, 1), ANZAHL_MAX)
        kontakte = partner | partner.commercial_partner_id
        leads = Lead.search([
            ('partner_id', 'in', kontakte.ids),
            ('probability', '<', 100),
            ('company_id', 'in', [self.env.company.id, False]),
        ], order='write_date desc, id desc', limit=anzahl)
        umsatz_lesbar = Lead._has_field_access(Lead._fields['expected_revenue'], 'read')
        anruf_sudo = anruf.sudo() if anruf else None
        ergebnis = []
        for lead in leads:
            zugeordnet = bool(
                anruf_sudo and anruf_sudo.res_model == LEAD_MODELL and anruf_sudo.res_id == lead.id)
            ergebnis.append({
                'id': lead.id,
                'name': lead.name or '',
                'stufe': self._wb_voip_stufe(lead),
                'erwarteter_umsatz': self._wb_voip_umsatz(lead) if umsatz_lesbar else False,
                'zugeordnet': zugeordnet,
            })
        return ergebnis

    @api.model
    def _wb_voip_stufe(self, lead):
        """Return the stage name of ``lead`` (empty if the caller may not read the stage)."""
        try:
            return lead.stage_id.name or ''
        except AccessError:
            return ''

    @api.model
    def _wb_voip_umsatz(self, lead):
        """Return the expected revenue of ``lead`` formatted with its currency (False if zero)."""
        try:
            wert = lead.expected_revenue
            if not wert:
                return False
            waehrung = lead.company_currency
            return format_amount(self.env, wert, waehrung) if waehrung else str(wert)
        except AccessError:
            return False
