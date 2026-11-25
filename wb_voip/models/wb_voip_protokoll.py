import logging

from odoo import api, fields, models
from odoo.exceptions import AccessError

from ..tools import serverfelder
from ..tools.rechte import nur_voip_benutzer
from .wb_voip_anruf import FINALE_ZUSTAENDE

_logger = logging.getLogger(__name__)


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

    _anruf_eindeutig = models.Constraint(
        'unique(anruf_id)', 'Zu diesem Anruf gibt es schon ein Protokoll.')

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
        alt = {p.id: p.ergebnis for p in self} if 'ergebnis' in vals else None
        ergebnis = super().write(vals)
        if alt:
            self._wb_voip_ergebnis_gewechselt(alt)
        return ergebnis

    def _wb_voip_ergebnis_gewechselt(self, alt):
        """Follow-up of a changed result (after the user's own write succeeded).

        Away from ``erreicht``: the missed entries this log closed are open again (with a new
        callback activity). To ``erreicht`` on an outgoing call: the open missed entries of the
        number are closed (again). Runs with sudo, only on entries linked to this log or the
        number of the user's own outgoing call; a failure never undoes the user's change.
        """
        for protokoll in self.sudo():
            vorher = alt.get(protokoll.id)
            if vorher == protokoll.ergebnis:
                continue
            anruf = protokoll.anruf_id
            try:
                with self.env.cr.savepoint():
                    if vorher == 'erreicht':
                        self.env['wb.voip.anruf'].sudo().search([
                            ('erledigt_durch_protokoll_id', '=', protokoll.id),
                            ('erledigt', '=', True)])._wb_voip_wieder_oeffnen()
                    elif protokoll.ergebnis == 'erreicht' and anruf \
                            and anruf.richtung == 'ausgehend' and anruf.user_id == protokoll.user_id \
                            and anruf.zustand in FINALE_ZUSTAENDE:
                        anruf._wb_voip_nummer_erreicht(protokoll)
            except Exception:  # noqa: BLE001 - die Änderung des Benutzers bleibt bestehen
                _logger.exception("[wb_voip] Folgen des Ergebniswechsels (Protokoll %s) fehlgeschlagen",
                                  protokoll.id)

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
        # Block-Hooks bekommen nur den eigenen Anruf, sonst einen leeren
        anruf = self.sudo().anruf_id
        if anruf.user_id != self.env.user:
            anruf = anruf.browse()
        return {
            'layout': layout._wb_voip_fuer_client(partner, anruf=anruf.with_user(self.env.user)),
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
