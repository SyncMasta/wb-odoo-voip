import { Component, reactive, useEffect, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

import { Anrufansicht } from "./anrufansicht";
import { StatusAnzeige } from "./status";

/**
 * Reiter des Softphones. Einträge: ``{ titel, Component, sequence }``. Spätere Module
 * (z. B. „Verpasst“) fügen hier eigene Reiter hinzu, ohne das Softphone zu patchen.
 */
export const reiterRegistry = registry.category("wb_voip_reiter");

/** Oberflächenzustand des Softphones (offen, aktiver Reiter), je Tab. */
export const softphoneDienst = {
    start() {
        const state = reactive({ offen: false, reiter: "waehlen" });
        return {
            state,
            oeffnen(reiter = null) {
                state.offen = true;
                if (reiter) {
                    state.reiter = reiter;
                }
            },
            schliessen() {
                state.offen = false;
            },
            umschalten() {
                state.offen = !state.offen;
            },
        };
    },
};
registry.category("services").add("wb_voip_softphone", softphoneDienst);

/** Inhalt des Softphone-Fensters: Zustand, Reiter oder Anrufansicht. */
export class Softphone extends Component {
    static template = "wb_voip.Softphone";
    static components = { Anrufansicht, StatusAnzeige };
    static props = { onClose: { type: Function, optional: true } };

    setup() {
        this.telefon = useService("wb_voip_telefon");
        this.softphone = useService("wb_voip_softphone");
        this.benachrichtigung = useService("wb_voip_benachrichtigung");
        this.state = useState(this.telefon.state);
        this.ui = useState(this.softphone.state);
        this.lokal = useState({ erlaubnis: this.benachrichtigung?.erlaubnis || "unsupported" });
    }

    get reiter() {
        return reiterRegistry
            .getEntries()
            .map(([schluessel, eintrag]) => ({ schluessel, ...eintrag }))
            .sort((a, b) => (a.sequence ?? 50) - (b.sequence ?? 50));
    }

    get aktiverReiter() {
        const alle = this.reiter;
        return alle.find((r) => r.schluessel === this.ui.reiter) || alle[0];
    }

    waehleReiter(schluessel) {
        this.ui.reiter = schluessel;
    }

    async benachrichtigungErlauben() {
        this.lokal.erlaubnis = await this.benachrichtigung.erlaubnisAnfragen();
    }

    schliessen() {
        if (this.props.onClose) {
            this.props.onClose();
        } else {
            this.softphone.schliessen();
        }
    }
}

/** Festes Fenster unten rechts; öffnet sich bei jedem Anruf von selbst. */
export class SoftphoneFenster extends Component {
    static template = "wb_voip.SoftphoneFenster";
    static components = { Softphone };
    static props = {};

    setup() {
        this.telefon = useService("wb_voip_telefon");
        this.softphone = useService("wb_voip_softphone");
        this.state = useState(this.telefon.state);
        this.ui = useState(this.softphone.state);
        useEffect(
            (anruf) => {
                if (anruf) {
                    this.softphone.oeffnen();
                }
            },
            () => [this.state.anruf]
        );
    }

    get sichtbar() {
        return Boolean(session.wb_voip_aktiv) && Boolean(this.ui.offen || this.state.anruf);
    }
}

registry.category("main_components").add("wb_voip.SoftphoneFenster", {
    Component: SoftphoneFenster,
});
