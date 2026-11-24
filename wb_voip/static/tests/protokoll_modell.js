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

    _records = [
        { id: 1, ergebnis: "erreicht", beschreibung: "<p>Notiz</p>" },
        { id: 77, ergebnis: "erreicht" },
    ];
    _views = {
        form: `<form>
            <div class="o_wb_voip_popup_aktionen"/>
            <field name="ergebnis"/>
            <field name="beschreibung"/>
        </form>`,
    };
}
