from psycopg2.extras import Json

# Ausgelieferte noupdate-Daten mit übersetzbaren Feldern:
# (Tabelle, Modell, XML-ID, Feld, bisher deutsch, neu englisch).
# Ein Update schreibt sie nicht neu; die Quellsprache ist seit 19.0.5.0.0 Englisch.
AUSLIEFERUNG = [
    ('mail_activity_type', 'mail.activity.type', 'wb_voip_activity_type_rueckruf', 'name',
     'Rückruf', 'Callback'),
    ('mail_activity_type', 'mail.activity.type', 'wb_voip_activity_type_rueckruf', 'summary',
     'Rückruf: verpasster Anruf', 'Callback: missed call'),
    ('wb_voip_aktion', 'wb.voip.aktion', 'wb_voip_aktion_neuer_kontakt', 'name',
     'Neuer Kontakt', 'New contact'),
    ('wb_voip_layout', 'wb.voip.layout', 'wb_voip_layout_anrufer_fenster_standard', 'name',
     'Standard Anrufer-Fenster', 'Default caller window'),
    ('wb_voip_layout', 'wb.voip.layout', 'wb_voip_layout_protokoll_popup_standard', 'name',
     'Standard Protokoll-Popup', 'Default call log popup'),
    ('ir_act_server', 'ir.actions.server', 'ir_cron_voip_aufraeumen_ir_actions_server', 'name',
     'WB VoIP: verwaiste Anrufe aufräumen', 'WB VoIP: Clean up orphaned calls'),
    ('ir_act_server', 'ir.actions.server', 'ir_cron_voip_loeschfrist_ir_actions_server', 'name',
     'WB VoIP: Löschfrist für Anrufdaten', 'WB VoIP: Retention period for call data'),
]


def auslieferungstexte_umstellen(cr):
    """Switch delivered noupdate texts to the English source; German moves to ``de_DE``.

    Only values still in the delivered state are touched: ``en_US`` is replaced only if it
    still equals the old German text, ``de_DE`` is set to that text only if it is missing.
    Values changed by the customer stay as they are. Returns the number of changed values.
    """
    geaendert = 0
    for tabelle, modell, xmlid, feld, alt, neu in AUSLIEFERUNG:
        cr.execute(f'''
            SELECT t.id, t."{feld}"
              FROM "{tabelle}" t
              JOIN ir_model_data d ON d.res_id = t.id
             WHERE d.module = 'wb_voip' AND d.name = %s AND d.model = %s
        ''', [xmlid, modell])
        zeile = cr.fetchone()
        if not zeile or not zeile[1]:
            continue
        datensatz_id, werte = zeile
        if werte.get('en_US') != alt:
            continue
        werte = dict(werte, en_US=neu)
        werte.setdefault('de_DE', alt)
        cr.execute(f'UPDATE "{tabelle}" SET "{feld}" = %s WHERE id = %s', [Json(werte), datensatz_id])
        if tabelle == 'ir_act_server':
            # ir_cron.cron_name ist aus dem Namen der Serveraktion berechnet gespeichert und zieht nicht nach
            cr.execute('UPDATE ir_cron SET cron_name = %s WHERE ir_actions_server_id = %s AND cron_name = %s',
                       [neu, datensatz_id, alt])
        geaendert += 1
    return geaendert


def migrate(cr, version):
    """Switch delivered noupdate texts to English, keeping German as ``de_DE``."""
    auslieferungstexte_umstellen(cr)
