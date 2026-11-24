from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools.rechte import GRUPPE_MANAGER

ORTE = [('anrufer_fenster', 'Anrufer-Fenster'), ('protokoll_popup', 'Protokoll-Popup')]
TEXT_MAX = 2000
ANZAHL_MIN, ANZAHL_MAX = 1, 20
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

    @api.model_create_multi
    def create(self, vals_list):
        # Standard layouts only come from the module data (install/update)
        if not self.env.context.get('install_mode') \
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
        elif vals.get('ist_standard') and not self.env.context.get('install_mode'):
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
