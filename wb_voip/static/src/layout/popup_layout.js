import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

import { ORT_POPUP } from "../aktionen/aktions_leiste";
import { LayoutBloecke } from "./bloecke";

/** Vom Popup-Dienst schon geladene Daten je Protokoll (einmal verwendet, dann entfernt). */
const vorgeladen = new Map();

/** Merkt die Antwort von ``wb_voip_popup_layout`` für das gleich geöffnete Popup vor. */
export function popupLayoutVormerken(protokollId, daten) {
    if (Number.isInteger(protokollId) && daten && typeof daten === "object") {
        vorgeladen.set(protokollId, daten);
    }
}

/** Lädt die rechte Spalte des Popups (``wb.voip.protokoll.wb_voip_popup_layout``). */
export async function popupLayoutLaden(orm, protokollId) {
    return orm.call("wb.voip.protokoll", "wb_voip_popup_layout", [[protokollId]]);
}

/**
 * Rechte Spalte des Protokoll-Popups nach dem Layout des Benutzers. Die linke Spalte
 * (Ergebnis, Beschreibung, Folgeaktivität) bleibt in der Formularansicht fest.
 */
export class PopupLayout extends Component {
    static template = "wb_voip.PopupLayout";
    static components = { LayoutBloecke };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.ort = ORT_POPUP;
        this.lokal = useState({ daten: null });
        onWillStart(async () => {
            const id = this.props.record?.resId;
            if (!Number.isInteger(id)) {
                return;
            }
            let daten = vorgeladen.get(id);
            vorgeladen.delete(id);
            if (!daten) {
                try {
                    daten = await popupLayoutLaden(this.orm, id);
                } catch {
                    // ohne rechte Spalte bleibt das Popup benutzbar
                    daten = null;
                }
            }
            this.lokal.daten = daten || null;
        });
    }

    get daten() {
        return this.lokal.daten;
    }
}

registry.category("view_widgets").add("wb_voip_popup_layout", {
    component: PopupLayout,
});
