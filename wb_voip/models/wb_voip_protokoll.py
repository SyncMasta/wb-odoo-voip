from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..tools import serverfelder
from ..tools.rechte import nur_voip_benutzer

# So viele frühere Protokolle desselben Kontakts zeigt das Popup nach dem Anruf
LETZTE_PROTOKOLLE = 5


class WbVoipProtokoll(models.Model):
    _name = 'wb.voip.protokoll'
    _description = 'WB VoIP: Anrufprotokoll'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'datum desc, id desc'
    _rec_name = 'anruf_id'

    # Der Server setzt diese Felder beim Beenden eines Anrufs; Benutzer ändern
    # nur Ergebnis und Beschreibung (Popup nach dem Anruf).
    _WB_VOIP_NUR_SERVER = ('anruf_id', 'user_id', 'richtung', 'dauer_sek', 'datum')

    datum = fields.Datetime(string='Datum', default=fields.Datetime.now, index=True)
    user_id = fields.Many2one(
        'res.users', string='Benutzer', required=True, index=True,
        default=lambda self: self.env.user, ondelete='cascade')
    partner_id = fields.Many2one(
        'res.partner', string='Kontakt', index=True, ondelete='set null', tracking=True)
    res_model = fields.Char(string='Datensatz-Modell')
    res_id = fields.Many2oneReference(string='Datensatz', model_field='res_model')
    richtung = fields.Selection(
        [('eingehend', 'Eingehend'), ('ausgehend', 'Ausgehend')], string='Richtung')
    ergebnis = fields.Selection(
        [('erreicht', 'Erreicht'), ('nicht_erreicht', 'Nicht erreicht'),
         ('mailbox', 'Mailbox'), ('besetzt', 'Besetzt')],
        string='Ergebnis', default='erreicht', tracking=True)
    dauer_sek = fields.Integer(string='Dauer (s)')
    beschreibung = fields.Html(string='Beschreibung')
    anruf_id = fields.Many2one(
        'wb.voip.anruf', string='Anruf', index=True, ondelete='set null', copy=False)

    # Rechte Spalte des Popups: Kontaktdaten und frühere Protokolle, nur Lesbares
    wb_voip_partner_telefon = fields.Char(
        string='Telefon', compute='_compute_wb_voip_partner_daten')
    wb_voip_partner_email = fields.Char(
        string='E-Mail', compute='_compute_wb_voip_partner_daten')
    wb_voip_letzte_protokoll_ids = fields.Many2many(
        'wb.voip.protokoll', string='Letzte Anrufe', compute='_compute_wb_voip_letzte_protokolle')

    _anruf_eindeutig = models.Constraint(
        'unique(anruf_id)', 'Zu diesem Anruf gibt es schon ein Protokoll.')

    @api.depends('partner_id')
    def _compute_wb_voip_partner_daten(self):
        # Als aufrufender Benutzer (kein sudo): Kontakte ohne Leserecht bleiben leer
        for protokoll in self:
            partner = protokoll.partner_id
            lesbar = bool(partner) and partner.has_access('read')
            protokoll.wb_voip_partner_telefon = partner.phone if lesbar else False
            protokoll.wb_voip_partner_email = partner.email if lesbar else False

    @api.depends('partner_id')
    def _compute_wb_voip_letzte_protokolle(self):
        # Als aufrufender Benutzer: Datensatzregeln begrenzen auf eigene Protokolle
        for protokoll in self:
            if not protokoll.partner_id:
                protokoll.wb_voip_letzte_protokoll_ids = False
                continue
            protokoll.wb_voip_letzte_protokoll_ids = self.search([
                ('partner_id', '=', protokoll.partner_id.id),
                ('id', '!=', protokoll._origin.id),
            ], limit=LETZTE_PROTOKOLLE)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                serverfelder.pruefen(self.env, vals, self._WB_VOIP_NUR_SERVER)
            self = self.with_context(
                serverfelder.kontext_ohne_defaults(self.env, self._WB_VOIP_NUR_SERVER))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            serverfelder.pruefen(self.env, vals, self._WB_VOIP_NUR_SERVER)
        return super().write(vals)

    def _wb_voip_text(self):
        """Return the chatter text, e.g. ``Anruf eingehend · erreicht · 1:05``.

        Uses the (translated) labels of the selection fields, not their keys.
        """
        self.ensure_one()
        richtungen = dict(self._fields['richtung']._description_selection(self.env))
        ergebnisse = dict(self._fields['ergebnis']._description_selection(self.env))
        sek = self.dauer_sek or 0
        return self.env._(
            'Anruf %(richtung)s · %(ergebnis)s · %(dauer)s',
            richtung=(richtungen.get(self.richtung) or '').lower(),
            ergebnis=(ergebnisse.get(self.ergebnis) or '').lower(),
            dauer='%d:%02d' % (sek // 60, sek % 60),
        )

    @nur_voip_benutzer
    def wb_voip_popup_aktionen(self):
        """Return the call buttons for the popup of this log entry (client data).

        Only the caller's own entry; the buttons are those shown in the popup that
        apply to the finished call. Running one goes through
        ``wb.voip.anruf.wb_voip_aktion_ausfuehren`` on the linked call.
        """
        self.ensure_one()
        self.check_access('read')
        if self.user_id != self.env.user:
            raise AccessError(self.env._("Dies ist nicht Ihr Anrufprotokoll."))
        anruf = self.sudo().anruf_id
        if not anruf:
            return []
        return anruf.with_user(self.env.user)._wb_voip_aktionen(ort='protokoll_popup')

    @nur_voip_benutzer
    def wb_voip_popup_layout(self):
        """Return the right column of the popup for this log entry (client data).

        ``{layout, kontakt, protokolle}`` for the place ``protokoll_popup``: the caller's
        popup layout with formatted field values of the contact, the contact card and the
        caller's earlier logs of the same contact (without this one). Only the caller's own
        entry; everything is read as the caller, nothing about an unreadable contact.
        """
        self.ensure_one()
        self.check_access('read')
        if self.user_id != self.env.user:
            raise AccessError(self.env._("Dies ist nicht Ihr Anrufprotokoll."))
        partner = self.partner_id
        if partner and not partner.has_access('read'):
            partner = partner.browse()
        layout = self.env.user._wb_voip_layout('protokoll_popup')
        Anruf = self.env['wb.voip.anruf']
        return {
            'layout': layout._wb_voip_fuer_client(partner),
            'kontakt': Anruf._wb_voip_kontakt_daten(partner),
            'protokolle': Anruf._wb_voip_kontakt_protokolle(
                partner, layout._wb_voip_anzahl('protokolle'), ohne=self),
        }

    @nur_voip_benutzer
    def action_wb_voip_folgeaktivitaet(self):
        """Return Odoo's standard "schedule activity" dialog for the follow-up.

        The activity goes to the linked record if the caller may write it (and it
        supports activities), otherwise to this log entry.
        """
        self.ensure_one()
        self.check_access('write')
        ziel = self.env['wb.voip.anruf']._wb_voip_ziel(self.res_model, self.res_id)
        if ziel is None or 'activity_ids' not in ziel._fields or not ziel.has_access('write'):
            ziel = self
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Folgeaktivität planen"),
            'res_model': 'mail.activity.schedule',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {
                'active_model': ziel._name,
                'active_ids': [ziel.id],
                'active_id': ziel.id,
            },
        }
