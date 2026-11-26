import { fields, models } from "@web/../tests/web_test_helpers";

/** Mock-Modell des Anrufprotokolls mit der Popup-Ansicht (für Hoot-Tests). */
export class WbVoipProtokoll extends models.Model {
    _name = "wb.voip.protokoll";

    ergebnis = fields.Selection({
        selection: [
            ["erreicht", "Erreicht"],
            ["nicht_erreicht", "Nicht erreicht"],
            ["mailbox", "Mailbox"],
            ["besetzt", "Besetzt"],
            ["falsche_nummer", "Falsche Nummer"],
        ],
    });
    beschreibung = fields.Html();
    anruf_id = fields.Integer();

    /** Standard: keine Buttons (Tests überschreiben das per onRpc). */
    wb_voip_popup_aktionen() {
        return [];
    }

    /** Standard: ausgeliefertes Popup-Layout ohne Kontakt (Tests überschreiben das per onRpc). */
    wb_voip_popup_layout() {
        return {
            layout: {
                id: 2,
                breite: "normal",
                bloecke: [
                    { art: "kontaktkopf", titel: false, spalte: "1", anzahl: false, text: false, felder: [] },
                    { art: "protokolle", titel: false, spalte: "1", anzahl: 5, text: false, felder: [] },
                ],
            },
            kontakt: false,
            protokolle: [],
        };
    }

    _records = [
        { id: 1, ergebnis: "erreicht", beschreibung: "<p>Notiz</p>", anruf_id: 5 },
        { id: 77, ergebnis: "erreicht" },
    ];
    // wie die Popup-Ansicht: links fest, rechts das Layout-Widget
    _views = {
        form: `<form>
            <field name="anruf_id" invisible="1"/>
            <div class="o_wb_voip_popup_aktionen"><widget name="wb_voip_popup_aktionen"/></div>
            <div class="row">
                <div class="col-7 o_wb_voip_popup_links">
                    <field name="ergebnis"/>
                    <field name="beschreibung"/>
                </div>
                <div class="col-5 o_wb_voip_popup_rechts"><widget name="wb_voip_popup_layout"/></div>
            </div>
        </form>`,
    };
}
