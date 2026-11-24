import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { effect } from "@web/core/utils/reactive";

const TAG = "wb_voip_anruf";

/**
 * Desktop-Benachrichtigung bei eingehenden Anrufen. Die Erlaubnis wird höchstens einmal
 * je Tab von selbst angefragt (beim ersten Anruf) oder über den Knopf im Softphone;
 * eine Ablehnung blockiert nie etwas.
 */
export class Benachrichtiger {
    /**
     * @param {object} deps
     * @param {typeof Notification|null} deps.api   window.Notification oder Fake
     * @param {() => boolean} deps.hatFokus         true: Fenster sichtbar und im Fokus
     */
    constructor({ api, hatFokus }) {
        this.api = api || null;
        this.hatFokus = hatFokus;
        this._gefragt = false;
        this._aktuell = null;
        this._schluessel = null;
    }

    /** "default" | "granted" | "denied" | "unsupported" */
    get erlaubnis() {
        return this.api ? this.api.permission : "unsupported";
    }

    async erlaubnisAnfragen() {
        if (!this.api || this.api.permission !== "default") {
            return this.erlaubnis;
        }
        this._gefragt = true;
        try {
            await this.api.requestPermission();
        } catch {
            // Browser verweigert die Anfrage: dann eben ohne Benachrichtigung
        }
        return this.erlaubnis;
    }

    async eingehend({ nummer, partner }) {
        if (!this.api) {
            return;
        }
        if (this.api.permission === "default" && !this._gefragt) {
            await this.erlaubnisAnfragen();
        }
        if (this.api.permission !== "granted" || this.hatFokus()) {
            return;
        }
        const body = partner ? `${partner[1]} (${nummer})` : String(nummer || "");
        try {
            const meldung = new this.api(_t("Eingehender Anruf"), { body, tag: TAG });
            meldung.onclick = () => {
                window.focus();
                this.schliessen();
            };
            this._aktuell = meldung;
        } catch {
            // z. B. Android-Chrome ohne Service Worker: ohne Benachrichtigung weiter
        }
    }

    schliessen() {
        try {
            this._aktuell?.close?.();
        } catch {
            // schon geschlossen
        }
        this._aktuell = null;
    }

    /** Beobachtet den Zustand des Telefon-Dienstes: klingelt ein Anruf, wird gemeldet. */
    beobachten(state) {
        effect(
            (s) => {
                const anruf = s.anruf;
                if (anruf && anruf.richtung === "eingehend" && !anruf.annahme) {
                    const schluessel = `${anruf.beginn}|${anruf.nummer}`;
                    if (schluessel !== this._schluessel) {
                        this._schluessel = schluessel;
                        this.eingehend({ nummer: anruf.nummer, partner: anruf.partner });
                    }
                } else if (this._schluessel) {
                    this._schluessel = null;
                    this.schliessen();
                }
            },
            [state]
        );
    }
}

export const benachrichtigungDienst = {
    dependencies: ["wb_voip_telefon"],
    start(env, { wb_voip_telefon: telefon }) {
        const benachrichtiger = new Benachrichtiger({
            api: window.Notification || null,
            hatFokus: () => document.visibilityState === "visible" && document.hasFocus(),
        });
        if (telefon?.state) {
            benachrichtiger.beobachten(telefon.state);
        }
        return benachrichtiger;
    },
};

registry.category("services").add("wb_voip_benachrichtigung", benachrichtigungDienst);
