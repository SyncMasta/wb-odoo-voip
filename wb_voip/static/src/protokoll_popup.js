import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";

/** Ansicht des Popups nach dem Anruf (wb.voip.protokoll). */
export const POPUP_ANSICHT = "wb_voip.wb_voip_protokoll_view_form_popup";

/**
 * Öffnet nach dem Auflegen das Protokoll-Popup, aber nur in dem Tab, der den Anruf
 * geführt hat, und nur, wenn der Server ein Protokoll angelegt hat. Verwerfen lässt das
 * vorbelegte Ergebnis stehen (es liegt schon in der Datenbank).
 */
export const protokollPopupDienst = {
    dependencies: ["dialog", "wb_voip_telefon"],
    start(env, { dialog, wb_voip_telefon: telefon }) {
        telefon.bus.addEventListener("anruf-beendet", ({ detail }) => {
            if (!detail?.eigenerTab || !detail.protokollId) {
                return;
            }
            dialog.add(FormViewDialog, {
                resModel: "wb.voip.protokoll",
                resId: detail.protokollId,
                title: _t("Anruf protokollieren"),
                size: "xl",
                canExpand: false,
                preventCreate: true,
                context: { form_view_ref: POPUP_ANSICHT },
            });
        });
    },
};

registry.category("services").add("wb_voip_protokoll_popup", protokollPopupDienst);
