import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { useService } from "@web/core/utils/hooks";

const FARBEN = new Set(["primary", "secondary", "success", "warning", "danger"]);
const ICON = /^fa-[a-z0-9-]{1,40}$/;

/** Orte, an denen der Server Buttons anbietet (``orte`` in den Client-Daten). */
export const ORT_FENSTER = "anrufer_fenster";
export const ORT_POPUP = "protokoll_popup";
export const ORT_SOFTPHONE = "softphone";

/** Server-Fehlertext für die Meldung. */
export function fehlertext(fehler) {
    return fehler?.data?.message || fehler?.message || _t("Unbekannter Fehler");
}

/**
 * Wählt die Buttons für einen Ort aus und macht sie darstellbar: Farbe und Symbol nur aus
 * den erlaubten Listen (nie Serverwerte als Klasse), Name nur als Text.
 */
export function aktionenFuerOrt(aktionen, ort) {
    return (aktionen || [])
        .filter((a) => Array.isArray(a.orte) && a.orte.includes(ort))
        .map((a) => ({
            id: a.id,
            name: a.name || "",
            klasse: `btn-${FARBEN.has(a.farbe) ? a.farbe : "secondary"}`,
            icon: typeof a.icon === "string" && ICON.test(a.icon) ? a.icon : false,
        }));
}

/**
 * Führt einen Server-Aufruf aus, der eine Aktion liefern kann: Fehler als Meldung,
 * eine zurückgegebene Fensteraktion wird geöffnet.
 */
export function useAktionAusfuehren() {
    const action = useService("action");
    const notification = useService("notification");
    return async (aufruf) => {
        let aktion;
        try {
            aktion = await aufruf();
        } catch (fehler) {
            notification.add(fehlertext(fehler), { type: "danger" });
            return;
        }
        if (aktion && typeof aktion === "object") {
            await action.doAction(aktion);
        }
    };
}

/**
 * Anruf-Buttons eines Ortes (Anrufer-Fenster, Protokoll-Popup, Softphone). Zeigt höchstens
 * ``max`` Buttons, der Rest steht unter „Mehr“. ``onAusfuehren(aktionId)`` führt aus.
 */
export class AktionsLeiste extends Component {
    static template = "wb_voip.AktionsLeiste";
    static components = { Dropdown, DropdownItem };
    static props = {
        aktionen: { type: Array },
        ort: { type: String },
        onAusfuehren: { type: Function },
        max: { type: Number, optional: true },
        itemKlasse: { type: String, optional: true },
    };

    get alle() {
        return aktionenFuerOrt(this.props.aktionen, this.props.ort);
    }

    get sichtbar() {
        const max = this.props.max ?? Infinity;
        return this.alle.length > max ? this.alle.slice(0, max) : this.alle;
    }

    get mehr() {
        return this.alle.slice(this.sichtbar.length);
    }
}
