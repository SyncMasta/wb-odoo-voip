from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command
from odoo.tools.misc import format_amount, format_date, format_datetime, formatLang

from ..tools.rechte import GRUPPE_MANAGER, nur_voip_manager
from .wb_voip_aktion import ART_MUSTER

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

# Block types allowed per place (the popup has no buttons, note or activities); at runtime
# always through ``wb.voip.layout._wb_voip_erlaubte_arten()`` (other modules add types)
ERLAUBTE_ARTEN = {
    'anrufer_fenster': ('kontaktkopf', 'felder', 'protokolle', 'aufgaben', 'notiz', 'buttons', 'text'),
    'protokoll_popup': ('kontaktkopf', 'felder', 'protokolle', 'text'),
}
# Block types with a number of entries; at runtime ``_wb_voip_gezaehlte_arten()``
GEZAEHLT = ('protokolle', 'aufgaben')
# Designer: limits of the data a client may send
DESIGNER_MAX_BLOECKE = 30
DESIGNER_MAX_FELDER = 30
DESIGNER_MAX_GRUPPEN = 50
DESIGNER_MAX_LAYOUTS = 200
NAME_MAX = 200
TITEL_MAX = 200
_DESIGNER_LAYOUT_SCHLUESSEL = frozenset({'name', 'ort', 'breite', 'group_ids', 'bloecke'})
_DESIGNER_BLOCK_SCHLUESSEL = frozenset({'art', 'titel', 'spalte', 'anzahl', 'text', 'feld_ids'})

# Delivered blocks (art, anzahl) per place; anzahl only for counted blocks
STANDARD_BLOECKE = {
    'anrufer_fenster': [('kontaktkopf', None), ('buttons', None), ('protokolle', 5),
                        ('aufgaben', 10), ('notiz', None)],
    'protokoll_popup': [('kontaktkopf', None), ('protokolle', 5)],
}


def _leer_zu_false(wert):
    """Return False for an empty optional text (None, False, only whitespace), else the value."""
    if wert is None or wert is False or (isinstance(wert, str) and not wert.strip()):
        return False
    return wert


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

    # --- Erweiterungspunkte für Blockarten ---

    @api.model
    def _wb_voip_erlaubte_arten(self):
        """Return ``{ort: (art, ...)}``: the block types each place allows (a fresh copy).

        Other modules add their block types (after ``selection_add`` on
        ``wb.voip.layout.block.art``) by overriding this and extending the tuples.
        """
        return {ort: tuple(arten) for ort, arten in ERLAUBTE_ARTEN.items()}

    @api.model
    def _wb_voip_gezaehlte_arten(self):
        """Return the block types that show a number of entries (field ``anzahl``, 1 to 20)."""
        return tuple(GEZAEHLT)

    @api.constrains('ist_standard', 'ort', 'active')
    def _check_standard(self):
        for layout in self.filtered('ist_standard'):
            if self.search_count([('ort', '=', layout.ort), ('ist_standard', '=', True),
                                  ('id', '!=', layout.id)], limit=1):
                raise ValidationError(self.env._("Je Fenster gibt es genau ein Standard-Layout."))
            if not layout.active:
                raise ValidationError(self.env._("Das Standard-Layout muss aktiv bleiben."))

    @api.constrains('ort', 'block_ids')
    def _check_bloecke_fuer_ort(self):
        """Only the block types of the place, the note at most once."""
        orte = dict(self._fields['ort']._description_selection(self.env))
        arten_namen = dict(self.env['wb.voip.layout.block']._fields['art']._description_selection(self.env))
        erlaubte_arten = self._wb_voip_erlaubte_arten()
        for layout in self:
            arten = layout.block_ids.mapped('art')
            for art in arten:
                if art not in erlaubte_arten.get(layout.ort, ()):
                    raise ValidationError(self.env._(
                        "Die Blockart „%(art)s“ gibt es im Fenster „%(ort)s“ nicht.",
                        art=arten_namen.get(art, art), ort=orte.get(layout.ort, layout.ort)))
            if arten.count('notiz') > 1:
                raise ValidationError(self.env._("Ein Layout hat höchstens einen Notiz-Block."))

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
        ergebnis = super().write(vals)
        if 'group_ids' in vals and self.sudo().filtered(lambda l: l.ist_standard and l.group_ids):
            raise self._wb_voip_standard_gruppen_fehler()
        return ergebnis

    def _wb_voip_standard_gruppen_fehler(self):
        return UserError(self.env._(
            "Das Standard-Layout gilt für alle; Gruppen nur an eigenen Layouts."))

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

    @nur_voip_manager
    def action_wb_voip_designer(self):
        """Open the layout designer for this layout (managers only)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'wb_voip.layout_designer',
            'name': self.env._("Layout-Designer"),
            'params': {'layout_id': self.id},
        }

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

    @api.model
    def _wb_voip_ersatz(self, ort):
        """Return an unsaved layout with the delivered blocks of ``ort``.

        Used when the standard layout is missing (e.g. deleted by SQL); nothing is written.
        """
        bloecke = [Command.create({'sequence': nr * 10, 'art': art, 'spalte': '1',
                                   **({'anzahl': anzahl} if anzahl else {})})
                   for nr, (art, anzahl) in enumerate(STANDARD_BLOECKE[ort], start=1)]
        return self.sudo().new({'name': ort, 'ort': ort, 'breite': 'normal', 'block_ids': bloecke})

    def _wb_voip_anzahl(self, art):
        """Return how many entries the blocks of ``art`` show (0 = no such block, max 20)."""
        self.ensure_one()
        anzahlen = [block.anzahl for block in self.sudo()._wb_voip_gueltige_bloecke()
                    if block.art == art]
        return min(max(anzahlen), ANZAHL_MAX) if anzahlen else 0

    def _wb_voip_fuer_client(self, partner, anruf=None):
        """Return the layout as client data, field values read as ``partner``'s user.

        ``partner`` is a ``res.partner`` record in the caller's environment that the caller
        may read, or empty; without it field blocks carry no values. ``anruf`` is the
        caller's own call in the caller's environment (empty or None: designer preview); it
        is handed to the block data hooks. The layout itself is read as superuser (the
        caller cannot read layouts; it was chosen for him beforehand).
        """
        self.ensure_one()
        layout = self.sudo()
        # Block-Daten in der Umgebung des Aufrufers (das Layout selbst ist oft sudo)
        aufrufer = anruf.env if anruf else partner.env
        Layout = aufrufer['wb.voip.layout']
        bloecke = [
            Layout._wb_voip_block_fuer_client(block.art, block.titel, block.spalte, block.anzahl,
                                              block.text, block.feld_ids, partner, anruf=anruf)
            for block in layout._wb_voip_gueltige_bloecke()]
        # ein Ersatz ohne Datensatz hat keine ID
        return {'id': layout._origin.id or False, 'breite': layout.breite, 'bloecke': bloecke}

    def _wb_voip_gueltige_bloecke(self):
        """Return the blocks in order that the place allows (first note only).

        Older data may hold blocks a place no longer allows; they are skipped at runtime.
        """
        self.ensure_one()
        erlaubt = self._wb_voip_erlaubte_arten().get(self.ort, ())
        ergebnis = self.env['wb.voip.layout.block']
        notiz = False
        for block in self.block_ids.sorted(lambda b: (b.sequence, b.id)):
            if block.art not in erlaubt or (block.art == 'notiz' and notiz):
                continue
            notiz = notiz or block.art == 'notiz'
            ergebnis |= block
        return ergebnis

    @api.model
    def _wb_voip_unerlaubte_bloecke_entfernen(self):
        """Delete blocks a place does not allow and notes beyond the first (migration)."""
        layouts = self.sudo().with_context(active_test=False).search([])
        for layout in layouts:
            (layout.block_ids - layout._wb_voip_gueltige_bloecke()).unlink()

    @api.model
    def _wb_voip_block_fuer_client(self, art, titel, spalte, anzahl, text, felder, partner,
                                   anruf=None):
        """Return one block as client data (``felder``: ``ir.model.fields`` records).

        If the model has a method ``_wb_voip_block_daten_<art>(anruf, block, partner)``, its
        result is added as ``daten`` (see ``_wb_voip_block_daten``).
        """
        block = {
            'art': art,
            'titel': titel or False,
            'spalte': spalte,
            'anzahl': min(anzahl, ANZAHL_MAX) if art in self._wb_voip_gezaehlte_arten() else False,
            'text': (text or '')[:TEXT_MAX] if art == 'text' else False,
            'felder': self._wb_voip_feldwerte(partner, felder) if art == 'felder' and partner else [],
        }
        methode = self._wb_voip_block_daten_methode(art)
        if methode is not None:
            if anruf is None:
                anruf = self.env['wb.voip.anruf']
            block['daten'] = methode(anruf, dict(block), partner)
        return block

    @api.model
    def _wb_voip_block_daten_methode(self, art):
        """Return the bound block data hook ``_wb_voip_block_daten_<art>`` or None.

        Contract of such a hook (added by other modules for their block types): called on
        ``wb.voip.layout`` in the caller's environment (never sudo) with ``anruf`` (the
        caller's own call, empty in the designer preview), ``block`` (a copy of the block's
        client data: ``art``, ``titel``, ``spalte``, ``anzahl``, ``text``, ``felder``) and
        ``partner`` (readable contact or empty). It returns JSON-serialisable data for the
        browser and must itself show only what the caller may read.
        """
        if not isinstance(art, str) or not ART_MUSTER.fullmatch(art):
            return None
        methode = getattr(self, f'_wb_voip_block_daten_{art}', None)
        return methode if callable(methode) else None

    # --- Designer (nur Administratoren von WB VoIP) ---

    def _wb_voip_ungueltig(self, was):
        return UserError(self.env._("Ungültige Layout-Daten: %s.", was))

    @api.model
    def _wb_voip_designer_layout(self, layout_id, leer_erlaubt=True):
        """Return the layout ``layout_id`` (False: standard caller-window layout)."""
        if leer_erlaubt and layout_id in (False, None):
            layout = self.search([('ort', '=', 'anrufer_fenster'), ('ist_standard', '=', True)], limit=1)
            if not layout:
                raise self._wb_voip_ungueltig(self.env._("Layout"))
            return layout
        if type(layout_id) is not int or layout_id <= 0:
            raise self._wb_voip_ungueltig(self.env._("Layout"))
        layout = self.browse(layout_id).exists()
        if not layout:
            raise self._wb_voip_ungueltig(self.env._("Layout"))
        return layout

    @api.model
    def _wb_voip_designer_felder(self):
        """Return the ``ir.model.fields`` of res.partner a layout may show (readable here)."""
        Partner = self.env['res.partner']
        namen = [name for name, field in Partner._fields.items()
                 if self._wb_voip_feld_erlaubt(field) and Partner._has_field_access(field, 'read')]
        return self.env['ir.model.fields'].sudo().search(
            [('model', '=', 'res.partner'), ('name', 'in', namen)])

    def _wb_voip_designer_daten(self):
        """Return the layout as the designer edits it (field ids instead of values)."""
        self.ensure_one()
        gezaehlt = self._wb_voip_gezaehlte_arten()
        bloecke = []
        for block in self.sudo().block_ids.sorted(lambda b: (b.sequence, b.id)):
            bloecke.append({
                'art': block.art,
                'titel': block.titel or False,
                'spalte': block.spalte,
                'anzahl': block.anzahl if block.art in gezaehlt else False,
                'text': (block.text or False) if block.art == 'text' else False,
                'feld_ids': block.feld_ids.ids if block.art == 'felder' else [],
            })
        return {
            'id': self.id,
            'name': self.name,
            'ort': self.ort,
            'breite': self.breite,
            'ist_standard': self.ist_standard,
            'group_ids': self.group_ids.ids,
            'bloecke': bloecke,
        }

    def _wb_voip_designer_pruefen(self, daten):
        """Check designer data strictly; return ``(layout_vals, block_vals_list)``.

        Raises ``UserError`` on anything unexpected (unknown keys, types, lengths, block
        types of another place, fields outside the allowed set); nothing is written here.
        """
        self.ensure_one()
        ungueltig = self._wb_voip_ungueltig
        if not isinstance(daten, dict) or set(daten) != _DESIGNER_LAYOUT_SCHLUESSEL:
            raise ungueltig(self.env._("Layout"))
        name = daten['name']
        if not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX:
            raise ungueltig(self.env._("Name"))
        erlaubte_arten = self._wb_voip_erlaubte_arten()
        gezaehlt = self._wb_voip_gezaehlte_arten()
        ort = daten['ort']
        if not isinstance(ort, str) or ort not in erlaubte_arten:
            raise ungueltig(self.env._("Fenster"))
        breite = daten['breite']
        if not isinstance(breite, str) or breite not in dict(self._fields['breite'].selection):
            raise ungueltig(self.env._("Breite"))
        gruppen = daten['group_ids']
        if (not isinstance(gruppen, list) or len(gruppen) > DESIGNER_MAX_GRUPPEN
                or any(type(g) is not int or g <= 0 for g in gruppen)
                or len(self.env['res.groups'].browse(set(gruppen)).exists()) != len(set(gruppen))):
            raise ungueltig(self.env._("Gruppen"))
        if gruppen and self.ist_standard:
            raise self._wb_voip_standard_gruppen_fehler()
        bloecke = daten['bloecke']
        if not isinstance(bloecke, list) or len(bloecke) > DESIGNER_MAX_BLOECKE:
            raise ungueltig(self.env._("Blöcke"))
        erlaubte_felder = set(self._wb_voip_designer_felder().ids)
        block_vals = []
        for nr, block in enumerate(bloecke, start=1):
            if not isinstance(block, dict) or 'art' not in block \
                    or not set(block) <= _DESIGNER_BLOCK_SCHLUESSEL:
                raise ungueltig(self.env._("Block"))
            art = block['art']
            if not isinstance(art, str) or art not in erlaubte_arten[ort]:
                raise ungueltig(self.env._("Blockart"))
            titel = _leer_zu_false(block.get('titel', False))
            if titel is not False and (not isinstance(titel, str) or len(titel) > TITEL_MAX):
                raise ungueltig(self.env._("Titel"))
            spalte = block.get('spalte', '1')
            if spalte not in ('1', '2'):
                raise ungueltig(self.env._("Spalte"))
            anzahl = block.get('anzahl', False)
            if anzahl is None:
                anzahl = False
            if anzahl is not False and type(anzahl) is not int:
                raise ungueltig(self.env._("Anzahl"))
            text = _leer_zu_false(block.get('text', False))
            if text is not False and (not isinstance(text, str) or len(text) > TEXT_MAX):
                raise ungueltig(self.env._("Text"))
            feld_ids = block.get('feld_ids', [])
            if (not isinstance(feld_ids, list) or len(feld_ids) > DESIGNER_MAX_FELDER
                    or any(type(f) is not int for f in feld_ids)
                    or not set(feld_ids) <= erlaubte_felder):
                raise ungueltig(self.env._("Felder"))
            if (art == 'felder') != bool(feld_ids):
                raise ungueltig(self.env._("Felder"))
            vals = {'sequence': nr * 10, 'art': art, 'titel': titel and titel.strip(),
                    'spalte': spalte}
            if art in gezaehlt:
                anzahl = 5 if anzahl is False else anzahl
                if not ANZAHL_MIN <= anzahl <= ANZAHL_MAX:
                    raise ungueltig(self.env._("Anzahl"))
                vals['anzahl'] = anzahl
            if art == 'text':
                vals['text'] = text
            if art == 'felder':
                # Reihenfolge wie gewählt, doppelte einmal
                vals['feld_ids'] = [Command.set(list(dict.fromkeys(feld_ids)))]
            block_vals.append(vals)
        if [b['art'] for b in block_vals].count('notiz') > 1:
            raise ungueltig(self.env._("Notiz"))
        layout_vals = {'name': name, 'ort': ort, 'breite': breite,
                       'group_ids': [Command.set(list(dict.fromkeys(gruppen)))]}
        return layout_vals, block_vals

    @api.model
    @nur_voip_manager
    def wb_voip_designer_laden(self, layout_id):
        """Return everything the designer needs for ``layout_id`` (False: standard window).

        ``{layout, arten, felder, orte, breiten, layouts, beispielkontakt_id}``; ``felder`` are
        the allowed contact fields (``ir.model.fields`` ids) the manager may read; ``arten``
        lists per place ``{art, label, gezaehlt}`` (from ``_wb_voip_erlaubte_arten``).
        """
        layout = self._wb_voip_designer_layout(layout_id)
        gezaehlt = self._wb_voip_gezaehlte_arten()
        arten_namen = dict(self.env['wb.voip.layout.block']._fields['art']._description_selection(self.env))
        Partner = self.env['res.partner']
        felder = [{'id': feld.id, 'name': feld.name,
                   'label': Partner._fields[feld.name]._description_string(self.env),
                   'typ': feld.ttype} for feld in self._wb_voip_designer_felder()]
        felder.sort(key=lambda f: (f['label'].lower(), f['name']))
        return {
            'layout': layout._wb_voip_designer_daten(),
            'arten': {ort: [{'art': art, 'label': arten_namen.get(art, art),
                             'gezaehlt': art in gezaehlt} for art in arten]
                      for ort, arten in self._wb_voip_erlaubte_arten().items()},
            'felder': felder,
            'orte': list(self._fields['ort']._description_selection(self.env)),
            'breiten': list(self._fields['breite']._description_selection(self.env)),
            'layouts': [{'id': l.id, 'name': l.name, 'ort': l.ort, 'ist_standard': l.ist_standard}
                        for l in self.search([], limit=DESIGNER_MAX_LAYOUTS)],
            'beispielkontakt_id': self.env.user.partner_id.id,
        }

    @api.model
    @nur_voip_manager
    def wb_voip_designer_speichern(self, layout_id, daten):
        """Replace name, place, width, groups and all blocks of ``layout_id`` at once.

        Everything is checked first; any invalid part changes nothing. Returns the new
        state as ``wb_voip_designer_laden`` does.
        """
        layout = self._wb_voip_designer_layout(layout_id, leer_erlaubt=False)
        layout_vals, block_vals = layout._wb_voip_designer_pruefen(daten)
        with self.env.cr.savepoint():
            layout.block_ids.unlink()
            layout.write({**layout_vals, 'block_ids': [Command.create(v) for v in block_vals]})
        return self.wb_voip_designer_laden(layout.id)

    @api.model
    @nur_voip_manager
    def wb_voip_designer_vorschau(self, layout_id, partner_id, entwurf=None):
        """Return preview data for the contact ``partner_id``, read as the manager.

        Same format as ``wb.voip.anruf.wb_voip_kontext`` (``layout``, ``kontakt``,
        ``protokolle``, ``aufgaben``). Without ``entwurf`` the saved layout is shown,
        otherwise the unsaved designer data (checked like for saving, never written).
        """
        layout = self._wb_voip_designer_layout(layout_id, leer_erlaubt=False)
        if type(partner_id) is not int or partner_id <= 0:
            raise self._wb_voip_ungueltig(self.env._("Kontakt"))
        partner = self.env['res.partner'].browse(partner_id).exists()
        if not partner:
            raise self._wb_voip_ungueltig(self.env._("Kontakt"))
        partner.check_access('read')
        if entwurf is None:
            layout_daten = layout._wb_voip_fuer_client(partner)
        else:
            layout_vals, block_vals = layout._wb_voip_designer_pruefen(entwurf)
            Feld = self.env['ir.model.fields'].sudo()
            layout_daten = {'id': layout.id, 'breite': layout_vals['breite'], 'bloecke': [
                self._wb_voip_block_fuer_client(
                    v['art'], v['titel'], v['spalte'], v.get('anzahl', 0), v.get('text'),
                    Feld.browse(v['feld_ids'][0][2]) if v.get('feld_ids') else Feld, partner)
                for v in block_vals]}
        Anruf = self.env['wb.voip.anruf']
        return {
            'layout': layout_daten,
            'kontakt': Anruf._wb_voip_kontakt_daten(partner),
            'protokolle': Anruf._wb_voip_kontakt_protokolle(partner, ANZAHL_MAX),
            'aufgaben': Anruf.new({})._wb_voip_kontakt_aufgaben(partner, ANZAHL_MAX),
        }

    @api.model
    def _wb_voip_feldwerte(self, partner, felder):
        """Return ``[{name, label, wert}]`` of the readable, non-empty values of ``felder``.

        Nothing if the user of ``partner`` may not read it (checked once for all fields).
        """
        ergebnis = []
        if not partner or not partner.has_access('read'):
            return ergebnis
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

        The caller has checked that the user may read ``record``. Empty string if he may
        not read the field or (for relations) any of the target records; never raises for
        missing rights.
        """
        record.ensure_one()
        try:
            if not record._has_field_access(field, 'read'):
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
        if typ in ('char', 'text') and len(text) > TEXT_FELD_MAX:
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

    @api.constrains('art', 'layout_id')
    def _check_art_fuer_ort(self):
        self.layout_id._check_bloecke_fuer_ort()

    @api.constrains('art', 'anzahl')
    def _check_anzahl(self):
        gezaehlt = self.env['wb.voip.layout']._wb_voip_gezaehlte_arten()
        for block in self.filtered(lambda b: b.art in gezaehlt):
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
