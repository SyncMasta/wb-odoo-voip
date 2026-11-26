from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import psycopg2

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import freeze_time, tagged
from odoo.tools import mute_logger

from odoo.addons.wb_voip_ansitel.models import ansitel_api

from .common import TOKEN, URL, AnsitelCase, antwort

LOGGER_ANLAGE = 'odoo.addons.wb_voip_ansitel.models.wb_voip_anlage'
LOGGER_ANRUF = 'odoo.addons.wb_voip_ansitel.models.wb_voip_anruf'
JETZT = datetime(2026, 11, 25, 10, 0, 0)  # UTC
BASIS = JETZT - timedelta(minutes=20)
BERLIN = ZoneInfo('Europe/Berlin')
UTC = ZoneInfo('UTC')
# Erfundene Geräte und Amtsleitung (keine echte Anlage)
TRUNK = 'amt-test'


def lokal(zeit, zone=BERLIN):
    """UTC (naive) -> local time text as the PBX writes it."""
    return zeit.replace(tzinfo=UTC).astimezone(zone).strftime('%Y-%m-%d %H:%M:%S')


# Rufnummern nur aus dem Bereich, den die Bundesnetzagentur für fiktive Nutzung (Film, Funk,
# Fernsehen, Beispiele) bereitstellt: Berlin 030 23125 000 bis 030 23125 999.
def nummer(i):
    """Fictitious national number of customer ``i`` (030 23125 000-999, Bundesnetzagentur)."""
    return '03023125%03d' % (i % 1000)


def e164(i):
    return '+493023125%03d' % (i % 1000)


# Weiterleitungsziel, ebenfalls aus dem fiktiven Bereich
ZIEL = '03023125999'


def cdr(lid, zeit, dst, kanal, disp, src, billsec=0, direction='incoming', duration=None,
        lastapp='Dial'):
    """One fake CDR leg (``zeit`` in UTC)."""
    return {
        'calldate': lokal(zeit), 'src': src, 'dst': dst, 'dstchannel': kanal,
        'disposition': disp, 'linkedid': lid, 'billsec': str(billsec),
        'duration': str(billsec + 12 if duration is None else duration),
        'direction': direction, 'sequence': '1', 'lastapp': lastapp,
    }


class FakeCdr:
    """Fake PBX: ``getcdr`` returns the stored legs whose ``calldate`` lies in the window."""

    def __init__(self):
        self.zeilen = []
        self.aufrufe = []
        self.fehler_code = None
        self.peers = []

    def __call__(self, _sitzung, url, json=None, **_kwargs):
        self.aufrufe.append(dict(json, url=url))
        aktion = json['action']
        if aktion == 'getpeer':
            return antwort({'status': 200, 'data': self.peers})
        if aktion == 'getcdr':
            if self.fehler_code:
                return antwort({'status': self.fehler_code}, self.fehler_code)
            treffer = [z for z in self.zeilen
                       if json['datetimefrom'] <= z['calldate'] <= json['datetimeto']]
            return antwort({'status': 200, 'data': treffer})
        return antwort({'status': 200, 'data': None})

    def getcdr(self):
        return [a for a in self.aufrufe if a['action'] == 'getcdr']


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestCdrLesen(AnsitelCase):
    """Reading the call detail records: paging, time zone, errors, channel names."""

    def _zeile(self, i, calldate='2026-11-21 11:14:35'):
        return {'calldate': calldate, 'src': nummer(i), 'dst': '10',
                'dstchannel': 'PJSIP/Tel51-%06x' % i, 'disposition': 'ANSWERED',
                'linkedid': 'L%d' % i}

    def test_blaettern_und_ortszeit(self):
        def seiten(_s, _url, json=None, **_kw):
            if json['linepos'] == '0':
                return antwort({'status': 200, 'resultcount': 1001,
                                'data': [self._zeile(i) for i in range(1000)]})
            return antwort({'status': 200, 'resultcount': 1001, 'data': [self._zeile(1000)]})

        post = self._post(side_effect=seiten)
        zeilen = self.api._cdr_lesen(
            self.anlage, datetime(2026, 11, 21, 10, 0, 0), datetime(2026, 11, 21, 11, 0, 0))
        self.assertEqual(len(zeilen), 1001)
        aufrufe = [c.kwargs['json'] for c in post.call_args_list]
        self.assertEqual([a['linepos'] for a in aufrufe], ['0', '1000'])
        self.assertEqual({a['action'] for a in aufrufe}, {'getcdr'})
        # Fenster in Ortszeit (Winter: UTC+1)
        self.assertEqual((aufrufe[0]['datetimefrom'], aufrufe[0]['datetimeto']),
                         ('2026-11-21 11:00:00', '2026-11-21 12:00:00'))
        self.assertEqual(zeilen[0]['zeit_utc'], datetime(2026, 11, 21, 10, 14, 35))
        self.assertEqual(zeilen[0]['linkedid'], 'L0')

    def test_sommerzeit(self):
        self._post(antwort({'status': 200, 'resultcount': 1,
                            'data': [self._zeile(1, '2026-07-01 12:00:00')]}))
        zeilen = self.api._cdr_lesen(self.anlage, datetime(2026, 7, 1, 9), datetime(2026, 7, 1, 11))
        self.assertEqual(zeilen[0]['zeit_utc'], datetime(2026, 7, 1, 10, 0, 0))

    def test_rueckstellstunde(self):
        # 25.10.2026, 02:30 Ortszeit gibt es zweimal (Sommerzeit endet um 03:00)
        self._post(antwort({'status': 200, 'data': [self._zeile(1, '2026-10-25 02:30:10'),
                                                    self._zeile(2, '2026-10-25 04:00:00')]}))
        zeilen = self.api._cdr_lesen(self.anlage, datetime(2026, 10, 25, 0), datetime(2026, 10, 25, 3))
        self.assertEqual(zeilen[0]['zeit_utc'], datetime(2026, 10, 25, 0, 30, 10))
        self.assertEqual(zeilen[0]['zeit_utc_alt'], datetime(2026, 10, 25, 1, 30, 10))
        self.assertIsNone(zeilen[1]['zeit_utc_alt'])

    def test_zeitzone_einstellbar(self):
        self.anlage.ansitel_tz = 'UTC'
        post = self._post(antwort({'status': 200, 'data': [self._zeile(1, '2026-11-21 11:14:35')]}))
        zeilen = self.api._cdr_lesen(self.anlage, datetime(2026, 11, 21, 10), datetime(2026, 11, 21, 12))
        self.assertEqual(post.call_args.kwargs['json']['datetimefrom'], '2026-11-21 10:00:00')
        self.assertEqual(zeilen[0]['zeit_utc'], datetime(2026, 11, 21, 11, 14, 35))

    def test_antwortformen(self):
        von, bis = datetime(2026, 11, 21, 9), datetime(2026, 11, 21, 11)
        self._post(antwort({'status': 200, 'data': {'cdr': [self._zeile(2)], 'resultcount': 1}}))
        self.assertEqual(len(self.api._cdr_lesen(self.anlage, von, bis)), 1)
        self._post(antwort({'status': 200, 'data': [self._zeile(3), self._zeile(4)]}))
        self.assertEqual(len(self.api._cdr_lesen(self.anlage, von, bis)), 2)
        self._post(antwort({'status': 200, 'resultcount': 0, 'data': []}))
        self.assertEqual(self.api._cdr_lesen(self.anlage, von, bis), [])
        self._post(antwort({'status': 200, 'data': None}))
        self.assertEqual(self.api._cdr_lesen(self.anlage, von, bis), [])

    def test_ohne_resultcount_weiterblaettern(self):
        def seiten(_s, _url, json=None, **_kw):
            if json['linepos'] == '0':
                return antwort({'status': 200, 'data': [self._zeile(i) for i in range(1000)]})
            return antwort({'status': 200, 'data': [self._zeile(1000)]})

        self._post(side_effect=seiten)
        self.assertEqual(len(self.api._cdr_lesen(
            self.anlage, datetime(2026, 11, 21, 9), datetime(2026, 11, 21, 11))), 1001)

    @mute_logger('odoo.addons.wb_voip_ansitel.models.ansitel_api')
    def test_seitenobergrenze(self):
        post = self._post(side_effect=lambda *_a, **_kw: antwort(
            {'status': 200, 'data': [self._zeile(i) for i in range(1000)]}))
        with self.assertRaises(ansitel_api.AnsitelFehler):
            self.api._cdr_lesen(self.anlage, datetime(2026, 11, 21, 9), datetime(2026, 11, 21, 11))
        self.assertEqual(post.call_count, ansitel_api.CDR_MAX_SEITEN)

    @mute_logger('odoo.addons.wb_voip_ansitel.models.ansitel_api')
    def test_fehler(self):
        von, bis = datetime(2026, 11, 21, 9), datetime(2026, 11, 21, 11)
        self._post(antwort({'status': 400}, 400))
        with self.assertRaises(ansitel_api.AnsitelFehler) as fang:
            self.api._cdr_lesen(self.anlage, von, bis)
        self.assertNotIn(TOKEN, str(fang.exception))
        for kaputt in ([{'calldate': 'kaputt'}], [{'src': '1'}], ['keine Zeile'], 'text'):
            self._post(antwort({'status': 200, 'data': kaputt}))
            with self.assertRaises(ansitel_api.AnsitelFehler) as fang:
                self.api._cdr_lesen(self.anlage, von, bis)
            self.assertEqual(str(fang.exception), 'Ansitel hat unverständlich geantwortet.')
            self.assertIsNone(fang.exception.__cause__)

    def test_kanal_geraet(self):
        for kanal, soll in [('PJSIP/Tel51-000000a1', 'Tel51'), ('SIP/Tel52-0001', 'Tel52'),
                            ('Local/03023125999@default-000a;1', ansitel_api.WEITERLEITUNG),
                            ('PJSIP/amt-test-0000', 'amt-test'), ('PJSIP/ohnenummer', 'ohnenummer'),
                            ('IAX2/Tel53-0001', None), ('DAHDI/1-1', None), ('PJSIP', None),
                            ('', None), (None, None), ('  ', None)]:
            self.assertEqual(self.api._kanal_geraet(kanal), soll, kanal)


class AbgleichCase(AnsitelCase):
    """Users A and B on the Ansitel PBX with softphone accounts, desk phones and a fake CDR."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_a.write({'wb_voip_sip_benutzer': 'TelA', 'wb_voip_ansitel_nebenstelle': '10'})
        cls.user_b.write({'wb_voip_anlage_id': cls.anlage.id, 'wb_voip_sip_benutzer': 'TelB',
                          'wb_voip_sip_passwort': 'geheim', 'wb_voip_ansitel_nebenstelle': '20'})
        Geraet = cls.env['wb.voip.geraet']
        for peer, name, user in [('TelA', 'TelA', cls.user_a), ('TischA', 'Tisch A', cls.user_a),
                                 ('TischB', 'Tisch B', cls.user_b), ('Empfang', 'Empfang', None)]:
            Geraet.create({'name': name, 'peer_name': peer, 'anlage_id': cls.anlage.id,
                           'user_id': user.id if user else False})
        cls.kunden = {i: cls.env['res.partner'].create({'name': f'Kunde {i}', 'phone': e164(i)})
                      for i in range(1, 30)}
        cls.Anruf = cls.env['wb.voip.anruf']
        cls.Protokoll = cls.env['wb.voip.protokoll']
        cls.typ = cls.env.ref('wb_voip.wb_voip_activity_type_rueckruf')

    def setUp(self):
        super().setUp()
        self._lizenz_gueltig()
        self.fake = FakeCdr()
        self._post(side_effect=self.fake)
        self._uhr = None
        self.addCleanup(lambda: self._uhr and self._uhr.stop())
        self.zeit_setzen(JETZT)
        self._nr = 0
        self.bus = []
        Users = type(self.env['res.users'])
        original = Users._bus_send
        meldungen = self.bus

        def mitschreiben(benutzer, typ, nutzlast, *args, **kwargs):
            meldungen.append((benutzer.id, typ))
            return original(benutzer, typ, nutzlast, *args, **kwargs)

        patcher = patch.object(Users, '_bus_send', mitschreiben)
        patcher.start()
        self.addCleanup(patcher.stop)

    def zeit_setzen(self, zeit):
        if self._uhr:
            self._uhr.stop()
        self._uhr = freeze_time(zeit)
        self._uhr.start()

    # --- Hilfen ---

    def lauf(self, zeilen=None):
        if zeilen is not None:
            self.fake.zeilen[:] = zeilen
        self.fake.aufrufe.clear()
        self.anlage.sudo()._wb_voip_ansitel_abgleich()
        self.env.flush_all()
        self.env.invalidate_all()

    def softphone(self, user, kunde, zeit):
        """A call as the softphone would have created it, started at ``zeit``."""
        self._nr += 1
        Anruf = self.Anruf.with_user(user)
        daten = Anruf.wb_voip_beginnen('eingehend', e164(kunde), f'abgleich-{self._nr}', False, False)
        anruf = Anruf.browse(daten['id'])
        anruf.sudo().write({'beginn': zeit})
        return anruf

    def verpasst(self, user, kunde, zeit):
        anruf = self.softphone(user, kunde, zeit)
        self.assertEqual(anruf.wb_voip_beendet(False, '487')['zustand'], 'verpasst')
        return anruf

    def von_gruppe(self, lid, user=None):
        domain = [('wb_voip_cdr_linkedid', '=', lid)]
        if user:
            domain.append(('user_id', '=', user.id))
        return self.Anruf.sudo().search(domain)

    def aktivitaeten(self, kunde):
        return self.env['mail.activity'].sudo().with_context(active_test=False).search([
            ('activity_type_id', '=', self.typ.id), ('res_model', '=', 'res.partner'),
            ('res_id', '=', self.kunden[kunde].id)])

    def zaehlen(self):
        """Counts that a second run over the same window must not change."""
        return (
            self.Anruf.sudo().search_count([]),
            self.env['mail.activity'].sudo().with_context(active_test=False).search_count(
                [('activity_type_id', '=', self.typ.id)]),
            self.Protokoll.sudo().search_count([]),
            self.env['mail.message'].sudo().search_count(
                [('model', '=', 'res.partner'), ('res_id', 'in', [k.id for k in self.kunden.values()])]),
        )


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestAbgleichFenster(AbgleichCase):
    """Window: first run, lower bound, overlap, quiescence, state written by SQL."""

    def test_erster_lauf_und_untergrenze(self):
        anlage = self.anlage.sudo()
        self.assertFalse(anlage.ansitel_cdr_stand)
        schreibdatum = anlage.write_date
        self.lauf([cdr('L1', BASIS, '10', 'PJSIP/TelA-000001', 'NO ANSWER', nummer(1))])
        aufruf = self.fake.getcdr()[0]
        self.assertEqual((aufruf['datetimefrom'], aufruf['datetimeto']),
                         (lokal(JETZT - timedelta(minutes=30)), lokal(JETZT - timedelta(seconds=60))))
        self.assertEqual(anlage.ansitel_cdr_stand, JETZT - timedelta(seconds=60))
        self.assertEqual(anlage.ansitel_cdr_ab, JETZT - timedelta(minutes=30))
        # Per SQL: Schreibdatum (Schlüssel des Peer-Caches) bleibt
        self.assertEqual(anlage.write_date, schreibdatum)
        # Zweiter Lauf 5 min später: Stand − 4 h, aber nie vor die Untergrenze
        self.zeit_setzen(JETZT + timedelta(minutes=5))
        self.lauf()
        aufruf = self.fake.getcdr()[0]
        self.assertEqual((aufruf['datetimefrom'], aufruf['datetimeto']),
                         (lokal(JETZT - timedelta(minutes=30)),
                          lokal(JETZT + timedelta(minutes=5) - timedelta(seconds=60))))
        # Später: Überlappung 4 h vor dem Stand, sobald das hinter der Untergrenze liegt
        self.zeit_setzen(JETZT + timedelta(hours=5))
        self.lauf()
        stand = JETZT + timedelta(hours=5) - timedelta(seconds=60)
        self.assertEqual(anlage.ansitel_cdr_stand, stand)
        self.zeit_setzen(JETZT + timedelta(hours=5, minutes=5))
        self.lauf()
        aufruf = self.fake.getcdr()[0]
        self.assertEqual(aufruf['datetimefrom'], lokal(stand - timedelta(hours=4)))
        self.assertEqual(anlage.ansitel_cdr_ab, JETZT - timedelta(minutes=30))

    def test_erster_lauf_scheitert_ohne_untergrenze(self):
        self.fake.fehler_code = 500
        with mute_logger('odoo.addons.wb_voip_ansitel.models.ansitel_api', LOGGER_ANLAGE):
            self.lauf([])
        anlage = self.anlage.sudo()
        self.assertFalse(anlage.ansitel_cdr_ab)
        self.assertFalse(anlage.ansitel_cdr_stand)
        self.fake.fehler_code = None
        self.zeit_setzen(JETZT + timedelta(hours=2))
        self.lauf()
        self.assertEqual(anlage.ansitel_cdr_ab, JETZT + timedelta(hours=2) - timedelta(minutes=30))

    def test_zu_viele_anrufe_fenster_halbiert(self):
        def post(_s, _url, json=None, **_kw):
            von = datetime.strptime(json['datetimefrom'], '%Y-%m-%d %H:%M:%S')
            bis = datetime.strptime(json['datetimeto'], '%Y-%m-%d %H:%M:%S')
            self.fake.aufrufe.append(dict(json))
            if bis - von > timedelta(minutes=15):
                return antwort({'status': 200, 'data': VOLL})  # immer volle Seiten
            return antwort({'status': 200, 'data': []})

        VOLL = [cdr('V', BASIS, '10', 'PJSIP/TelA-1', 'NO ANSWER', nummer(1))] * 1000
        self._post(side_effect=post)
        self.lauf()
        # 29 min -> 14,5 min: gelesen, Stand = Ende des kleineren Fensters
        von = JETZT - timedelta(minutes=30)
        bis = von + (JETZT - timedelta(seconds=60) - von) / 2
        self.assertEqual(self.anlage.sudo().ansitel_cdr_stand, bis)
        self.assertEqual(self.fake.aufrufe[-1]['datetimeto'], lokal(bis))

    def test_zu_viele_anrufe_auch_klein(self):
        VOLL = [cdr('V', BASIS, '10', 'PJSIP/TelA-1', 'NO ANSWER', nummer(1))] * 1000
        self._post(side_effect=lambda *_a, **_kw: antwort({'status': 200, 'data': VOLL}))
        with self.assertLogs(LOGGER_ANLAGE, level='ERROR') as logs:
            self.lauf()
        self.assertEqual(len([z for z in logs.output if 'ERROR' in z]), 1)
        # aufgegeben bei 10 min, der Stand geht trotzdem weiter
        von = JETZT - timedelta(minutes=30)
        self.assertEqual(self.anlage.sudo().ansitel_cdr_stand, von + timedelta(minutes=10))
        self.assertFalse(self.von_gruppe('V'))

    def _stand_setzen(self, stand, ab):
        self.env.cr.execute(
            "UPDATE wb_voip_anlage SET ansitel_cdr_stand = %s, ansitel_cdr_ab = %s WHERE id = %s",
            [stand, ab, self.anlage.id])
        self.anlage.invalidate_recordset()

    def test_zu_viele_in_der_ueberlappung(self):
        # Stand 5 min alt; die Überlappung (4 h davor) ist voll, ab dem Stand ist es wenig
        stand = JETZT - timedelta(minutes=5)
        self._stand_setzen(stand, JETZT - timedelta(days=1))
        VOLL = [cdr('V', BASIS, '10', 'PJSIP/TelA-1', 'NO ANSWER', nummer(1))] * 1000

        def post(_s, _url, json=None, **_kw):
            self.fake.aufrufe.append(dict(json))
            if json['datetimefrom'] < lokal(stand):
                return antwort({'status': 200, 'data': VOLL})
            return antwort({'status': 200, 'data': []})

        self._post(side_effect=post)
        self.lauf()
        # zweiter Versuch ohne Überlappung, ganzes Fenster bis jetzt − 60 s
        self.assertEqual((self.fake.aufrufe[-1]['datetimefrom'], self.fake.aufrufe[-1]['datetimeto']),
                         (lokal(stand), lokal(JETZT - timedelta(seconds=60))))
        self.assertEqual(self.anlage.sudo().ansitel_cdr_stand, JETZT - timedelta(seconds=60))

    def test_zu_viele_stand_geht_nie_zurueck(self):
        # Stand 2 h alt, alles voll: nie unter den alten Stand, das kleinste Fenster wird übersprungen
        stand = JETZT - timedelta(hours=2)
        self._stand_setzen(stand, JETZT - timedelta(days=1))
        VOLL = [cdr('V', BASIS, '10', 'PJSIP/TelA-1', 'NO ANSWER', nummer(1))] * 1000
        self._post(side_effect=lambda *_a, **_kw: antwort({'status': 200, 'data': VOLL}))
        with self.assertLogs(LOGGER_ANLAGE, level='ERROR') as logs:
            self.lauf()
        self.assertEqual(len(logs.output), 1)
        neu = self.anlage.sudo().ansitel_cdr_stand
        self.assertGreater(neu, stand)
        self.assertEqual(neu, stand + timedelta(minutes=10))
        # nächster Lauf beginnt wieder 4 h vor dem neuen Stand, rückt aber weiter vor
        with self.assertLogs(LOGGER_ANLAGE, level='ERROR'):
            self.lauf()
        self.assertEqual(self.anlage.sudo().ansitel_cdr_stand, neu + timedelta(minutes=10))

    def test_sql_nur_erlaubte_felder(self):
        with self.assertRaises(ValueError):
            self.anlage.sudo()._wb_voip_ansitel_sql_setzen('name', 'x')

    def test_rueckstellstunde_findet_softphone_anruf(self):
        # Zweites Vorkommen von 02:30 Ortszeit = 01:30 UTC (Winterzeit)
        jetzt = datetime(2026, 10, 25, 1, 50, 0)
        self.zeit_setzen(jetzt)
        beginn = datetime(2026, 10, 25, 1, 30, 10)
        anruf = self.softphone(self.user_a, 26, beginn)
        self.lauf([dict(cdr('D1', beginn, '10', 'PJSIP/TelA-000030', 'NO ANSWER', nummer(26)),
                        calldate='2026-10-25 02:30:10')])
        self.assertEqual(self.von_gruppe('D1', self.user_a), anruf)
        self.assertEqual(anruf.zustand, 'verpasst')
        self.assertEqual(self.Anruf.sudo().search_count([('nummer_e164', '=', e164(26))]), 1)

    def test_hoechstens_sechs_stunden(self):
        self.env.cr.execute(
            "UPDATE wb_voip_anlage SET ansitel_cdr_stand = %s, ansitel_cdr_ab = %s WHERE id = %s",
            [JETZT - timedelta(days=2), JETZT - timedelta(days=3), self.anlage.id])
        self.anlage.invalidate_recordset()
        self.lauf([])
        aufruf = self.fake.getcdr()[0]
        von = JETZT - timedelta(days=2) - timedelta(hours=4)
        self.assertEqual((aufruf['datetimefrom'], aufruf['datetimeto']),
                         (lokal(von), lokal(von + timedelta(hours=6))))
        self.assertEqual(self.anlage.sudo().ansitel_cdr_stand, von + timedelta(hours=6))

    def test_ruhe(self):
        # Gruppe endet erst 30 s vor jetzt: noch nicht abgeschlossen (Ruhe 60 s)
        beginn = JETZT - timedelta(seconds=72)
        zeilen = [cdr('L2', beginn, '10', 'PJSIP/TelA-000002', 'NO ANSWER', nummer(2), duration=42)]
        self.lauf(zeilen)
        self.assertFalse(self.von_gruppe('L2'))
        self.zeit_setzen(JETZT + timedelta(minutes=5))
        self.lauf()
        self.assertEqual(self.von_gruppe('L2', self.user_a).zustand, 'verpasst')

    def test_nur_gruppen_mit_eingehendem_zweig(self):
        self.lauf([cdr('L3', BASIS, nummer(3), f'PJSIP/{TRUNK}-01', 'ANSWERED', '10',
                       billsec=20, direction='outgoing')])
        self.assertFalse(self.von_gruppe('L3'))

    def test_stand_nur_fuer_systemverwaltung(self):
        self.lauf([])
        with self.assertRaises(AccessError):
            self.anlage.with_user(self.user_manager).read(['ansitel_cdr_stand'])
        with self.assertRaises(AccessError):
            self.anlage.with_user(self.user_manager).read(['ansitel_cdr_ab'])

    def test_cron(self):
        cron = self.env.ref('wb_voip_ansitel.ir_cron_ansitel_abgleich')
        self.assertEqual((cron.interval_number, cron.interval_type), (5, 'minutes'))
        self.assertEqual(cron.model_id.model, 'wb.voip.anlage')
        self.assertIn('_wb_voip_ansitel_abgleich_alle', cron.code)


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestAbgleichRobust(AbgleichCase):
    """One failing PBX does not stop the others; logs without secrets; no licence needed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.anlage2 = cls.env['wb.voip.anlage'].create({
            'name': 'Zweite Anlage', 'modus': 'test', 'ws_url': 'wss://anlage2.test/ws',
            'sip_domain': 'anlage2.test', 'ansitel_aktiv': True,
            'ansitel_api_url': 'https://pbx2.example.invalid/awi/api/rest', 'ansitel_token': TOKEN})
        cls.user_b.wb_voip_anlage_id = cls.anlage2

    def test_fehler_einer_anlage(self):
        zeilen = [cdr('L1', BASIS, '20', 'PJSIP/TelB-000001', 'NO ANSWER', nummer(1))]
        self.fake.zeilen[:] = zeilen

        def post(sitzung, url, json=None, **kw):
            if url == URL and json['action'] == 'getcdr':
                return antwort({'status': 500}, 500)
            return self.fake(sitzung, url, json=json, **kw)

        self._post(side_effect=post)
        with self.assertLogs('odoo.addons.wb_voip_ansitel', level='DEBUG') as logs:
            self.env['wb.voip.anlage']._wb_voip_ansitel_abgleich_alle()
        self.env.invalidate_all()
        # Anlage 1: Stand bleibt leer, Anlage 2 abgeglichen
        self.assertFalse(self.anlage.sudo().ansitel_cdr_stand)
        self.assertTrue(self.anlage2.sudo().ansitel_cdr_stand)
        self.assertEqual(self.von_gruppe('L1', self.user_b).zustand, 'verpasst')
        text = '\n'.join(logs.output)
        for geheim in (TOKEN, URL, 'pbx2.example.invalid', nummer(1), e164(1)):
            self.assertNotIn(geheim, text)

    def test_unerwartete_ausnahme_einer_anlage(self):
        original = type(self.env['wb.voip.anlage'])._wb_voip_ansitel_abgleich

        def abgleich(anlagen):
            if anlagen == self.anlage:
                raise RuntimeError(f'kaputt {TOKEN}')
            return original(anlagen)

        self.fake.zeilen[:] = [cdr('L1', BASIS, '20', 'PJSIP/TelB-000001', 'NO ANSWER', nummer(1))]
        with patch.object(type(self.env['wb.voip.anlage']), '_wb_voip_ansitel_abgleich', abgleich), \
                self.assertLogs(LOGGER_ANLAGE, level='WARNING') as logs:
            self.env['wb.voip.anlage']._wb_voip_ansitel_abgleich_alle()
        self.assertEqual(self.von_gruppe('L1', self.user_b).zustand, 'verpasst')
        self.assertNotIn(TOKEN, '\n'.join(logs.output))

    def test_inaktive_anlage_wird_nicht_gelesen(self):
        self.anlage2.sudo().ansitel_aktiv = False
        self.env['wb.voip.anlage']._wb_voip_ansitel_abgleich_alle()
        self.assertEqual({a['url'] for a in self.fake.getcdr()}, {URL})

    def test_ohne_lizenz(self):
        # Abgleichen liest nur und schreibt die Anrufliste; es schaltet nichts an der Anlage
        self._lizenz_fehlt()
        self.lauf([cdr('L1', BASIS, '10', 'PJSIP/TelA-000001', 'NO ANSWER', nummer(1))])
        self.assertEqual(self.von_gruppe('L1', self.user_a).zustand, 'verpasst')

    def test_fehler_einer_gruppe(self):
        original = type(self.Anruf)._wb_voip_cdr_gruppe

        def gruppe(modell, anlage, zweige):
            if zweige[0]['linkedid'] == 'L1':
                raise ValueError('kaputt')
            return original(modell, anlage, zweige)

        zeilen = [cdr('L1', BASIS, '10', 'PJSIP/TelA-000001', 'NO ANSWER', nummer(1)),
                  cdr('L2', BASIS, '10', 'PJSIP/TelA-000002', 'NO ANSWER', nummer(2))]
        with patch.object(type(self.Anruf), '_wb_voip_cdr_gruppe', gruppe), \
                self.assertLogs(LOGGER_ANLAGE, level='WARNING'):
            self.lauf(zeilen)
        self.assertFalse(self.von_gruppe('L1'))
        self.assertTrue(self.von_gruppe('L2'))
        self.assertTrue(self.anlage.sudo().ansitel_cdr_stand)


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestAbgleichRegeln(AbgleichCase):
    """Answer detection and the transitions of the Phase-4 missed-call logic."""

    def test_odoo_zu_niemand_nimmt_an(self):
        self.lauf([cdr('L1', BASIS, '10', 'PJSIP/TelA-000001', 'NO ANSWER', nummer(1))])
        anruf = self.von_gruppe('L1', self.user_a)
        self.assertEqual(len(anruf), 1)
        self.assertEqual((anruf.zustand, anruf.wb_voip_herkunft, anruf.beginn, anruf.richtung),
                         ('verpasst', 'anlage', BASIS, 'eingehend'))
        self.assertEqual((anruf.nummer, anruf.nummer_e164), (nummer(1), e164(1)))
        self.assertEqual(anruf.partner_id, self.kunden[1])
        self.assertEqual(anruf.company_id, self.user_a.company_id)
        self.assertEqual(anruf.ende, BASIS + timedelta(seconds=12))
        self.assertTrue(anruf.rueckruf_aktivitaet_id.active)
        self.assertEqual(anruf.rueckruf_aktivitaet_id.user_id, self.user_a)
        self.assertEqual((anruf.protokoll_id.ergebnis, anruf.protokoll_id.datum),
                         ('nicht_erreicht', BASIS))
        self.assertFalse(self.von_gruppe('L1', self.user_b))
        # Phase-4-Liste und Zähler
        Anruf = self.Anruf.with_user(self.user_a)
        self.assertEqual(Anruf.wb_voip_verpasst_zaehler(), 1)
        zeile = Anruf.wb_voip_verpasst_liste()[0]
        self.assertEqual((zeile['art'], zeile['anruf_id'], zeile['geraet']), ('verpasst', anruf.id, False))
        self.assertIn((self.user_a.id, 'wb_voip/verpasst'), self.bus)

    def test_softphone_verpasst_bleibt(self):
        anruf = self.verpasst(self.user_a, 2, BASIS)
        aktivitaet, protokoll = anruf.rueckruf_aktivitaet_id, anruf.protokoll_id
        self.lauf([cdr('L2', BASIS + timedelta(seconds=1), '10', 'PJSIP/TelA-000002', 'NO ANSWER', nummer(2))])
        self.assertEqual(self.von_gruppe('L2', self.user_a), anruf)
        self.assertEqual(anruf.zustand, 'verpasst')
        self.assertEqual(anruf.wb_voip_herkunft, 'softphone')
        self.assertEqual((anruf.rueckruf_aktivitaet_id, anruf.protokoll_id), (aktivitaet, protokoll))

    def test_klingelt_ohne_tab_wird_verpasst(self):
        anruf = self.softphone(self.user_a, 3, BASIS)
        self.lauf([cdr('L3', BASIS, '10', 'PJSIP/TelA-000003', 'NO ANSWER', nummer(3))])
        self.assertEqual(anruf.zustand, 'verpasst')
        self.assertTrue(anruf.rueckruf_aktivitaet_id)
        self.assertEqual(anruf.protokoll_id.ergebnis, 'nicht_erreicht')

    def test_eigenes_anderes_geraet(self):
        anruf = self.verpasst(self.user_a, 4, BASIS)
        aktivitaet, protokoll = anruf.rueckruf_aktivitaet_id, anruf.protokoll_id
        self.assertTrue(aktivitaet and protokoll)
        self.lauf([cdr('L4', BASIS + timedelta(seconds=1), '10', 'PJSIP/TelA-000004', 'NO ANSWER', nummer(4)),
                   cdr('L4', BASIS + timedelta(seconds=1), '10', 'PJSIP/TischA-000005', 'ANSWERED',
                       nummer(4), billsec=240)])
        self.assertEqual(self.von_gruppe('L4', self.user_a), anruf)
        self.assertEqual((anruf.zustand, anruf.wb_voip_geraet, anruf.dauer_sek),
                         ('anderswo', 'Tisch A', 240))
        self.assertFalse(aktivitaet.exists())
        self.assertFalse(protokoll.exists())
        self.assertFalse(anruf.angenommen_anruf_id)
        Anruf = self.Anruf.with_user(self.user_a)
        zeile = Anruf.wb_voip_verpasst_liste()[0]
        self.assertEqual((zeile['art'], zeile['geraet'], zeile['geraet_dauer_sek'], zeile['bemerkung_moeglich']),
                         ('anderswo', 'Tisch A', 240, True))
        self.assertEqual(Anruf.wb_voip_verpasst_zaehler(), 0)

    def test_eigenes_softphone_angenommen_unveraendert(self):
        anruf = self.softphone(self.user_a, 5, BASIS)
        anruf.wb_voip_angenommen()
        anruf.wb_voip_beendet('Gespräch', False)
        vorher = (anruf.zustand, anruf.annahme, anruf.ende, anruf.dauer_sek, anruf.protokoll_id)
        self.lauf([cdr('L5', BASIS - timedelta(seconds=2), '10', 'PJSIP/TelA-000006', 'ANSWERED',
                       nummer(5), billsec=60)])
        self.assertEqual(self.von_gruppe('L5', self.user_a), anruf)
        self.assertEqual((anruf.zustand, anruf.annahme, anruf.ende, anruf.dauer_sek, anruf.protokoll_id),
                         vorher)
        self.assertFalse(anruf.wb_voip_geraet)

    def test_eigenes_softphone_klingelt_wird_beendet(self):
        anruf = self.softphone(self.user_a, 6, BASIS)
        # 12 s Klingeln, 60 s Gespräch: Annahme = calldate + (duration − billsec)
        self.lauf([cdr('L6', BASIS, '10', 'PJSIP/TelA-000007', 'ANSWERED', nummer(6), billsec=60)])
        annahme = BASIS + timedelta(seconds=12)
        self.assertEqual((anruf.zustand, anruf.annahme, anruf.dauer_sek), ('beendet', annahme, 60))
        self.assertEqual(anruf.ende, annahme + timedelta(seconds=60))
        self.assertFalse(anruf.rueckruf_aktivitaet_id)
        self.assertEqual((anruf.protokoll_id.ergebnis, anruf.protokoll_id.dauer_sek), ('erreicht', 60))

    def test_eigenes_softphone_verpasst_wird_beendet(self):
        anruf = self.verpasst(self.user_a, 28, BASIS)
        aktivitaet, protokoll = anruf.rueckruf_aktivitaet_id, anruf.protokoll_id
        self.assertEqual(protokoll.ergebnis, 'nicht_erreicht')
        self.lauf([cdr('L28', BASIS, '10', 'PJSIP/TelA-000020', 'ANSWERED', nummer(28), billsec=45,
                       duration=50)])
        self.assertEqual((anruf.zustand, anruf.annahme, anruf.dauer_sek),
                         ('beendet', BASIS + timedelta(seconds=5), 45))
        # Verpasst-Folgen über den Kern zurückgenommen, neues Protokoll „erreicht“
        self.assertFalse(aktivitaet.exists())
        self.assertFalse(protokoll.exists())
        self.assertEqual((anruf.protokoll_id.ergebnis, anruf.protokoll_id.dauer_sek), ('erreicht', 45))
        self.assertEqual(self.Anruf.with_user(self.user_a).wb_voip_verpasst_liste(), [])

    def test_laeuft_nie_angefasst(self):
        anruf = self.softphone(self.user_a, 7, BASIS)
        anruf.wb_voip_angenommen()
        self.lauf([cdr('L7', BASIS, '10', 'PJSIP/TischA-000008', 'ANSWERED', nummer(7), billsec=60)])
        self.assertEqual(anruf.zustand, 'laeuft')
        self.assertFalse(anruf.wb_voip_cdr_linkedid)
        self.assertFalse(anruf.wb_voip_geraet)

    def test_kollege_nimmt_am_tischtelefon_an(self):
        anruf_a = self.verpasst(self.user_a, 8, BASIS)
        self.lauf([cdr('L8', BASIS, '10', 'PJSIP/TelA-000009', 'NO ANSWER', nummer(8)),
                   cdr('L8', BASIS, '20', 'PJSIP/TischB-00000a', 'ANSWERED', nummer(8), billsec=90)])
        anruf_b = self.von_gruppe('L8', self.user_b)
        self.assertEqual(len(anruf_b), 1)
        self.assertEqual((anruf_b.wb_voip_herkunft, anruf_b.zustand, anruf_b.wb_voip_geraet, anruf_b.beginn),
                         ('anlage', 'anderswo', 'Tisch B', BASIS))
        self.assertFalse(anruf_b.angenommen_anruf_id)
        self.assertEqual((anruf_a.zustand, anruf_a.angenommen_anruf_id, anruf_a.wb_voip_geraet),
                         ('anderswo', anruf_b, 'Tisch B'))
        self.assertFalse(anruf_a.rueckruf_aktivitaet_id)
        info = anruf_a.with_user(self.user_a)._wb_voip_angenommen_info()
        self.assertEqual((info['name'], info['geraet'], info['dauer_sek']), (self.user_b.name, 'Tisch B', 90))
        # A: Zeile nennt den Kollegen mit Gerät, nicht das Gerät allein; keine „Bemerkung“ (die schreibt B)
        zeile = self.Anruf.with_user(self.user_a).wb_voip_verpasst_liste()[0]
        self.assertEqual(zeile['kollege'], {'name': self.user_b.name, 'dauer_sek': 90, 'notiz': '',
                                            'geraet': 'Tisch B'})
        self.assertEqual((zeile['geraet'], zeile['geraet_dauer_sek'], zeile['bemerkung_moeglich']),
                         (False, False, False))
        zeile_b = self.Anruf.with_user(self.user_b).wb_voip_verpasst_liste()[0]
        self.assertTrue(zeile_b['bemerkung_moeglich'])

    def test_kollege_nimmt_im_softphone_konto_an(self):
        # B nimmt mit seinem Softphone-Konto an (z. B. anderes Gerät), Odoo hatte keinen Anruf
        anruf_a = self.verpasst(self.user_a, 9, BASIS)
        self.lauf([cdr('L9', BASIS, '10', 'PJSIP/TelA-00000b', 'NO ANSWER', nummer(9)),
                   cdr('L9', BASIS, '20', 'PJSIP/TelB-00000c', 'ANSWERED', nummer(9), billsec=30)])
        anruf_b = self.von_gruppe('L9', self.user_b)
        self.assertEqual((anruf_b.zustand, anruf_b.annahme, anruf_b.dauer_sek),
                         ('beendet', BASIS + timedelta(seconds=12), 30))
        self.assertEqual(anruf_b.protokoll_id.ergebnis, 'erreicht')
        self.assertEqual((anruf_a.zustand, anruf_a.angenommen_anruf_id, anruf_a.wb_voip_geraet),
                         ('anderswo', anruf_b, 'TelB'))
        # A sieht den Kollegen (ohne Gerät: sein Softphone), nicht „angenommen auf TelB“
        zeile = self.Anruf.with_user(self.user_a).wb_voip_verpasst_liste()[0]
        self.assertEqual(zeile['kollege'], {'name': self.user_b.name, 'dauer_sek': 30, 'notiz': ''})
        self.assertFalse(zeile['geraet'])

    def test_kollege_hat_im_odoo_softphone_angenommen(self):
        anruf_a = self.softphone(self.user_a, 10, BASIS)
        anruf_b = self.softphone(self.user_b, 10, BASIS + timedelta(seconds=1))
        anruf_b.wb_voip_angenommen()
        anruf_b.wb_voip_beendet(False, False)
        anruf_a.invalidate_recordset()
        vorher_b = (anruf_b.zustand, anruf_b.annahme, anruf_b.ende)
        self.lauf([cdr('L10', BASIS, '10', 'PJSIP/TelA-00000d', 'NO ANSWER', nummer(10)),
                   cdr('L10', BASIS, '20', 'PJSIP/TelB-00000e', 'ANSWERED', nummer(10), billsec=30)])
        self.assertEqual((anruf_b.zustand, anruf_b.annahme, anruf_b.ende), vorher_b)
        self.assertEqual(self.von_gruppe('L10', self.user_b), anruf_b)
        self.assertEqual((anruf_a.zustand, anruf_a.angenommen_anruf_id), ('anderswo', anruf_b))
        self.assertEqual(len(self.von_gruppe('L10')), 2)

    def test_gemeinsames_geraet_ohne_besitzer(self):
        alt = self.verpasst(self.user_b, 11, BASIS - timedelta(minutes=30))
        anruf = self.verpasst(self.user_a, 11, BASIS)
        self.lauf([cdr('L11', BASIS, '10', 'PJSIP/TelA-00000f', 'NO ANSWER', nummer(11)),
                   cdr('L11', BASIS, '30', 'PJSIP/Empfang-000010', 'ANSWERED', nummer(11), billsec=50)])
        self.assertEqual((anruf.zustand, anruf.wb_voip_geraet, anruf.dauer_sek), ('anderswo', 'Empfang', 50))
        self.assertFalse(anruf.angenommen_anruf_id)
        # kein Besitzer: neutraler Text ohne Benutzernamen
        self.assertTrue(alt.erledigt)
        self.assertEqual(alt.erledigt_text, 'Angenommen auf Empfang')
        self.assertFalse(self.von_gruppe('L11', self.user_b))

    def test_weiterleitung(self):
        self.lauf([cdr('L12', BASIS, '10', 'Local/03023125999@default-0001;1', 'NO ANSWER', nummer(12)),
                   cdr('L12', BASIS, ZIEL, f'PJSIP/{TRUNK}-000001', 'ANSWERED', nummer(12),
                       billsec=30, direction='outgoing')])
        anruf = self.von_gruppe('L12', self.user_a)
        self.assertEqual((len(anruf), anruf.zustand, anruf.wb_voip_geraet, anruf.dauer_sek),
                         (1, 'anderswo', 'Weiterleitung', 30))
        self.assertFalse(anruf.rueckruf_aktivitaet_id)
        self.assertFalse(anruf.protokoll_id)
        self.assertEqual(anruf.ende, BASIS + timedelta(seconds=42))

    def test_weiterleitung_erledigt_neutral(self):
        alt = self.verpasst(self.user_b, 29, BASIS - timedelta(minutes=30))
        self.lauf([cdr('L29', BASIS, '10', 'Local/03023125999@default-0009;1', 'NO ANSWER', nummer(29)),
                   cdr('L29', BASIS, ZIEL, f'PJSIP/{TRUNK}-000009', 'ANSWERED', nummer(29),
                       billsec=30, direction='outgoing')])
        self.assertEqual(alt.erledigt_text, 'Angenommen auf Weiterleitung')

    def test_rueckruf_erledigt_dann_anderswo_oeffnet_wieder(self):
        anruf = self.verpasst(self.user_a, 27, BASIS)
        # Rückruf erreicht: Eintrag erledigt, Protokoll gemerkt
        Anruf = self.Anruf.with_user(self.user_a)
        daten = Anruf.wb_voip_beginnen('ausgehend', e164(27), 'rueckruf-27', False, False)
        rueckruf = Anruf.browse(daten['id'])
        rueckruf.wb_voip_angenommen()
        rueckruf.wb_voip_beendet(False, False)
        protokoll = rueckruf.sudo().protokoll_id
        self.assertTrue(anruf.erledigt)
        self.assertEqual(anruf.erledigt_durch_protokoll_id, protokoll)
        # Abgleich: in Wahrheit am Tischtelefon angenommen
        self.lauf([cdr('L27', BASIS, '10', 'PJSIP/TischA-00001b', 'ANSWERED', nummer(27), billsec=20)])
        self.assertEqual((anruf.zustand, anruf.erledigt), ('anderswo', True))
        aktivitaeten = self.aktivitaeten(27)
        # Rückruf-Ergebnis geändert: Eintrag wieder offen, ohne neue Rückruf-Aktivität
        protokoll.with_user(self.user_a).ergebnis = 'mailbox'
        self.assertFalse(anruf.erledigt)
        self.assertFalse(anruf.rueckruf_aktivitaet_id)
        self.assertEqual(self.aktivitaeten(27), aktivitaeten)
        zeile = Anruf.wb_voip_verpasst_liste()[0]
        self.assertEqual((zeile['anruf_id'], zeile['art'], zeile['geraet']), (anruf.id, 'anderswo', 'Tisch A'))
        # zurück auf „erreicht“: wieder erledigt, keine neue Aktivität
        protokoll.with_user(self.user_a).ergebnis = 'erreicht'
        self.assertTrue(anruf.erledigt)
        self.assertEqual(anruf.erledigt_durch_protokoll_id, protokoll)
        self.assertEqual(self.aktivitaeten(27), aktivitaeten)
        self.assertEqual(Anruf.wb_voip_verpasst_liste(), [])

    def test_weiterleitung_eingehend_auf_amt(self):
        self.lauf([cdr('L13', BASIS, '10', f'PJSIP/{TRUNK}-000002', 'NO ANSWER', nummer(13)),
                   cdr('L13', BASIS, ZIEL, f'PJSIP/{TRUNK}-000003', 'ANSWERED', nummer(13),
                       billsec=15, direction='outgoing')])
        self.assertEqual(self.von_gruppe('L13', self.user_a).wb_voip_geraet, 'Weiterleitung')

    def test_weiterleitung_ohne_gespraech_nicht_angenommen(self):
        self.lauf([cdr('L14', BASIS, '10', 'Local/03023125999@default-0003;1', 'NO ANSWER', nummer(14)),
                   cdr('L14', BASIS, ZIEL, f'PJSIP/{TRUNK}-000004', 'ANSWERED', nummer(14),
                       billsec=0, direction='outgoing')])
        self.assertEqual(self.von_gruppe('L14', self.user_a).zustand, 'verpasst')

    def test_local_ohne_amt_nicht_angenommen(self):
        self.lauf([cdr('L15', BASIS, '10', 'Local/gruppe@default-0004;1', 'ANSWERED', nummer(15), billsec=20)])
        self.assertEqual(self.von_gruppe('L15', self.user_a).zustand, 'verpasst')

    def test_anrufbeantworter_und_warteschlange_nicht_angenommen(self):
        alt = self.verpasst(self.user_b, 16, BASIS - timedelta(minutes=30))
        self.lauf([
            cdr('L16', BASIS, '10', 'PJSIP/TelA-000011', 'NO ANSWER', nummer(16)),
            cdr('L16', BASIS + timedelta(seconds=20), '10', 'Local/vm-10@default-0005;1', 'ANSWERED',
                nummer(16), billsec=25, lastapp='VoiceMail'),
            cdr('L17', BASIS, '10', 'PJSIP/TischA-000012', 'ANSWERED', nummer(17), billsec=25,
                lastapp='Queue'),
        ])
        anruf = self.von_gruppe('L16', self.user_a)
        self.assertEqual((anruf.zustand, anruf.wb_voip_geraet), ('verpasst', False))
        self.assertTrue(anruf.rueckruf_aktivitaet_id.active)
        self.assertFalse(alt.erledigt)
        self.assertEqual(self.von_gruppe('L17', self.user_a).zustand, 'verpasst')

    def test_amtsleitung_ist_kein_geraet(self):
        # Ausgehender Zweig auf einem unbekannten Gerät = Amtsleitung; ein Dial darauf ist keine Annahme
        self.lauf([cdr('L18', BASIS, '10', f'PJSIP/{TRUNK}-000005', 'ANSWERED', nummer(18), billsec=0),
                   cdr('L18', BASIS, ZIEL, f'PJSIP/{TRUNK}-000006', 'NO ANSWER', nummer(18),
                       direction='outgoing')])
        self.assertEqual(self.von_gruppe('L18', self.user_a).zustand, 'verpasst')

    def test_annahme_erledigt_offene_eintraege_bis_ende(self):
        frueher = self.verpasst(self.user_b, 19, BASIS - timedelta(minutes=30))
        spaeter = self.verpasst(self.user_b, 19, BASIS + timedelta(minutes=10))
        self.lauf([cdr('L19', BASIS, '10', 'PJSIP/TischA-000013', 'ANSWERED', nummer(19), billsec=60)])
        anruf = self.von_gruppe('L19', self.user_a)
        self.assertEqual((anruf.zustand, anruf.wb_voip_geraet), ('anderswo', 'Tisch A'))
        self.assertTrue(frueher.erledigt)
        self.assertEqual(frueher.erledigt_text, f'Answered by {self.user_a.name}')
        self.assertFalse(spaeter.erledigt)
        # beide teilen sich die Rückruf-Aktivität am Kontakt; der offene Eintrag hält sie offen
        self.assertTrue(spaeter.rueckruf_aktivitaet_id.active)

    def test_unterdrueckte_nummer(self):
        mit_nummer = self.softphone(self.user_a, 20, BASIS)
        self.lauf([cdr('L20', BASIS, '10', 'PJSIP/TelA-000014', 'NO ANSWER', 'anonymous')])
        anruf = self.von_gruppe('L20', self.user_a)
        self.assertNotEqual(anruf, mit_nummer)
        self.assertEqual((anruf.zustand, anruf.nummer, anruf.nummer_e164), ('verpasst', False, False))
        self.assertFalse(anruf.rueckruf_aktivitaet_id)
        self.assertEqual(mit_nummer.zustand, 'klingelt')

    def test_unbeteiligte_und_fremde_anlage(self):
        anlage2 = self.env['wb.voip.anlage'].create({
            'name': 'Fremde Anlage', 'modus': 'test', 'ws_url': 'wss://anlage3.test/ws',
            'sip_domain': 'anlage3.test'})
        self.user_b.wb_voip_anlage_id = anlage2
        self.lauf([cdr('L21', BASIS, '20', 'PJSIP/TelB-000015', 'NO ANSWER', nummer(21))])
        self.assertFalse(self.von_gruppe('L21'))

    def test_zweimal_dasselbe_fenster(self):
        """Review Focus 4: a second run over the same window changes nothing."""
        self.verpasst(self.user_a, 22, BASIS)
        self.verpasst(self.user_a, 23, BASIS + timedelta(minutes=1))
        self.verpasst(self.user_b, 25, BASIS - timedelta(minutes=10))
        zeilen = [
            cdr('L22', BASIS, '10', 'PJSIP/TelA-000016', 'NO ANSWER', nummer(22)),
            cdr('L23', BASIS + timedelta(minutes=1), '10', 'PJSIP/TischA-000017', 'ANSWERED', nummer(23), billsec=40),
            cdr('L24', BASIS + timedelta(minutes=2), '10', 'PJSIP/TelA-000018', 'NO ANSWER', nummer(24)),
            cdr('L25', BASIS + timedelta(minutes=3), '10', 'PJSIP/TelA-000019', 'NO ANSWER', nummer(25)),
            cdr('L25', BASIS + timedelta(minutes=3), '20', 'PJSIP/TischB-00001a', 'ANSWERED', nummer(25), billsec=70),
            cdr('L26', BASIS + timedelta(minutes=4), '10', 'Local/03023125999@default-0006;1', 'NO ANSWER', nummer(26)),
            cdr('L26', BASIS + timedelta(minutes=4), ZIEL, f'PJSIP/{TRUNK}-000007', 'ANSWERED', nummer(26),
                billsec=10, direction='outgoing'),
        ]
        self.lauf(zeilen)
        vorher = self.zaehlen()
        zustaende = {a.id: (a.zustand, a.wb_voip_geraet, a.angenommen_anruf_id.id, a.erledigt, a.ende,
                            a.rueckruf_aktivitaet_id.id, a.protokoll_id.id)
                     for a in self.Anruf.sudo().search([])}
        self.bus.clear()
        for minuten in (0, 5):
            self.zeit_setzen(JETZT + timedelta(minutes=minuten))
            self.lauf()
            self.assertEqual(self.zaehlen(), vorher)
            self.assertEqual({a.id: (a.zustand, a.wb_voip_geraet, a.angenommen_anruf_id.id, a.erledigt, a.ende,
                                     a.rueckruf_aktivitaet_id.id, a.protokoll_id.id)
                              for a in self.Anruf.sudo().search([])}, zustaende)
            self.assertEqual(self.bus, [])
        # Bemerkung, dann noch ein Lauf: das Protokoll bleibt das eine
        eigener = self.von_gruppe('L23', self.user_a)
        eigener.with_user(self.user_a).wb_voip_bemerkung_speichern('Rückfrage geklärt')
        protokolle = self.Protokoll.sudo().search_count([])
        self.lauf()
        self.assertEqual(self.Protokoll.sudo().search_count([]), protokolle)
        self.assertEqual(eigener.zustand, 'anderswo')

    def test_linkedid_je_benutzer_eindeutig(self):
        werte = {'user_id': self.user_a.id, 'richtung': 'eingehend', 'zustand': 'verpasst',
                 'wb_voip_cdr_linkedid': 'L99'}
        self.Anruf.sudo().create(werte)
        self.Anruf.sudo().create(dict(werte, user_id=self.user_b.id))
        with mute_logger('odoo.sql_db'), self.assertRaises(psycopg2.errors.UniqueViolation), \
                self.env.cr.savepoint():
            self.Anruf.sudo().create(werte)
            self.env.flush_all()

    def test_felder_nur_server(self):
        anruf = self.softphone(self.user_a, 27, BASIS)
        for feld, wert in [('wb_voip_geraet', 'X'), ('wb_voip_cdr_linkedid', 'L'),
                           ('wb_voip_herkunft', 'anlage')]:
            with self.assertRaises(UserError):
                anruf.with_user(self.user_a).write({feld: wert})
            with self.assertRaises(UserError):
                self.Anruf.with_user(self.user_a).create(
                    {'richtung': 'eingehend', feld: wert})
        # auch über default_*-Kontext nicht (Kern entfernt default_<Serverfeld>)
        self.assertTrue({'wb_voip_geraet', 'wb_voip_cdr_linkedid', 'wb_voip_herkunft'}
                        <= set(self.Anruf._wb_voip_nur_server()))


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestBemerkung(AbgleichCase):
    """„Bemerkung“ on a call answered on another own device."""

    def _anderswo(self, kunde=1, billsec=240):
        self.lauf([cdr(f'B{kunde}', BASIS, '10', 'PJSIP/TischA-000001', 'ANSWERED', nummer(kunde),
                       billsec=billsec)])
        return self.von_gruppe(f'B{kunde}', self.user_a).with_user(self.user_a)

    def test_bemerkung_legt_einmal_protokoll_an(self):
        anruf = self._anderswo()
        self.assertFalse(anruf.protokoll_id)
        self.assertFalse(anruf.wb_voip_bemerkung_speichern(''))
        self.assertFalse(anruf.protokoll_id)
        self.assertTrue(anruf.wb_voip_bemerkung_speichern('  Angebot schicken  '))
        protokoll = anruf.sudo().protokoll_id
        self.assertEqual((protokoll.ergebnis, protokoll.dauer_sek, protokoll.richtung, protokoll.datum),
                         ('erreicht', 240, 'eingehend', BASIS))
        self.assertEqual(protokoll.partner_id, self.kunden[1])
        self.assertIn('Angenommen auf Tisch A.', protokoll.beschreibung)
        self.assertIn('Angebot schicken', protokoll.beschreibung)
        self.assertEqual(anruf.notiz, 'Angebot schicken')
        # Zweites Speichern: dasselbe Protokoll, nur die Beschreibung
        anruf.wb_voip_bemerkung_speichern('Termin vereinbart')
        self.assertEqual(anruf.sudo().protokoll_id, protokoll)
        self.assertIn('Termin vereinbart', protokoll.beschreibung)
        self.assertNotIn('Angebot schicken', protokoll.beschreibung)
        self.assertEqual(self.Protokoll.sudo().search_count([('anruf_id', '=', anruf.id)]), 1)
        # Liste zeigt die Bemerkung
        zeile = self.Anruf.with_user(self.user_a).wb_voip_verpasst_liste()[0]
        self.assertEqual(zeile['bemerkung'], 'Termin vereinbart')
        # Leer: Bemerkung weg, Protokoll bleibt mit dem Gerät
        anruf.wb_voip_bemerkung_speichern('')
        self.assertTrue(protokoll.exists())
        self.assertIn('Angenommen auf Tisch A.', protokoll.beschreibung)
        self.assertNotIn('Termin', protokoll.beschreibung)
        self.assertFalse(anruf.notiz)

    def test_von_hand_geaendert_wird_angehaengt(self):
        anruf = self._anderswo(2)
        anruf.wb_voip_bemerkung_speichern('Erster Text')
        protokoll = anruf.sudo().protokoll_id
        protokoll.with_user(self.user_a).write({'beschreibung': '<p>Eigene Zusammenfassung</p>'})
        anruf.wb_voip_bemerkung_speichern('Nachtrag')
        self.assertIn('Eigene Zusammenfassung', protokoll.beschreibung)
        self.assertIn('Nachtrag', protokoll.beschreibung)

    def test_mehrzeilig_und_von_hand_formatiert(self):
        anruf = self._anderswo(10)
        anruf.wb_voip_bemerkung_speichern('Zeile 1\nZeile 2')
        protokoll = anruf.sudo().protokoll_id
        anruf.wb_voip_bemerkung_speichern('Zeile 3\nZeile 4')
        # unverändert erkannt (Hash), also ersetzt statt angehängt
        self.assertIn('Zeile 3', protokoll.beschreibung)
        self.assertIn('Zeile 4', protokoll.beschreibung)
        self.assertNotIn('Zeile 1', protokoll.beschreibung)
        self.assertEqual(protokoll.beschreibung.count('Angenommen auf Tisch A.'), 1)
        protokoll.with_user(self.user_a).write({'beschreibung': '<p><b>Wichtig</b></p>'})
        anruf.wb_voip_bemerkung_speichern('Nachtrag\nzweite Zeile')
        self.assertIn('<b>Wichtig</b>', protokoll.beschreibung)
        self.assertIn('Nachtrag', protokoll.beschreibung)
        self.assertIn('zweite Zeile', protokoll.beschreibung)
        # Hash nur Server: Benutzer kann ihn nicht setzen
        with self.assertRaises(UserError):
            anruf.write({'wb_voip_bemerkung_hash': 'x'})

    def test_geloeschtes_protokoll_wird_neu_angelegt(self):
        anruf = self._anderswo(3)
        anruf.wb_voip_bemerkung_speichern('Eins')
        anruf.sudo().protokoll_id.unlink()
        anruf.wb_voip_bemerkung_speichern('Zwei')
        self.assertIn('Zwei', anruf.sudo().protokoll_id.beschreibung)

    def test_kollege_sieht_rohtext(self):
        self.lauf([cdr('B4', BASIS, '10', 'PJSIP/TelA-000002', 'NO ANSWER', nummer(4)),
                   cdr('B4', BASIS, '20', 'PJSIP/TischB-000003', 'ANSWERED', nummer(4), billsec=60)])
        anruf_b = self.von_gruppe('B4', self.user_b).with_user(self.user_b)
        anruf_a = self.von_gruppe('B4', self.user_a)
        self.bus.clear()
        anruf_b.wb_voip_bemerkung_speichern('Vertrag besprochen')
        self.assertIn((self.user_a.id, 'wb_voip/verpasst'), self.bus)
        info = anruf_a.with_user(self.user_a)._wb_voip_angenommen_info()
        self.assertEqual(info['notiz'], 'Vertrag besprochen')

    def test_nur_eigener_anderswo_mit_geraet(self):
        anruf = self._anderswo(5)
        with self.assertRaises(UserError):  # AccessError ist ein UserError
            anruf.with_user(self.user_b).wb_voip_bemerkung_speichern('fremd')
        verpasst = self.verpasst(self.user_a, 6, BASIS)
        with self.assertRaises(UserError):
            verpasst.wb_voip_bemerkung_speichern('x')
        with self.assertRaises(UserError):
            anruf.wb_voip_bemerkung_speichern(['kein', 'text'])
        with self.assertRaises(UserError):
            anruf.wb_voip_bemerkung_speichern('x' * 20_000)
        # Anderswo mit Kollegen-Verknüpfung: Bemerkung schreibt der Kollege
        self.lauf([cdr('B7', BASIS, '10', 'PJSIP/TelA-000004', 'NO ANSWER', nummer(7)),
                   cdr('B7', BASIS, '20', 'PJSIP/TischB-000005', 'ANSWERED', nummer(7), billsec=60)])
        with self.assertRaises(UserError):
            self.von_gruppe('B7', self.user_a).with_user(self.user_a).wb_voip_bemerkung_speichern('x')

    def test_ohne_voip_gruppe(self):
        anruf = self._anderswo(8)
        self.user_a.group_ids = [(3, self.env.ref('wb_voip.group_wb_voip_user').id)]
        with self.assertRaises(AccessError):
            anruf.with_user(self.user_a).wb_voip_bemerkung_speichern('x')

    def test_erledigen_wirkt_auf_geraete_eintrag(self):
        anruf = self._anderswo(9)
        self.assertFalse(anruf.wb_voip_verpasst_erledigen('erledigt'))
        self.assertTrue(anruf.sudo().erledigt)
        self.assertEqual(self.Anruf.with_user(self.user_a).wb_voip_verpasst_liste(), [])


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestGeraete(AbgleichCase):
    """Device table: load from the PBX (getpeer), rights."""

    def test_laden(self):
        self.env['wb.voip.geraet'].search([]).unlink()
        self.fake.peers = [
            {'pk_peerId': '1', 'peerName': 'TelA', 'peerInternalCallerId': '11', 'peerDisplayName': 'Softphone A'},
            {'pk_peerId': '2', 'peerName': 'Tisch20', 'peerInternalCallerId': '20'},
            {'pk_peerId': '3', 'peerName': 'Lager', 'peerInternalCallerId': '99'},
            {'pk_peerId': '4', 'peerName': '  '},
            'kaputt',
        ]
        ergebnis = self.anlage.action_wb_voip_ansitel_geraete_laden()
        self.assertEqual(ergebnis['params']['type'], 'success')
        geraete = self.env['wb.voip.geraet'].search([('anlage_id', '=', self.anlage.id)])
        nach_peer = {g.peer_name: g for g in geraete}
        self.assertEqual(sorted(nach_peer), ['Lager', 'TelA', 'Tisch20'])
        self.assertEqual(nach_peer['TelA'].name, 'Softphone A')
        self.assertEqual(nach_peer['TelA'].user_id, self.user_a)  # über den SIP-Benutzer
        self.assertEqual(nach_peer['Tisch20'].user_id, self.user_b)  # über die Nebenstelle
        self.assertEqual(nach_peer['Tisch20'].nebenstelle, '20')
        self.assertFalse(nach_peer['Lager'].user_id)
        self.anlage.action_wb_voip_ansitel_geraete_laden()
        self.assertEqual(self.env['wb.voip.geraet'].search_count([('anlage_id', '=', self.anlage.id)]), 3)
        for aufruf in self.fake.aufrufe:
            self.assertFalse(any(TOKEN in str(v) for k, v in aufruf.items() if k != 'token'))

    @mute_logger('odoo.addons.wb_voip_ansitel.models.ansitel_api')
    def test_laden_fehler(self):
        self._post(antwort({'status': 500}, 500))
        with self.assertRaises(UserError) as fang:
            self.anlage.action_wb_voip_ansitel_geraete_laden()
        self.assertEqual(str(fang.exception), 'Ansitel nicht erreichbar.')
        self._post(antwort({'status': 200, 'data': 'text'}))
        with self.assertRaises(UserError):
            self.anlage.action_wb_voip_ansitel_geraete_laden()

    def test_rechte(self):
        geraet = self.env['wb.voip.geraet'].search([('peer_name', '=', 'TischA')])
        als_manager = geraet.with_user(self.user_manager)
        self.assertEqual(als_manager.name, 'Tisch A')
        with self.assertRaises(AccessError):
            als_manager.write({'user_id': self.user_manager.id})
        with self.assertRaises(AccessError):
            self.env['wb.voip.geraet'].with_user(self.user_manager).create(
                {'name': 'x', 'peer_name': 'x', 'anlage_id': self.anlage.id})
        with self.assertRaises(AccessError):
            als_manager.unlink()
        with self.assertRaises(AccessError):
            geraet.with_user(self.user_a).read(['name'])
        with self.assertRaises(AccessError):
            self.anlage.with_user(self.user_manager).action_wb_voip_ansitel_geraete_laden()
        self.assertFalse(self.fake.aufrufe)

    def test_eindeutig_und_nicht_leer(self):
        Geraet = self.env['wb.voip.geraet']
        with mute_logger('odoo.sql_db'), self.assertRaises(Exception), self.env.cr.savepoint():
            Geraet.create({'name': 'Doppelt', 'peer_name': 'TischA', 'anlage_id': self.anlage.id})
            self.env.flush_all()
        with self.assertRaises(Exception), self.env.cr.savepoint():
            Geraet.create({'name': 'Leer', 'peer_name': '   ', 'anlage_id': self.anlage.id})
        neu = Geraet.create({'name': 'Neu', 'peer_name': ' Neu1 ', 'anlage_id': self.anlage.id})
        self.assertEqual(neu.peer_name, 'Neu1')
