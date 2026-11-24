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
        ],
    });
    beschreibung = fields.Html();
    anruf_id = fields.Integer();

    /** Standard: keine Buttons (Tests überschreiben das per onRpc). */
    wb_voip_popup_aktionen() {
        return [];
    }

    _records = [
        { id: 1, ergebnis: "erreicht", beschreibung: "<p>Notiz</p>", anruf_id: 5 },
        { id: 77, ergebnis: "erreicht" },
    ];
    _views = {
        form: `<form>
            <field name="anruf_id" invisible="1"/>
            <div class="o_wb_voip_popup_aktionen"><widget name="wb_voip_popup_aktionen"/></div>
            <field name="ergebnis"/>
            <field name="beschreibung"/>
        </form>`,
    };
}
