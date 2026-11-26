from psycopg2.extras import Json

# Ausgelieferte noupdate-Daten mit übersetzbaren Feldern:
# (Tabelle, Modell, XML-ID, Feld, bisher deutsch, neu englisch).
# Ein Update schreibt sie nicht neu; die Quellsprache ist seit 19.0.2.0.0 Englisch.
AUSLIEFERUNG = [
    ('wb_voip_taste', 'wb.voip.taste', 'taste_ab', 'name', 'AB', 'VM'),
    ('wb_voip_taste', 'wb.voip.taste', 'taste_handy', 'name', '→ Handy', '→ Mobile'),
    ('wb_voip_taste', 'wb.voip.taste', 'taste_dnd', 'name', 'Nicht stören', 'Do not disturb'),
    ('ir_act_server', 'ir.actions.server', 'ir_cron_ansitel_abgleich_ir_actions_server', 'name',
     'WB VoIP Ansitel: Abgleich mit der Anrufliste', 'WB VoIP Ansitel: Call list reconciliation'),
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
             WHERE d.module = 'wb_voip_ansitel' AND d.name = %s AND d.model = %s
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
        geaendert += 1
    return geaendert


def migrate(cr, version):
    """Switch delivered noupdate texts to English, keeping German as ``de_DE``."""
    auslieferungstexte_umstellen(cr)
