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
        self.env.cr.execute('UPDATE ir_cron SET cron_name = %s WHERE ir_actions_server_id = %s',
                            ['WB VoIP: verwaiste Anrufe aufräumen', cron])

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
        self.env.cr.execute('SELECT cron_name FROM ir_cron WHERE ir_actions_server_id = %s', [cron])
        self.assertEqual(self.env.cr.fetchone()[0], 'WB VoIP: Clean up orphaned calls')
        # Ein zweiter Lauf ändert nichts mehr
        self.assertEqual(migration.auslieferungstexte_umstellen(self.env.cr), 0)


def _migration_nachzug():
    """Load the post-migration of 19.0.5.0.1 (the folder name is no valid module name)."""
    pfad = file_path('wb_voip/migrations/19.0.5.0.1/post-migration.py')
    spec = importlib.util.spec_from_file_location('wb_voip_migration_19_0_5_0_1', pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@tagged('wb_voip', 'post_install', '-at_install')
class TestMigrationCronNamen(TransactionCase):
    """Migration 19.0.5.0.1: the stored ``ir_cron.cron_name`` follows the English source."""

    def _cron(self, xmlid):
        return self.env.ref('wb_voip.' + xmlid).ir_actions_server_id.id

    def _name(self, cron_id, name=None):
        if name is not None:
            self.env.flush_all()
            self.env.cr.execute('UPDATE ir_cron SET cron_name = %s WHERE ir_actions_server_id = %s',
                                [name, cron_id])
        self.env.cr.execute('SELECT cron_name FROM ir_cron WHERE ir_actions_server_id = %s', [cron_id])
        return self.env.cr.fetchone()[0]

    def test_alter_stand(self):
        aufraeumen = self._cron('ir_cron_voip_aufraeumen')
        frist = self._cron('ir_cron_voip_loeschfrist')
        self._name(aufraeumen, 'WB VoIP: verwaiste Anrufe aufräumen')
        self._name(frist, 'Mein eigener Name')
        migration = _migration_nachzug()
        self.assertEqual(migration.cron_namen_nachziehen(self.env.cr), 1)
        self.assertEqual(self._name(aufraeumen), 'WB VoIP: Clean up orphaned calls')
        # vom Kunden geändert: unverändert
        self.assertEqual(self._name(frist), 'Mein eigener Name')
        # idempotent
        self.assertEqual(migration.cron_namen_nachziehen(self.env.cr), 0)
