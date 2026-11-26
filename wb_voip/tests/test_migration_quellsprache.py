import importlib.util

from psycopg2.extras import Json

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path


def _migration():
    """Load the post-migration of 19.0.5.0.0 (the folder name is no valid module name)."""
    pfad = file_path('wb_voip/migrations/19.0.5.0.0/post-migration.py')
    spec = importlib.util.spec_from_file_location('wb_voip_migration_19_0_5_0_0', pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@tagged('wb_voip', 'post_install', '-at_install')
class TestMigrationAuslieferung(TransactionCase):
    """Migration 19.0.5.0.0: delivered noupdate texts move to English, German to de_DE."""

    def _setzen(self, tabelle, xmlid, feld, werte):
        datensatz = self.env.ref(f'wb_voip.{xmlid}')
        self.env.flush_all()
        self.env.cr.execute(f'UPDATE "{tabelle}" SET "{feld}" = %s WHERE id = %s',
                            [Json(werte), datensatz.id])
        return datensatz.id

    def _lesen(self, tabelle, feld, datensatz_id):
        self.env.cr.execute(f'SELECT "{feld}" FROM "{tabelle}" WHERE id = %s', [datensatz_id])
        return self.env.cr.fetchone()[0]

    def test_alter_stand(self):
        typ = self._setzen('mail_activity_type', 'wb_voip_activity_type_rueckruf', 'name',
                           {'en_US': 'Rückruf'})
        self._setzen('mail_activity_type', 'wb_voip_activity_type_rueckruf', 'summary',
                     {'en_US': 'Rückruf: verpasster Anruf', 'de_DE': 'Bitte zurückrufen'})
        button = self._setzen('wb_voip_aktion', 'wb_voip_aktion_neuer_kontakt', 'name',
                              {'en_US': 'Kontakt anlegen'})
        layout = self._setzen('wb_voip_layout', 'wb_voip_layout_anrufer_fenster_standard', 'name',
                              {'en_US': 'Standard Anrufer-Fenster'})
        cron = self._setzen('ir_act_server', 'ir_cron_voip_aufraeumen_ir_actions_server', 'name',
                            {'en_US': 'WB VoIP: verwaiste Anrufe aufräumen'})

        migration = _migration()
        self.assertEqual(migration.auslieferungstexte_umstellen(self.env.cr), 4)
        self.env.invalidate_all()

        self.assertEqual(self._lesen('mail_activity_type', 'name', typ),
                         {'en_US': 'Callback', 'de_DE': 'Rückruf'})
        # Eigene deutsche Fassung bleibt, Englisch kommt dazu
        self.assertEqual(self._lesen('mail_activity_type', 'summary', typ),
                         {'en_US': 'Callback: missed call', 'de_DE': 'Bitte zurückrufen'})
        # Vom Kunden geändert: unverändert
        self.assertEqual(self._lesen('wb_voip_aktion', 'name', button), {'en_US': 'Kontakt anlegen'})
        self.assertEqual(self._lesen('wb_voip_layout', 'name', layout),
                         {'en_US': 'Default caller window', 'de_DE': 'Standard Anrufer-Fenster'})
        self.assertEqual(self._lesen('ir_act_server', 'name', cron),
                         {'en_US': 'WB VoIP: Clean up orphaned calls',
                          'de_DE': 'WB VoIP: verwaiste Anrufe aufräumen'})
        # Ein zweiter Lauf ändert nichts mehr
        self.assertEqual(migration.auslieferungstexte_umstellen(self.env.cr), 0)
