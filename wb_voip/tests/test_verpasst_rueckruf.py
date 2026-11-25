from datetime import timedelta
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import freeze_time, tagged

from .common import WbVoipCase

NUMMER = '+49 170 7654321'
START = '2026-11-25 09:00:00'


@tagged('wb_voip', 'post_install', '-at_install')
class TestVerpasstRueckruf(WbVoipCase):
    """Missed calls: callback via log closes entries, a changed result reopens them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kontakt = cls.env['res.partner'].create({'name': 'Rita Rückruf', 'phone': NUMMER})
        cls.typ = cls.env.ref('wb_voip.wb_voip_activity_type_rueckruf')

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()
        self._zaehler = 0
        self.bus = []
        Users = type(self.env['res.users'])
        original = Users._bus_send
        meldungen = self.bus

        def mitschreiben(benutzer, typ, nutzlast, *args, **kwargs):
            meldungen.append((benutzer.id, typ, nutzlast))
            return original(benutzer, typ, nutzlast, *args, **kwargs)

        patcher = patch.object(Users, '_bus_send', mitschreiben)
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- Hilfen ---

    def _anruf(self, user, richtung='eingehend', nummer=NUMMER):
        self._zaehler += 1
        Anruf = self.env['wb.voip.anruf'].with_user(user)
        daten = Anruf.wb_voip_beginnen(richtung, nummer, f'rueckruf-{self._zaehler}', False, False)
        return Anruf.browse(daten['id'])

    def _verpasst(self, user):
        anruf = self._anruf(user)
        self.assertEqual(anruf.wb_voip_beendet(False, '487')['zustand'], 'verpasst')
        return anruf

    def _rueckruf(self, user, zeit, sip_grund=False, angenommen=True):
        """Outgoing call of ``user`` five minutes later; returns (call, log)."""
        zeit.tick(timedelta(minutes=5))
        raus = self._anruf(user, richtung='ausgehend')
        if angenommen:
            raus.wb_voip_angenommen()
        zeit.tick(timedelta(seconds=30))
        daten = raus.wb_voip_beendet(False, sip_grund)
        return raus, self.env['wb.voip.protokoll'].sudo().browse(daten['protokoll_id'])

    def _aktivitaeten(self, user):
        return self.env['mail.activity'].sudo().with_context(active_test=False).search([
            ('activity_type_id', '=', self.typ.id), ('res_model', '=', 'res.partner'),
            ('res_id', '=', self.kontakt.id), ('user_id', '=', user.id)])

    def _verpasst_meldungen(self, user):
        return [m for m in self.bus if m[0] == user.id and m[1] == 'wb_voip/verpasst']

    # --- Ausgehender Anruf erledigt ---

    def test_ausgehend_erreicht_erledigt(self):
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            aktivitaet = alt.sudo().rueckruf_aktivitaet_id
            raus, protokoll = self._rueckruf(self.user_b, zeit)
        self.assertEqual(protokoll.ergebnis, 'erreicht')
        alt = alt.sudo()
        self.assertTrue(alt.erledigt)
        self.assertEqual(alt.erledigt_text, 'Zurückgerufen von VoIP Benutzer B')
        self.assertEqual(alt.erledigt_durch_protokoll_id, protokoll)
        self.assertFalse(aktivitaet.exists() and aktivitaet.active)

    def test_ausgehend_nicht_angenommen_erledigt_nichts(self):
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            _raus, protokoll = self._rueckruf(self.user_b, zeit, sip_grund='486', angenommen=False)
        self.assertEqual(protokoll.ergebnis, 'besetzt')
        self.assertFalse(alt.sudo().erledigt)
        self.assertTrue(alt.sudo().rueckruf_aktivitaet_id.active)

    # --- Ergebnis ändern: öffnet wieder / erledigt wieder ---

    def test_ergebnis_wechsel_oeffnet_wieder_und_erledigt_erneut(self):
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            erste = alt.sudo().rueckruf_aktivitaet_id
            _raus, protokoll = self._rueckruf(self.user_b, zeit)
            self.assertTrue(alt.sudo().erledigt)
            self.assertFalse(erste.active)
            self.bus.clear()
            protokoll.with_user(self.user_b).write({'ergebnis': 'mailbox'})
            alt = alt.sudo()
            self.assertFalse(alt.erledigt)
            self.assertFalse(alt.erledigt_am)
            self.assertFalse(alt.erledigt_text)
            neu = alt.rueckruf_aktivitaet_id
            self.assertTrue(neu.active)
            self.assertNotEqual(neu, erste)
            self.assertEqual(neu.user_id, self.user_a)
            # die archivierte bleibt bestehen und wird nicht wiederverwendet
            self.assertTrue(erste.exists())
            self.assertFalse(erste.active)
            self.assertEqual(len(self._aktivitaeten(self.user_a)), 2)
            self.assertTrue(self._verpasst_meldungen(self.user_a))
            self.assertTrue(all(m[2] == {} for m in self._verpasst_meldungen(self.user_a)))
            # zurück auf „erreicht“: wieder erledigt, neue Aktivität abgeschlossen
            protokoll.with_user(self.user_b).write({'ergebnis': 'erreicht'})
        self.assertTrue(alt.erledigt)
        self.assertEqual(alt.erledigt_text, 'Zurückgerufen von VoIP Benutzer B')
        self.assertEqual(alt.erledigt_durch_protokoll_id, protokoll)
        self.assertFalse(neu.active)
        self.assertEqual(len(self._aktivitaeten(self.user_a)), 2)

    def test_wiederoeffnen_nutzt_aktive_aktivitaet(self):
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            _raus, protokoll = self._rueckruf(self.user_b, zeit)
            # in der Zwischenzeit gibt es für den Kontakt wieder eine offene Aktivität
            zeit.tick(timedelta(minutes=1))
            neuer = self._verpasst(self.user_a)
            aktive = neuer.sudo().rueckruf_aktivitaet_id
            protokoll.with_user(self.user_b).write({'ergebnis': 'nicht_erreicht'})
        self.assertEqual(alt.sudo().rueckruf_aktivitaet_id, aktive)
        self.assertEqual(len(self._aktivitaeten(self.user_a)), 2)

    def test_ergebnis_unveraendert_tut_nichts(self):
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            _raus, protokoll = self._rueckruf(self.user_b, zeit)
            schreibdatum = alt.sudo().write_date
            protokoll.with_user(self.user_b).write({'ergebnis': 'erreicht'})
            protokoll.with_user(self.user_b).write({'beschreibung': '<p>Termin vereinbart</p>'})
        self.assertTrue(alt.sudo().erledigt)
        self.assertEqual(alt.sudo().write_date, schreibdatum)
        self.assertEqual(len(self._aktivitaeten(self.user_a)), 1)

    def test_anderes_ergebnis_ohne_erledigte_eintraege(self):
        with freeze_time(START) as zeit:
            self._verpasst(self.user_a)
            _raus, protokoll = self._rueckruf(self.user_b, zeit, sip_grund='486', angenommen=False)
            protokoll.with_user(self.user_b).write({'ergebnis': 'mailbox'})
        self.assertEqual(len(self._aktivitaeten(self.user_a)), 1)

    def test_ausgehendes_protokoll_nachtraeglich_erreicht(self):
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            _raus, protokoll = self._rueckruf(self.user_b, zeit, sip_grund='486', angenommen=False)
            self.assertFalse(alt.sudo().erledigt)
            protokoll.with_user(self.user_b).write({'ergebnis': 'erreicht'})
        alt = alt.sudo()
        self.assertTrue(alt.erledigt)
        self.assertEqual(alt.erledigt_text, 'Zurückgerufen von VoIP Benutzer B')
        self.assertEqual(alt.erledigt_durch_protokoll_id, protokoll)
        self.assertFalse(alt.rueckruf_aktivitaet_id.active)

    def test_eingehende_annahme_wird_nicht_wieder_geoeffnet(self):
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            zeit.tick(timedelta(minutes=5))
            b = self._anruf(self.user_b)
            b.wb_voip_angenommen()
            daten = b.wb_voip_beendet(False, False)
            protokoll = self.env['wb.voip.protokoll'].sudo().browse(daten['protokoll_id'])
            self.assertTrue(alt.sudo().erledigt)
            protokoll.with_user(self.user_b).write({'ergebnis': 'mailbox'})
        self.assertTrue(alt.sudo().erledigt)
        self.assertEqual(len(self._aktivitaeten(self.user_a)), 1)

    def test_anderer_rueckruf_loest_alte_verknuepfung(self):
        # Eintrag wurde durch Protokoll P1 erledigt und wieder geöffnet; ein späterer Rückruf mit
        # P2 erledigt ihn erneut. Danach darf P1 ihn nicht mehr berühren.
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            _raus1, p1 = self._rueckruf(self.user_b, zeit)
            p1.with_user(self.user_b).write({'ergebnis': 'mailbox'})
            self.assertFalse(alt.sudo().erledigt)
            _raus2, p2 = self._rueckruf(self.user_a, zeit)
            self.assertTrue(alt.sudo().erledigt)
            self.assertEqual(alt.sudo().erledigt_durch_protokoll_id, p2)
            p1.with_user(self.user_b).write({'ergebnis': 'erreicht'})
            p1.with_user(self.user_b).write({'ergebnis': 'besetzt'})
        self.assertTrue(alt.sudo().erledigt)

    # --- Rechte ---

    def test_fremdes_protokoll_nicht_aenderbar(self):
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            _raus, protokoll = self._rueckruf(self.user_b, zeit)
            with self.assertRaises(AccessError):
                protokoll.with_user(self.user_a).write({'ergebnis': 'mailbox'})
        self.assertEqual(protokoll.ergebnis, 'erreicht')
        self.assertTrue(alt.sudo().erledigt)

    def test_eigenes_protokoll_ohne_schreibrecht_am_anruf(self):
        # Benutzer dürfen die Serverfelder des Anrufs nicht ändern; das Wiederöffnen läuft mit sudo
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            _raus, protokoll = self._rueckruf(self.user_b, zeit)
            with self.assertRaises(UserError):
                alt.with_user(self.user_b).write({'erledigt': False})
            protokoll.with_user(self.user_b).write({'ergebnis': 'nicht_erreicht'})
        self.assertFalse(alt.sudo().erledigt)

    # --- Aktivität im Chatter erledigt (Review Focus 3) ---

    def test_aktivitaet_erledigt_schliesst_eintrag(self):
        with freeze_time(START):
            alt = self._verpasst(self.user_a)
            aktivitaet = alt.sudo().rueckruf_aktivitaet_id
            self.bus.clear()
            aktivitaet.with_user(self.user_a).action_feedback(feedback='Kunde per Mail erreicht')
        alt = alt.sudo()
        self.assertTrue(alt.erledigt)
        self.assertTrue(alt.erledigt_am)
        self.assertEqual(alt.erledigt_text, 'Kunde per Mail erreicht')
        self.assertFalse(alt.erledigt_durch_protokoll_id)
        self.assertFalse(aktivitaet.active)
        self.assertTrue(aktivitaet.exists())
        self.assertTrue(self._verpasst_meldungen(self.user_a))

    def test_aktivitaet_erledigt_ohne_text(self):
        with freeze_time(START):
            alt = self._verpasst(self.user_a)
            alt.sudo().rueckruf_aktivitaet_id.with_user(self.user_a).action_feedback()
        self.assertEqual(alt.sudo().erledigt_text, 'Aufgabe erledigt')

    def test_aktivitaet_erledigt_html_wird_klartext(self):
        with freeze_time(START):
            alt = self._verpasst(self.user_a)
            alt.sudo().rueckruf_aktivitaet_id.with_user(self.user_a).action_feedback(
                feedback='<p>Rückruf <b>folgt</b></p><script>x</script>')
        self.assertNotIn('<', alt.sudo().erledigt_text)
        self.assertIn('Rückruf', alt.sudo().erledigt_text)

    def test_geteilte_aktivitaet_schliesst_alle_eintraege(self):
        with freeze_time(START) as zeit:
            e1 = self._verpasst(self.user_a)
            zeit.tick(timedelta(minutes=1))
            e2 = self._verpasst(self.user_a)
            aktivitaet = e1.sudo().rueckruf_aktivitaet_id
            self.assertEqual(aktivitaet, e2.sudo().rueckruf_aktivitaet_id)
            aktivitaet.with_user(self.user_a).action_feedback(feedback='erledigt')
        self.assertTrue(e1.sudo().erledigt)
        self.assertTrue(e2.sudo().erledigt)

    def test_aktivitaet_erledigt_andere_eintraege_bleiben(self):
        with freeze_time(START):
            a = self._verpasst(self.user_a)
            b = self._verpasst(self.user_b)
            a.sudo().rueckruf_aktivitaet_id.with_user(self.user_a).action_feedback(feedback='x')
        self.assertTrue(a.sudo().erledigt)
        self.assertFalse(b.sudo().erledigt)
        self.assertTrue(b.sudo().rueckruf_aktivitaet_id.active)

    def test_aktivitaet_fremder_typ_ohne_wirkung(self):
        with freeze_time(START):
            alt = self._verpasst(self.user_a)
            sonst = self.kontakt.activity_schedule(
                'mail.mail_activity_data_todo', summary='Sonst', user_id=self.user_a.id)
            sonst.with_user(self.user_a).action_feedback(feedback='x')
        self.assertFalse(alt.sudo().erledigt)

    def test_review_focus_3_aktivitaet_dann_ergebnis_geaendert(self):
        # Aktivität im Chatter erledigt → Eintrag erledigt; danach Protokoll-Ergebnis geändert →
        # neue Aktivität; die archivierten werden nie wiederverwendet, nie gelöscht, nie erneut
        # abgeschlossen.
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            erste = alt.sudo().rueckruf_aktivitaet_id
            _raus, protokoll = self._rueckruf(self.user_b, zeit)
            self.assertTrue(alt.sudo().erledigt)
            self.assertFalse(erste.active)
            protokoll.with_user(self.user_b).write({'ergebnis': 'mailbox'})
            zweite = alt.sudo().rueckruf_aktivitaet_id
            self.assertTrue(zweite.active)
            self.assertNotEqual(zweite, erste)
            # Aktivität im Chatter erledigt
            zweite.with_user(self.user_a).action_feedback(feedback='Kunde meldet sich selbst')
            alt = alt.sudo()
            self.assertTrue(alt.erledigt)
            self.assertEqual(alt.erledigt_text, 'Kunde meldet sich selbst')
            self.assertFalse(zweite.active)
            nachrichten = self.env['mail.message'].sudo().search_count([
                ('model', '=', 'res.partner'), ('res_id', '=', self.kontakt.id),
                ('body', 'ilike', 'Kunde meldet sich selbst')])
            # Ergebnis erneut geändert: erledigter Eintrag bleibt, nichts wird neu angelegt
            protokoll.with_user(self.user_b).write({'ergebnis': 'erreicht'})
            protokoll.with_user(self.user_b).write({'ergebnis': 'besetzt'})
            self.assertTrue(alt.erledigt)
            self.assertEqual(alt.erledigt_text, 'Kunde meldet sich selbst')
            self.assertEqual(self.env['mail.message'].sudo().search_count([
                ('model', '=', 'res.partner'), ('res_id', '=', self.kontakt.id),
                ('body', 'ilike', 'Kunde meldet sich selbst')]), nachrichten)
            # ein neuer verpasster Anruf bekommt eine neue Aktivität (keine archivierte)
            zeit.tick(timedelta(minutes=1))
            neu = self._verpasst(self.user_a)
        dritte = neu.sudo().rueckruf_aktivitaet_id
        self.assertTrue(dritte.active)
        self.assertNotIn(dritte, erste | zweite)
        self.assertTrue(erste.exists() and zweite.exists())
        self.assertFalse(erste.active or zweite.active)
        self.assertEqual(len(self._aktivitaeten(self.user_a)), 3)
