import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";

import { breiteVon } from "./layout/bloecke";
import { popupLayoutLaden, popupLayoutVormerken } from "./layout/popup_layout";

/** Ansicht des Popups nach dem Anruf (wb.voip.protokoll). */
export const POPUP_ANSICHT = "wb_voip.wb_voip_protokoll_view_form_popup";

/** Dialoggröße je Layout-Breite: groß, extra groß, Vollbild. */
export const POPUP_GROESSE = { normal: "lg", breit: "xl", sehr_breit: "fs" };

/**
 * Öffnet nach dem Auflegen das Protokoll-Popup, aber nur in dem Tab, der den Anruf
 * geführt hat, und nur, wenn der Server ein Protokoll angelegt hat. Verwerfen lässt das
 * vorbelegte Ergebnis stehen (es liegt schon in der Datenbank). Die Größe folgt der
 * Breite des Popup-Layouts; die Daten der rechten Spalte werden dafür vorab geladen.
 */
export const protokollPopupDienst = {
    dependencies: ["dialog", "orm", "wb_voip_telefon"],
    start(env, { dialog, orm, wb_voip_telefon: telefon }) {
        telefon.bus.addEventListener("anruf-beendet", async ({ detail }) => {
            const id = detail?.protokollId;
            if (!detail?.eigenerTab || !Number.isInteger(id)) {
                return;
            }
            let daten = null;
            try {
                daten = await popupLayoutLaden(orm, id);
            } catch {
                // Größe dann „normal“, das Widget versucht es selbst noch einmal
            }
            popupLayoutVormerken(id, daten);
            dialog.add(FormViewDialog, {
                resModel: "wb.voip.protokoll",
                resId: id,
                title: _t("Anruf protokollieren"),
                size: POPUP_GROESSE[breiteVon(daten?.layout)],
                canExpand: false,
                preventCreate: true,
                context: { form_view_ref: POPUP_ANSICHT },
            });
        });
    },
};

registry.category("services").add("wb_voip_protokoll_popup", protokollPopupDienst);
