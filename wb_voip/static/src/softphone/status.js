import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

import { NICHT_EINGERICHTET } from "../dienste/telefon_dienst";

/** Farbe (Odoo-Klasse) und Text je Zustand des Telefon-Dienstes. */
export const STATUS = {
    verbunden: { farbe: "text-success", text: _t("Verbunden") },
    verbinde: { farbe: "text-muted", text: _t("Verbinde …") },
    aus: { farbe: "text-muted", text: _t("Aus") },
    anderer_tab: { farbe: "text-warning", text: _t("Telefon läuft in einem anderen Tab") },
    getrennt: { farbe: "text-danger", text: _t("Nicht verbunden") },
    ohne_lizenz: { farbe: "text-danger", text: _t("Lizenz fehlt oder abgelaufen") },
    nicht_konfiguriert: {
        farbe: "text-danger",
        text: _t("Telefonie ist noch nicht eingerichtet."),
    },
};

export function statusInfo(status) {
    return STATUS[status] || STATUS.aus;
}

export const LIZENZ_AKTION = "wb_license_client.wb_license_activate_wizard_action";

/** Zustandszeile im Softphone mit Hinweisen und „Neu verbinden“. */
export class StatusAnzeige extends Component {
    static template = "wb_voip.StatusAnzeige";
    static props = {};

    setup() {
        this.telefon = useService("wb_voip_telefon");
        this.action = useService("action");
        this.state = useState(this.telefon.state);
    }

    get info() {
        return statusInfo(this.state.status);
    }

    get istManager() {
        return Boolean(session.wb_voip_manager);
    }

    get lizenzAktivierbar() {
        return this.state.status === "ohne_lizenz" && Boolean(user.isSystem);
    }

    /** „aus“, weil dem Benutzer Anlage oder SIP-Benutzer fehlt. */
    get nichtEingerichtet() {
        // Meldung ist übersetzt (_t): als Text vergleichen
        const fehler = this.state.fehler;
        return (
            this.state.status === "aus" &&
            Boolean(fehler) &&
            String(fehler) === String(NICHT_EINGERICHTET)
        );
    }

    /** Meldung des Dienstes, sofern sie nicht schon im Zustandstext steht. */
    get fehler() {
        const fehler = this.state.fehler;
        if (!fehler || String(fehler) === String(this.info.text)) {
            return false;
        }
        if (this.state.status === "nicht_konfiguriert" || this.nichtEingerichtet) {
            // Eigener Hinweistext; technische Meldung (Schlüsselname) nur für Administratoren
            return false;
        }
        return fehler;
    }

    neuVerbinden() {
        this.telefon.neuVerbinden();
    }

    lizenzAktivieren() {
        this.action.doAction(LIZENZ_AKTION);
    }
}
