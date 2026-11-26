from markupsafe import Markup

from odoo import api, models
from odoo.tools import html2plaintext

from ..tools.eingaben import limit_pruefen
from ..tools.rechte import nur_voip_benutzer
from .wb_voip_anruf import ERLEDIGT_TEXT_MAX

# Obergrenzen je Aufruf (ASVS V11.1.4): höchstens so viele Stapel bzw. gelesene Aktivitäten
AUFGABEN_MAX_STAPEL = 10
AUFGABEN_MAX_GELESEN = 2000
AUFGABEN_MIN_STAPEL = 50


def _feedback_text(feedback):
    """Return the feedback as plain text: HTML is converted, plain text (e.g. "Preis < 100") kept."""
    if not feedback:
        return ''
    text = str(feedback)
    if isinstance(feedback, Markup) or text.lstrip().startswith('<'):
        return html2plaintext(text).strip()
    return text.strip()


class MailActivity(models.Model):
    _inherit = 'mail.activity'

    def _action_done(self, feedback=False, attachment_ids=None):
        """Marking a callback activity done also closes the missed entries that use it.

        The open entries are collected before (the activity is archived by the standard
        method); text = feedback of the activity as plain text (HTML, i.e. ``Markup`` or a string
        starting with ``<``, is converted; other text stays as typed), else "Task done".
        Entries closed by ``_wb_voip_nummer_erreicht`` are already done and not touched again.
        """
        offen = self.env['wb.voip.anruf'].sudo().search([
            ('rueckruf_aktivitaet_id', 'in', self.ids), ('erledigt', '=', False),
            ('zustand', '=', 'verpasst')]) if self.ids else self.env['wb.voip.anruf']
        ergebnis = super()._action_done(feedback=feedback, attachment_ids=attachment_ids)
        if offen:
            text = _feedback_text(feedback)[:ERLEDIGT_TEXT_MAX]
            for besitzer in offen.user_id:
                # gespeicherter Text: in der Sprache des Besitzers (auch aus einem Serverlauf), ohne
                # Sprache in der Firma der Einträge (wenn alle zu einer gehören), sonst der aktuellen
                eintraege = offen.filtered(lambda a: a.user_id == besitzer)
                sprache = eintraege.with_context(lang=eintraege._wb_voip_sprache(besitzer)).env
                eintraege._wb_voip_durch_aktivitaet_erledigt(text or sprache._("Task done"))
        return ergebnis

    @api.model
    @nur_voip_benutzer
    def wb_voip_aufgaben(self, limit=50):
        """Return the caller's open activities whose record has a contact with a number.

        The contact is the record itself (``res.partner``) or its ``partner_id``. Only
        records and contacts the caller may read count. At most ``limit`` entries, earliest
        deadline first: ``{id, titel, faellig, res_model, res_id, res_name, partner, nummer}``.
        """
        limit_pruefen(self.env, limit)
        Partner = self.env['res.partner']
        felder = Partner._wb_voip_nummernfelder()
        ergebnis = []
        offset = 0
        stapel = 0
        stapel_groesse = max(limit * 2, AUFGABEN_MIN_STAPEL)
        domain = [('user_id', '=', self.env.uid),
                  ('res_model', 'in', self._wb_voip_aufgaben_modelle()), ('res_id', '!=', 0)]
        while len(ergebnis) < limit and stapel < AUFGABEN_MAX_STAPEL \
                and offset < AUFGABEN_MAX_GELESEN:
            aktivitaeten = self.search(
                domain, order='date_deadline, id',
                limit=min(stapel_groesse, AUFGABEN_MAX_GELESEN - offset), offset=offset)
            if not aktivitaeten:
                break
            stapel += 1
            offset += len(aktivitaeten)
            kontakte = self._wb_voip_kontakte(aktivitaeten)
            for aktivitaet in aktivitaeten:
                datensatz, kontakt = kontakte.get(aktivitaet.id, (None, None))
                if not kontakt:
                    continue
                nummer = next((kontakt[feld] for feld in felder if kontakt[feld]), False)
                if not nummer:
                    continue
                ergebnis.append({
                    'id': aktivitaet.id,
                    'titel': aktivitaet.summary or aktivitaet.activity_type_id.name or '',
                    'faellig': aktivitaet.date_deadline and str(aktivitaet.date_deadline),
                    'res_model': datensatz._name,
                    'res_id': datensatz.id,
                    'res_name': datensatz.display_name,
                    'partner': [kontakt.id, kontakt.display_name],
                    'nummer': nummer,
                })
                if len(ergebnis) >= limit:
                    break
        return ergebnis

    @api.model
    def _wb_voip_aufgaben_modelle(self):
        """Return the models whose records name a contact: ``res.partner`` and models with a
        ``partner_id`` many2one to ``res.partner`` (from ``ir.model.fields``)."""
        felder = self.env['ir.model.fields'].sudo().search_fetch(
            [('name', '=', 'partner_id'), ('ttype', '=', 'many2one'),
             ('relation', '=', 'res.partner')], ['model'])
        modelle = {'res.partner'} | {
            modell for modell in felder.mapped('model')
            if modell in self.env and not self.env[modell]._abstract
            and not self.env[modell]._transient}
        return sorted(modelle)

    def _wb_voip_kontakte(self, aktivitaeten):
        """Map activity id -> (record, contact) for records and contacts the caller may read."""
        ergebnis = {}
        je_modell = {}
        for aktivitaet in aktivitaeten:
            je_modell.setdefault(aktivitaet.res_model, []).append(aktivitaet)
        for modell, liste in je_modell.items():
            if modell not in self.env:
                continue
            Modell = self.env[modell]
            if modell != 'res.partner' and not (
                    'partner_id' in Modell._fields
                    and Modell._fields['partner_id'].comodel_name == 'res.partner'):
                continue
            lesbar = Modell.browse({a.res_id for a in liste}).exists()._filtered_access('read')
            if modell == 'res.partner':
                kontakte = {d.id: (d, d) for d in lesbar}
            else:
                partner = lesbar.partner_id._filtered_access('read')
                kontakte = {d.id: (d, d.partner_id) for d in lesbar if d.partner_id in partner}
            for aktivitaet in liste:
                if aktivitaet.res_id in kontakte:
                    ergebnis[aktivitaet.id] = kontakte[aktivitaet.res_id]
        return ergebnis
