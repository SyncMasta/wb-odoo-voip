import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";

import { waehlbar } from "../dienste/telefon_dienst";
import { formatDauer, rueckrufZiel, waehlenMitHinweis } from "../softphone/waehlen_hilfe";

export const VERPASST_LIMIT = 50;
/** Höchstlänge des Erledigt-Textes (wie auf dem Server geprüft). */
export const MAX_ERLEDIGT_TEXT = 2000;

/** Sichtbarkeit des Browser-Tabs; Tests ersetzen sie. */
export const seite = {
    sichtbar: () => document.visibilityState === "visible",
};

/**
 * Reiter „Verpasst“: offene verpasste Anrufe je Nummer. Beim Öffnen und bei jedem Bus-Impuls
 * wird die Liste nur im sichtbaren Tab geladen und danach als gesehen markiert.
 */
export class Verpasstliste extends Component {
    static template = "wb_voip.Verpasstliste";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.telefon = useService("wb_voip_telefon");
        this.verpasst = useService("wb_voip_verpasst");
        this.maxText = MAX_ERLEDIGT_TEXT;
        this.telefonState = useState(this.telefon.state);
        this.lokal = useState({
            zeilen: [],
            fehler: false,
            geladen: false,
            erledigen: null, // Schlüssel der Zeile mit offener Eingabe
            text: "",
            notizen: {}, // Schlüssel → Notiz sichtbar
        });
        onWillStart(() => this.laden());
        useBus(this.verpasst.bus, "aktualisieren", () => this.aktualisieren());
        const beiSichtbarkeit = () => this.aktualisieren();
        onMounted(() => {
            document.addEventListener("visibilitychange", beiSichtbarkeit);
            // erst nach dem Einhängen: ein geänderter Zähler würde sonst den Aufbau neu starten
            if (seite.sichtbar()) {
                this.verpasst.gesehen();
            }
        });
        onWillUnmount(() => document.removeEventListener("visibilitychange", beiSichtbarkeit));
    }

    /** Lädt die Liste neu und markiert sie als gesehen, aber nur im sichtbaren Tab. */
    async aktualisieren() {
        if (!seite.sichtbar()) {
            return;
        }
        await this.laden();
        await this.verpasst.gesehen();
    }

    async laden() {
        try {
            this.lokal.zeilen = await this.orm.call("wb.voip.anruf", "wb_voip_verpasst_liste", [], {
                limit: VERPASST_LIMIT,
            });
            this.lokal.fehler = false;
        } catch (fehler) {
            this.lokal.fehler =
                fehler?.data?.message || _t("Verpasste Anrufe konnten nicht geladen werden.");
        }
        this.lokal.geladen = true;
    }

    name(zeile) {
        return zeile.partner ? zeile.partner[1] : zeile.nummer || _t("Unbekannt");
    }

    zeit(zeile) {
        return zeile.letzter_beginn
            ? formatDateTime(deserializeDateTime(zeile.letzter_beginn))
            : "";
    }

    /** Hinweis „↪ angenommen von <Name> (<Dauer>)“ für ``anderswo``. */
    hinweis(zeile) {
        const kollege = zeile.kollege;
        if (zeile.art !== "anderswo" || !kollege) {
            return "";
        }
        const name = kollege.name || _t("Kollege");
        return kollege.dauer_sek
            ? _t("↪ angenommen von %s (%s)", name, formatDauer(kollege.dauer_sek))
            : _t("↪ angenommen von %s", name);
    }

    anrufbar(zeile) {
        return (
            Boolean(waehlbar(zeile.nummer)) &&
            ["verbunden", "anderer_tab"].includes(this.telefonState.status)
        );
    }

    jetztAnrufen(zeile) {
        return waehlenMitHinweis(this.env, zeile.nummer, rueckrufZiel(zeile));
    }

    erledigenOeffnen(zeile) {
        this.lokal.erledigen = zeile.schluessel;
        this.lokal.text = "";
    }

    erledigenAbbrechen() {
        this.lokal.erledigen = null;
        this.lokal.text = "";
    }

    async erledigenSpeichern(zeile) {
        const text = String(this.lokal.text || "").slice(0, MAX_ERLEDIGT_TEXT);
        let rest;
        try {
            rest = await this.orm.call("wb.voip.anruf", "wb_voip_verpasst_erledigen", [
                [zeile.anruf_id],
                text,
            ]);
        } catch (fehler) {
            this.lokal.fehler = fehler?.data?.message || _t("Erledigen ist fehlgeschlagen.");
            return;
        }
        this.lokal.fehler = false;
        this.erledigenAbbrechen();
        const index = this.lokal.zeilen.findIndex((z) => z.schluessel === zeile.schluessel);
        if (index >= 0) {
            if (rest) {
                this.lokal.zeilen.splice(index, 1, rest);
            } else {
                this.lokal.zeilen.splice(index, 1);
            }
        }
        // den Zähler aktualisiert der Server per Bus-Meldung
    }

    notizUmschalten(zeile) {
        this.lokal.notizen[zeile.schluessel] = !this.lokal.notizen[zeile.schluessel];
    }
}

registry.category("wb_voip_reiter").add("verpasst", {
    titel: _t("Verpasst"),
    Component: Verpasstliste,
    sequence: 25,
});
