import ast
import re
from urllib.parse import urlsplit

from odoo import api, fields, models
from odoo.fields import Domain
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools import serverfelder
from ..tools.rechte import GRUPPE_SYSTEM, nur_voip_manager

ICON_MUSTER = re.compile(r'^fa-[a-z0-9-]{1,40}$')
URL_MAX = 500
URL_PLATZHALTER = ('nummer', 'nummer_e164', 'partner_id')
URL_PLATZHALTER_MUSTER = re.compile(r'\{([^{}]*)\}')
# Odoo's standard condition operators (anything else is rejected)
DOMAIN_OPERATOREN = frozenset((
    '=', '!=', '<=', '<', '>', '>=', '=?', '=like', '=ilike', 'like', 'not like',
    'ilike', 'not ilike', 'in', 'not in', 'child_of', 'parent_of', 'any', 'not any',
))
DOMAIN_MAX = 2000
DOMAIN_TIEFE = 3
# Technische Gruppe der Serveraktionen aus dem Assistenten (hat niemand)
GRUPPE_BUTTON_AKTION = 'wb_voip.group_wb_voip_button_aktion'
# Felder, die nur der Server (Assistent) setzt
_NUR_SERVER = ('wb_voip_assistent',)
# An Buttons aus dem Assistenten nur über den Assistenten änderbar
_NUR_ASSISTENT = ('art', 'server_action_id')
# Wer einen Code-Button aus dem Assistenten sieht und wann (auch Archivieren und
# Reaktivieren): nur Systemadministratoren
_CODE_PUBLIKUM = ('active', 'group_ids', 'partner_domain', 'ort_anrufer_fenster',
                  'ort_protokoll_popup', 'ort_softphone', 'richtung', 'zustand')


def assistenten_aktion(server):
    """Return True if ``server`` carries exactly the technical group of the button wizard."""
    server = server.sudo()
    gruppe = server.env.ref(GRUPPE_BUTTON_AKTION, raise_if_not_found=False)
    return bool(server) and bool(gruppe) and server.group_ids == gruppe


def serveraktion_erlaubt(server):
    """Return True if a button may run this server action.

    Odoo 19 runs ``object_create``, ``object_copy``, ``multi`` children, webhooks and the
    mail types with superuser rights; only ``code`` (runs with the caller's env) and
    ``object_write`` on a field of the record itself (we write it as the caller) are allowed.
    """
    server = server.sudo()
    if not server:
        return False
    if server.state == 'code':
        return True
    if server.state == 'object_write':
        pfad = server.update_path or ''
        return bool(pfad) and '.' not in pfad and bool(server.update_field_id)
    return False


class WbVoipAktion(models.Model):
    """Customer-configurable call button (shown in the caller window, log popup, softphone)."""

    _name = 'wb.voip.aktion'
    _description = 'WB VoIP: Anruf-Button'
    _order = 'sequence, id'

    name = fields.Char(string='Name', required=True, translate=True)
    sequence = fields.Integer(string='Reihenfolge', default=10)
    active = fields.Boolean(string='Aktiv', default=True)
    icon = fields.Char(string='Symbol', help="Font-Awesome-Klasse, z. B. fa-user")
    farbe = fields.Selection(
        [('primary', 'Primär'), ('secondary', 'Sekundär'), ('success', 'Grün'),
         ('warning', 'Gelb'), ('danger', 'Rot')],
        string='Farbe', required=True, default='secondary')
    ort_anrufer_fenster = fields.Boolean(string='Im Anrufer-Fenster')
    ort_protokoll_popup = fields.Boolean(string='Im Protokoll-Popup')
    ort_softphone = fields.Boolean(string='Im Softphone')
    richtung = fields.Selection(
        [('beide', 'Beide'), ('eingehend', 'Eingehend'), ('ausgehend', 'Ausgehend')],
        string='Richtung', required=True, default='beide')
    zustand = fields.Selection(
        [('immer', 'Immer'), ('klingelt', 'Während es klingelt'),
         ('laeuft', 'Während des Gesprächs'), ('beendet', 'Nach dem Anruf')],
        string='Zustand', required=True, default='immer')
    # Ziele und Gruppen nur für Administratoren lesbar; die Laufzeit liest sie nach den
    # Prüfungen per sudo
    group_ids = fields.Many2many(
        'res.groups', 'wb_voip_aktion_res_groups_rel', 'aktion_id', 'group_id',
        string='Gruppen', groups='wb_voip.group_wb_voip_manager',
        help="Leer = alle Benutzer von WB VoIP.")
    partner_domain = fields.Char(
        string='Bedingung für Kontakte', groups='wb_voip.group_wb_voip_manager',
        help="Domain auf Kontakte, z. B. [('is_company', '=', True)]. Leer = immer; "
             "ohne Kontakt am Anruf passt eine Bedingung nie.")
    art = fields.Selection(
        [('fenster', 'Fenster öffnen'), ('serveraktion', 'Serveraktion'),
         ('url', 'Webadresse öffnen'), ('neuer_kontakt', 'Neuer Kontakt')],
        string='Art', required=True, default='fenster')
    act_window_id = fields.Many2one(
        'ir.actions.act_window', string='Fensteraktion', ondelete='cascade',
        groups='wb_voip.group_wb_voip_manager')
    server_action_id = fields.Many2one(
        'ir.actions.server', string='Serveraktion', ondelete='cascade',
        groups='wb_voip.group_wb_voip_manager')
    url_vorlage = fields.Char(
        string='Webadresse', groups='wb_voip.group_wb_voip_manager',
        help="Nur https://. Platzhalter: {nummer}, {nummer_e164}, {partner_id}.")
    # Nur der Assistent setzt das Kennzeichen (nach seinen Prüfungen); lesbar ist es für alle,
    # geschrieben wird es nur vom Server (siehe create/write)
    wb_voip_assistent = fields.Boolean(
        string='Mit Assistent angelegt', readonly=True, copy=False)

    # --- Schutz der Assistenten-Daten ---

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                serverfelder.pruefen(self.env, vals, _NUR_SERVER)
            self = self.with_context(serverfelder.kontext_ohne_defaults(self.env, _NUR_SERVER))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            serverfelder.pruefen(self.env, vals, _NUR_SERVER)
            if set(vals) & set(_NUR_ASSISTENT) and self.sudo().filtered('wb_voip_assistent'):
                raise UserError(self.env._(
                    "Die Aktion dieses Buttons wurde mit dem Assistenten angelegt. "
                    "Bitte mit „Mit Assistent bearbeiten“ ändern."))
            if set(vals) & set(_CODE_PUBLIKUM) and not self.env.user.has_group(GRUPPE_SYSTEM) \
                    and self._wb_voip_code_buttons():
                raise AccessError(self.env._(
                    "Bei Buttons mit Python-Code dürfen nur Systemadministratoren ändern, "
                    "wer den Button sieht und wann."))
        return super().write(vals)

    def _wb_voip_code_buttons(self):
        """Return the wizard-made buttons in ``self`` that run Python code (as superuser)."""
        return self.sudo().with_context(active_test=False).filtered(
            lambda a: a.wb_voip_assistent and a.server_action_id.state == 'code')

    def copy(self, default=None):
        # Eine Kopie teilte die Serveraktion, ohne Kennzeichen und ohne Schutz des Assistenten
        if self.sudo().filtered('wb_voip_assistent'):
            raise UserError(self.env._("Bitte mit dem Assistenten neu anlegen."))
        return super().copy(default)

    def unlink(self):
        if not self.env.su and not self.env.user.has_group(GRUPPE_SYSTEM) \
                and self._wb_voip_code_buttons():
            raise AccessError(self.env._(
                "Buttons mit Python-Code dürfen nur Systemadministratoren löschen."))
        # Serveraktionen aus dem Assistenten merken; gelöscht wird erst nach dem Löschen der
        # Buttons (das prüft die Rechte) und nur, wenn kein anderer Button sie nutzt
        server = self.sudo().server_action_id.filtered(assistenten_aktion)
        ergebnis = super().unlink()
        self.env['wb.voip.aktion']._wb_voip_serveraktionen_aufraeumen(server)
        return ergebnis

    @api.model
    def _wb_voip_serveraktionen_aufraeumen(self, server):
        """Delete the wizard's server actions in ``server`` that no button uses any more."""
        for einzeln in server.sudo().exists():
            if not assistenten_aktion(einzeln):
                continue
            genutzt = self.sudo().with_context(active_test=False).search_count(
                [('server_action_id', '=', einzeln.id)], limit=1)
            if not genutzt:
                einzeln.unlink()

    @nur_voip_manager
    def action_wb_voip_assistent_bearbeiten(self):
        """Open the button wizard on this wizard-made button (Python code: system admins only)."""
        self.ensure_one()
        self.check_access('write')
        aktion = self.sudo()
        if not aktion.wb_voip_assistent:
            raise UserError(self.env._("Dieser Button wurde nicht mit dem Assistenten angelegt."))
        if aktion.server_action_id.state == 'code' and not self.env.user.has_group(GRUPPE_SYSTEM):
            raise AccessError(self.env._("Python-Code dürfen nur Systemadministratoren bearbeiten."))
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Button mit Assistent bearbeiten"),
            'res_model': 'wb.voip.aktion.assistent',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_aktion_id': self.id},
        }

    # --- Prüfungen ---

    @api.constrains('art', 'act_window_id', 'server_action_id', 'url_vorlage')
    def _check_art(self):
        for aktion in self:
            if aktion.art == 'fenster' and not aktion.act_window_id:
                raise ValidationError(self.env._("Bitte eine Fensteraktion wählen."))
            if aktion.art == 'serveraktion' and not aktion.server_action_id:
                raise ValidationError(self.env._("Bitte eine Serveraktion wählen."))
            if aktion.art == 'url' and not aktion.url_vorlage:
                raise ValidationError(self.env._("Bitte eine Webadresse angeben."))

    @api.constrains('art', 'server_action_id')
    def _check_serveraktion(self):
        for aktion in self:
            if aktion.art == 'serveraktion' and aktion.server_action_id \
                    and not serveraktion_erlaubt(aktion.server_action_id):
                raise ValidationError(self.env._(
                    "Diese Art von Serveraktion ist für Anruf-Buttons nicht erlaubt."))

    @api.constrains('icon')
    def _check_icon(self):
        for aktion in self:
            if aktion.icon and not ICON_MUSTER.fullmatch(aktion.icon):
                raise ValidationError(self.env._(
                    "Das Symbol muss eine Font-Awesome-Klasse sein, z. B. fa-user."))

    @api.constrains('ort_anrufer_fenster', 'ort_protokoll_popup', 'ort_softphone')
    def _check_ort(self):
        for aktion in self:
            if not (aktion.ort_anrufer_fenster or aktion.ort_protokoll_popup or aktion.ort_softphone):
                raise ValidationError(self.env._("Bitte mindestens einen Ort wählen."))

    @api.constrains('url_vorlage')
    def _check_url_vorlage(self):
        for aktion in self:
            url = aktion.url_vorlage
            if not url:
                continue
            if (len(url) > URL_MAX or not url.startswith('https://')
                    or any(p not in URL_PLATZHALTER for p in URL_PLATZHALTER_MUSTER.findall(url))
                    or '{' in URL_PLATZHALTER_MUSTER.sub('', url)
                    or '}' in URL_PLATZHALTER_MUSTER.sub('', url)
                    or not self._wb_voip_url_ok(url)):
                raise ValidationError(self.env._(
                    "Die Webadresse muss mit https:// beginnen, höchstens %(max)s Zeichen lang sein "
                    "und darf nur {nummer}, {nummer_e164} und {partner_id} als Platzhalter enthalten.",
                    max=URL_MAX))

    @staticmethod
    def _wb_voip_url_ok(url):
        """Return True for an https URL without whitespace/control chars and with a fixed host."""
        if any(ord(c) <= 32 or ord(c) == 127 for c in url):
            return False
        try:
            teile = urlsplit(url)
        except ValueError:
            return False
        return teile.scheme == 'https' and bool(teile.netloc) and '{' not in teile.netloc

    @api.constrains('partner_domain')
    def _check_partner_domain(self):
        for aktion in self:
            if aktion.partner_domain and aktion._wb_voip_domain() is None:
                raise ValidationError(self.env._("Ungültige Bedingung für Kontakte."))

    def _wb_voip_domain(self):
        """Parse and validate ``partner_domain``; return a ``Domain`` (or None if invalid).

        Never evaluates code: ``ast.literal_eval`` only, then every field path is
        checked against ``res.partner``.
        """
        self.ensure_one()
        text = (self.partner_domain or '').strip()
        if not text:
            return Domain.TRUE
        if len(text) > DOMAIN_MAX:
            return None
        try:
            wert = ast.literal_eval(text)
            if not isinstance(wert, list):
                return None
            domain = Domain(wert)
            # constraints run as sudo: check with the rights of the acting user
            akteur = self.with_user(self.env.uid).env
            if not self._wb_voip_domain_pruefen(domain, akteur['res.partner'], 0):
                return None
        except Exception:  # noqa: BLE001 - any parsing problem means "invalid"
            return None
        return domain

    def _wb_voip_domain_pruefen(self, domain, modell, tiefe):
        """Return True if all conditions use known, searchable fields and standard operators."""
        if tiefe > DOMAIN_TIEFE:
            return False
        for bedingung in domain.iter_conditions():
            if bedingung.operator not in DOMAIN_OPERATOREN:
                return False
            aktuell = modell
            pfad = bedingung.field_expr.split('.')
            for i, name in enumerate(pfad):
                feld = aktuell._fields.get(name.split(':')[0])
                if feld is None or not (feld.store or feld.search):
                    return False
                # the author may only use what he can read himself
                if not aktuell.has_access('read') or not aktuell._has_field_access(feld, 'read'):
                    return False
                if i < len(pfad) - 1:
                    if not feld.comodel_name:
                        return False
                    aktuell = modell.env[feld.comodel_name]
            if bedingung.operator in ('any', 'not any'):
                feld = aktuell._fields.get(pfad[-1].split(':')[0])
                unter = bedingung.value
                if isinstance(unter, (list, tuple)):
                    unter = Domain(unter)
                if not feld.comodel_name or not isinstance(unter, Domain):
                    return False
                if not self._wb_voip_domain_pruefen(
                        unter, modell.env[feld.comodel_name], tiefe + 1):
                    return False
        return True

    # --- Auswahl und Client-Daten ---

    def _wb_voip_passend(self, anruf):
        """Return True if this button applies to the call ``anruf`` (for its user)."""
        self.ensure_one()
        if self.richtung != 'beide' and self.richtung != anruf.richtung:
            return False
        if self.zustand == 'beendet':
            if anruf.zustand not in ('beendet', 'abgelehnt', 'besetzt', 'fehlgeschlagen'):
                return False
        elif self.zustand != 'immer' and self.zustand != anruf.zustand:
            return False
        viewer = self.env.user
        if anruf.user_id != viewer:
            return False
        # Gruppen sind nur für Administratoren lesbar: nach der Eigentümerprüfung per sudo lesen
        gruppen = self.sudo().group_ids
        if gruppen and not (gruppen & viewer.all_group_ids):
            return False
        # Serveraktionen können sich später ändern: hier erneut prüfen
        if self.art == 'serveraktion' and not serveraktion_erlaubt(self.sudo().server_action_id):
            return False
        if self.sudo().partner_domain and self.sudo().partner_domain.strip():
            partner = anruf.partner_id
            if not partner:
                return False
            # Evaluate the stored condition as the viewer, never with sudo
            domain = self.sudo()._wb_voip_domain()
            if domain is None:
                return False
            try:
                partner = partner.with_user(viewer)
                if not partner.has_access('read'):
                    return False
                return bool(partner.filtered_domain(domain))
            except AccessError:
                return False
        return True

    def _wb_voip_fuer_client(self):
        """Return the data the browser needs to draw this button (no secrets)."""
        self.ensure_one()
        orte = []
        if self.ort_anrufer_fenster:
            orte.append('anrufer_fenster')
        if self.ort_protokoll_popup:
            orte.append('protokoll_popup')
        if self.ort_softphone:
            orte.append('softphone')
        return {
            'id': self.id,
            'name': self.name,
            'icon': self.icon or False,
            'farbe': self.farbe,
            # nur die Art, keine Ziele (Fensteraktion, Serveraktion, Webadresse)
            'art': self.art,
            'orte': orte,
        }
