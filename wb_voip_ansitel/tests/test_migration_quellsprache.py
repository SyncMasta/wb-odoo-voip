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
        # Ein zweiter Lauf ändert nichts mehr
        self.assertEqual(migration.auslieferungstexte_umstellen(self.env.cr), 0)
