import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

import { statusInfo } from "./status";

/** Telefonsymbol in der Systray mit Statuspunkt; öffnet und schließt das Softphone. */
export class WbVoipSystray extends Component {
    static template = "wb_voip.Systray";
    static props = {};

    setup() {
        this.telefon = useService("wb_voip_telefon");
        this.softphone = useService("wb_voip_softphone");
        this.state = useState(this.telefon.state);
        this.ui = useState(this.softphone.state);
    }

    get info() {
        return statusInfo(this.state.status);
    }

    get titel() {
        return _t("Telefon: %s", this.info.text);
    }

    umschalten() {
        this.softphone.umschalten();
    }
}

registry.category("systray").add(
    "wb_voip.softphone",
    { Component: WbVoipSystray, isDisplayed: () => Boolean(session.wb_voip_aktiv) },
    { sequence: 90 }
);
