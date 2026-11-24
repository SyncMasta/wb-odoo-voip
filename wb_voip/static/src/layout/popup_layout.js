import { Component, onWillStart, useEffect, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

import { ORT_POPUP } from "../aktionen/aktions_leiste";
import { POPUP_GROESSE } from "../protokoll_popup";
import { LayoutBloecke, breiteVon } from "./bloecke";

/** Klassen der Dialoggrößen, die das Widget umstellen darf. */
const GROESSEN_KLASSEN = Object.values(POPUP_GROESSE).map((g) => `modal-${g}`);

/** Lädt die rechte Spalte des Popups (``wb.voip.protokoll.wb_voip_popup_layout``). */
export async function popupLayoutLaden(orm, protokollId) {
    return orm.call("wb.voip.protokoll", "wb_voip_popup_layout", [[protokollId]]);
}

/**
 * Rechte Spalte des Protokoll-Popups nach dem Layout des Benutzers. Die linke Spalte
 * (Ergebnis, Beschreibung, Folgeaktivität) bleibt in der Formularansicht fest. Das Widget
 * lädt ohne zu blockieren (das Formular erscheint sofort) und stellt danach die Größe des
 * umgebenden Dialogs auf die Layout-Breite.
 */
export class PopupLayout extends Component {
    static template = "wb_voip.PopupLayout";
    static components = { LayoutBloecke };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        // ohne Popup-Dienst (Formular anderswo eingebettet) nur keine Größe merken
        try {
            this.popup = useService("wb_voip_protokoll_popup");
        } catch {
            this.popup = null;
        }
        this.ort = ORT_POPUP;
        this.wurzel = useRef("wurzel");
        this.lokal = useState({ daten: null });
        // bewusst ohne await: das Formular wartet nicht auf die rechte Spalte
        onWillStart(() => {
            this.laden();
        });
        useEffect(
            (daten) => {
                if (daten?.layout) {
                    this.groesseSetzen(breiteVon(daten.layout));
                }
            },
            () => [this.lokal.daten]
        );
    }

    async laden() {
        const id = this.props.record?.resId;
        if (!Number.isInteger(id)) {
            return;
        }
        let daten = null;
        try {
            daten = await popupLayoutLaden(this.orm, id);
        } catch {
            // ohne rechte Spalte bleibt das Popup benutzbar
            return;
        }
        if (daten && typeof daten === "object") {
            this.lokal.daten = daten;
        }
    }

    /** Stellt die Größe des umgebenden Dialogs um (nur die bekannten Größenklassen). */
    groesseSetzen(breite) {
        this.popup?.breiteMerken(breite);
        const dialog = this.wurzel.el?.closest(".modal-dialog");
        if (!dialog) {
            return;
        }
        const ziel = `modal-${POPUP_GROESSE[breite]}`;
        dialog.classList.remove(...GROESSEN_KLASSEN.filter((k) => k !== ziel));
        dialog.classList.add(ziel);
    }

    get daten() {
        return this.lokal.daten;
    }
}

registry.category("view_widgets").add("wb_voip_popup_layout", {
    component: PopupLayout,
});
