import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { deserializeDate, formatDate } from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

import { waehlenMitHinweis } from "./waehlen_hilfe";

export const AUFGABEN_LIMIT = 50;

/** Reiter „Aufgaben“: eigene offene Aktivitäten mit anrufbarem Kontakt. */
export class Aufgabenliste extends Component {
    static template = "wb_voip.Aufgabenliste";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.lokal = useState({ aufgaben: [], fehler: false });
        onWillStart(() => this.laden());
    }

    async laden() {
        try {
            this.lokal.aufgaben = await this.orm.call("mail.activity", "wb_voip_aufgaben", [], {
                limit: AUFGABEN_LIMIT,
            });
            this.lokal.fehler = false;
        } catch (fehler) {
            this.lokal.fehler = fehler?.data?.message || _t("Tasks could not be loaded.");
        }
    }

    faellig(aufgabe) {
        return aufgabe.faellig ? formatDate(deserializeDate(aufgabe.faellig)) : "";
    }

    waehlen(aufgabe) {
        return waehlenMitHinweis(this.env, aufgabe.nummer, {
            resModel: aufgabe.res_model,
            resId: aufgabe.res_id,
        });
    }
}

registry.category("wb_voip_reiter").add("aufgaben", {
    titel: _t("Tasks"),
    Component: Aufgabenliste,
    sequence: 40,
});
