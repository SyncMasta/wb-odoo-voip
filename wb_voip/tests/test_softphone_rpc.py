from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import WbVoipCase


@tagged('wb_voip', 'post_install', '-at_install')
class TestSoftphoneRpc(WbVoipCase):
    """RPC methods behind the softphone tabs: contacts, tasks, own calls."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.user_ohne = Users.create({
            'name': 'Ohne VoIP', 'login': 'wb_voip_sp_ohne', 'email': 'sp_ohne@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        Partner = cls.env['res.partner']
        cls.mit_nummer = Partner.create({'name': 'Softi Telefonfreund', 'phone': '+49 170 1112223'})
        cls.mit_nummer_2 = Partner.create({'name': 'Softi Zweiter', 'phone': '0170 4445556'})
        cls.ohne_nummer = Partner.create({'name': 'Softi Stumm'})
        # Kontakt einer fremden Firma: für A nicht lesbar (Mehrfirmen-Regel)
        cls.fremdfirma = cls.env['res.company'].create({'name': 'Softi Fremdfirma'})
        cls.fremd = Partner.create({
            'name': 'Softi Fremd', 'phone': '+49 170 9998887', 'company_id': cls.fremdfirma.id})

    # --- Kontakte ---

    def _suchen(self, *args, user=None, **kwargs):
        return self.env['res.partner'].with_user(user or self.user_a).wb_voip_kontakte_suchen(
            *args, **kwargs)

    def test_kontakte_nur_mit_nummer_und_lesbar(self):
        ergebnis = self._suchen('Softi')
        ids = {k['id'] for k in ergebnis}
        self.assertIn(self.mit_nummer.id, ids)
        self.assertIn(self.mit_nummer_2.id, ids)
        self.assertNotIn(self.ohne_nummer.id, ids)
        self.assertNotIn(self.fremd.id, ids)
        eintrag = next(k for k in ergebnis if k['id'] == self.mit_nummer.id)
        self.assertEqual(set(eintrag), {'id', 'name', 'nummer', 'firma'})
        self.assertEqual(eintrag['nummer'], '+49 170 1112223')

    def test_kontakte_suche_nach_nummer(self):
        # andere Schreibweise derselben Nummer
        ids = {k['id'] for k in self._suchen('01701112223')}
        self.assertEqual(ids, {self.mit_nummer.id})

    def test_kontakte_limit(self):
        self.assertEqual(len(self._suchen('Softi', limit=1)), 1)

    def test_kontakte_eingaben_pruefen(self):
        for falsch in (None, 5, ['Softi'], 'x' * 101):
            with self.assertRaises(UserError):
                self._suchen(falsch)
        for limit in (0, 101, '5', True, 2.5):
            with self.assertRaises(UserError):
                self._suchen('Softi', limit=limit)
        self.assertTrue(self._suchen('x' * 100) is not None)
        self.assertTrue(isinstance(self._suchen('Softi', limit=100), list))

    def test_kontakte_nur_voip_benutzer(self):
        with self.assertRaises(AccessError):
            self._suchen('Softi', user=self.user_ohne)

    # --- Aufgaben ---

    def _aufgabe(self, datensatz, user, summary):
        return datensatz.activity_schedule(
            'mail.mail_activity_data_todo', user_id=user.id, summary=summary)

    def test_aufgaben_eigene_mit_nummer(self):
        eigene = self._aufgabe(self.mit_nummer, self.user_a, 'Softi anrufen')
        self._aufgabe(self.ohne_nummer, self.user_a, 'ohne Nummer')
        self._aufgabe(self.mit_nummer_2, self.user_b, 'gehört B')
        self._aufgabe(self.fremd.sudo(), self.user_a, 'nicht lesbar')
        ergebnis = self.env['mail.activity'].with_user(self.user_a).wb_voip_aufgaben()
        self.assertEqual([a['id'] for a in ergebnis], [eigene.id])
        eintrag = ergebnis[0]
        self.assertEqual(eintrag['titel'], 'Softi anrufen')
        self.assertEqual(eintrag['nummer'], '+49 170 1112223')
        self.assertEqual(eintrag['res_model'], 'res.partner')
        self.assertEqual(eintrag['res_id'], self.mit_nummer.id)
        self.assertEqual(eintrag['partner'], [self.mit_nummer.id, 'Softi Telefonfreund'])
        self.assertEqual(set(eintrag),
                         {'id', 'titel', 'faellig', 'res_model', 'res_id', 'res_name',
                          'partner', 'nummer'})

    def test_aufgaben_limit(self):
        for i in range(3):
            self._aufgabe(self.mit_nummer, self.user_a, f'Aufgabe {i}')
        Activity = self.env['mail.activity'].with_user(self.user_a)
        self.assertEqual(len(Activity.wb_voip_aufgaben(limit=2)), 2)
        for limit in (0, 101, '3', None):
            with self.assertRaises(UserError):
                Activity.wb_voip_aufgaben(limit=limit)

    def test_aufgaben_nur_voip_benutzer(self):
        with self.assertRaises(AccessError):
            self.env['mail.activity'].with_user(self.user_ohne).wb_voip_aufgaben()

    # --- Anrufliste ---

    def _anruf(self, user, sip_call_id):
        return self.env['wb.voip.anruf'].with_user(user).wb_voip_beginnen(
            'ausgehend', '+49 170 1112223', sip_call_id, False, False)

    def test_liste_nur_eigene(self):
        self._lizenz_gueltig()
        eigen = self._anruf(self.user_a, 'sp-a')
        self.user_b.write({'wb_voip_anlage_id': self.anlage.id, 'wb_voip_sip_benutzer': '101'})
        self._anruf(self.user_b, 'sp-b')
        liste = self.env['wb.voip.anruf'].with_user(self.user_a).wb_voip_liste()
        self.assertEqual([a['id'] for a in liste], [eigen['id']])
        self.assertEqual(liste[0]['partner'], (self.mit_nummer.id, 'Softi Telefonfreund'))
        # Auch Administratoren sehen hier nur ihre eigenen Anrufe
        self.assertEqual(
            self.env['wb.voip.anruf'].with_user(self.user_manager).wb_voip_liste(), [])

    def test_liste_kontakt_nicht_mehr_lesbar(self):
        self._lizenz_gueltig()
        daten = self._anruf(self.user_a, 'sp-c')
        self.mit_nummer.company_id = self.fremdfirma
        liste = self.env['wb.voip.anruf'].with_user(self.user_a).wb_voip_liste()
        self.assertEqual(liste[0]['id'], daten['id'])
        self.assertFalse(liste[0]['partner'])

    def test_liste_limit(self):
        self._lizenz_gueltig()
        for i in range(3):
            self._anruf(self.user_a, f'sp-l{i}')
        Anruf = self.env['wb.voip.anruf'].with_user(self.user_a)
        self.assertEqual(len(Anruf.wb_voip_liste(limit=2)), 2)
        for limit in (0, 101, '3', False):
            with self.assertRaises(UserError):
                Anruf.wb_voip_liste(limit=limit)

    def test_liste_nur_voip_benutzer(self):
        with self.assertRaises(AccessError):
            self.env['wb.voip.anruf'].with_user(self.user_ohne).wb_voip_liste()
