import { useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

import { formatDauer } from "@wb_voip/softphone/waehlen_hilfe";
import { Verpasstliste } from "@wb_voip/verpasst/verpasst_liste";

/** Höchstlänge der Bemerkung (wie auf dem Server geprüft). */
export const MAX_BEMERKUNG = 10000;

/**
 * Verpasst-Liste mit Angaben aus der Anrufliste der Anlage: „↪ angenommen auf <Gerät> (Dauer)“,
 * beim Kollegen auch dessen Gerät, und „Bemerkung“ zu einem auf einem eigenen Gerät
 * angenommenen Anruf.
 */
patch(Verpasstliste.prototype, {
    setup() {
        super.setup(...arguments);
        this.maxBemerkung = MAX_BEMERKUNG;
        this.bemerkung = useState({ offen: null, text: "", speichert: false, fehler: false });
    },

    hinweis(zeile) {
        if (zeile.art === "anderswo" && zeile.geraet) {
            return zeile.geraet_dauer_sek
                ? _t("↪ angenommen auf %s (%s)", zeile.geraet, formatDauer(zeile.geraet_dauer_sek))
                : _t("↪ angenommen auf %s", zeile.geraet);
        }
        const kollege = zeile.kollege;
        if (zeile.art === "anderswo" && kollege && kollege.geraet) {
            const name = kollege.name || _t("Kollege");
            return kollege.dauer_sek
                ? _t("↪ angenommen von %s · %s (%s)", name, kollege.geraet, formatDauer(kollege.dauer_sek))
                : _t("↪ angenommen von %s · %s", name, kollege.geraet);
        }
        return super.hinweis(zeile);
    },

    bemerkungOeffnen(zeile) {
        this.bemerkung.offen = zeile.schluessel;
        this.bemerkung.text = zeile.bemerkung || "";
        this.bemerkung.fehler = false;
    },

    bemerkungAbbrechen() {
        this.bemerkung.offen = null;
        this.bemerkung.text = "";
    },

    bemerkungTaste(ereignis) {
        if (ereignis.key === "Escape") {
            ereignis.stopPropagation();
            this.bemerkungAbbrechen();
        }
    },

    async bemerkungSpeichern(zeile) {
        if (this.bemerkung.speichert) {
            return;
        }
        this.bemerkung.speichert = true;
        try {
            await this.orm.call("wb.voip.anruf", "wb_voip_bemerkung_speichern", [
                [zeile.anruf_id],
                String(this.bemerkung.text || "").slice(0, MAX_BEMERKUNG),
            ]);
        } catch (fehler) {
            this.bemerkung.fehler =
                fehler?.data?.message || _t("Die Bemerkung konnte nicht gespeichert werden.");
            return;
        } finally {
            this.bemerkung.speichert = false;
        }
        this.bemerkungAbbrechen();
        await this.laden();
    },
});
