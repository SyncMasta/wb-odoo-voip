from odoo import api, fields, models

from ..tools.eingaben import limit_pruefen, suchtext_pruefen
from ..tools.nummer import e164
from ..tools.rechte import nur_voip_benutzer


class ResPartner(models.Model):
    _inherit = 'res.partner'

    wb_voip_nummern_e164 = fields.Char(
        string='Rufnummern (E.164)', compute='_compute_wb_voip_nummern_e164',
        store=True, index=True, copy=False)

    wb_voip_protokoll_anzahl = fields.Integer(
        string='Anrufe', compute='_compute_wb_voip_protokoll_anzahl',
        groups='wb_voip.group_wb_voip_user')

    @api.depends_context('uid')
    def _compute_wb_voip_protokoll_anzahl(self):
        # Als aufrufender Benutzer gezählt: Datensatzregeln begrenzen auf eigene Protokolle
        gruppen = self.env['wb.voip.protokoll']._read_group(
            [('partner_id', 'in', self.ids)], ['partner_id'], ['__count'])
        anzahl = {partner.id: n for partner, n in gruppen}
        for partner in self:
            partner.wb_voip_protokoll_anzahl = anzahl.get(partner.id, 0)

    @nur_voip_benutzer
    def action_wb_voip_protokolle(self):
        """Open the call logs of this contact."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Anrufe"),
            'res_model': 'wb.voip.protokoll',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }

    @api.model
    @nur_voip_benutzer
    def wb_voip_kontakte_suchen(self, suchtext, limit=20):
        """Return up to ``limit`` readable contacts with a phone number for the softphone.

        Matches name, e-mail or number (any notation of the number). Read as the caller,
        record rules apply. Each entry: ``{id, name, nummer, firma}``.
        """
        suchtext_pruefen(self.env, suchtext)
        limit_pruefen(self.env, limit)
        domain = [('wb_voip_nummern_e164', '!=', False)]
        text = suchtext.strip()
        if text:
            bedingungen = [('complete_name', 'ilike', text), ('email', 'ilike', text)]
            ziffern = ''.join(z for z in text if z.isdigit())
            # „000“ o. Ä. würde nach dem Abschneiden der Nullen auf alle Nummern passen
            if len(ziffern.lstrip('0')) >= 3:
                bedingungen.append(('phone', 'ilike', text))
                # Nummer in anderer Schreibweise: über die normalisierte Form suchen
                normal = e164(text, self._wb_voip_standard_land())
                if normal:
                    bedingungen.append(('wb_voip_nummern_e164', 'ilike', normal))
                bedingungen.append(('wb_voip_nummern_e164', 'ilike', ziffern.lstrip('0')))
            domain += ['|'] * (len(bedingungen) - 1) + bedingungen
        partner = self.search(domain, limit=limit, order='complete_name, id')
        felder = self._wb_voip_nummernfelder()
        ergebnis = []
        for kontakt in partner:
            nummer = next((kontakt[feld] for feld in felder if kontakt[feld]), False)
            ergebnis.append({
                'id': kontakt.id,
                'name': kontakt.name or kontakt.display_name,
                'nummer': nummer,
                # gespeichertes Feld am Kontakt selbst: kein Lesen der (evtl. gesperrten) Firma
                'firma': (kontakt.commercial_company_name or False) if kontakt.parent_id else False,
            })
        return ergebnis

    @api.model
    def _wb_voip_standard_land(self):
        """Return the country code used for numbers without country: main company, else DE."""
        hauptfirma = self.env.ref('base.main_company', raise_if_not_found=False)
        return (hauptfirma and hauptfirma.sudo().country_id.code) or 'DE'

    @api.model
    def _wb_voip_nummernfelder(self):
        """Return the phone fields that feed the number lookup. Bridges extend via super()."""
        return ['phone']

    @api.depends(lambda self: self._wb_voip_nummernfelder() + ['country_id'])
    def _compute_wb_voip_nummern_e164(self):
        # Land: Kontakt, dann Firma des Kontakts, dann Hauptfirma, dann DE
        standard_land = self._wb_voip_standard_land()
        felder = self._wb_voip_nummernfelder()
        for partner in self:
            land = (partner.country_id.code
                    or partner.sudo().company_id.country_id.code
                    or standard_land)
            nummern = []
            for feld in felder:
                normal = e164(partner[feld], land)
                if normal and normal not in nummern:
                    nummern.append(normal)
            partner.wb_voip_nummern_e164 = ' '.join(nummern) or False

    @api.model
    def _wb_voip_finden(self, nummer_e164):
        """Return the one active contact owning this E.164 number, else an empty set.

        More than one match is ambiguous and yields no assignment.
        """
        if not nummer_e164:
            return self.browse()
        treffer = self.sudo().search(
            [('wb_voip_nummern_e164', 'ilike', nummer_e164)]
        ).filtered(lambda p: nummer_e164 in (p.wb_voip_nummern_e164 or '').split())
        return treffer if len(treffer) == 1 else self.browse()
