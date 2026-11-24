from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.misc import format_amount, format_date, format_datetime, formatLang

from ..tools.rechte import GRUPPE_MANAGER

ORTE = [('anrufer_fenster', 'Anrufer-Fenster'), ('protokoll_popup', 'Protokoll-Popup')]
TEXT_MAX = 2000
ANZAHL_MIN, ANZAHL_MAX = 1, 20
# Ausgabe der Feldwerte: Textfelder gekürzt, Many2many höchstens so viele Namen
TEXT_FELD_MAX = 500
M2M_MAX = 10
FELD_TYPEN = ('char', 'text', 'selection', 'date', 'datetime', 'boolean', 'integer', 'float',
              'monetary', 'many2one', 'many2many')
# Name parts that never appear in a layout (credentials)
FELD_NAMENTEILE = ('password', 'token', 'secret', 'key')
# Partner fields that never appear in a layout, whatever their name
_WB_VOIP_LAYOUT_SPERRFELDER = (
    'signup_token', 'signup_type', 'signup_expiration', 'signup_valid', 'signup_url',
    'user_ids', 'message_ids', 'activity_ids', 'website_message_ids', 'message_follower_ids',
    'message_partner_ids', 'message_main_attachment_id', 'rating_ids',
)
# Many2one targets a layout field may point to (everything else is rejected)
ERLAUBTE_ZIELE = ('res.partner', 'res.country', 'res.country.state', 'res.partner.title',
                  'res.partner.category', 'res.lang', 'res.partner.industry', 'res.currency')
# Many2many targets
ERLAUBTE_ZIELE_M2M = ('res.partner.category',)

# Delivered blocks (art, anzahl) per place; anzahl only for counted blocks
STANDARD_BLOECKE = {
    'anrufer_fenster': [('kontaktkopf', None), ('buttons', None), ('protokolle', 5),
                        ('aufgaben', 10), ('notiz', None)],
    'protokoll_popup': [('kontaktkopf', None), ('protokolle', 5)],
}


class WbVoipLayout(models.Model):
    """Window layout (blocks and their order) for the caller window or the log popup."""

    _name = 'wb.voip.layout'
    _description = 'WB VoIP: Fenster-Layout'
    _order = 'ort, sequence, id'

    name = fields.Char(string='Name', required=True, translate=True)
    ort = fields.Selection(ORTE, string='Fenster', required=True, default='anrufer_fenster')
    sequence = fields.Integer(string='Reihenfolge', default=20)
    active = fields.Boolean(string='Aktiv', default=True)
    # Nur Administratoren sehen die Gruppen; die Laufzeit sucht nach der Prüfung per sudo
    group_ids = fields.Many2many(
        'res.groups', 'wb_voip_layout_res_groups_rel', 'layout_id', 'group_id',
        string='Gruppen', groups='wb_voip.group_wb_voip_manager',
        help="Leer = alle Benutzer von WB VoIP. Bei mehreren passenden Layouts gewinnt die "
             "kleinste Reihenfolge.")
    ist_standard = fields.Boolean(
        string='Standard-Layout', readonly=True, copy=False,
        help="Wird mit dem Modul ausgeliefert, gilt ohne passendes eigenes Layout. "
             "Nicht löschbar, nicht archivierbar.")
    breite = fields.Selection(
        [('normal', 'Normal'), ('breit', 'Breit'), ('sehr_breit', 'Sehr breit')],
        string='Breite', required=True, default='normal',
        help="Anrufer-Fenster: 720, 960 oder 1200 px (höchstens Bildschirmbreite). "
             "Popup: Dialoggröße groß, extra groß oder Vollbild.")
    block_ids = fields.One2many('wb.voip.layout.block', 'layout_id', string='Blöcke', copy=True)

    _wb_voip_layout_standard_je_ort = models.UniqueIndex(
        '(ort) WHERE ist_standard IS TRUE',
        'Je Fenster gibt es genau ein Standard-Layout.')

    @api.constrains('ist_standard', 'ort', 'active')
    def _check_standard(self):
        for layout in self.filtered('ist_standard'):
            if self.search_count([('ort', '=', layout.ort), ('ist_standard', '=', True),
                                  ('id', '!=', layout.id)], limit=1):
                raise ValidationError(self.env._("Je Fenster gibt es genau ein Standard-Layout."))
            if not layout.active:
                raise ValidationError(self.env._("Das Standard-Layout muss aktiv bleiben."))

    def _wb_voip_daten_laden(self):
        """Return True only while the module data loader runs (superuser plus install_mode).

        ``install_mode`` alone is a plain context key that an RPC client could send.
        """
        return bool(self.env.su and self.env.context.get('install_mode'))

    @api.model_create_multi
    def create(self, vals_list):
        # Standard layouts only come from the module data (install/update)
        if not self._wb_voip_daten_laden() \
                and any(vals.get('ist_standard') for vals in vals_list):
            raise UserError(self.env._("Standard-Layouts kommen nur aus den Moduldaten."))
        return super().create(vals_list)

    def write(self, vals):
        if self.filtered('ist_standard'):
            if 'active' in vals and not vals['active']:
                raise UserError(self.env._("Das Standard-Layout kann nicht archiviert werden."))
            for feld in ('ist_standard', 'ort'):
                if feld in vals and any(vals[feld] != layout[feld] for layout in self):
                    raise UserError(self.env._(
                        "Beim Standard-Layout lassen sich Standard-Kennzeichen und Fenster nicht ändern."))
        elif vals.get('ist_standard') and not self._wb_voip_daten_laden():
            raise UserError(self.env._("Standard-Layouts kommen nur aus den Moduldaten."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_nicht_standard(self):
        if self.filtered('ist_standard'):
            raise UserError(self.env._("Das Standard-Layout kann nicht gelöscht werden."))

    def action_auf_standard_zuruecksetzen(self):
        """Reset the blocks of a standard layout to the delivered state (managers only)."""
        if not self.env.user.has_group(GRUPPE_MANAGER):
            raise AccessError(self.env._("Nur für Administratoren von WB VoIP."))
        for layout in self:
            if not layout.ist_standard:
                raise UserError(self.env._("Nur das Standard-Layout lässt sich zurücksetzen."))
            layout._wb_voip_bloecke_setzen()
        return True

    def _wb_voip_bloecke_setzen(self):
        """Replace the blocks of standard layouts by the delivered ones."""
        Block = self.env['wb.voip.layout.block']
        for layout in self:
            layout.block_ids.unlink()
            for nr, (art, anzahl) in enumerate(STANDARD_BLOECKE[layout.ort], start=1):
                Block.create({
                    'layout_id': layout.id, 'sequence': nr * 10, 'art': art,
                    **({'anzahl': anzahl} if anzahl else {}),
                })

    # --- Laufzeit: Layout für den Client ---

    def _wb_voip_anzahl(self, art):
        """Return how many entries the blocks of ``art`` show (0 = no such block, max 20)."""
        self.ensure_one()
        anzahlen = [block.anzahl for block in self.sudo().block_ids if block.art == art]
        return min(max(anzahlen), ANZAHL_MAX) if anzahlen else 0

    def _wb_voip_fuer_client(self, partner):
        """Return the layout as client data, field values read as ``partner``'s user.

        ``partner`` is a ``res.partner`` record in the caller's environment that the caller
        may read, or empty; without it field blocks carry no values. The layout itself is
        read as superuser (the caller cannot read layouts; it was chosen for him beforehand).
        """
        self.ensure_one()
        layout = self.sudo()
        bloecke = []
        for block in layout.block_ids.sorted(lambda b: (b.sequence, b.id)):
            gezaehlt = block.art in ('protokolle', 'aufgaben')
            bloecke.append({
                'art': block.art,
                'titel': block.titel or False,
                'spalte': block.spalte,
                'anzahl': min(block.anzahl, ANZAHL_MAX) if gezaehlt else False,
                'text': (block.text or '')[:TEXT_MAX] if block.art == 'text' else False,
                'felder': self._wb_voip_feldwerte(partner, block.feld_ids)
                if block.art == 'felder' and partner else [],
            })
        return {'id': layout.id, 'breite': layout.breite, 'bloecke': bloecke}

    @api.model
    def _wb_voip_feldwerte(self, partner, felder):
        """Return ``[{name, label, wert}]`` of the readable, non-empty values of ``felder``."""
        ergebnis = []
        for feld in felder.sudo():
            field = partner._fields.get(feld.name)
            if field is None or not self._wb_voip_feld_erlaubt(field):
                continue
            wert = self._wb_voip_feldwert(partner, field)
            if wert:
                ergebnis.append({'name': field.name, 'label': field._description_string(partner.env),
                                 'wert': wert})
        return ergebnis

    @api.model
    def _wb_voip_feld_erlaubt(self, field):
        """Runtime counterpart of the block constraint (stored field of an allowed type/target)."""
        ziele = {'many2one': ERLAUBTE_ZIELE, 'many2many': ERLAUBTE_ZIELE_M2M}
        return (field.type in FELD_TYPEN and field.store
                and field.name not in _WB_VOIP_LAYOUT_SPERRFELDER
                and not any(teil in field.name.lower() for teil in FELD_NAMENTEILE)
                and (field.type not in ziele or field.comodel_name in ziele[field.type]))

    @api.model
    def _wb_voip_feldwert(self, record, field):
        """Return the value of ``field`` on ``record`` as text, read as the record's user.

        Empty string if the user may not read the record, the field or (for relations)
        any of the target records; never raises for missing rights.
        """
        record.ensure_one()
        try:
            if not record.has_access('read') or not record._has_field_access(field, 'read'):
                return ''
            return self._wb_voip_wert_text(record, field, record[field.name])
        except AccessError:
            return ''

    @api.model
    def _wb_voip_wert_text(self, record, field, wert):
        """Format ``wert`` of ``field`` in the language and time zone of ``record``'s user."""
        env = record.env
        typ = field.type
        if typ == 'boolean':
            return env._("Ja") if wert else env._("Nein")
        if typ in ('many2one', 'many2many'):
            lesbar = wert._filtered_access('read')
            namen = [name for name in lesbar[:M2M_MAX].mapped('display_name') if name]
            if typ == 'many2many' and len(lesbar) > M2M_MAX:
                namen.append('…')
            return ', '.join(namen)
        if wert is False or wert is None:
            return ''
        if typ == 'selection':
            return dict(field._description_selection(env)).get(wert) or ''
        if typ == 'date':
            return format_date(env, wert)
        if typ == 'datetime':
            return format_datetime(env, wert, tz=env.user.tz or 'UTC')
        if typ == 'integer':
            return formatLang(env, wert, digits=0)
        if typ == 'float':
            digits = field.get_digits(env)
            return formatLang(env, wert, digits=digits[1] if digits else 2)
        if typ == 'monetary':
            waehrung_feld = field.get_currency_field(record)
            waehrung = record[waehrung_feld] if waehrung_feld else None
            if waehrung:
                return format_amount(env, wert, waehrung)
            return formatLang(env, wert)
        text = str(wert)
        if typ == 'text' and len(text) > TEXT_FELD_MAX:
            text = text[:TEXT_FELD_MAX - 1] + '…'
        return text

    @api.model
    def _wb_voip_standard_bloecke_sicherstellen(self):
        """Idempotent: give standard layouts without blocks their delivered blocks.

        Called from the post-init hook and a migration; never touches a layout that has blocks.
        """
        for layout in self.sudo().with_context(active_test=False).search([('ist_standard', '=', True)]):
            if not layout.block_ids and layout.ort in STANDARD_BLOECKE:
                layout._wb_voip_bloecke_setzen()


class WbVoipLayoutBlock(models.Model):
    """One block of a window layout."""

    _name = 'wb.voip.layout.block'
    _description = 'WB VoIP: Layout-Block'
    _order = 'layout_id, sequence, id'

    layout_id = fields.Many2one(
        'wb.voip.layout', string='Layout', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(string='Reihenfolge', default=10)
    art = fields.Selection(
        [('kontaktkopf', 'Kontaktkopf'), ('felder', 'Kontaktfelder'),
         ('protokolle', 'Letzte Protokolle'), ('aufgaben', 'Offene Aufgaben'),
         ('notiz', 'Notiz'), ('buttons', 'Buttons'), ('text', 'Text')],
        string='Art', required=True, default='kontaktkopf')
    titel = fields.Char(string='Titel', translate=True)
    spalte = fields.Selection(
        [('1', 'Spalte 1'), ('2', 'Spalte 2')], string='Spalte', required=True, default='1',
        help="Zwei Spalten gibt es ab der Breite „Breit“.")
    feld_ids = fields.Many2many(
        'ir.model.fields', 'wb_voip_layout_block_feld_rel', 'block_id', 'feld_id',
        string='Felder', ondelete='cascade',
        domain="[('model', '=', 'res.partner'), ('store', '=', True), "
               "('ttype', 'in', %s)]" % (list(FELD_TYPEN),))
    anzahl = fields.Integer(string='Anzahl', default=5, help="Protokolle/Aufgaben, 1 bis 20.")
    text = fields.Text(string='Text', translate=True, help="Nur Klartext, höchstens 2000 Zeichen.")

    @api.constrains('art', 'anzahl')
    def _check_anzahl(self):
        for block in self.filtered(lambda b: b.art in ('protokolle', 'aufgaben')):
            if not ANZAHL_MIN <= block.anzahl <= ANZAHL_MAX:
                raise ValidationError(self.env._(
                    "Die Anzahl muss zwischen %(min)s und %(max)s liegen.",
                    min=ANZAHL_MIN, max=ANZAHL_MAX))

    @api.constrains('text')
    def _check_text(self):
        for block in self:
            if block.text and len(block.text) > TEXT_MAX:
                raise ValidationError(self.env._(
                    "Der Text darf höchstens %(max)s Zeichen lang sein.", max=TEXT_MAX))

    @api.constrains('art', 'feld_ids')
    def _check_felder(self):
        Partner = self.env['res.partner']
        for block in self:
            if block.art == 'felder' and not block.feld_ids:
                raise ValidationError(self.env._("Bitte mindestens ein Feld wählen."))
            for feld in block.feld_ids.sudo():
                name = feld.name or ''
                ziele = {'many2one': ERLAUBTE_ZIELE, 'many2many': ERLAUBTE_ZIELE_M2M}
                if (feld.model != 'res.partner' or name not in Partner._fields
                        or feld.ttype not in FELD_TYPEN or not feld.store
                        or name in _WB_VOIP_LAYOUT_SPERRFELDER
                        or any(teil in name.lower() for teil in FELD_NAMENTEILE)
                        or (feld.ttype in ziele and feld.relation not in ziele[feld.ttype])):
                    raise ValidationError(self.env._(
                        "Das Feld „%(feld)s“ ist in Layouts nicht erlaubt.",
                        feld=feld.field_description or name))
