from contextlib import contextmanager

from odoo.tests import tagged

from .common import WbVoipCrmCase


class _Zurueck(Exception):
    """Marks the always-rolled-back end of ``_isoliert``."""


@tagged('wb_voip_crm', 'post_install', '-at_install')
class TestRueckruf(WbVoipCrmCase):
    """Callback activity and protocol chatter on the lead (Review Focus 1 and 2)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.typ = cls.env.ref('wb_voip.wb_voip_activity_type_rueckruf')

    def _verpasst(self, benutzer=None, res_model=False, res_id=False):
        benutzer = benutzer or self.vertrieb_eigene
        daten = self._beginnen(benutzer, res_model, res_id)
        anruf = self.env['wb.voip.anruf'].with_user(benutzer).browse(daten['id'])
        self.assertEqual(anruf.wb_voip_beendet(False, '487')['zustand'], 'verpasst')
        return anruf

    def _aktivitaeten(self, datensatz, benutzer=None):
        return self.env['mail.activity'].sudo().with_context(active_test=False).search([
            ('activity_type_id', '=', self.typ.id), ('res_model', '=', datensatz._name),
            ('res_id', '=', datensatz.id),
            ('user_id', '=', (benutzer or self.vertrieb_eigene).id)])

    def _akt(self, anruf):
        return anruf.sudo().rueckruf_aktivitaet_id

    @contextmanager
    def _isoliert(self):
        """Run the block in a savepoint that is always rolled back (also on a failure)."""
        try:
            with self.env.cr.savepoint():
                yield
                raise _Zurueck()
        except _Zurueck:
            pass

    # --- Ziel ---

    def test_aktivitaet_am_vorgang(self):
        lead = self._lead()
        anruf = self._verpasst()
        self.assertEqual(anruf.sudo().res_model, 'crm.lead')
        akt = self._akt(anruf)
        self.assertEqual((akt.res_model, akt.res_id), ('crm.lead', lead.id))
        self.assertEqual(akt.user_id, self.vertrieb_eigene)
        self.assertFalse(self._aktivitaeten(self.kontakt))

    def test_ohne_vorgang_am_kontakt(self):
        anruf = self._verpasst()
        akt = self._akt(anruf)
        self.assertEqual((akt.res_model, akt.res_id), ('res.partner', self.kontakt.id))

    def test_ziel_ist_nur_der_aktive_vorgang(self):
        lead = self._lead()
        anruf = self._verpasst().sudo()
        self.assertEqual(anruf._wb_voip_rueckruf_ziel(), lead)
        # gewonnen, verloren oder archiviert: nicht mehr der Vorgang, sondern der Kontakt
        for ende, nach in (('gewonnen', self._gewonnen), ('verloren', self._verloren),
                           ('archiviert', self._archiviert)):
            with self.subTest(ende=ende), self._isoliert():
                nach(lead)
                self.assertEqual(anruf._wb_voip_rueckruf_ziel(), self.kontakt)

    def test_wiederverwendung_je_besitzer_und_vorgang(self):
        lead = self._lead()
        erst = self._verpasst()
        zweit = self._verpasst()
        self.assertEqual(self._akt(erst), self._akt(zweit))
        self.assertEqual(len(self._aktivitaeten(lead)), 1)

    def test_vorgang_per_klick_hat_vorrang(self):
        self._lead(name='Zugeordnet')
        anderer = self._lead(name='Anderer')
        anruf = self._verpasst(res_model='crm.lead', res_id=anderer.id)
        self.assertEqual(self._akt(anruf).res_id, anderer.id)

    # --- Review Focus 1: ohne Leserecht ---

    def test_ohne_leserecht_kontakt(self):
        # Vorgang gehört V2; V1 (Regel „eigene“) darf ihn nicht lesen -> Kontakt
        lead = self._lead(user_id=self.vertrieb_alle.id)
        anruf = self._verpasst(res_model='crm.lead', res_id=lead.id)
        akt = self._akt(anruf)
        self.assertEqual((akt.res_model, akt.res_id), ('res.partner', self.kontakt.id))
        self.assertFalse(self._aktivitaeten(lead))

    def test_ohne_crm_rechte_kontakt(self):
        lead = self._lead()
        anruf = self._verpasst(benutzer=self.ohne_crm)
        akt = self._akt(anruf)
        self.assertEqual((akt.res_model, akt.res_id), ('res.partner', self.kontakt.id))
        self.assertFalse(self._aktivitaeten(lead, self.ohne_crm))

    def test_vorgang_nach_zuordnung_unlesbar(self):
        # Zuordnung steht, danach wird der Vorgang an einen anderen übergeben (vor Auflegen)
        lead = self._lead()
        daten = self._beginnen(self.vertrieb_eigene)
        lead.user_id = self.vertrieb_alle
        anruf = self.env['wb.voip.anruf'].with_user(self.vertrieb_eigene).browse(daten['id'])
        anruf.wb_voip_beendet(False, '487')
        akt = self._akt(anruf)
        self.assertEqual((akt.res_model, akt.res_id), ('res.partner', self.kontakt.id))

    # --- Review Focus 2: Vorgang endet ---

    def _pruefen_weiter_bearbeitbar(self, anruf, lead, mit_chatter):
        akt = self._akt(anruf)
        self.assertTrue(akt)
        self.assertEqual((akt.res_model, akt.res_id), ('crm.lead', lead.id))
        self.assertTrue(akt.active)
        self.assertFalse(anruf.sudo().erledigt)
        gruppe = self.env['wb.voip.anruf'].with_user(self.vertrieb_eigene)
        self.assertEqual(len(gruppe.wb_voip_verpasst_liste()), 1)
        if mit_chatter:
            akt.with_user(self.vertrieb_eigene).action_feedback(feedback='Erledigt im Chatter')
            self.assertTrue(anruf.sudo().erledigt)
            self.assertEqual(anruf.sudo().erledigt_text, 'Erledigt im Chatter')
        else:
            self.assertFalse(
                gruppe.browse(anruf.id).wb_voip_verpasst_erledigen('Rückruf gemacht'))
            self.assertTrue(anruf.sudo().erledigt)
            self.assertEqual(anruf.sudo().erledigt_text, 'Rückruf gemacht')
        self.assertFalse(akt.exists() and akt.active)

    def _gewonnen(self, lead):
        lead.action_set_won()
        self.assertEqual(lead.probability, 100)

    def _verloren(self, lead):
        lead.action_set_lost()
        self.assertFalse(lead.active)

    def _archiviert(self, lead):
        lead.action_archive()
        self.assertFalse(lead.active)

    def test_vorgang_gewonnen_erledigen_per_haken(self):
        for ende, nach in (('gewonnen', self._gewonnen), ('verloren', self._verloren),
                           ('archiviert', self._archiviert)):
            for chatter in (False, True):
                with self.subTest(ende=ende, chatter=chatter), self._isoliert():
                    lead = self._lead()
                    anruf = self._verpasst()
                    nach(lead)
                    self._pruefen_weiter_bearbeitbar(anruf, lead, chatter)

    def test_vorgang_endet_aktivitaet_bleibt(self):
        for ende, nach in (('gewonnen', self._gewonnen), ('verloren', self._verloren),
                           ('archiviert', self._archiviert)):
            with self.subTest(ende=ende), self._isoliert():
                lead = self._lead()
                anruf = self._verpasst()
                akt = self._akt(anruf)
                nach(lead)
                self.assertTrue(akt.exists())
                self.assertEqual(self._akt(anruf), akt)
                self.assertTrue(akt.active)

    def test_neuer_anruf_nach_ende_geht_an_kontakt(self):
        lead = self._lead()
        self._gewonnen(lead)
        anruf = self._verpasst()
        akt = self._akt(anruf)
        self.assertEqual((akt.res_model, akt.res_id), ('res.partner', self.kontakt.id))

    # --- Wiedereröffnen ---

    def test_wieder_oeffnen_am_vorgang(self):
        lead = self._lead()
        anruf = self._verpasst()
        alt = self._akt(anruf)
        self.env['wb.voip.anruf'].with_user(self.vertrieb_eigene).browse(anruf.id) \
            .wb_voip_verpasst_erledigen('fertig')
        self.assertFalse(alt.active)
        anruf.sudo()._wb_voip_wieder_oeffnen()
        neu = self._akt(anruf)
        self.assertNotEqual(neu, alt)
        self.assertTrue(neu.active)
        self.assertEqual((neu.res_model, neu.res_id), ('crm.lead', lead.id))
        self.assertFalse(anruf.sudo().erledigt)

    # --- Protokoll-Chatter ---

    def _notizen(self, datensatz):
        return self.env['mail.message'].sudo().search([
            ('model', '=', datensatz._name), ('res_id', '=', datensatz.id),
            ('message_type', '=', 'comment')])

    def test_chatter_am_vorgang(self):
        lead = self._lead()
        anruf = self._verpasst()
        self.assertTrue(anruf.sudo().protokoll_id)
        self.assertEqual(len(self._notizen(lead)), 1)
        self.assertFalse(self._notizen(self.kontakt))

    def test_chatter_am_kontakt_ohne_schreibrecht(self):
        # Leserecht am Vorgang, aber kein Schreibrecht: Notiz landet am Kontakt
        lead = self._lead()
        self.env['ir.rule'].sudo().create({
            'name': 'wb_voip_crm Test: Vorgang nicht schreibbar',
            'model_id': self.env.ref('crm.model_crm_lead').id,
            'domain_force': '[(0, "=", 1)]',
            'perm_read': False, 'perm_write': True, 'perm_create': False, 'perm_unlink': False})
        lead_benutzer = lead.with_user(self.vertrieb_eigene)
        self.assertTrue(lead_benutzer.has_access('read'))
        self.assertFalse(lead_benutzer.has_access('write'))
        self._verpasst()
        self.assertFalse(self._notizen(lead))
        self.assertEqual(len(self._notizen(self.kontakt)), 1)
