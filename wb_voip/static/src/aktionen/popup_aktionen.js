import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

import { AktionsLeiste, ORT_POPUP, useAktionAusfuehren } from "./aktions_leiste";

/** Id eines Many2one-Werts (Objekt, Paar oder Zahl, je nach Odoo-Stand). */
function idVon(wert) {
    const id = Array.isArray(wert) ? wert[0] : wert?.id ?? wert;
    return Number.isInteger(id) ? id : false;
}

/**
 * Anruf-Buttons im Protokoll-Popup nach dem Anruf. Die Liste liefert
 * ``wb.voip.protokoll.wb_voip_popup_aktionen`` (nur das eigene Protokoll); ausgeführt wird
 * über den zugehörigen Anruf, damit die Prüfungen dieselben sind wie im Anrufer-Fenster.
 */
export class PopupAktionen extends Component {
    static template = "wb_voip.PopupAktionen";
    static components = { AktionsLeiste };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.ausfuehren = useAktionAusfuehren();
        this.ort = ORT_POPUP;
        this.lokal = useState({ aktionen: [] });
        onWillStart(async () => {
            const id = this.props.record?.resId;
            if (!Number.isInteger(id)) {
                return;
            }
            try {
                this.lokal.aktionen =
                    (await this.orm.call("wb.voip.protokoll", "wb_voip_popup_aktionen", [[id]])) ||
                    [];
            } catch {
                // ohne Buttons ist das Popup trotzdem benutzbar
                this.lokal.aktionen = [];
            }
        });
    }

    async aktionAusfuehren(aktionId) {
        const record = this.props.record;
        const anrufId = idVon(record?.data?.anruf_id);
        if (!anrufId) {
            return;
        }
        // Ergebnis und Notiz zuerst speichern: der Button kann ein anderes Fenster öffnen
        // und das Popup schließen; ungültige Eingaben brechen ab (Odoo zeigt den Fehler)
        if (typeof record.save === "function" && !(await record.save())) {
            return;
        }
        return this.ausfuehren(() =>
            this.orm.call("wb.voip.anruf", "wb_voip_aktion_ausfuehren", [[anrufId], aktionId])
        );
    }
}

registry.category("view_widgets").add("wb_voip_popup_aktionen", {
    component: PopupAktionen,
});
