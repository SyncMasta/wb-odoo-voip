from datetime import timedelta
from unittest.mock import patch

from markupsafe import Markup

from odoo.tests import freeze_time, tagged

from .common import WbVoipCase

NUMMER = '+49 170 7654321'
START = '2026-11-25 09:00:00'


@tagged('wb_voip', 'post_install', '-at_install')
class TestVerpasstNachbesserung(WbVoipCase):
    """Missed calls: indexes, late "answered" after elsewhere, company context, reopening."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.firma = cls.env.company
        # Kontakt gehört der Hauptfirma: lesbar nur mit ihr im Firmen-Kontext
        cls.kontakt = cls.env['res.partner'].create({
            'name': 'Frieda Firma', 'phone': NUMMER, 'company_id': cls.firma.id})
        cls.typ = cls.env.ref('wb_voip.wb_voip_activity_type_rueckruf')
        cls.firma2 = cls.env['res.company'].create({'name': 'Zweitfirma Nachbesserung'})
        # B arbeitet in beiden Firmen, A nur in der Hauptfirma
        cls.user_b.write({'company_ids': [(4, cls.firma2.id)]})
        gruppen = [cls.env.ref('base.group_user').id,
                   cls.env.ref('wb_voip.group_wb_voip_user').id,
                   cls.env.ref('base.group_partner_manager').id]
        cls.user_e = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'VoIP Benutzer E', 'login': 'wb_voip_e', 'email': 'wb_voip_e@example.com',
            'group_ids': [(6, 0, gruppen)],
        })

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()
        self._zaehler = 0
        # Bus-Meldungen nur mitschreiben (kein Versand nötig)
        Users = type(self.env['res.users'])
        patcher = patch.object(Users, '_bus_send', lambda *args, **kwargs: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _anruf(self, user, richtung='eingehend', firma=None):
        self._zaehler += 1
        Anruf = self.env['wb.voip.anruf'].with_user(user)
        if firma:
            Anruf = Anruf.with_context(allowed_company_ids=[firma.id])
        daten = Anruf.wb_voip_beginnen(richtung, NUMMER, f'nachb-{self._zaehler}', False, False)
        return Anruf.browse(daten['id'])

    def _verpasst(self, user):
        anruf = self._anruf(user)
        self.assertEqual(anruf.wb_voip_beendet(False, '487')['zustand'], 'verpasst')
        return anruf

    def _rueckruf(self, user, zeit, firma=None):
        zeit.tick(timedelta(minutes=5))
        raus = self._anruf(user, richtung='ausgehend', firma=firma)
        raus.wb_voip_angenommen()
        zeit.tick(timedelta(seconds=30))
        daten = raus.wb_voip_beendet(False, False)
        return raus, self.env['wb.voip.protokoll'].sudo().browse(daten['protokoll_id'])

    # --- 1. Indizes auf den neuen Fremdschlüsseln ---

    def test_indizes_auf_fremdschluesseln(self):
        for spalte in ('angenommen_anruf_id', 'rueckruf_aktivitaet_id', 'erledigt_durch_protokoll_id'):
            with self.subTest(spalte=spalte):
                self.env.cr.execute(
                    "SELECT indexdef FROM pg_indexes WHERE tablename = 'wb_voip_anruf' "
                    "AND indexdef LIKE %s", [f'%({spalte})%'])
                definitionen = [zeile[0] for zeile in self.env.cr.fetchall()]
                self.assertTrue(definitionen, f"kein Index auf {spalte}")
                self.assertTrue(any('IS NOT NULL' in d for d in definitionen), definitionen)

    # --- 2. „Doch angenommen“ für „anderswo“ nur frisch ---

    def _kollege_nimmt_an(self, zeit):
        a = self._anruf(self.user_a)
        b = self._anruf(self.user_b)
        zeit.tick(timedelta(seconds=1))
        b.wb_voip_angenommen()
        self.assertEqual(a.sudo().zustand, 'anderswo')
        return a, b

    def _kollegen_eintrag(self, a):
        """Open missed entry of E that a (wrong) answer of ``a`` would turn into ``anderswo``."""
        return self.env['wb.voip.anruf'].sudo().create({
            'user_id': self.user_e.id, 'richtung': 'eingehend', 'nummer': NUMMER,
            'nummer_e164': a.sudo().nummer_e164, 'zustand': 'verpasst',
            'sip_call_id': f'kollege-{a.id}', 'company_id': self.firma.id,
            'beginn': a.sudo().beginn - timedelta(seconds=5),
        })

    def test_angenommen_nach_anderswo_frisch(self):
        with freeze_time(START) as zeit:
            a, _b = self._kollege_nimmt_an(zeit)
            zeit.tick(timedelta(seconds=60))
            daten = a.wb_voip_angenommen()
        self.assertEqual(daten['zustand'], 'laeuft')
        self.assertTrue(a.sudo().annahme)

    def test_angenommen_nach_anderswo_spaet_noop(self):
        with freeze_time(START) as zeit:
            a, b = self._kollege_nimmt_an(zeit)
            kollege = self._kollegen_eintrag(a)
            zeit.tick(timedelta(hours=1))
            schreibdatum = a.sudo().write_date
            daten = a.wb_voip_angenommen()
        a_sudo = a.sudo()
        self.assertEqual(daten['zustand'], 'anderswo')
        self.assertFalse(a_sudo.annahme)
        self.assertEqual(a_sudo.angenommen_anruf_id, b.sudo())
        self.assertEqual(a_sudo.write_date, schreibdatum)
        self.assertEqual(kollege.zustand, 'verpasst')
        self.assertFalse(kollege.erledigt)
        self.assertFalse(kollege.angenommen_anruf_id)

    def test_beendet_angenommen_nach_anderswo_spaet_noop(self):
        with freeze_time(START) as zeit:
            a, b = self._kollege_nimmt_an(zeit)
            kollege = self._kollegen_eintrag(a)
            zeit.tick(timedelta(hours=1))
            daten = a.wb_voip_beendet(False, False, angenommen=True)
        a_sudo = a.sudo()
        self.assertEqual(daten['zustand'], 'anderswo')
        self.assertFalse(daten['protokoll_id'])
        self.assertFalse(a_sudo.annahme)
        self.assertEqual(a_sudo.angenommen_anruf_id, b.sudo())
        self.assertEqual(kollege.zustand, 'verpasst')
        self.assertFalse(kollege.erledigt)

    def test_beendet_angenommen_nach_anderswo_frisch(self):
        with freeze_time(START) as zeit:
            a, _b = self._kollege_nimmt_an(zeit)
            zeit.tick(timedelta(seconds=90))
            daten = a.wb_voip_beendet(False, False, angenommen=True)
        self.assertEqual(daten['zustand'], 'beendet')

    # --- 3. Leserecht des Eintrag-Besitzers mit dessen Firmen ---

    def test_wiederoeffnen_aus_anderer_firma_mit_aktivitaet(self):
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            erste = alt.sudo().rueckruf_aktivitaet_id
            self.assertTrue(erste)
            _raus, protokoll = self._rueckruf(self.user_b, zeit, firma=self.firma)
            self.assertTrue(alt.sudo().erledigt)
            # B arbeitet jetzt in der Zweitfirma und ändert das Ergebnis
            protokoll.with_user(self.user_b).with_context(
                allowed_company_ids=[self.firma2.id]).write({'ergebnis': 'mailbox'})
        alt = alt.sudo()
        self.assertFalse(alt.erledigt)
        neu = alt.rueckruf_aktivitaet_id
        self.assertTrue(neu.active)
        self.assertNotEqual(neu, erste)
        self.assertEqual(neu.user_id, self.user_a)

    # --- 4. Wieder geöffnet zählt wieder ---

    def test_wiedergeoeffnet_ungesehen(self):
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            Anruf = self.env['wb.voip.anruf'].with_user(self.user_a)
            self.assertEqual(Anruf.wb_voip_verpasst_gesehen(), 0)
            self.assertTrue(alt.sudo().gesehen)
            _raus, protokoll = self._rueckruf(self.user_b, zeit)
            protokoll.with_user(self.user_b).write({'ergebnis': 'mailbox'})
        self.assertFalse(alt.sudo().erledigt)
        self.assertFalse(alt.sudo().gesehen)
        self.assertEqual(Anruf.wb_voip_verpasst_zaehler(), 1)

    # --- 9. Rückmeldung: nur HTML wird umgewandelt ---

    def test_rueckmeldung_klartext_mit_kleiner_zeichen(self):
        with freeze_time(START):
            alt = self._verpasst(self.user_a)
            alt.sudo().rueckruf_aktivitaet_id.with_user(self.user_a).action_feedback(
                feedback='Preis < 100 und <Rabatt> zugesagt\nTermin am Freitag')
        self.assertEqual(alt.sudo().erledigt_text,
                         'Preis < 100 und <Rabatt> zugesagt\nTermin am Freitag')

    def test_rueckmeldung_markup_wird_klartext(self):
        with freeze_time(START):
            alt = self._verpasst(self.user_a)
            alt.sudo().rueckruf_aktivitaet_id.with_user(self.user_a).action_feedback(
                feedback=Markup('<p>Termin <b>Freitag</b></p>'))
        self.assertNotIn('<', alt.sudo().erledigt_text)
        self.assertIn('Termin', alt.sudo().erledigt_text)
