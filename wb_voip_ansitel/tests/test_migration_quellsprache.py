import importlib.util

from psycopg2.extras import Json

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path


def _migration():
    """Load the post-migration of 19.0.2.0.0 (the folder name is no valid module name)."""
    pfad = file_path('wb_voip_ansitel/migrations/19.0.2.0.0/post-migration.py')
    spec = importlib.util.spec_from_file_location('wb_voip_ansitel_migration_19_0_2_0_0', pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestMigrationAuslieferung(TransactionCase):
    """Migration 19.0.2.0.0: delivered noupdate texts move to English, German to de_DE."""

    def _setzen(self, tabelle, xmlid, feld, werte):
        datensatz = self.env.ref(f'wb_voip_ansitel.{xmlid}')
        self.env.flush_all()
        self.env.cr.execute(f'UPDATE "{tabelle}" SET "{feld}" = %s WHERE id = %s',
                            [Json(werte), datensatz.id])
        return datensatz.id

    def _lesen(self, tabelle, feld, datensatz_id):
        self.env.cr.execute(f'SELECT "{feld}" FROM "{tabelle}" WHERE id = %s', [datensatz_id])
        return self.env.cr.fetchone()[0]

    def test_alter_stand(self):
        ab = self._setzen('wb_voip_taste', 'taste_ab', 'name', {'en_US': 'AB'})
        handy = self._setzen('wb_voip_taste', 'taste_handy', 'name',
                             {'en_US': '→ Handy', 'de_DE': '→ Mobil'})
        dnd = self._setzen('wb_voip_taste', 'taste_dnd', 'name', {'en_US': 'Ruhe bitte'})
        cron = self._setzen('ir_act_server', 'ir_cron_ansitel_abgleich_ir_actions_server', 'name',
                            {'en_US': 'WB VoIP Ansitel: Abgleich mit der Anrufliste'})
        self.env.cr.execute('UPDATE ir_cron SET cron_name = %s WHERE ir_actions_server_id = %s',
                            ['WB VoIP Ansitel: Abgleich mit der Anrufliste', cron])

        migration = _migration()
        self.assertEqual(migration.auslieferungstexte_umstellen(self.env.cr), 3)
        self.env.invalidate_all()

        self.assertEqual(self._lesen('wb_voip_taste', 'name', ab), {'en_US': 'VM', 'de_DE': 'AB'})
        # Eigene deutsche Fassung bleibt, Englisch kommt dazu
        self.assertEqual(self._lesen('wb_voip_taste', 'name', handy),
                         {'en_US': '→ Mobile', 'de_DE': '→ Mobil'})
        # Vom Kunden geändert: unverändert
        self.assertEqual(self._lesen('wb_voip_taste', 'name', dnd), {'en_US': 'Ruhe bitte'})
        self.assertEqual(self._lesen('ir_act_server', 'name', cron),
                         {'en_US': 'WB VoIP Ansitel: Call list reconciliation',
                          'de_DE': 'WB VoIP Ansitel: Abgleich mit der Anrufliste'})
        self.env.cr.execute('SELECT cron_name FROM ir_cron WHERE ir_actions_server_id = %s', [cron])
        self.assertEqual(self.env.cr.fetchone()[0], 'WB VoIP Ansitel: Call list reconciliation')
        # Ein zweiter Lauf ändert nichts mehr
        self.assertEqual(migration.auslieferungstexte_umstellen(self.env.cr), 0)


def _migration_nachzug():
    """Load the post-migration of 19.0.2.0.1 (the folder name is no valid module name)."""
    pfad = file_path('wb_voip_ansitel/migrations/19.0.2.0.1/post-migration.py')
    spec = importlib.util.spec_from_file_location('wb_voip_ansitel_migration_19_0_2_0_1', pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@tagged('wb_voip_ansitel', 'post_install', '-at_install')
class TestMigrationCronNamen(TransactionCase):
    """Migration 19.0.2.0.1: the stored ``ir_cron.cron_name`` follows the English source."""

    def _cron(self, xmlid):
        return self.env.ref('wb_voip_ansitel.' + xmlid).ir_actions_server_id.id

    def _name(self, cron_id, name=None):
        if name is not None:
            self.env.flush_all()
            self.env.cr.execute('UPDATE ir_cron SET cron_name = %s WHERE ir_actions_server_id = %s',
                                [name, cron_id])
        self.env.cr.execute('SELECT cron_name FROM ir_cron WHERE ir_actions_server_id = %s', [cron_id])
        return self.env.cr.fetchone()[0]

    def test_alter_stand(self):
        cron = self._cron('ir_cron_ansitel_abgleich')
        self._name(cron, 'WB VoIP Ansitel: Abgleich mit der Anrufliste')
        migration = _migration_nachzug()
        self.assertEqual(migration.cron_namen_nachziehen(self.env.cr), 1)
        self.assertEqual(self._name(cron), 'WB VoIP Ansitel: Call list reconciliation')
        # idempotent
        self.assertEqual(migration.cron_namen_nachziehen(self.env.cr), 0)

    def test_vom_kunden_geaendert(self):
        cron = self._cron('ir_cron_ansitel_abgleich')
        self._name(cron, 'Mein eigener Name')
        self.assertEqual(_migration_nachzug().cron_namen_nachziehen(self.env.cr), 0)
        self.assertEqual(self._name(cron), 'Mein eigener Name')
