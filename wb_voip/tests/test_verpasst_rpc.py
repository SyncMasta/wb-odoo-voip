from datetime import timedelta
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import freeze_time, tagged

from .common import WbVoipCase

NUMMER = '+49 170 7654321'
NUMMER2 = '+49 170 1112223'
START = '2026-11-25 09:00:00'


@tagged('wb_voip', 'post_install', '-at_install')
class TestVerpasstRpc(WbVoipCase):
    """Missed calls: RPC for list, counter, "seen" and the done check mark."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kontakt = cls.env['res.partner'].create({'name': 'Vera Verpasst', 'phone': NUMMER})
        cls.kontakt2 = cls.env['res.partner'].create({'name': 'Max Zweit', 'phone': NUMMER2})
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
        daten = Anruf.wb_voip_beginnen(richtung, nummer, f'rpc-{self._zaehler}', False, False)
        return Anruf.browse(daten['id'])

    def _verpasst(self, user, nummer=NUMMER):
        anruf = self._anruf(user, nummer=nummer)
        self.assertEqual(anruf.wb_voip_beendet(False, '487')['zustand'], 'verpasst')
        return anruf

    def _anderswo(self, zeit, notiz=False):
        """A misses, B answers within seconds; returns (A's entry, B's call)."""
        a = self._anruf(self.user_a)
        b = self._anruf(self.user_b)
        a.wb_voip_beendet(False, '487')
        zeit.tick(timedelta(seconds=2))
        b.wb_voip_angenommen()
        zeit.tick(timedelta(seconds=60))
        b.wb_voip_beendet(notiz, False, True)
        return a, b

    def _liste(self, user, **kwargs):
        return self.env['wb.voip.anruf'].with_user(user).wb_voip_verpasst_liste(**kwargs)

    def _zaehler_von(self, user):
        return self.env['wb.voip.anruf'].with_user(user).wb_voip_verpasst_zaehler()

    def _sperre(self, partner):
        self.env['ir.rule'].create({
            'name': 'Test: Kontakt gesperrt', 'model_id': self.env.ref('base.model_res_partner').id,
            'domain_force': "[('id', '!=', %d)]" % partner.id,
        })

    # --- Liste ---

    def test_liste_gruppiert_je_nummer_neueste_zuerst(self):
        with freeze_time(START) as zeit:
            self._verpasst(self.user_a)
            zeit.tick(timedelta(minutes=1))
            self._verpasst(self.user_a, nummer=NUMMER2)
            zeit.tick(timedelta(minutes=1))
            neuester = self._verpasst(self.user_a)
            liste = self._liste(self.user_a)
        self.assertEqual(len(liste), 2)
        erste, zweite = liste
        self.assertEqual(erste['anzahl'], 2)
        self.assertEqual(erste['art'], 'verpasst')
        self.assertEqual(erste['anruf_id'], neuester.id)
        self.assertEqual(erste['schluessel'], '+491707654321')
        self.assertIsInstance(erste['schluessel'], str)
        self.assertEqual(erste['partner'], [self.kontakt.id, 'Vera Verpasst'])
        self.assertEqual(zweite['anzahl'], 1)
        self.assertEqual(zweite['partner'], [self.kontakt2.id, 'Max Zweit'])
        self.assertGreater(erste['letzter_beginn'], zweite['letzter_beginn'])
        self.assertFalse(erste['kollege'])

    def test_liste_nur_eigene_und_offene(self):
        with freeze_time(START):
            self._verpasst(self.user_b)
            erledigt = self._verpasst(self.user_a, nummer=NUMMER2)
            erledigt.sudo().write({'erledigt': True})
            self._anruf(self.user_a).wb_voip_beendet(False, '603')
            self.assertEqual(self._liste(self.user_a), [])
            self.assertEqual(len(self._liste(self.user_b)), 1)

    def test_liste_ohne_e164_nach_nummer(self):
        with freeze_time(START):
            a = self._verpasst(self.user_a, nummer='Anonym')
            b = self._verpasst(self.user_a, nummer='Anonym')
            self.assertFalse(a.sudo().nummer_e164)
            liste = self._liste(self.user_a)
        self.assertEqual(len(liste), 1)
        self.assertEqual(liste[0]['anzahl'], 2)
        self.assertEqual(liste[0]['schluessel'], 'Anonym')
        self.assertEqual(liste[0]['anruf_id'], b.id)

    def test_liste_limit_gilt_fuer_gruppen(self):
        with freeze_time(START) as zeit:
            for nummer in (NUMMER, NUMMER2, '+49 170 5550001'):
                self._verpasst(self.user_a, nummer=nummer)
                self._verpasst(self.user_a, nummer=nummer)
                zeit.tick(timedelta(minutes=1))
            self.assertEqual(len(self._liste(self.user_a, limit=2)), 2)
            self.assertEqual(self._liste(self.user_a, limit=2)[0]['schluessel'], '+491705550001')

    def test_liste_limit_geprueft(self):
        for limit in (0, 101, '5', True, None):
            with self.subTest(limit=limit), self.assertRaises(UserError):
                self._liste(self.user_a, limit=limit)

    def test_liste_anderswo_mit_kollege(self):
        with freeze_time(START) as zeit:
            a, b = self._anderswo(zeit, notiz='Wollte Angebot')
            liste = self._liste(self.user_a)
        self.assertEqual(len(liste), 1)
        zeile = liste[0]
        self.assertEqual(zeile['art'], 'anderswo')
        self.assertEqual(zeile['anruf_id'], a.id)
        self.assertEqual(zeile['kollege'], {
            'name': 'VoIP Benutzer B', 'dauer_sek': b.sudo().dauer_sek, 'notiz': 'Wollte Angebot'})
        self.assertEqual(set(zeile['kollege']), {'name', 'dauer_sek', 'notiz'})

    def test_liste_anderswo_ohne_verknuepfung_unsichtbar(self):
        with freeze_time(START):
            a = self._verpasst(self.user_a)
            a.sudo().write({'zustand': 'anderswo'})
            self.assertEqual(self._liste(self.user_a), [])
            self.assertEqual(self._zaehler_von(self.user_a), 0)

    def test_kollegen_notiz_gekuerzt(self):
        with freeze_time(START) as zeit:
            a, _b = self._anderswo(zeit, notiz='x' * 500)
            zeile = self._liste(self.user_a)[0]
        self.assertEqual(len(zeile['kollege']['notiz']), 300)

    def test_kollegen_info_nur_nach_eigentumspruefung(self):
        with freeze_time(START) as zeit:
            a, _b = self._anderswo(zeit, notiz='geheim')
        a_sudo = a.sudo()
        self.assertEqual(a_sudo.with_user(self.user_a)._wb_voip_angenommen_info()['name'],
                         'VoIP Benutzer B')
        # fremder Aufrufer (B ist nicht Besitzer von A's Eintrag) bekommt nichts
        self.assertFalse(a_sudo.with_user(self.user_b)._wb_voip_angenommen_info())
        # verpasst-Eintrag: keine Kollegen-Info
        self.assertFalse(self._verpasst(self.user_a)._wb_voip_angenommen_info())

    def test_kollegen_info_nur_gleiche_firma(self):
        with freeze_time(START) as zeit:
            a, b = self._anderswo(zeit)
            firma2 = self.env['res.company'].create({'name': 'Fremdfirma'})
            b.sudo().company_id = firma2
            self.assertFalse(a.with_user(self.user_a)._wb_voip_angenommen_info())
            self.assertFalse(self._liste(self.user_a)[0]['kollege'])

    # --- Kontakt nicht lesbar (Review Focus 5) ---

    def test_kontakt_nicht_lesbar_nur_nummer(self):
        with freeze_time(START):
            self._verpasst(self.user_a)
            self._sperre(self.kontakt)
            zeile = self._liste(self.user_a)[0]
        self.assertFalse(zeile['partner'])
        self.assertEqual(zeile['nummer'], NUMMER)
        self.assertNotIn('Vera', str(zeile))

    def test_kontakt_nicht_lesbar_erledigen_ohne_aktivitaet(self):
        with freeze_time(START):
            anruf = self._anruf(self.user_a)
            self._sperre(self.kontakt)
            anruf.wb_voip_beendet(False, '487')
            self.assertFalse(anruf.sudo().rueckruf_aktivitaet_id)
            self.assertTrue(anruf.wb_voip_verpasst_erledigen('fertig') is False)
            self.assertTrue(anruf.sudo().erledigt)
            self.assertFalse(self.env['mail.activity'].sudo().search([
                ('activity_type_id', '=', self.typ.id), ('res_id', '=', self.kontakt.id)]))

    # --- Zähler und „gesehen“ ---

    def test_zaehler_ohne_anderswo_und_erledigte(self):
        with freeze_time(START) as zeit:
            self._anderswo(zeit)
            self.assertEqual(self._zaehler_von(self.user_a), 0)
            self._verpasst(self.user_a, nummer=NUMMER2)
            erledigt = self._verpasst(self.user_a, nummer='+49 170 5550001')
            erledigt.sudo().write({'erledigt': True})
            self._verpasst(self.user_b)
            self.assertEqual(self._zaehler_von(self.user_a), 1)
            self.assertEqual(self._zaehler_von(self.user_b), 1)

    def test_gesehen_setzt_alle_eigenen_offenen(self):
        with freeze_time(START) as zeit:
            self._anderswo(zeit)
            x = self._verpasst(self.user_a, nummer=NUMMER2)
            fremd = self._verpasst(self.user_b)
            erledigt = self._verpasst(self.user_a, nummer='+49 170 5550001')
            erledigt.sudo().write({'erledigt': True})
            self.bus.clear()
            neuer_zaehler = self.env['wb.voip.anruf'].with_user(self.user_a).wb_voip_verpasst_gesehen()
        self.assertEqual(neuer_zaehler, 0)
        self.assertTrue(x.sudo().gesehen)
        self.assertFalse(fremd.sudo().gesehen)
        self.assertFalse(erledigt.sudo().gesehen)
        self.assertEqual(self._zaehler_von(self.user_b), 1)
        # weitere Tabs desselben Benutzers werden benachrichtigt, ohne Nutzdaten
        meldungen = [m for m in self.bus if m[1] == 'wb_voip/verpasst']
        self.assertEqual({m[0] for m in meldungen}, {self.user_a.id})
        self.assertTrue(all(m[2] == {} for m in meldungen))

    def test_neuer_verpasst_zaehlt_wieder(self):
        with freeze_time(START):
            self._verpasst(self.user_a)
            self.env['wb.voip.anruf'].with_user(self.user_a).wb_voip_verpasst_gesehen()
            self.assertEqual(self._zaehler_von(self.user_a), 0)
            self._verpasst(self.user_a)
            self.assertEqual(self._zaehler_von(self.user_a), 1)

    # --- Erledigen (Review Focus 4) ---

    def test_erledigen_schliesst_gruppe_und_aktivitaet(self):
        with freeze_time(START):
            a1 = self._verpasst(self.user_a)
            a2 = self._verpasst(self.user_a)
            andere_gruppe = self._verpasst(self.user_a, nummer=NUMMER2)
            aktivitaet = a1.sudo().rueckruf_aktivitaet_id
            self.assertEqual(aktivitaet, a2.sudo().rueckruf_aktivitaet_id)
            self.bus.clear()
            ergebnis = a2.wb_voip_verpasst_erledigen('Zurückgerufen, alles klar')
        self.assertIs(ergebnis, False)
        for anruf in (a1, a2):
            self.assertTrue(anruf.sudo().erledigt)
            self.assertEqual(anruf.sudo().erledigt_text, 'Zurückgerufen, alles klar')
            self.assertTrue(anruf.sudo().erledigt_am)
        self.assertFalse(andere_gruppe.sudo().erledigt)
        self.assertFalse(aktivitaet.active)
        self.assertEqual([z['schluessel'] for z in self._liste(self.user_a)], ['+491701112223'])
        self.assertTrue([m for m in self.bus if m[1] == 'wb_voip/verpasst'])

    def test_erledigen_gemeinsame_aktivitaet_bleibt_offen(self):
        # Zwei Nummern desselben Kontakts teilen sich die Rückruf-Aktivität
        with freeze_time(START):
            a1 = self._verpasst(self.user_a)
            a2 = self._anruf(self.user_a, nummer='+49 170 5550001')
            a2.sudo().partner_id = self.kontakt  # zweite Nummer desselben Kontakts
            a2.wb_voip_beendet(False, '487')
            aktivitaet = a1.sudo().rueckruf_aktivitaet_id
            self.assertEqual(aktivitaet, a2.sudo().rueckruf_aktivitaet_id)
            a1.wb_voip_verpasst_erledigen('')
            self.assertTrue(a1.sudo().erledigt)
            self.assertFalse(a2.sudo().erledigt)
            self.assertTrue(aktivitaet.active)
            a2.wb_voip_verpasst_erledigen('')
            self.assertFalse(aktivitaet.active)

    def test_erledigen_text_ist_klartext(self):
        with freeze_time(START):
            anruf = self._verpasst(self.user_a)
            aktivitaet = anruf.sudo().rueckruf_aktivitaet_id
            anruf.wb_voip_verpasst_erledigen('<script>alert(1)</script> & <b>fett</b>')
            nachricht = self.env['mail.message'].sudo().search([
                ('model', '=', 'res.partner'), ('res_id', '=', self.kontakt.id),
                ('body', 'ilike', 'alert')], limit=1)
        self.assertFalse(aktivitaet.active)
        self.assertTrue(nachricht)
        self.assertNotIn('<script>', nachricht.body)
        self.assertNotIn('<b>', nachricht.body)
        self.assertEqual(anruf.sudo().erledigt_text, '<script>alert(1)</script> & <b>fett</b>')

    def test_erledigen_text_zu_lang(self):
        with freeze_time(START):
            anruf = self._verpasst(self.user_a)
            with self.assertRaises(UserError):
                anruf.wb_voip_verpasst_erledigen('x' * 2001)
            self.assertFalse(anruf.sudo().erledigt)
            anruf.wb_voip_verpasst_erledigen('x' * 2000)
            self.assertTrue(anruf.sudo().erledigt)

    def test_erledigen_text_falscher_typ(self):
        with freeze_time(START):
            anruf = self._verpasst(self.user_a)
            for text in (5, None, ['a'], {'a': 1}, True):
                with self.subTest(text=text), self.assertRaises(UserError):
                    anruf.wb_voip_verpasst_erledigen(text)
            self.assertFalse(anruf.sudo().erledigt)
            anruf.wb_voip_verpasst_erledigen(False)
            self.assertTrue(anruf.sudo().erledigt)

    def test_erledigen_fremder_eintrag(self):
        with freeze_time(START):
            fremd = self._verpasst(self.user_b)
            with self.assertRaises(AccessError):
                fremd.with_user(self.user_a).wb_voip_verpasst_erledigen('x')
            self.assertFalse(fremd.sudo().erledigt)

    def test_erledigen_bereits_erledigt_ist_noop(self):
        with freeze_time(START):
            anruf = self._verpasst(self.user_a)
            anruf.wb_voip_verpasst_erledigen('erstes')
            am = anruf.sudo().erledigt_am
            self.bus.clear()
            self.assertIs(anruf.wb_voip_verpasst_erledigen('zweites'), False)
            self.assertEqual(anruf.sudo().erledigt_text, 'erstes')
            self.assertEqual(anruf.sudo().erledigt_am, am)
            self.assertFalse([m for m in self.bus if m[1] == 'wb_voip/verpasst'])

    def test_erledigen_kein_verpasst_eintrag(self):
        with freeze_time(START):
            anruf = self._anruf(self.user_a)
            anruf.wb_voip_beendet(False, '603')
            with self.assertRaises(UserError):
                anruf.wb_voip_verpasst_erledigen('x')
            self.assertFalse(anruf.sudo().erledigt)

    def test_erledigen_anderswo(self):
        with freeze_time(START) as zeit:
            a, _b = self._anderswo(zeit)
            self.assertIs(a.wb_voip_verpasst_erledigen('gesehen'), False)
            self.assertTrue(a.sudo().erledigt)
            self.assertEqual(self._liste(self.user_a), [])

    def test_erledigen_anderswo_ohne_verknuepfung_abgelehnt(self):
        with freeze_time(START):
            a = self._verpasst(self.user_a)
            a.sudo().write({'zustand': 'anderswo'})
            with self.assertRaises(UserError):
                a.wb_voip_verpasst_erledigen('x')

    def test_erledigen_andere_firma_derselben_nummer_bleibt(self):
        with freeze_time(START):
            a1 = self._verpasst(self.user_a)
            a2 = self._verpasst(self.user_a)
            a2.sudo().company_id = self.env['res.company'].create({'name': 'Zweitfirma'})
            a1.wb_voip_verpasst_erledigen('')
            # Gruppe = Nummer; beide Einträge gehören dem Benutzer und sind sichtbar
            self.assertTrue(a1.sudo().erledigt)
            self.assertTrue(a2.sudo().erledigt)

    def test_erledigen_ohne_lizenz(self):
        with freeze_time(START):
            anruf = self._verpasst(self.user_a)
            self._lizenz_fehlt()
            self.assertEqual(len(self._liste(self.user_a)), 1)
            self.assertEqual(self._zaehler_von(self.user_a), 1)
            anruf.wb_voip_verpasst_erledigen('ok')
            self.assertTrue(anruf.sudo().erledigt)
            self.assertEqual(self._liste(self.user_a), [])

    def test_rpc_ohne_voip_recht(self):
        nutzer = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Ohne VoIP', 'login': 'wb_voip_ohne', 'email': 'ohne@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        Anruf = self.env['wb.voip.anruf'].with_user(nutzer)
        for aufruf in (Anruf.wb_voip_verpasst_liste, Anruf.wb_voip_verpasst_zaehler,
                       Anruf.wb_voip_verpasst_gesehen):
            with self.subTest(aufruf=aufruf.__name__), self.assertRaises(AccessError):
                aufruf()
