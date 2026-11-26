# Cron-Namen der ausgelieferten Serveraktionen: ``ir_cron.cron_name`` wird aus dem Namen der Serveraktion
# berechnet gespeichert und hat die Umstellung auf die englische Quellsprache (19.0.5.0.0) nicht mitgemacht.
# (XML-ID der Serveraktion, bisheriger deutscher Name, neuer englischer Name)
CRONS = [
    ('ir_cron_voip_aufraeumen_ir_actions_server',
     'WB VoIP: verwaiste Anrufe aufräumen', 'WB VoIP: Clean up orphaned calls'),
    ('ir_cron_voip_loeschfrist_ir_actions_server',
     'WB VoIP: Löschfrist für Anrufdaten', 'WB VoIP: Retention period for call data'),
]


def cron_namen_nachziehen(cr):
    """Set ``ir_cron.cron_name`` to the English name where it still has the old German one.

    Idempotent; a name that the customer changed stays. Returns the number of changed crons.
    """
    geaendert = 0
    for xmlid, alt, neu in CRONS:
        cr.execute('''
            UPDATE ir_cron c SET cron_name = %s
              FROM ir_model_data d
             WHERE d.module = 'wb_voip' AND d.name = %s AND d.model = 'ir.actions.server'
               AND c.ir_actions_server_id = d.res_id AND c.cron_name = %s
        ''', [neu, xmlid, alt])
        geaendert += cr.rowcount
    return geaendert


def migrate(cr, version):
    """Bring the stored cron names in line with the English source."""
    cron_namen_nachziehen(cr)
