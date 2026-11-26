import importlib.util

from psycopg2.extras import Json

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path


def _migration():
    """Load the post-migration of 19.0.2.0.0 (the folder name is no valid module name)."""
    pfad = file_path('wb_voip_crm/migrations/19.0.2.0.0/post-migration.py')
    spec = importlib.util.spec_from_file_location('wb_voip_crm_migration_19_0_2_0_0', pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@tagged('wb_voip_crm', 'post_install', '-at_install')
class TestMigrationAuslieferung(TransactionCase):
    """Migration 19.0.2.0.0: delivered noupdate texts move to English, German to de_DE."""

    def _setzen(self, werte):
        button = self.env.ref('wb_voip_crm.wb_voip_aktion_vorgang')
        self.env.flush_all()
        self.env.cr.execute('UPDATE wb_voip_aktion SET name = %s WHERE id = %s', [Json(werte), button.id])
        return button.id

    def _lesen(self, datensatz_id):
        self.env.cr.execute('SELECT name FROM wb_voip_aktion WHERE id = %s', [datensatz_id])
        return self.env.cr.fetchone()[0]

    def test_alter_stand(self):
        button = self._setzen({'en_US': 'Vorgang anlegen'})
        migration = _migration()
        self.assertEqual(migration.auslieferungstexte_umstellen(self.env.cr), 1)
        self.assertEqual(self._lesen(button), {'en_US': 'Create opportunity', 'de_DE': 'Vorgang anlegen'})
        # Ein zweiter Lauf ändert nichts mehr
        self.assertEqual(migration.auslieferungstexte_umstellen(self.env.cr), 0)

    def test_vom_kunden_geaendert(self):
        button = self._setzen({'en_US': 'Neue Chance', 'de_DE': 'Neue Chance'})
        self.assertEqual(_migration().auslieferungstexte_umstellen(self.env.cr), 0)
        self.assertEqual(self._lesen(button), {'en_US': 'Neue Chance', 'de_DE': 'Neue Chance'})


def _migration_2_0_1():
    """Load the post-migration of 19.0.2.0.1 (the folder name is no valid module name)."""
    pfad = file_path('wb_voip_crm/migrations/19.0.2.0.1/post-migration.py')
    spec = importlib.util.spec_from_file_location('wb_voip_crm_migration_19_0_2_0_1', pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@tagged('wb_voip_crm', 'post_install', '-at_install')
class TestMigrationTitelVorlage(TransactionCase):
    """Migration 19.0.2.0.1: the example title template becomes translatable."""

    def _setzen(self, werte):
        button = self.env.ref('wb_voip_crm.wb_voip_aktion_vorgang')
        self.env.flush_all()
        self.env.cr.execute('UPDATE wb_voip_aktion SET titel_vorlage = %s WHERE id = %s', [Json(werte), button.id])
        return button.id

    def _lesen(self, datensatz_id):
        self.env.cr.execute('SELECT titel_vorlage FROM wb_voip_aktion WHERE id = %s', [datensatz_id])
        return self.env.cr.fetchone()[0]

    def test_alter_stand(self):
        button = self._setzen({'en_US': 'Anruf von {kontakt} ({datum})'})
        migration = _migration_2_0_1()
        self.assertEqual(migration.auslieferungstexte_umstellen(self.env.cr), 1)
        self.assertEqual(self._lesen(button), {
            'en_US': 'Call from {kontakt} ({datum})', 'de_DE': 'Anruf von {kontakt} ({datum})'})
        self.assertEqual(migration.auslieferungstexte_umstellen(self.env.cr), 0)

    def test_vom_kunden_geaendert(self):
        button = self._setzen({'en_US': 'Rückruf {kontakt}'})
        self.assertEqual(_migration_2_0_1().auslieferungstexte_umstellen(self.env.cr), 0)
        self.assertEqual(self._lesen(button), {'en_US': 'Rückruf {kontakt}'})
