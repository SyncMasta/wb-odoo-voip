from unittest.mock import patch

from odoo.tests import tagged

from .common import WbVoipCase

NUMMER = '+49 170 5550123'


@tagged('wb_voip', 'post_install', '-at_install')
class TestUebernahme(WbVoipCase):
    """Importing finished calls and logs (sudo create) has no side effects.

    A migration from another phone system creates ``wb.voip.anruf`` and ``wb.voip.protokoll``
    directly. This must not post to chatters, create callback activities, send bus messages or
    need a license; those follow-ups belong to the RPC methods that end a live call.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kontakt = cls.env['res.partner'].create({'name': 'Ida Import', 'phone': NUMMER})

    def test_direktes_anlegen_ohne_nebenwirkungen(self):
        # bewusst ohne _lizenz_gueltig(): das Anlegen darf keine Lizenzprüfung brauchen
        Users = type(self.env['res.users'])
        original = Users._bus_send
        meldungen = []

        def mitschreiben(benutzer, typ, nutzlast, *args, **kwargs):
            meldungen.append(typ)
            return original(benutzer, typ, nutzlast, *args, **kwargs)

        Nachricht = self.env['mail.message']
        Aktivitaet = self.env['mail.activity'].with_context(active_test=False)
        nachrichten = Nachricht.search_count([])
        aktivitaeten = Aktivitaet.search_count([])
        with patch.object(Users, '_bus_send', mitschreiben):
            for zustand, ergebnis in (('verpasst', 'nicht_erreicht'), ('beendet', 'erreicht'),
                                      ('besetzt', 'besetzt'), ('fehlgeschlagen', 'falsche_nummer')):
                anruf = self.env['wb.voip.anruf'].sudo().create({
                    'user_id': self.user_a.id, 'richtung': 'eingehend', 'zustand': zustand,
                    'nummer': NUMMER, 'nummer_e164': NUMMER.replace(' ', ''),
                    'partner_id': self.kontakt.id, 'sip_call_id': f'import-{zustand}'})
                protokoll = self.env['wb.voip.protokoll'].sudo().create({
                    'anruf_id': anruf.id, 'user_id': self.user_a.id, 'richtung': 'eingehend',
                    'ergebnis': ergebnis, 'partner_id': self.kontakt.id})
                anruf.protokoll_id = protokoll
        self.assertEqual(meldungen, [])
        self.assertEqual(Aktivitaet.search_count([]), aktivitaeten)
        # nichts am Kontakt; das Protokoll selbst bekommt nur Odoos eigene „angelegt“-Zeile
        self.assertFalse(Nachricht.search([('model', '=', 'res.partner'), ('res_id', '=', self.kontakt.id)]))
        self.assertFalse(Nachricht.search([('model', '=', 'wb.voip.anruf')]))
        self.assertLessEqual(Nachricht.search_count([]) - nachrichten, 4)
