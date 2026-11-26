import hashlib
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import html2plaintext, plaintext2html

from odoo.addons.wb_voip.models.wb_voip_anruf import (
    GLEICHZEITIG_SEKUNDEN, MAX_NOTIZ, MAX_NUMMER, _text_pruefen)
from odoo.addons.wb_voip.tools.nummer import e164
from odoo.addons.wb_voip.tools.rechte import nur_voip_benutzer

from .ansitel_api import WEITERLEITUNG

_logger = logging.getLogger(__name__)

# Unterdrückte Rufnummer in der Anrufliste
OHNE_NUMMER = ('anonymous', 'unknown', 'restricted')
# Vorläufige Zustände: der Abgleich darf sie endgültig machen
VORLAEUFIG = ('neu', 'klingelt')
# Höchstlängen der Angaben aus der Anrufliste (fremde Daten)
MAX_LINKEDID = 128
MAX_GERAET = 128


def _ganzzahl(wert):
    """Return a number of the call list (the PBX sends text); unreadable counts as 0."""
    try:
        return max(int(wert or 0), 0)
    except (TypeError, ValueError):
        return 0


def zweig_ende(zweig):
    """End of a CDR leg (UTC): start plus total duration.

    The PBX writes a leg only when it hangs up, so "group finished" depends on this end,
    not on ``calldate``.
    """
    return zweig['zeit_utc'] + timedelta(seconds=_ganzzahl(zweig.get('duration')))


def _annahmezeit(zweig):
    """Answer time of a leg (UTC): start plus ringing time (``duration`` − ``billsec``)."""
    klingeln = max(_ganzzahl(zweig.get('duration')) - _ganzzahl(zweig.get('billsec')), 0)
    return zweig['zeit_utc'] + timedelta(seconds=klingeln)


class WbVoipAnruf(models.Model):
    _inherit = 'wb.voip.anruf'

    wb_voip_cdr_linkedid = fields.Char(
        string='Call in the PBX (linkedid)', copy=False, readonly=True, index='btree_not_null',
        help="Identifier of the call in the PBX call list; at most one call per user.")
    wb_voip_herkunft = fields.Selection(
        [('softphone', 'Softphone'), ('anlage', 'PBX call list')], string='Origin',
        default='softphone', readonly=True,
        help='"PBX call list": Odoo did not see the call (e.g. it was closed); '
             'the reconciliation created it.')
    wb_voip_geraet = fields.Char(string='Answered on (device)', copy=False, readonly=True)
    # Hash der zuletzt erzeugten Protokollbeschreibung: weicht sie ab, hat jemand sie von Hand geändert
    wb_voip_bemerkung_hash = fields.Char(string='Remark hash', copy=False, readonly=True)

    _wb_voip_cdr_linkedid_je_benutzer = models.Constraint(
        'unique(user_id, wb_voip_cdr_linkedid)',
        'This PBX call (linkedid) already exists for the user.')

    @api.model
    def _wb_voip_nur_server(self):
        return super()._wb_voip_nur_server() + (
            'wb_voip_cdr_linkedid', 'wb_voip_herkunft', 'wb_voip_geraet', 'wb_voip_bemerkung_hash')

    @api.model
    def _wb_voip_wieder_oeffnen_zustaende(self):
        # Ein per Rückruf erledigter Eintrag, den der Abgleich später „anderswo“ gesetzt hat,
        # öffnet sich bei geändertem Rückruf-Ergebnis wieder (ohne neue Rückruf-Aktivität)
        return tuple(super()._wb_voip_wieder_oeffnen_zustaende()) + ('anderswo',)

    # --- Erweiterungen der Verpasst-Liste ---

    @api.model
    def _wb_voip_anderswo_sichtbar_domain(self):
        # Auch ohne Kollegen sichtbar, wenn die Anlage das annehmende Gerät nennt
        return super()._wb_voip_anderswo_sichtbar_domain() | Domain('wb_voip_geraet', '!=', False)

    def _wb_voip_mit_geraet(self):
        """True for an ``anderswo`` call that the PBX answered on a named device."""
        self.ensure_one()
        anruf = self.sudo()
        return anruf.zustand == 'anderswo' and bool(anruf.wb_voip_geraet)

    def _wb_voip_bemerkung_moeglich(self):
        """True if the owner may add a remark: answered on a device, not by a colleague."""
        self.ensure_one()
        return self._wb_voip_mit_geraet() and not self.sudo().angenommen_anruf_id

    def _wb_voip_eigenes_protokoll(self):
        """Return (sudo) the call's own log entry, or empty if missing or re-linked."""
        anruf = self.sudo()
        protokoll = anruf.protokoll_id.exists()
        if protokoll and protokoll.anruf_id == anruf and protokoll.user_id == anruf.user_id:
            return protokoll
        return protokoll.browse()

    def _wb_voip_verpasst_zeile(self):
        zeile = super()._wb_voip_verpasst_zeile()
        neuester = self[0].sudo()
        # Gerät nur ohne Kollegen: sonst nennt ``kollege`` Name und Gerät
        moeglich = neuester._wb_voip_bemerkung_moeglich()
        zeile.update({
            'geraet': neuester.wb_voip_geraet if moeglich else False,
            'geraet_dauer_sek': neuester.dauer_sek if moeglich else False,
            'bemerkung_moeglich': moeglich,
            'bemerkung': (neuester.notiz or False)
            if moeglich and neuester._wb_voip_eigenes_protokoll() else False,
        })
        return zeile

    def _wb_voip_angenommen_info(self):
        info = super()._wb_voip_angenommen_info()
        # nur mit Gerät: ohne Abgleich bleibt die Angabe des Kerns unverändert
        geraet = info and self.sudo().angenommen_anruf_id.wb_voip_geraet
        if geraet:
            info['geraet'] = geraet
        return info

    def _wb_voip_protokoll_werte(self):
        werte = super()._wb_voip_protokoll_werte()
        anruf = self.sudo()
        if anruf._wb_voip_mit_geraet():
            # Auf einem Gerät angenommen: erreicht, mit Gerät und Bemerkung
            werte['ergebnis'] = 'erreicht'
            werte['beschreibung'] = plaintext2html(anruf._wb_voip_bemerkung_text())
        return werte

    def _wb_voip_bemerkung_text(self):
        """Return the log description of a call answered on a device: device plus remark."""
        self.ensure_one()
        anruf = self.sudo()
        text = self.env._("Answered on %s.", anruf.wb_voip_geraet)
        return f"{text}\n{anruf.notiz}" if anruf.notiz else text

    @staticmethod
    def _wb_voip_beschreibung_hash(beschreibung):
        return hashlib.sha256(str(beschreibung or '').encode()).hexdigest()

    def _wb_voip_bemerkung_merken(self, protokoll):
        """Remember the hash of the description just generated (as stored, after sanitising)."""
        protokoll.invalidate_recordset(['beschreibung'])
        self.sudo().wb_voip_bemerkung_hash = self._wb_voip_beschreibung_hash(protokoll.beschreibung)

    @nur_voip_benutzer
    def wb_voip_bemerkung_speichern(self, text):
        """Save a remark on the caller's own call answered on a device (call list of the PBX).

        The first remark creates the log entry "reached" (device and duration from the PBX);
        later ones only update its description. A description changed by hand gets the new
        text appended instead. Empty text without log: nothing happens (``False``); empty text
        with log: the remark is removed, the log stays with the device. Works without a
        licence (like closing missed entries). Returns ``True`` if something was saved.
        """
        _text_pruefen(self.env, text, self.env._("Remark"), MAX_NOTIZ, pflicht=False)
        anruf = self._wb_voip_eigener()
        if not anruf._wb_voip_bemerkung_moeglich():
            raise UserError(self.env._("No remark can be saved for this call."))
        text = (text or '').strip()
        protokoll = anruf._wb_voip_eigenes_protokoll()
        if anruf.protokoll_id and not protokoll:
            # gelöscht oder umgehängt: neu anlegen statt still nur die Notiz zu speichern
            anruf.protokoll_id = False
        if not text and not protokoll:
            return False
        anruf.notiz = text or False
        if protokoll:
            aktuell = protokoll.beschreibung or ''
            unveraendert = not html2plaintext(aktuell).strip() \
                or self._wb_voip_beschreibung_hash(aktuell) == anruf.wb_voip_bemerkung_hash
            werte = {}
            if unveraendert:
                werte['beschreibung'] = plaintext2html(anruf._wb_voip_bemerkung_text())
            elif text:
                # von Hand geändert: anhängen, das Vorhandene bleibt wie es ist
                werte['beschreibung'] = Markup(aktuell) + plaintext2html(text)
            if protokoll.ergebnis != 'erreicht':
                werte['ergebnis'] = 'erreicht'
            if werte:
                # als Besitzer (Verfolgung nennt ihn), sudo nach der Prüfung oben
                protokoll.with_user(anruf.user_id).sudo().write(werte)
            if unveraendert:
                anruf._wb_voip_bemerkung_merken(protokoll)
        else:
            anruf._wb_voip_bemerkung_merken(anruf._wb_voip_protokollieren())
        # Auch die Kollegen, die auf diesen Anruf zeigen (deren Notiz), neu laden lassen
        (anruf | anruf.search([('angenommen_anruf_id', '=', anruf.id)]))._wb_voip_verpasst_melden()
        return True

    # --- Abgleich mit der Anrufliste der Anlage (läuft im Cron, sudo) ---

    @api.model
    def _wb_voip_cdr_gruppe(self, anlage, zweige):
        """Align the calls of the involved users with one group of the PBX call list.

        ``zweige``: all legs of one ``linkedid``, at least one incoming. Idempotent: a second
        run with the same legs changes nothing. Who answered is decided by
        ``_wb_voip_cdr_annahme``; the owner of the answering device is handled first so that
        the others can link to his call. Returns True if a call changed or was created.
        """
        Users = self.env['res.users'].sudo()
        Geraet = self.env['wb.voip.geraet'].sudo()
        Ansitel = self.env['wb.voip.ansitel']
        zweige = sorted(zweige, key=lambda z: (z['zeit_utc'], _ganzzahl(z.get('sequence'))))
        lid = str(zweige[0].get('linkedid') or '').strip()[:MAX_LINKEDID]
        zeit = zweige[0]['zeit_utc']
        # Rückstellstunde im Herbst: die Ortszeit gibt es zweimal, beide Zeiten probieren
        zeiten = [zeit, zweige[0]['zeit_utc_alt']] if zweige[0].get('zeit_utc_alt') else [zeit]
        ende = max(zweig_ende(z) for z in zweige)
        # eingehende Zweige zuerst, sonst in der Reihenfolge der Anlage (stabil sortiert)
        zweige = sorted(zweige, key=lambda z: z.get('direction') != 'incoming')
        src = str(zweige[0].get('src') or '').strip()[:MAX_NUMMER]
        if src.lower() in OHNE_NUMMER:
            src = ''

        geraete = {Ansitel._kanal_geraet(z.get('dstchannel')) for z in zweige} - {None, WEITERLEITUNG}
        ziele = {str(z.get('dst') or '').strip() for z in zweige} - {''}
        besitzer_der_geraete = Geraet.search([
            ('anlage_id', '=', anlage.id), ('peer_name', 'in', list(geraete)),
            ('user_id', '!=', False)]).user_id
        beteiligte = Users.search([
            ('wb_voip_anlage_id', '=', anlage.id),
            '|', '|', ('wb_voip_ansitel_nebenstelle', 'in', list(ziele)),
            ('wb_voip_sip_benutzer', 'in', list(geraete)),
            ('id', 'in', besitzer_der_geraete.ids),
        ], order='id').filtered(lambda u: u.has_group('wb_voip.group_wb_voip_user'))

        geraet, dauer, annahme = self._wb_voip_cdr_annahme(anlage, zweige)
        besitzer, geraet_name = Users, False
        if geraet == WEITERLEITUNG:
            geraet_name = self.env._("Call forwarding")
        elif geraet:
            eintrag = Geraet._fuer(anlage, geraet)
            geraet_name = (eintrag.name or geraet)[:MAX_GERAET]
            besitzer = eintrag.user_id
            if not besitzer:
                treffer = Users.search([
                    ('wb_voip_anlage_id', '=', anlage.id), ('wb_voip_sip_benutzer', '=', geraet)])
                besitzer = treffer if len(treffer) == 1 else Users

        anrufe = {}
        geaendert = self.sudo().browse()
        for user in (beteiligte & besitzer) + (beteiligte - besitzer):
            anruf = self._wb_voip_cdr_finden(anlage, user, lid, zeiten, src)
            if anruf.zustand == 'laeuft':
                continue  # läuft noch im Softphone: nie anfassen
            neu = not anruf
            if neu:
                anruf = self._wb_voip_cdr_anlegen(anlage, user, lid, zeit, src)
            elif not anruf.wb_voip_cdr_linkedid:
                anruf.wb_voip_cdr_linkedid = lid
            anrufe[user] = anruf
            kollegen_anruf = anrufe.get(besitzer) if besitzer and besitzer != user else None
            if anruf._wb_voip_cdr_ergebnis(user, geraet, geraet_name, dauer, kollegen_anruf,
                                           annahme, ende) or neu:
                geaendert |= anruf
        geaendert._wb_voip_verpasst_melden()
        # Angenommen (wo auch immer): offene Verpasst-Einträge der Nummer bei allen Benutzern
        # erledigen, wie bei der Annahme im Softphone. Nur bis zum Ende dieser Gruppe (der Abgleich
        # läuft verspätet) und nur, wenn dieser Lauf etwas geändert hat (ein zweiter Lauf bleibt
        # ohne Wirkung). Ohne Nummer nie.
        if geraet and geaendert:
            annehmer = anrufe.get(besitzer) if besitzer else None
            erreicht = annehmer or geaendert[0]
            if erreicht.nummer_e164:
                # ohne Besitzer (gemeinsames Gerät, Weiterleitung) neutral: kein Benutzername
                text = None if annehmer else self.env._("Answered on %s", geraet_name)
                erreicht._wb_voip_nummer_erreicht(bis=ende, text=text)
        return bool(geaendert)

    @api.model
    def _wb_voip_cdr_annahme(self, anlage, zweige):
        """Return ``(device, talk seconds, answer time)``; device ``WEITERLEITUNG`` or ``None``.

        Answer time (UTC) = ``calldate`` + (``duration`` − ``billsec``) of the answering leg.

        ``zweige``: incoming legs first.

        * Answered on a device: leg with ``ANSWERED`` and ``lastapp`` ``Dial`` on a device that
          is not the trunk.
        * Forwarding: a ``Local/...`` leg or an incoming leg on the trunk, and an outgoing leg
          on the trunk with ``ANSWERED`` and ``billsec`` > 0 in the same group.
        * Otherwise (voicemail, queue, ``Local`` without answered trunk leg): not answered.

        Trunk = device of an outgoing leg that is neither in the device table nor a SIP user of
        the PBX. Known limit: a mobile mailbox behind a forwarding answers from the PBX's point
        of view and counts as forwarding.
        """
        Ansitel = self.env['wb.voip.ansitel']

        def geraet_von(zweig):
            return Ansitel._kanal_geraet(zweig.get('dstchannel'))

        geraete = {geraet_von(z) for z in zweige} - {None, WEITERLEITUNG}
        bekannt = set(self.env['wb.voip.geraet'].sudo().with_context(active_test=False).search([
            ('anlage_id', '=', anlage.id), ('peer_name', 'in', list(geraete)),
        ]).mapped('peer_name')) | set(self.env['res.users'].sudo().with_context(active_test=False).search([
            ('wb_voip_anlage_id', '=', anlage.id), ('wb_voip_sip_benutzer', 'in', list(geraete)),
        ]).mapped('wb_voip_sip_benutzer'))
        amt = {geraet_von(z) for z in zweige if z.get('direction') != 'incoming'} \
            - {None, WEITERLEITUNG} - bekannt
        for zweig in zweige:
            geraet = geraet_von(zweig)
            if geraet and geraet != WEITERLEITUNG and geraet not in amt \
                    and zweig.get('disposition') == 'ANSWERED' and zweig.get('lastapp') == 'Dial':
                return geraet, _ganzzahl(zweig.get('billsec')), _annahmezeit(zweig)
        umgeleitet = any(
            geraet_von(z) == WEITERLEITUNG
            or (z.get('direction') == 'incoming' and geraet_von(z) in amt)
            for z in zweige)
        if umgeleitet:
            for zweig in zweige:
                if zweig.get('direction') != 'incoming' and geraet_von(zweig) in amt \
                        and zweig.get('disposition') == 'ANSWERED' \
                        and _ganzzahl(zweig.get('billsec')) > 0:
                    return WEITERLEITUNG, _ganzzahl(zweig.get('billsec')), _annahmezeit(zweig)
        return None, 0, None

    @api.model
    def _wb_voip_cdr_nummer(self, anlage, src):
        """Return ``(nummer, nummer_e164)`` of the caller (suppressed: both False)."""
        if not src:
            return False, False
        return src, e164(src, anlage.sudo().country_id.code or 'DE') or False

    @api.model
    def _wb_voip_cdr_finden(self, anlage, user, lid, zeiten, src):
        """Return (sudo) the user's call of this group: by ``linkedid``, else an incoming call
        without ``linkedid`` started within ``GLEICHZEITIG_SEKUNDEN`` of one of ``zeiten`` (UTC
        candidates of the start, two in the repeated autumn hour) with the same number
        (suppressed: only calls without number), nearest first."""
        Anruf = self.sudo()
        anruf = Anruf.search([('user_id', '=', user.id), ('wb_voip_cdr_linkedid', '=', lid)], limit=1)
        if anruf:
            return anruf
        fenster = timedelta(seconds=GLEICHZEITIG_SEKUNDEN)
        nummer, nummer_e164 = self._wb_voip_cdr_nummer(anlage, src)
        domain = [
            ('user_id', '=', user.id), ('richtung', '=', 'eingehend'),
            ('wb_voip_cdr_linkedid', '=', False),
            ('beginn', '>=', min(zeiten) - fenster), ('beginn', '<=', max(zeiten) + fenster),
        ]
        if nummer_e164:
            domain.append(('nummer_e164', '=', nummer_e164))
        elif nummer:
            domain += [('nummer_e164', '=', False), ('nummer', '=', nummer)]
        else:
            domain.append(('nummer_e164', '=', False))
        kandidaten = Anruf.search(domain, limit=50)

        def abstand(anruf):
            return min(abs(anruf.beginn - zeit) for zeit in zeiten)

        kandidaten = kandidaten.filtered(lambda a: abstand(a) <= fenster)
        return min(kandidaten, default=Anruf.browse(), key=lambda a: (abstand(a), a.id))

    @api.model
    def _wb_voip_cdr_anlegen(self, anlage, user, lid, zeit, src):
        """Create the call Odoo did not see (e.g. closed); final state follows in ``_ergebnis``."""
        nummer, nummer_e164 = self._wb_voip_cdr_nummer(anlage, src)
        # Zuordnung mit den Rechten und der Firma des Benutzers, wie beim Anruf im Softphone
        als_benutzer = self.with_user(user).with_company(user.company_id)
        ziel, partner_id = als_benutzer._wb_voip_ziel_und_kontakt(False, False, nummer_e164)
        return self.sudo().create({
            'user_id': user.id,
            'richtung': 'eingehend',
            'nummer': nummer,
            'nummer_e164': nummer_e164,
            # „neu“ ist kein offener Zustand: das Team sieht kein kurzes Klingeln
            'zustand': 'neu',
            'partner_id': partner_id,
            'res_model': ziel._name if ziel is not None else False,
            'res_id': ziel.id if ziel is not None else False,
            'beginn': zeit,
            'company_id': user.company_id.id,
            'wb_voip_herkunft': 'anlage',
            'wb_voip_cdr_linkedid': lid,
        })

    def _wb_voip_cdr_ergebnis(self, user, geraet, geraet_name, dauer, kollegen_anruf, annahme, ende):
        """Apply the result of the call list to this call; True if something changed.

        * Not answered anywhere: a ringing (or new) call becomes ``verpasst`` with log and
          callback activity (Phase-4 logic), anything else stays.
        * Answered with the user's own softphone account: a provisional ``klingelt``/``verpasst``
          becomes ``beendet`` (answer time and duration from the list) with the log "reached"
          (core path, after the missed follow-up was undone); answered calls stay as they are.
        * Answered elsewhere (own other device, colleague, shared device, forwarding):
          ``anderswo`` with device and duration, linked to the colleague's call if any; the
          activity and an untouched log "not reached" of a missed call are removed.

        ``ende``: end of the group; used as end of calls created from the list.
        """
        self.ensure_one()
        anruf = self.sudo()
        aus_anlage = anruf.wb_voip_herkunft == 'anlage'
        if not geraet:
            if anruf.zustand not in VORLAEUFIG:
                return False
            anruf.write({'zustand': 'verpasst',
                         'ende': ende if aus_anlage else (anruf.ende or fields.Datetime.now())})
            anruf._wb_voip_protokollieren()
            anruf._wb_voip_nach_beenden(False)
            return True
        if anruf.zustand not in VORLAEUFIG + ('verpasst', 'anderswo'):
            return False
        aktivitaet, protokoll = anruf.rueckruf_aktivitaet_id, anruf.protokoll_id
        if geraet == (user.wb_voip_sip_benutzer or '').strip():
            if anruf.zustand == 'anderswo':
                return False
            werte = {
                'zustand': 'beendet', 'annahme': annahme,
                'ende': annahme + timedelta(seconds=dauer), 'dauer_sek': dauer,
            }
        else:
            werte = {'zustand': 'anderswo', 'dauer_sek': dauer}
            if not anruf.wb_voip_geraet:
                # nur einmal: ein später umbenanntes Gerät ändert den Anruf nicht
                werte['wb_voip_geraet'] = geraet_name
            if aus_anlage:
                werte['ende'] = ende
            elif anruf.zustand != 'anderswo':
                werte['ende'] = anruf.ende or fields.Datetime.now()
            if kollegen_anruf and not anruf.angenommen_anruf_id:
                werte['angenommen_anruf_id'] = kollegen_anruf.id
        if anruf.zustand != 'anderswo':
            werte['rueckruf_aktivitaet_id'] = False
        werte = {
            feld: wert for feld, wert in werte.items()
            if (anruf[feld].id if anruf._fields[feld].type == 'many2one' else anruf[feld]) != wert
        }
        if not werte:
            return False
        war_vorlaeufig = anruf.zustand in VORLAEUFIG + ('verpasst',)
        anruf.write(werte)
        if war_vorlaeufig:
            anruf._wb_voip_verpasst_aufraeumen(aktivitaet, protokoll)
        if anruf.zustand == 'beendet':
            anruf._wb_voip_protokollieren()
        return True
