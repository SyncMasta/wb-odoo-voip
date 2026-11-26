import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

import { waehlenMitHinweis } from "./waehlen_hilfe";

export const TASTEN = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"];

/** Zifferntasten; meldet jede Taste über ``onTaste``. */
export class Tastenfeld extends Component {
    static template = "wb_voip.Tastenfeld";
    static props = { onTaste: Function };

    get tasten() {
        return TASTEN;
    }
}

/** Reiter „Wählen“: Eingabe, Tastenfeld und „Anrufen“. */
export class Waehltastatur extends Component {
    static template = "wb_voip.Waehltastatur";
    static components = { Tastenfeld };
    static props = ["*"];

    setup() {
        this.lokal = useState({ nummer: "" });
    }

    taste(ziffer) {
        this.lokal.nummer += ziffer;
    }

    loeschen() {
        this.lokal.nummer = this.lokal.nummer.slice(0, -1);
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.anrufen();
        }
    }

    async anrufen() {
        const nummer = this.lokal.nummer.trim();
        if (!nummer) {
            return;
        }
        if (await waehlenMitHinweis(this.env, nummer)) {
            this.lokal.nummer = "";
        }
    }
}

registry.category("wb_voip_reiter").add("waehlen", {
    titel: _t("Dial"),
    Component: Waehltastatur,
    sequence: 10,
});
