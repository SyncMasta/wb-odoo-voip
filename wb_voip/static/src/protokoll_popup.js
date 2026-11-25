import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";

import { breiteVon } from "./layout/bloecke";

/** Ansicht des Popups nach dem Anruf (wb.voip.protokoll). */
export const POPUP_ANSICHT = "wb_voip.wb_voip_protokoll_view_form_popup";

/** Dialoggröße je Layout-Breite: groß, extra groß, Vollbild. */
export const POPUP_GROESSE = { normal: "lg", breit: "xl", sehr_breit: "fs" };

/**
 * Öffnet nach dem Auflegen das Protokoll-Popup, aber nur in dem Tab, der den Anruf
 * geführt hat, und nur, wenn der Server ein Protokoll angelegt hat. Bei zwei Leitungen öffnet
 * es nur für die zuletzt endende (``popup`` im Ereignis), damit sich keine Popups stapeln. Verwerfen lässt das
 * vorbelegte Ergebnis stehen (es liegt schon in der Datenbank). Der Dialog öffnet sofort
 * in der zuletzt gesehenen Größe; das Widget der rechten Spalte lädt das Layout selbst und
 * passt die Größe danach an (``breiteMerken``).
 */
export const protokollPopupDienst = {
    dependencies: ["dialog", "wb_voip_telefon"],
    start(env, { dialog, wb_voip_telefon: telefon }) {
        let letzteBreite = "normal";
        telefon.bus.addEventListener("anruf-beendet", ({ detail }) => {
            const id = detail?.protokollId;
            // popup false: neben dieser Leitung läuft noch eine andere (zweite Leitung)
            if (!detail?.eigenerTab || !Number.isInteger(id) || detail.popup === false) {
                return;
            }
            dialog.add(FormViewDialog, {
                resModel: "wb.voip.protokoll",
                resId: id,
                title: _t("Anruf protokollieren"),
                size: POPUP_GROESSE[letzteBreite],
                canExpand: false,
                preventCreate: true,
                context: { form_view_ref: POPUP_ANSICHT },
            });
        });
        return {
            /** Merkt die Breite des zuletzt geladenen Popup-Layouts für das nächste Popup. */
            breiteMerken(breite) {
                letzteBreite = breiteVon({ breite });
            },
        };
    },
};

registry.category("services").add("wb_voip_protokoll_popup", protokollPopupDienst);
