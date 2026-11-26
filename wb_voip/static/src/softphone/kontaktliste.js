import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";

import { waehlenMitHinweis } from "./waehlen_hilfe";

export const KONTAKTE_LIMIT = 20;
/** Höchstlänge des Suchtexts (wie auf dem Server geprüft). */
export const MAX_SUCHTEXT = 100;

/** Reiter „Kontakte“: Suche nach Name, E-Mail oder Nummer. */
export class Kontaktliste extends Component {
    static template = "wb_voip.Kontaktliste";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.maxSuchtext = MAX_SUCHTEXT;
        this.lokal = useState({ suchtext: "", kontakte: [], fehler: false });
        this._letzte = null;
        this.suchenVerzoegert = useDebounced(() => this.suchen(), 300);
        onWillStart(() => this.suchen());
    }

    async suchen() {
        const text = this.lokal.suchtext.slice(0, MAX_SUCHTEXT);
        if (text === this._letzte) {
            return;
        }
        this._letzte = text;
        try {
            const kontakte = await this.orm.call("res.partner", "wb_voip_kontakte_suchen", [text], {
                limit: KONTAKTE_LIMIT,
            });
            if (text === this._letzte) {
                this.lokal.kontakte = kontakte;
                this.lokal.fehler = false;
            }
        } catch (fehler) {
            this._letzte = null;
            this.lokal.fehler = fehler?.data?.message || _t("Contacts could not be loaded.");
        }
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.suchen();
        }
    }

    waehlen(kontakt) {
        return waehlenMitHinweis(this.env, kontakt.nummer, {
            resModel: "res.partner",
            resId: kontakt.id,
        });
    }
}

registry.category("wb_voip_reiter").add("kontakte", {
    titel: _t("Contacts"),
    Component: Kontaktliste,
    sequence: 30,
});
