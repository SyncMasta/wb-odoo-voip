import importlib.util
import os
from datetime import timedelta
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import freeze_time, tagged

from .common import WbVoipCase

NUMMER = '+49 170 7654321'
START = '2026-11-25 09:00:00'


@tagged('wb_voip', 'post_install', '-at_install')
class TestVerpasstLogik(WbVoipCase):
    """Missed calls: callback activity, simultaneous calls, closing entries on answer."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kontakt = cls.env['res.partner'].create({'name': 'Karla Kundin', 'phone': NUMMER})
        cls.typ = cls.env.ref('wb_voip.wb_voip_activity_type_rueckruf')
        gruppen = [cls.env.ref('base.group_user').id,
                   cls.env.ref('wb_voip.group_wb_voip_user').id,
                   cls.env.ref('base.group_partner_manager').id]
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        # Zweite Firma: Benutzer C gehört nur ihr an
        cls.firma2 = cls.env['res.company'].create({'name': 'Zweitfirma'})
        cls.user_c = Users.create({
            'name': 'VoIP Benutzer C', 'login': 'wb_voip_c', 'lang': 'en_US', 'email': 'wb_voip_c@example.com',
            'company_id': cls.firma2.id, 'company_ids': [(6, 0, [cls.firma2.id])],
            'group_ids': [(6, 0, gruppen)],
        })
        # D darf Kontakte lesen, aber nicht ändern
        cls.user_d = Users.create({
            'name': 'VoIP Benutzer D', 'login': 'wb_voip_d', 'lang': 'en_US', 'email': 'wb_voip_d@example.com',
            'group_ids': [(6, 0, gruppen[:2])],
        })

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
        daten = Anruf.wb_voip_beginnen(richtung, nummer, f'logik-{self._zaehler}', False, False)
        return Anruf.browse(daten['id'])

    def _verpasst(self, user, nummer=NUMMER):
        anruf = self._anruf(user, nummer=nummer)
        self.assertEqual(anruf.wb_voip_beendet(False, '487')['zustand'], 'verpasst')
        return anruf

    def _aktivitaeten(self, user, partner=None):
        return self.env['mail.activity'].sudo().with_context(active_test=False).search([
            ('activity_type_id', '=', self.typ.id), ('res_model', '=', 'res.partner'),
            ('res_id', '=', (partner or self.kontakt).id), ('user_id', '=', user.id)])

    def _verpasst_meldungen(self, user):
        return [m for m in self.bus if m[0] == user.id and m[1] == 'wb_voip/verpasst']

    # --- Felder ---

    def test_aktivitaetstyp(self):
        self.assertEqual(self.typ.category, 'phonecall')
        self.assertFalse(self.typ.res_model)

    def test_firma_des_benutzers(self):
        self.assertEqual(self._anruf(self.user_a).sudo().company_id, self.env.company)
        self.assertEqual(self._anruf(self.user_c).sudo().company_id, self.firma2)

    def test_neue_felder_nur_server(self):
        anruf = self._anruf(self.user_a)
        for feld, wert in (('company_id', self.firma2.id), ('angenommen_anruf_id', anruf.id),
                           ('rueckruf_aktivitaet_id', False), ('erledigt', True),
                           ('erledigt_am', '2026-11-25 10:00:00'), ('erledigt_text', 'x'),
                           ('erledigt_durch_protokoll_id', False), ('gesehen', True)):
            with self.subTest(feld=feld), self.assertRaises(UserError):
                anruf.write({feld: wert})

    # --- Rückruf-Aktivität ---

    def test_verpasst_legt_rueckruf_aktivitaet_an(self):
        anruf = self._verpasst(self.user_a)
        aktivitaet = anruf.sudo().rueckruf_aktivitaet_id
        self.assertTrue(aktivitaet)
        self.assertEqual(aktivitaet.activity_type_id, self.typ)
        self.assertEqual(aktivitaet.res_model, 'res.partner')
        self.assertEqual(aktivitaet.res_id, self.kontakt.id)
        self.assertEqual(aktivitaet.user_id, self.user_a)
        # In der Sprache des Empfängers (auf einer Datenbank nur mit Deutsch ist das Deutsch)
        erwartet = 'Rückruf: verpasster Anruf' if self.user_a.lang == 'de_DE' else 'Callback: missed call'
        self.assertEqual(aktivitaet.summary, erwartet)
        self.assertTrue(self._verpasst_meldungen(self.user_a))
        self.assertTrue(all(m[2] == {} for m in self._verpasst_meldungen(self.user_a)))

    def test_aktivitaet_wiederverwendet(self):
        erst = self._verpasst(self.user_a)
        zweit = self._verpasst(self.user_a)
        self.assertEqual(len(self._aktivitaeten(self.user_a)), 1)
        self.assertEqual(erst.sudo().rueckruf_aktivitaet_id, zweit.sudo().rueckruf_aktivitaet_id)

    def test_aktivitaet_je_benutzer(self):
        self._verpasst(self.user_a)
        self._verpasst(self.user_b)
        self.assertEqual(len(self._aktivitaeten(self.user_a)), 1)
        self.assertEqual(len(self._aktivitaeten(self.user_b)), 1)

    def test_erledigte_aktivitaet_nicht_wiederverwendet(self):
        erst = self._verpasst(self.user_a)
        alt = erst.sudo().rueckruf_aktivitaet_id
        alt.with_user(self.user_a).action_feedback(feedback='erledigt')
        self.assertFalse(alt.active)
        zweit = self._verpasst(self.user_a)
        neu = zweit.sudo().rueckruf_aktivitaet_id
        self.assertTrue(neu.active)
        self.assertNotEqual(neu, alt)

    def test_ohne_kontakt_keine_aktivitaet(self):
        anruf = self._verpasst(self.user_a, nummer='+49 170 9999999')
        self.assertFalse(anruf.sudo().partner_id)
        self.assertFalse(anruf.sudo().rueckruf_aktivitaet_id)

    def test_kontakt_nicht_lesbar_keine_aktivitaet(self):
        # Review Focus 5: Kontakt war beim Klingeln lesbar, beim Verpassen nicht mehr
        anruf = self._anruf(self.user_a)
        self.assertEqual(anruf.sudo().partner_id, self.kontakt)
        self.env['ir.rule'].create({
            'name': 'Test: Kontakt gesperrt', 'model_id': self.env.ref('base.model_res_partner').id,
            'domain_force': "[('id', '!=', %d)]" % self.kontakt.id,
        })
        self.assertEqual(anruf.wb_voip_beendet(False, '487')['zustand'], 'verpasst')
        self.assertFalse(anruf.sudo().rueckruf_aktivitaet_id)
        self.assertFalse(self._aktivitaeten(self.user_a))

    def test_kontakt_von_anfang_an_nicht_lesbar(self):
        self.env['ir.rule'].create({
            'name': 'Test: Kontakt gesperrt', 'model_id': self.env.ref('base.model_res_partner').id,
            'domain_force': "[('id', '!=', %d)]" % self.kontakt.id,
        })
        anruf = self._verpasst(self.user_a)
        self.assertFalse(anruf.sudo().partner_id)
        self.assertFalse(self._aktivitaeten(self.user_a))

    def test_aktivitaet_ohne_schreibrecht_am_kontakt(self):
        self.user_d.write({'wb_voip_anlage_id': self.anlage.id})
        self.assertTrue(self.kontakt.with_user(self.user_d).has_access('read'))
        self.assertFalse(self.kontakt.with_user(self.user_d).has_access('write'))
        anruf = self._verpasst(self.user_d)
        aktivitaet = anruf.sudo().rueckruf_aktivitaet_id
        self.assertTrue(aktivitaet)
        self.assertEqual(aktivitaet.user_id, self.user_d)

    def test_abgelehnt_keine_aktivitaet(self):
        anruf = self._anruf(self.user_a)
        anruf.wb_voip_beendet(False, '603')
        self.assertFalse(anruf.sudo().rueckruf_aktivitaet_id)
        self.assertFalse(self._aktivitaeten(self.user_a))

    def test_zweites_beenden_keine_zweite_aktivitaet(self):
        anruf = self._verpasst(self.user_a)
        aktivitaet = anruf.sudo().rueckruf_aktivitaet_id
        aktivitaet.sudo().unlink()
        anruf.wb_voip_beendet(False, '487')
        self.assertFalse(self._aktivitaeten(self.user_a))

    # --- Gleichzeitige Anrufe ---

    def test_verpasst_dann_kollege_nimmt_an(self):
        # Review Focus 1: A meldet „verpasst“, kurz danach nimmt B denselben Anruf an
        with freeze_time(START) as zeit:
            a = self._anruf(self.user_a)
            b = self._anruf(self.user_b)
            a.wb_voip_beendet(False, '487')
            aktivitaet = a.sudo().rueckruf_aktivitaet_id
            protokoll = a.sudo().protokoll_id
            self.assertTrue(aktivitaet)
            self.assertEqual(protokoll.ergebnis, 'nicht_erreicht')
            self.bus.clear()
            zeit.tick(timedelta(seconds=2))
            b.wb_voip_angenommen()
        a_sudo = a.sudo()
        self.assertEqual(a_sudo.zustand, 'anderswo')
        self.assertEqual(a_sudo.angenommen_anruf_id, b.sudo())
        self.assertFalse(a_sudo.rueckruf_aktivitaet_id)
        self.assertFalse(aktivitaet.exists())
        self.assertFalse(protokoll.exists())
        self.assertFalse(a_sudo.protokoll_id)
        # Zähler sinkt: kein offener „verpasst“ mehr für A
        self.assertFalse(self.env['wb.voip.anruf'].sudo().search_count([
            ('user_id', '=', self.user_a.id), ('zustand', '=', 'verpasst'),
            ('erledigt', '=', False)]))
        self.assertTrue(self._verpasst_meldungen(self.user_a))

    def test_bearbeitetes_protokoll_bleibt(self):
        with freeze_time(START) as zeit:
            a = self._anruf(self.user_a)
            b = self._anruf(self.user_b)
            a.wb_voip_beendet(False, '487')
            protokoll = a.sudo().protokoll_id
            protokoll.with_user(self.user_a).write({'beschreibung': '<p>Kunde wollte Angebot</p>'})
            zeit.tick(timedelta(seconds=2))
            b.wb_voip_angenommen()
        self.assertEqual(a.sudo().zustand, 'anderswo')
        self.assertTrue(protokoll.exists())

    def test_geaendertes_ergebnis_bleibt(self):
        with freeze_time(START) as zeit:
            a = self._anruf(self.user_a)
            b = self._anruf(self.user_b)
            a.wb_voip_beendet(False, '487')
            protokoll = a.sudo().protokoll_id
            protokoll.with_user(self.user_a).write({'ergebnis': 'mailbox'})
            zeit.tick(timedelta(seconds=2))
            b.wb_voip_angenommen()
        self.assertTrue(protokoll.exists())

    def test_nie_geaendertes_protokoll_per_schreibdatum(self):
        with freeze_time(START) as zeit:
            a = self._anruf(self.user_a)
            b = self._anruf(self.user_b)
            a.wb_voip_beendet(False, '487')
            protokoll = a.sudo().protokoll_id
            # später geändert (z. B. Kontakt): Schreibdatum weicht ab
            self.env.cr.execute(
                "UPDATE wb_voip_protokoll SET write_date = create_date + interval '1 minute' "
                "WHERE id = %s", [protokoll.id])
            protokoll.invalidate_recordset()
            zeit.tick(timedelta(seconds=2))
            b.wb_voip_angenommen()
        self.assertTrue(protokoll.exists())

    def test_geteilte_aktivitaet_nicht_geloescht(self):
        with freeze_time(START) as zeit:
            frueher = self._verpasst(self.user_a)
            zeit.tick(timedelta(minutes=5))
            a = self._anruf(self.user_a)
            b = self._anruf(self.user_b)
            a.wb_voip_beendet(False, '487')
            aktivitaet = a.sudo().rueckruf_aktivitaet_id
            self.assertEqual(aktivitaet, frueher.sudo().rueckruf_aktivitaet_id)
            zeit.tick(timedelta(seconds=2))
            b.wb_voip_angenommen()
        # nicht gelöscht, sondern mit dem früheren Eintrag erledigt
        self.assertTrue(aktivitaet.exists())
        self.assertFalse(aktivitaet.active)
        self.assertTrue(frueher.sudo().erledigt)

    def test_klingelnder_kollege_wird_anderswo(self):
        with freeze_time(START) as zeit:
            a = self._anruf(self.user_a)
            b = self._anruf(self.user_b)
            zeit.tick(timedelta(seconds=3))
            b.wb_voip_angenommen()
            self.assertEqual(a.sudo().zustand, 'anderswo')
            self.assertEqual(a.sudo().angenommen_anruf_id, b.sudo())
            # der Client meldet danach „anderswo“: bleibt so, kein Protokoll
            daten = a.wb_voip_beendet(False, 'anderswo')
        self.assertEqual(daten['zustand'], 'anderswo')
        self.assertFalse(daten['protokoll_id'])

    def test_anderswo_verknuepft_mit_angenommenem(self):
        with freeze_time(START) as zeit:
            b = self._anruf(self.user_b)
            a = self._anruf(self.user_a)
            zeit.tick(timedelta(seconds=1))
            b.wb_voip_angenommen()
            # B's Annahme hat A schon umgestellt; zur Probe A zurück auf „klingelt“
            a.sudo().write({'zustand': 'klingelt', 'angenommen_anruf_id': False})
            a.wb_voip_beendet(False, 'anderswo')
        self.assertEqual(a.sudo().zustand, 'anderswo')
        self.assertEqual(a.sudo().angenommen_anruf_id, b.sudo())
        self.assertFalse(a.sudo().rueckruf_aktivitaet_id)

    def test_anderswo_vor_annahme_wird_nachtraeglich_verknuepft(self):
        with freeze_time(START) as zeit:
            a = self._anruf(self.user_a)
            b = self._anruf(self.user_b)
            a.wb_voip_beendet(False, 'anderswo')
            self.assertFalse(a.sudo().angenommen_anruf_id)
            zeit.tick(timedelta(seconds=1))
            b.wb_voip_angenommen()
        self.assertEqual(a.sudo().angenommen_anruf_id, b.sudo())

    def test_ausserhalb_zeitfenster_nicht_gleichzeitig(self):
        with freeze_time(START) as zeit:
            a = self._anruf(self.user_a)
            zeit.tick(timedelta(seconds=11))
            b = self._anruf(self.user_b)
            b.wb_voip_angenommen()
        self.assertEqual(a.sudo().zustand, 'klingelt')
        self.assertFalse(a.sudo().angenommen_anruf_id)

    def test_ohne_e164_nie_verknuepft(self):
        with freeze_time(START):
            a = self._anruf(self.user_a, nummer='Unbekannt')
            b = self._anruf(self.user_b, nummer='Unbekannt')
            self.assertFalse(a.sudo().nummer_e164)
            b.wb_voip_angenommen()
        self.assertEqual(a.sudo().zustand, 'klingelt')

    def test_eigener_zweiter_anruf_nicht_gleichzeitig(self):
        # anderer Benutzer ist Pflicht: der eigene zweite Anruf bleibt unberührt
        with freeze_time(START):
            a1 = self._anruf(self.user_a)
            a2 = self._anruf(self.user_a)
            a2.wb_voip_angenommen()
        self.assertEqual(a1.sudo().zustand, 'klingelt')

    def test_ausgehend_nicht_gleichzeitig(self):
        with freeze_time(START):
            a = self._anruf(self.user_a, richtung='ausgehend')
            b = self._anruf(self.user_b)
            b.wb_voip_angenommen()
        self.assertEqual(a.sudo().zustand, 'klingelt')

    def test_gleichzeitige_hoechstens_50(self):
        with freeze_time(START):
            b = self._anruf(self.user_b)
            Anruf = self.env['wb.voip.anruf'].sudo()
            Anruf.create([{
                'user_id': self.user_a.id, 'richtung': 'eingehend', 'nummer': NUMMER,
                'nummer_e164': b.sudo().nummer_e164, 'zustand': 'klingelt',
                'sip_call_id': f'viele-{i}', 'company_id': self.env.company.id,
            } for i in range(60)])
            self.assertEqual(len(b.sudo()._wb_voip_gleichzeitige(['klingelt'])), 50)

    # --- Zwei Firmen ---

    def test_zwei_firmen_gleiche_nummer(self):
        # Review Focus 2: Annahme in Firma 1 erledigt nichts in Firma 2 und verknüpft nicht
        with freeze_time(START) as zeit:
            alt_a = self._verpasst(self.user_a)
            alt_c = self._verpasst(self.user_c)
            zeit.tick(timedelta(minutes=5))
            b = self._anruf(self.user_b)
            c = self._anruf(self.user_c)
            self.bus.clear()
            b.wb_voip_angenommen()
        self.assertTrue(alt_a.sudo().erledigt)
        self.assertFalse(alt_c.sudo().erledigt)
        self.assertTrue(alt_c.sudo().rueckruf_aktivitaet_id.active)
        self.assertEqual(c.sudo().zustand, 'klingelt')
        self.assertFalse(c.sudo().angenommen_anruf_id)
        self.assertFalse(self._verpasst_meldungen(self.user_c))

    def test_zwei_firmen_anderswo_nicht_verknuepft(self):
        with freeze_time(START) as zeit:
            b = self._anruf(self.user_b)
            c = self._anruf(self.user_c)
            zeit.tick(timedelta(seconds=1))
            b.wb_voip_angenommen()
            c.wb_voip_beendet(False, 'anderswo')
        self.assertEqual(c.sudo().zustand, 'anderswo')
        self.assertFalse(c.sudo().angenommen_anruf_id)

    def test_zwei_firmen_verpasst_bleibt_verpasst(self):
        with freeze_time(START) as zeit:
            c = self._anruf(self.user_c)
            b = self._anruf(self.user_b)
            c.wb_voip_beendet(False, '487')
            zeit.tick(timedelta(seconds=1))
            b.wb_voip_angenommen()
        self.assertEqual(c.sudo().zustand, 'verpasst')
        self.assertTrue(c.sudo().protokoll_id)
        self.assertTrue(c.sudo().rueckruf_aktivitaet_id)

    # --- Erledigen durch Annahme ---

    def test_annahme_erledigt_alle_benutzer(self):
        with freeze_time(START) as zeit:
            alt_a = self._verpasst(self.user_a)
            alt_b = self._verpasst(self.user_b)
            akt_a = alt_a.sudo().rueckruf_aktivitaet_id
            akt_b = alt_b.sudo().rueckruf_aktivitaet_id
            zeit.tick(timedelta(minutes=5))
            neu = self._anruf(self.user_b)
            self.bus.clear()
            neu.wb_voip_angenommen()
        for alt in (alt_a.sudo(), alt_b.sudo()):
            self.assertTrue(alt.erledigt)
            self.assertTrue(alt.erledigt_am)
            self.assertEqual(alt.erledigt_text, 'Answered by VoIP Benutzer B')
            self.assertFalse(alt.erledigt_durch_protokoll_id)
            self.assertEqual(alt.zustand, 'verpasst')
        for akt in (akt_a, akt_b):
            self.assertTrue(akt.exists())
            self.assertFalse(akt.active)
        nachricht = self.kontakt.message_ids.filtered(
            lambda m: 'Answered by VoIP Benutzer B' in (m.body or ''))
        self.assertTrue(nachricht)
        self.assertTrue(self._verpasst_meldungen(self.user_a))
        self.assertTrue(self._verpasst_meldungen(self.user_b))

    def test_angenommen_per_beendet_erledigt(self):
        # Annahme nur über wb_voip_beendet(angenommen=True) (wb_voip_angenommen verloren)
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            zeit.tick(timedelta(minutes=5))
            neu = self._anruf(self.user_b)
            neu.wb_voip_beendet(False, False, angenommen=True)
        self.assertTrue(alt.sudo().erledigt)

    def test_spaeterer_verpasst_bleibt_offen(self):
        with freeze_time(START) as zeit:
            neu = self._anruf(self.user_b)
            neu.wb_voip_angenommen()
            zeit.tick(timedelta(minutes=5))
            spaeter = self._verpasst(self.user_a)
            neu.sudo()._wb_voip_nummer_erreicht()
        self.assertFalse(spaeter.sudo().erledigt)

    def test_anderswo_eintrag_bleibt_offen(self):
        with freeze_time(START) as zeit:
            a = self._anruf(self.user_a)
            b = self._anruf(self.user_b)
            b.wb_voip_angenommen()
            zeit.tick(timedelta(minutes=5))
            neu = self._anruf(self.user_b)
            neu.wb_voip_angenommen()
        self.assertEqual(a.sudo().zustand, 'anderswo')
        self.assertFalse(a.sudo().erledigt)

    def test_ausgehend_erledigt_nichts(self):
        # nicht angenommen (besetzt): kein Rückruf-Erfolg; angenommen siehe test_verpasst_rueckruf
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            zeit.tick(timedelta(minutes=5))
            raus = self._anruf(self.user_b, richtung='ausgehend')
            zeit.tick(timedelta(seconds=30))
            raus.wb_voip_beendet(False, '486')
        self.assertFalse(alt.sudo().erledigt)
        self.assertTrue(alt.sudo().rueckruf_aktivitaet_id.active)

    def test_nummer_erreicht_mit_protokoll(self):
        # Schnittstelle für Task 3: Rückruf über ein Protokoll
        with freeze_time(START) as zeit:
            alt = self._verpasst(self.user_a)
            zeit.tick(timedelta(minutes=5))
            raus = self._anruf(self.user_b, richtung='ausgehend')
            raus.wb_voip_angenommen()
            daten = raus.wb_voip_beendet(False, False)
            protokoll = self.env['wb.voip.protokoll'].sudo().browse(daten['protokoll_id'])
            raus.sudo()._wb_voip_nummer_erreicht(protokoll)
        alt = alt.sudo()
        self.assertTrue(alt.erledigt)
        self.assertEqual(alt.erledigt_text, 'Called back by VoIP Benutzer B')
        self.assertEqual(alt.erledigt_durch_protokoll_id, protokoll)

    # --- Nachbesserung: echte Annahme nach „anderswo“, später gemeldetes „verpasst“ ---

    def _kollege_nimmt_an(self, zeit):
        a = self._anruf(self.user_a)
        b = self._anruf(self.user_b)
        zeit.tick(timedelta(seconds=1))
        b.wb_voip_angenommen()
        self.assertEqual(a.sudo().zustand, 'anderswo')
        self.assertTrue(a.sudo().ende)
        return a, b

    def test_echte_annahme_nach_anderswo(self):
        # gleiche Absendernummer (Zentrale), zwei verschiedene Anrufe: A nimmt wirklich an
        with freeze_time(START) as zeit:
            a, b = self._kollege_nimmt_an(zeit)
            zeit.tick(timedelta(seconds=2))
            daten = a.wb_voip_angenommen()
            self.assertEqual(daten['zustand'], 'laeuft')
            a_sudo = a.sudo()
            self.assertTrue(a_sudo.annahme)
            self.assertFalse(a_sudo.angenommen_anruf_id)
            self.assertFalse(a_sudo.ende)
            self.assertEqual(b.sudo().zustand, 'laeuft')
            zeit.tick(timedelta(seconds=30))
            daten = a.wb_voip_beendet(False, False)
        self.assertEqual(daten['zustand'], 'beendet')
        self.assertEqual(daten['dauer_sek'], 30)
        protokoll = self.env['wb.voip.protokoll'].sudo().browse(daten['protokoll_id'])
        self.assertEqual(protokoll.ergebnis, 'erreicht')

    def test_beendet_angenommen_nach_anderswo(self):
        with freeze_time(START) as zeit:
            a, _b = self._kollege_nimmt_an(zeit)
            zeit.tick(timedelta(seconds=5))
            daten = a.wb_voip_beendet(False, False, angenommen=True)
        a_sudo = a.sudo()
        self.assertEqual(daten['zustand'], 'beendet')
        self.assertTrue(a_sudo.annahme)
        self.assertFalse(a_sudo.angenommen_anruf_id)
        protokoll = self.env['wb.voip.protokoll'].sudo().browse(daten['protokoll_id'])
        self.assertEqual(protokoll.ergebnis, 'erreicht')

    def test_anderswo_ohne_angenommen_bleibt_final(self):
        with freeze_time(START) as zeit:
            a, b = self._kollege_nimmt_an(zeit)
            daten = a.wb_voip_beendet(False, '487')
        self.assertEqual(daten['zustand'], 'anderswo')
        self.assertEqual(a.sudo().angenommen_anruf_id, b.sudo())
        self.assertFalse(daten['protokoll_id'])

    def _verpasst_nach_kollegen_annahme(self, b_beenden):
        with freeze_time(START) as zeit:
            a = self._anruf(self.user_a)
            b = self._anruf(self.user_b)
            zeit.tick(timedelta(seconds=1))
            b.wb_voip_angenommen()
            if b_beenden:
                zeit.tick(timedelta(seconds=5))
                b.wb_voip_beendet(False, False)
            # A's Client meldet erst jetzt ein schlichtes 487 (ohne Grund „anderswo“)
            a.sudo().write({'zustand': 'klingelt', 'angenommen_anruf_id': False, 'ende': False})
            daten = a.wb_voip_beendet(False, '487')
        a_sudo = a.sudo()
        self.assertEqual(daten['zustand'], 'anderswo')
        self.assertEqual(a_sudo.angenommen_anruf_id, b.sudo())
        self.assertFalse(a_sudo.rueckruf_aktivitaet_id)
        self.assertFalse(self._aktivitaeten(self.user_a))
        self.assertFalse(a_sudo.protokoll_id)
        self.assertFalse(self.env['wb.voip.protokoll'].sudo().search([('anruf_id', '=', a.id)]))

    def test_verpasst_nach_kollegen_annahme_laeuft(self):
        self._verpasst_nach_kollegen_annahme(b_beenden=False)

    def test_verpasst_nach_kollegen_annahme_beendet(self):
        self._verpasst_nach_kollegen_annahme(b_beenden=True)

    # --- Migration 19.0.4.0.0 ---

    def test_migration_firma_aus_benutzer(self):
        a = self._anruf(self.user_a)
        c = self._anruf(self.user_c)
        self.env.cr.execute("UPDATE wb_voip_anruf SET company_id = %s WHERE id = %s",
                            [self.firma2.id, a.id])
        self.env.cr.execute("UPDATE wb_voip_anruf SET company_id = NULL WHERE id = %s", [c.id])
        self.env.invalidate_all()
        pfad = os.path.join(os.path.dirname(__file__), '..', 'migrations', '19.0.4.0.0',
                            'post-migration.py')
        spec = importlib.util.spec_from_file_location('wb_voip_mig_19_0_4_0_0', pfad)
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
        modul.migrate(self.env.cr, '19.0.3.0.1')
        self.env.invalidate_all()
        self.assertEqual(a.sudo().company_id, self.user_a.company_id)
        self.assertEqual(c.sudo().company_id, self.firma2)
