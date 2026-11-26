import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";

import { waehlbar } from "../dienste/telefon_dienst";
import { formatDauer, rueckrufZiel, waehlenMitHinweis } from "./waehlen_hilfe";

export const ANRUFE_LIMIT = 50;

const ZUSTAENDE = {
    neu: _t("New"),
    klingelt: _t("Ringing"),
    laeuft: _t("Running"),
    beendet: _t("Ended"),
    abgelehnt: _t("Rejected"),
    besetzt: _t("Busy"),
    fehlgeschlagen: _t("Not reached"),
    verpasst: _t("Missed"),
    anderswo: _t("Answered elsewhere"),
};

/** Reiter „Anrufe“: die letzten eigenen Anrufe, neu geladen nach jedem Anrufende. */
export class Anrufliste extends Component {
    static template = "wb_voip.Anrufliste";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.telefon = useService("wb_voip_telefon");
        this.lokal = useState({ anrufe: [], fehler: false, geladen: false });
        onWillStart(() => this.laden());
        useBus(this.telefon.bus, "anruf-beendet", () => this.laden());
    }

    async laden() {
        try {
            this.lokal.anrufe = await this.orm.call("wb.voip.anruf", "wb_voip_liste", [], {
                limit: ANRUFE_LIMIT,
            });
            this.lokal.fehler = false;
        } catch (fehler) {
            this.lokal.fehler = fehler?.data?.message || _t("Calls could not be loaded.");
        }
        this.lokal.geladen = true;
    }

    name(anruf) {
        return anruf.partner ? anruf.partner[1] : anruf.nummer || _t("Unknown");
    }

    zeit(anruf) {
        return anruf.beginn ? formatDateTime(deserializeDateTime(anruf.beginn)) : "";
    }

    zustand(anruf) {
        return ZUSTAENDE[anruf.zustand] || anruf.zustand;
    }

    dauer(anruf) {
        return anruf.dauer_sek ? formatDauer(anruf.dauer_sek) : "";
    }

    waehlbar(anruf) {
        return Boolean(waehlbar(anruf.nummer));
    }

    waehlen(anruf) {
        return waehlenMitHinweis(this.env, anruf.nummer, rueckrufZiel(anruf));
    }
}

registry.category("wb_voip_reiter").add("anrufe", {
    titel: _t("Calls"),
    Component: Anrufliste,
    sequence: 20,
});
