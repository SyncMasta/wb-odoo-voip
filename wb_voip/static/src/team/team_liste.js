import { Component, onMounted, onWillStart, onWillUnmount, useEffect, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime } from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";

import { waehlbar } from "../dienste/telefon_dienst";
import { formatDauer, waehlenMitHinweis } from "../softphone/waehlen_hilfe";
import { seite } from "../verpasst/verpasst_liste";

/** Neuladen bei offenem, sichtbarem Reiter: das Ablaufen eines Lebenszeichens sendet keinen Bus. */
export const TEAM_NEULADEN_MS = 60_000;

/** Punkt je Status; ``klingelt`` blinkt (nur ohne reduzierte Bewegung, siehe SCSS). */
const PUNKT = {
    frei: "text-success",
    telefoniert: "text-danger",
    klingelt: "text-warning o_wb_voip_team_blinkt",
    abgemeldet: "text-muted",
};

/**
 * Reiter „Team“: Status der Kollegen mit Anrufen, Heranholen und (während eines eigenen
 * Gesprächs) Übergeben. Lädt beim Öffnen, bei Bus-Meldung und alle 60 s, jeweils nur im
 * sichtbaren Tab und höchstens ein Abruf zugleich.
 */
export class TeamListe extends Component {
    static template = "wb_voip.TeamListe";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.telefon = useService("wb_voip_telefon");
        this.team = useService("wb_voip_team");
        // Rückfrage und Konferenz kommen mit späteren Aufgaben in den Telefon-Dienst
        this.kannRueckfrage = typeof this.telefon.rueckfrage === "function";
        this.kannKonferenz = typeof this.telefon.konferenz === "function";
        this.telefonState = useState(this.telefon.state);
        this.lokal = useState({
            kollegen: [],
            praefix: false,
            fehler: false,
            geladen: false,
            jetzt: Date.now(),
        });
        this._laeuft = false;
        this._nochmal = false;
        onWillStart(() => this.laden());
        useBus(this.team.bus, "aktualisieren", () => this.aktualisieren());
        const beiSichtbarkeit = () => this.aktualisieren();
        let neuladen = null;
        onMounted(() => {
            document.addEventListener("visibilitychange", beiSichtbarkeit);
            neuladen = setInterval(() => this.aktualisieren(), TEAM_NEULADEN_MS);
        });
        onWillUnmount(() => {
            document.removeEventListener("visibilitychange", beiSichtbarkeit);
            clearInterval(neuladen);
        });
        // 1-s-Takt für die Dauer, nur solange jemand telefoniert und der Reiter offen ist
        useEffect(
            (zaehlen) => {
                if (!zaehlen) {
                    return;
                }
                this.lokal.jetzt = Date.now();
                const takt = setInterval(() => (this.lokal.jetzt = Date.now()), 1000);
                return () => clearInterval(takt);
            },
            () => [this.dauerZaehlt]
        );
    }

    get dauerZaehlt() {
        return this.lokal.kollegen.some((k) => k.status === "telefoniert" && k.seit);
    }

    /** Lädt die Liste neu, aber nur im sichtbaren Tab; Parallelität wird zu einem Nachlauf. */
    async aktualisieren() {
        if (!seite.sichtbar()) {
            return;
        }
        if (this._laeuft) {
            this._nochmal = true;
            return;
        }
        this._laeuft = true;
        try {
            do {
                this._nochmal = false;
                await this.laden();
            } while (this._nochmal && seite.sichtbar());
        } finally {
            this._laeuft = false;
        }
    }

    async laden() {
        try {
            const daten = await this.orm.call("res.users", "wb_voip_team", []);
            this.lokal.kollegen = Array.isArray(daten?.kollegen) ? daten.kollegen : [];
            this.lokal.praefix = typeof daten?.heranholen_praefix === "string"
                ? daten.heranholen_praefix
                : false;
            this.lokal.fehler = false;
        } catch (fehler) {
            this.lokal.fehler = fehler?.data?.message || _t("Team konnte nicht geladen werden.");
        }
        this.lokal.geladen = true;
    }

    // --- Darstellung ----------------------------------------------------------------

    punkt(kollege) {
        return PUNKT[kollege.status] || PUNKT.abgemeldet;
    }

    statusText(kollege) {
        switch (kollege.status) {
            case "frei":
                return _t("Frei");
            case "klingelt":
                return _t("Klingelt");
            case "telefoniert": {
                const dauer = this.dauer(kollege);
                return dauer ? _t("Telefoniert %s", dauer) : _t("Telefoniert");
            }
            default:
                return _t("Abgemeldet");
        }
    }

    /** Dauer seit Annahme („m:ss“), im Browser mit der lokalen Uhr weitergezählt. */
    dauer(kollege) {
        if (!kollege.seit) {
            return "";
        }
        const beginn = deserializeDateTime(kollege.seit);
        if (!beginn.isValid) {
            return "";
        }
        return formatDauer((this.lokal.jetzt - beginn.toMillis()) / 1000);
    }

    /** Beschriftung (Tooltip und Bildschirmleser) eines Knopfes für diesen Kollegen. */
    beschriftung(art, kollege) {
        const name = kollege.name;
        switch (art) {
            case "anrufen":
                return _t("Anrufen: %s", name);
            case "heranholen":
                return _t("Heranholen: %s", name);
            case "uebergeben":
                return _t("Übergeben: %s", name);
            case "rueckfrage":
                return _t("Mit Rückfrage: %s", name);
            default:
                return _t("Konferenz: %s", name);
        }
    }

    // --- Knöpfe -----------------------------------------------------------------------

    get telefonBereit() {
        return ["verbunden", "anderer_tab"].includes(this.telefonState.status);
    }

    /** Eigenes Gespräch läuft (angenommen): nur dann Übergeben, Rückfrage, Konferenz. */
    get imGespraech() {
        return this.telefonState.anruf?.zustand === "laeuft";
    }

    erreichbar(kollege) {
        return kollege.status !== "abgemeldet" && Boolean(waehlbar(kollege.nebenstelle));
    }

    anrufbar(kollege) {
        return this.telefonBereit && this.erreichbar(kollege);
    }

    heranholbar(kollege) {
        return (
            this.telefonBereit &&
            kollege.status === "klingelt" &&
            Boolean(this.lokal.praefix) &&
            Boolean(waehlbar(this.lokal.praefix + kollege.nebenstelle))
        );
    }

    uebergebbar(kollege) {
        return this.imGespraech && this.erreichbar(kollege);
    }

    anrufen(kollege) {
        return waehlenMitHinweis(this.env, kollege.nebenstelle);
    }

    heranholen(kollege) {
        return waehlenMitHinweis(this.env, this.lokal.praefix + kollege.nebenstelle);
    }

    uebergeben(kollege) {
        return this._gespraech(() => this.telefon.weiterleiten(kollege.nebenstelle));
    }

    rueckfrage(kollege) {
        return this._gespraech(() => this.telefon.rueckfrage(kollege.nebenstelle));
    }

    konferenz(kollege) {
        return this._gespraech(() => this.telefon.konferenz(kollege.nebenstelle));
    }

    async _gespraech(aktion) {
        const ok = await aktion();
        if (ok === false) {
            this.env.services.notification?.add(
                this.telefonState.fehler || _t("Aktion konnte nicht ausgeführt werden."),
                { type: "danger" }
            );
        }
    }
}

registry.category("wb_voip_reiter").add("team", {
    titel: _t("Team"),
    Component: TeamListe,
    sequence: 27,
});
