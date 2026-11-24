from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tools.safe_eval import test_python_expr

from ..models.wb_voip_aktion import (
    GRUPPE_BUTTON_AKTION, ICON_MUSTER, assistenten_aktion, serveraktion_erlaubt)
from ..models.wb_voip_layout import FELD_NAMENTEILE, _WB_VOIP_LAYOUT_SPERRFELDER
from ..tools.rechte import GRUPPE_SYSTEM, nur_voip_manager
from .wb_voip_aktion_vorlagen import VORLAGEN

NAME_MAX = 200
CHAR_MAX = 1000
TEXT_MAX = 10_000
CODE_MAX = 20_000
# Feldtypen, die „Feld setzen“ schreiben kann (Many2many nur „hinzufügen“)
FELD_TYPEN = ('char', 'text', 'selection', 'boolean', 'integer', 'float', 'date',
              'many2one', 'many2many')
# Modelle, die nie Ziel einer Assistenten-Aktion sind (Präfixe der technischen Namen)
GESPERRTE_MODELLE = ('ir.', 'base.', 'bus.', 'mail.', 'wb.voip.', 'wb.license.',
                     'res.users', 'res.groups')
# Relationsziele, auf die kein Wert zeigen darf
GESPERRTE_ZIELE = ('ir.', 'base.', 'bus.', 'wb.voip.', 'wb.license.', 'res.groups')
# ORM-Felder, die nie gesetzt werden
MAGISCHE_FELDER = ('id', 'display_name', 'create_uid', 'create_date', 'write_uid',
                   'write_date', '__last_update')


class WbVoipAktionAssistentOption(models.TransientModel):
    """Choice offered by the wizard (field or selection value).

    VoIP managers cannot read ``ir.model.fields`` (Odoo grants that to the access
    rights administrators only), so the wizard lists the allowed choices itself. An
    option only carries a technical name; ``action_anlegen`` checks the name again.
    """

    _name = 'wb.voip.aktion.assistent.option'
    _description = 'WB VoIP: Auswahl im Button-Assistenten'
    _rec_name = 'label'
    _order = 'label, id'

    assistent_id = fields.Many2one(
        'wb.voip.aktion.assistent', string='Assistent', required=True, ondelete='cascade')
    art = fields.Selection([('feld', 'Feld'), ('auswahl', 'Auswahlwert')], required=True)
    name = fields.Char(string='Technischer Name', required=True)
    label = fields.Char(string='Bezeichnung', required=True)
    feld_name = fields.Char(string='Feld')


class WbVoipAktionAssistent(models.TransientModel):
    """Wizard that creates a call button together with an allowed server action."""

    _name = 'wb.voip.aktion.assistent'
    _description = 'WB VoIP: Button-Assistent'

    aktion_id = fields.Many2one('wb.voip.aktion', string='Button', ondelete='cascade')
    schritt = fields.Selection(
        [('button', 'Button'), ('aktion', 'Aktion'), ('feld', 'Feld')], string='Schritt',
        default='button', required=True)

    # --- Schritt 1: Button ---
    name = fields.Char(string='Bezeichnung')
    icon = fields.Char(string='Symbol', help="Font-Awesome-Klasse, z. B. fa-user")
    farbe = fields.Selection(
        [('primary', 'Primär'), ('secondary', 'Sekundär'), ('success', 'Grün'),
         ('warning', 'Gelb'), ('danger', 'Rot')],
        string='Farbe', required=True, default='secondary')
    ort_anrufer_fenster = fields.Boolean(string='Im Anrufer-Fenster', default=True)
    ort_protokoll_popup = fields.Boolean(string='Im Protokoll-Popup')
    ort_softphone = fields.Boolean(string='Im Softphone')
    richtung = fields.Selection(
        [('beide', 'Beide'), ('eingehend', 'Eingehend'), ('ausgehend', 'Ausgehend')],
        string='Richtung', required=True, default='beide')
    zustand = fields.Selection(
        [('immer', 'Immer'), ('klingelt', 'Während es klingelt'),
         ('laeuft', 'Während des Gesprächs'), ('beendet', 'Nach dem Anruf')],
        string='Zustand', required=True, default='immer')
    group_ids = fields.Many2many(
        'res.groups', 'wb_voip_aktion_assistent_res_groups_rel', 'assistent_id', 'group_id',
        string='Gruppen', help="Leer = alle Benutzer von WB VoIP.")

    # --- Schritt 2: Aktion ---
    art = fields.Selection(selection='_selection_art', string='Was soll passieren?',
                           required=True, default='feld')
    vorlage = fields.Selection(selection='_selection_vorlage', string='Vorlage')
    ziel = fields.Selection(
        [('kontakt', 'Kontakt des Anrufs'), ('datensatz', 'Datensatz des Anrufs'),
         ('anruf', 'Anruf')],
        string='Ziel', required=True, default='kontakt',
        help="„Datensatz des Anrufs“ ist der Datensatz, aus dem heraus angerufen wurde. "
             "„Anruf“ nur für Python-Code.")
    modell = fields.Selection(selection='_selection_modell', string='Modell')
    option_ids = fields.One2many('wb.voip.aktion.assistent.option', 'assistent_id')
    feld_option_id = fields.Many2one(
        'wb.voip.aktion.assistent.option', string='Feld', ondelete='set null',
        domain="[('assistent_id', '=', id), ('art', '=', 'feld')]")
    feld_name = fields.Char(string='Technischer Feldname')
    feld_typ = fields.Char(string='Feldtyp', compute='_compute_feld_typ')
    wert_char = fields.Char(string='Wert')
    wert_text = fields.Text(string='Text')
    wert_integer = fields.Integer(string='Zahl')
    wert_float = fields.Float(string='Dezimalzahl')
    wert_boolean = fields.Boolean(string='Ja/Nein')
    wert_date = fields.Date(string='Datum')
    wert_auswahl_option_id = fields.Many2one(
        'wb.voip.aktion.assistent.option', string='Auswahl', ondelete='set null',
        domain="[('assistent_id', '=', id), ('art', '=', 'auswahl'), ('feld_name', '=', feld_name)]")
    wert_auswahl = fields.Char(string='Technischer Auswahlwert')
    wert_res_model = fields.Char(compute='_compute_feld_typ')
    wert_res_id = fields.Many2oneReference(string='Datensatz', model_field='wert_res_model')
    wert_kategorie_id = fields.Many2one('res.partner.category', string='Kategorie')
    wert_sprache = fields.Selection(selection='_selection_sprache', string='Sprache')
    code = fields.Text(string='Python-Code', groups='base.group_system')

    # --- Auswahllisten ---

    @api.model
    def _selection_art(self):
        """Return the action types; Python code only for system administrators."""
        arten = [('feld', self.env._("Feld setzen")), ('vorlage', self.env._("Vorlage"))]
        if self.env.user.has_group(GRUPPE_SYSTEM):
            arten.append(('code', self.env._("Python-Code")))
        return arten

    @api.model
    def _selection_vorlage(self):
        namen = {
            'kategorie_hinzufuegen': self.env._("Kategorie am Kontakt hinzufügen"),
            'sprache_setzen': self.env._("Sprache des Kontakts setzen"),
        }
        return [(schluessel, namen.get(schluessel, vorlage['name']))
                for schluessel, vorlage in VORLAGEN.items()]

    @api.model
    def _selection_sprache(self):
        return self.env['res.lang'].get_installed()

    @api.model
    def _selection_modell(self):
        """Return the models the caller may target (technical name and label)."""
        # Schreibrecht aus Odoos gecachter ACL-Liste (je Benutzer), dann nur noch billige Prüfungen
        schreibbar = self.env['ir.model.access']._get_allowed_models('write')
        modelle = self.env['ir.model'].sudo().search(
            [('transient', '=', False), ('model', 'in', list(schreibbar))])
        return [(modell.model, f'{modell.name} ({modell.model})') for modell in modelle
                if self._wb_voip_modell_erlaubt(modell.model, schreibbar)]

    # --- Berechnete Angaben ---

    @api.depends('art', 'vorlage', 'ziel', 'modell', 'feld_name')
    def _compute_feld_typ(self):
        for assistent in self:
            field = assistent._wb_voip_field()
            assistent.feld_typ = field.type if field is not None else False
            assistent.wert_res_model = field.comodel_name \
                if field is not None and field.type in ('many2one', 'many2many') else False

    def _wb_voip_field(self):
        """Return the chosen field object of step 3 (or None)."""
        self.ensure_one()
        modell = self._wb_voip_modellname()
        if self.art != 'feld' or not modell or modell not in self.env or not self.feld_name:
            return None
        return self.env[modell]._fields.get(self.feld_name)

    @api.onchange('feld_option_id')
    def _onchange_feld_option_id(self):
        if self.feld_option_id:
            self.feld_name = self.feld_option_id.name
            self.wert_auswahl_option_id = False
            self.wert_auswahl = False
            self.wert_res_id = False

    @api.onchange('wert_auswahl_option_id')
    def _onchange_wert_auswahl_option_id(self):
        if self.wert_auswahl_option_id:
            self.wert_auswahl = self.wert_auswahl_option_id.name

    @api.model
    def _wb_voip_erlaubte_felder(self, modell):
        """Return the allowed fields of ``modell`` for "set field" (field objects)."""
        if not self._wb_voip_modell_erlaubt(modell):
            return []
        Modell = self.env[modell]
        return [field for field in Modell._fields.values()
                if self._wb_voip_feld_erlaubt(Modell, field)]

    def _wb_voip_optionen_erzeugen(self):
        """Rebuild the field and selection value choices of step 3."""
        self.ensure_one()
        modell = self._wb_voip_modellname()
        self.option_ids.unlink()
        Option = self.env['wb.voip.aktion.assistent.option']
        werte = []
        for field in self._wb_voip_erlaubte_felder(modell):
            werte.append({'assistent_id': self.id, 'art': 'feld', 'name': field.name,
                          'label': f'{field._description_string(self.env)} ({field.name})'})
            if field.type == 'selection':
                for wert, label in field._description_selection(self.env):
                    werte.append({'assistent_id': self.id, 'art': 'auswahl', 'name': wert,
                                  'label': label, 'feld_name': field.name})
        optionen = Option.create(werte)
        feld = optionen.filtered(lambda o: o.art == 'feld' and o.name == self.feld_name)[:1]
        auswahl = optionen.filtered(lambda o: o.art == 'auswahl' and o.feld_name == self.feld_name
                                    and o.name == self.wert_auswahl)[:1]
        self.write({'feld_option_id': feld.id, 'wert_auswahl_option_id': auswahl.id})

    # --- Regeln für Modelle, Felder und Werte ---

    def _wb_voip_modellname(self):
        """Return the technical model name the action will run on (or False)."""
        self.ensure_one()
        if self.art == 'vorlage':
            vorlage = VORLAGEN.get(self.vorlage)
            return vorlage['modell'] if vorlage else False
        if self.ziel == 'kontakt':
            return 'res.partner'
        if self.ziel == 'anruf':
            return 'wb.voip.anruf' if self.art == 'code' else False
        return self.modell or False

    @api.model
    def _wb_voip_modell_erlaubt(self, modell, schreibbar=None):
        """Return True if the caller may target ``modell`` (regular, writable, not technical).

        ``schreibbar`` is the caller's set of writable models, if already known.
        """
        if not isinstance(modell, str) or modell not in self.env:
            return False
        Modell = self.env[modell]
        if Modell._abstract or Modell._transient or not Modell._auto:
            return False
        if modell.startswith(GESPERRTE_MODELLE):
            return False
        if schreibbar is not None:
            return modell in schreibbar
        return Modell.has_access('write')

    @api.model
    def _wb_voip_feld_erlaubt(self, Modell, field, vorlage=False):
        """Return True if the caller may let a button set ``field`` of ``Modell``.

        Stored, writable, neither related nor computed, of an allowed type, no
        credential or technical name, relation targets not technical, and the caller
        has write access to the field. Selection fields need a static list; both rules
        are relaxed for the delivered templates (fixed fields, values checked there).
        """
        if field is None or field.type not in FELD_TYPEN:
            return False
        if not field.store or field.readonly or field.related:
            return False
        # berechnete Felder (auch mit inverse/readonly=False) nur in mitgelieferten Vorlagen,
        # z. B. die Sprache, die Odoo vom übergeordneten Kontakt vorbelegt
        if field.compute and not vorlage:
            return False
        name = field.name
        if name in MAGISCHE_FELDER or name in _WB_VOIP_LAYOUT_SPERRFELDER \
                or any(teil in name.lower() for teil in FELD_NAMENTEILE):
            return False
        if field.type in ('many2one', 'many2many'):
            if not field.comodel_name or field.comodel_name not in self.env \
                    or field.comodel_name.startswith(GESPERRTE_ZIELE):
                return False
        if field.type == 'selection' and not vorlage and not isinstance(field.selection, list):
            return False
        return Modell._has_field_access(field, 'write')

    def _wb_voip_wert(self, field):
        """Return the server action values for the fixed value of ``field`` (checked)."""
        self.ensure_one()
        typ = field.type
        if typ == 'char':
            wert = self.wert_char or ''
            if len(wert) > CHAR_MAX:
                raise UserError(self.env._("Der Wert ist zu lang."))
            return {'value': wert}
        if typ == 'text':
            wert = self.wert_text or ''
            if len(wert) > TEXT_MAX:
                raise UserError(self.env._("Der Text ist zu lang."))
            return {'value': wert}
        if typ == 'integer':
            return {'value': str(int(self.wert_integer))}
        if typ == 'float':
            return {'value': repr(float(self.wert_float))}
        if typ == 'boolean':
            return {'update_boolean_value': 'true' if self.wert_boolean else 'false'}
        if typ == 'date':
            return {'value': fields.Date.to_string(self.wert_date) if self.wert_date else False}
        if typ == 'selection':
            werte = [wert for wert, _label in field._description_selection(self.env)]
            if not self.wert_auswahl or self.wert_auswahl not in werte:
                raise UserError(self.env._("Bitte einen Wert der Auswahl wählen."))
            return {'value': self.wert_auswahl}
        # many2one / many2many: ein Datensatz des Zielmodells, den der Benutzer lesen darf
        res_id = self.wert_res_id
        datensatz = self.env[field.comodel_name].browse(res_id) \
            if type(res_id) is int and res_id > 0 else None
        return self._wb_voip_datensatz_wert(field, datensatz)

    def _wb_voip_datensatz_wert(self, field, datensatz):
        """Return the values for a relation value ``datensatz`` (must be readable by the caller)."""
        if not datensatz or datensatz._name != field.comodel_name:
            raise UserError(self.env._("Bitte einen Datensatz aus „%s“ wählen.",
                                       field._description_string(self.env)))
        datensatz = datensatz.with_env(self.env).exists()
        if not datensatz or not datensatz.has_access('read'):
            raise UserError(self.env._("Bitte einen Datensatz wählen, den Sie lesen dürfen."))
        werte = {'value': str(datensatz.id)}
        if field.type == 'many2many':
            werte['update_m2m_operation'] = 'add'
        return werte

    def _wb_voip_button_pruefen(self):
        """Raise ``UserError`` unless the button data of step 1 is complete and valid."""
        self.ensure_one()
        name = (self.name or '').strip()
        if not name or len(name) > NAME_MAX:
            raise UserError(self.env._("Bitte eine Bezeichnung (höchstens %s Zeichen) angeben.",
                                       NAME_MAX))
        if self.icon and not ICON_MUSTER.fullmatch(self.icon):
            raise UserError(self.env._(
                "Das Symbol muss eine Font-Awesome-Klasse sein, z. B. fa-user."))
        if not (self.ort_anrufer_fenster or self.ort_protokoll_popup or self.ort_softphone):
            raise UserError(self.env._("Bitte mindestens einen Ort wählen."))

    def _wb_voip_bestehend_pruefen(self, aktion):
        """Raise unless the caller may rework the existing button ``aktion`` here."""
        aktion.check_access('write')
        if not aktion.sudo().wb_voip_assistent:
            raise UserError(self.env._("Dieser Button wurde nicht mit dem Assistenten angelegt."))
        if aktion.sudo().server_action_id.state == 'code' \
                and not self.env.user.has_group(GRUPPE_SYSTEM):
            raise AccessError(self.env._("Python-Code dürfen nur Systemadministratoren bearbeiten."))

    def _wb_voip_serveraktion_werte(self):
        """Check step 2 and return the values of the server action to create (without group)."""
        self.ensure_one()
        if self.art == 'code':
            # serverseitig, unabhängig von der Auswahlliste
            if not self.env.user.has_group(GRUPPE_SYSTEM):
                raise AccessError(self.env._("Python-Code dürfen nur Systemadministratoren anlegen."))
            modell = self._wb_voip_modellname()
            if modell != 'wb.voip.anruf' and not self._wb_voip_modell_erlaubt(modell):
                raise UserError(self.env._("Dieses Modell ist nicht erlaubt."))
            code = self.sudo().code or ''
            if not code.strip() or len(code) > CODE_MAX:
                raise UserError(self.env._("Bitte Python-Code (höchstens %s Zeichen) angeben.",
                                           CODE_MAX))
            fehler = test_python_expr(expr=code.strip(), mode='exec')
            if fehler:
                raise UserError(fehler)
            return modell, {'state': 'code', 'code': code}
        if self.art == 'vorlage':
            vorlage = VORLAGEN.get(self.vorlage)
            if not vorlage:
                raise UserError(self.env._("Bitte eine Vorlage wählen."))
            modell = vorlage['modell']
            Modell = self.env[modell]
            field = Modell._fields.get(vorlage['feld'])
            if not self._wb_voip_modell_erlaubt(modell) \
                    or not self._wb_voip_feld_erlaubt(Modell, field, vorlage=True):
                raise UserError(self.env._("Sie dürfen dieses Feld nicht ändern."))
            if vorlage['wert'] == 'wert_kategorie_id':
                werte = self._wb_voip_datensatz_wert(field, self.wert_kategorie_id)
            else:
                sprachen = [code for code, _name in self._selection_sprache()]
                if not self.wert_sprache or self.wert_sprache not in sprachen:
                    raise UserError(self.env._("Bitte eine Sprache wählen."))
                werte = {'value': self.wert_sprache}
            return modell, dict(werte, state='object_write', update_path=field.name,
                                evaluation_type='value')
        if self.art != 'feld':
            raise UserError(self.env._("Bitte wählen, was passieren soll."))
        if self.ziel not in ('kontakt', 'datensatz'):
            raise UserError(self.env._("Bitte als Ziel Kontakt oder Datensatz wählen."))
        modell = self._wb_voip_modellname()
        if not self._wb_voip_modell_erlaubt(modell):
            raise UserError(self.env._("Dieses Modell ist nicht erlaubt."))
        Modell = self.env[modell]
        field = Modell._fields.get(self.feld_name) if isinstance(self.feld_name, str) else None
        if not self._wb_voip_feld_erlaubt(Modell, field):
            raise UserError(self.env._("Dieses Feld darf ein Button nicht setzen."))
        werte = self._wb_voip_wert(field)
        return modell, dict(werte, state='object_write', update_path=field.name,
                            evaluation_type='value')

    # --- Laden zum Bearbeiten ---

    @api.model
    def default_get(self, fields_list):
        werte = super().default_get(fields_list)
        if werte.get('aktion_id'):
            aktion = self.env['wb.voip.aktion'].browse(werte['aktion_id']).exists()
            if not aktion:
                raise UserError(self.env._("Der Button existiert nicht mehr."))
            self._wb_voip_bestehend_pruefen(aktion)
            geladen = self._wb_voip_aus_button(aktion)
            werte.update({k: v for k, v in geladen.items() if k in fields_list})
        return werte

    @api.model
    def _wb_voip_aus_button(self, aktion):
        """Return wizard values describing the existing wizard button ``aktion``."""
        aktion = aktion.sudo()
        server = aktion.server_action_id
        werte = {
            'schritt': 'button',
            'name': aktion.name, 'icon': aktion.icon or False, 'farbe': aktion.farbe,
            'ort_anrufer_fenster': aktion.ort_anrufer_fenster,
            'ort_protokoll_popup': aktion.ort_protokoll_popup,
            'ort_softphone': aktion.ort_softphone,
            'richtung': aktion.richtung, 'zustand': aktion.zustand,
            'group_ids': [Command.set(aktion.group_ids.ids)],
        }
        modell = server.model_id.model
        if modell == 'res.partner':
            werte['ziel'] = 'kontakt'
        elif modell == 'wb.voip.anruf':
            werte['ziel'] = 'anruf'
        else:
            werte.update(ziel='datensatz', modell=modell)
        if server.state == 'code':
            werte.update(art='code', code=server.code or '')
            return werte
        for schluessel, vorlage in VORLAGEN.items():
            if vorlage['modell'] == modell and vorlage['feld'] == server.update_path:
                werte.update(art='vorlage', vorlage=schluessel)
                if vorlage['wert'] == 'wert_kategorie_id':
                    werte['wert_kategorie_id'] = int(server.value or 0) or False
                else:
                    werte['wert_sprache'] = server.value or False
                return werte
        feld = server.update_field_id
        werte.update(art='feld', feld_name=feld.name)
        typ = feld.ttype
        if typ in ('char', 'text'):
            werte[f'wert_{typ}'] = server.value or False
        elif typ == 'integer':
            werte['wert_integer'] = int(server.value or 0)
        elif typ == 'float':
            werte['wert_float'] = float(server.value or 0)
        elif typ == 'boolean':
            werte['wert_boolean'] = server.update_boolean_value == 'true'
        elif typ == 'date':
            werte['wert_date'] = server.value or False
        elif typ == 'selection':
            werte['wert_auswahl'] = server.value or False
        elif typ in ('many2one', 'many2many') and server.value:
            werte['wert_res_id'] = int(server.value)
        return werte

    # --- Schritte ---

    def _wb_voip_wieder_oeffnen(self):
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Button-Assistent"),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
        }

    @nur_voip_manager
    def action_weiter(self):
        """Check the current step and go to the next one (step 3 only for "set field")."""
        self.ensure_one()
        if self.schritt == 'button':
            self._wb_voip_button_pruefen()
            self.schritt = 'aktion'
        elif self.schritt == 'aktion':
            if self.art != 'feld':
                raise UserError(self.env._("Diese Art wird direkt gespeichert."))
            if self.ziel not in ('kontakt', 'datensatz'):
                raise UserError(self.env._("Bitte als Ziel Kontakt oder Datensatz wählen."))
            if not self._wb_voip_modell_erlaubt(self._wb_voip_modellname()):
                raise UserError(self.env._("Bitte ein erlaubtes Modell wählen."))
            self._wb_voip_optionen_erzeugen()
            self.schritt = 'feld'
        return self._wb_voip_wieder_oeffnen()

    @nur_voip_manager
    def action_zurueck(self):
        """Go back one step."""
        self.ensure_one()
        self.schritt = 'aktion' if self.schritt == 'feld' else 'button'
        return self._wb_voip_wieder_oeffnen()

    @nur_voip_manager
    def action_anlegen(self):
        """Create (or rework) the button and its server action after all checks.

        Everything is checked as the caller; only then the server action is created with
        ``sudo()`` (managers have no rights on ``ir.actions.server``). It gets the
        technical group (nobody can run it outside WB VoIP) and no binding.
        """
        self.ensure_one()
        # keine default_*-Schlüssel aus dem Kontext an die angelegten Datensätze weitergeben
        selbst = self.with_context({k: v for k, v in self.env.context.items()
                                    if not k.startswith('default_')})
        if selbst.aktion_id:
            selbst._wb_voip_bestehend_pruefen(selbst.aktion_id)
        else:
            selbst.env['wb.voip.aktion'].check_access('create')
        selbst._wb_voip_button_pruefen()
        modell, server_werte = selbst._wb_voip_serveraktion_werte()
        gruppe = selbst.env.ref(GRUPPE_BUTTON_AKTION)
        name = selbst.name.strip()
        server_werte.update({
            'name': f'WB VoIP: {name}',
            'model_id': selbst.env['ir.model']._get_id(modell),
            'group_ids': [Command.set(gruppe.ids)],
            'binding_model_id': False,
        })
        server = selbst.env['ir.actions.server'].sudo().create(server_werte)
        # Gegenprobe mit denselben Regeln wie zur Laufzeit
        if not serveraktion_erlaubt(server) or not assistenten_aktion(server) or server.warning \
                or (server.state == 'object_write'
                    and server.update_field_id.name != server_werte['update_path']):
            raise UserError(selbst.env._("Die Serveraktion konnte nicht sicher angelegt werden."))
        button_werte = {
            'name': name, 'icon': selbst.icon or False, 'farbe': selbst.farbe,
            'ort_anrufer_fenster': selbst.ort_anrufer_fenster,
            'ort_protokoll_popup': selbst.ort_protokoll_popup,
            'ort_softphone': selbst.ort_softphone,
            'richtung': selbst.richtung, 'zustand': selbst.zustand,
            'group_ids': [Command.set(selbst.group_ids.ids)],
        }
        Aktion = selbst.env['wb.voip.aktion']
        if selbst.aktion_id:
            aktion = selbst.aktion_id
            alt = aktion.sudo().server_action_id
            aktion.write(button_werte)
            aktion.sudo().write({'art': 'serveraktion', 'server_action_id': server.id})
            Aktion._wb_voip_serveraktionen_aufraeumen(alt)
        else:
            aktion = Aktion.create(dict(button_werte, art='serveraktion',
                                        server_action_id=server.id))
            aktion.sudo().write({'wb_voip_assistent': True})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'wb.voip.aktion',
            'res_id': aktion.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }
