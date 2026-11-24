import ast
import re
from urllib.parse import urlsplit

from odoo import api, fields, models
from odoo.fields import Domain
from odoo.exceptions import AccessError, ValidationError

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
    group_ids = fields.Many2many(
        'res.groups', 'wb_voip_aktion_res_groups_rel', 'aktion_id', 'group_id',
        string='Gruppen', help="Leer = alle Benutzer von WB VoIP.")
    partner_domain = fields.Char(
        string='Bedingung für Kontakte', groups='wb_voip.group_wb_voip_manager',
        help="Domain auf Kontakte, z. B. [('is_company', '=', True)]. Leer = immer; "
             "ohne Kontakt am Anruf passt eine Bedingung nie.")
    art = fields.Selection(
        [('fenster', 'Fenster öffnen'), ('serveraktion', 'Serveraktion'),
         ('url', 'Webadresse öffnen'), ('neuer_kontakt', 'Neuer Kontakt')],
        string='Art', required=True, default='fenster')
    act_window_id = fields.Many2one('ir.actions.act_window', string='Fensteraktion', ondelete='cascade')
    server_action_id = fields.Many2one('ir.actions.server', string='Serveraktion', ondelete='cascade')
    url_vorlage = fields.Char(
        string='Webadresse', groups='wb_voip.group_wb_voip_manager',
        help="Nur https://. Platzhalter: {nummer}, {nummer_e164}, {partner_id}.")

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
        if self.group_ids and not (self.group_ids & viewer.all_group_ids):
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
            # nur die Art (z. B. „neuer_kontakt“ im Anrufer-Fenster ausblenden), keine Ziele
            'art': self.art,
            'orte': orte,
        }
