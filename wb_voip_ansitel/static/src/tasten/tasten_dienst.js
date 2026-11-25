import { reactive } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { effect } from "@web/core/utils/reactive";
import { session } from "@web/session";

/** Bus-Kanal des Servers: gleicher Inhalt wie die Antwort von ``wb_voip_tasten_zustand``. */
export const BUS_TASTEN = "wb_voip/tasten";
/** Abfrage im Telefon-Tab jede Minute. */
export const TAKT_MS = 60_000;
/** Ohne Schnelltasten (geladen, nicht verfügbar, kein Fehler) genügt eine seltene Abfrage. */
export const TAKT_RUHE_MS = 600_000;
/** So kurz nach einem Lesen zählt der Wechsel zum Telefon-Tab nicht als Anlass für ein weiteres. */
export const KURZ_MS = 10_000;

/** Soll die Leiste in dieser Lage („softphone“ oder „rand“) gezeigt werden? */
export function tastenSichtbar(zustand, ort, lage) {
    if (!zustand || !zustand.verfuegbar) {
        return false;
    }
    return ort === "beide" || ort === lage;
}

/**
 * Übernimmt nur die bekannten Felder einer Serverantwort oder Bus-Meldung (der Browser
 * bekommt nie Zugangsdaten; was nicht passt, wird verworfen).
 */
function bereinigen(daten) {
    if (!daten || typeof daten !== "object" || !Array.isArray(daten.tasten)) {
        return null;
    }
    const text = (wert) => (typeof wert === "string" && wert ? wert : false);
    return {
        verfuegbar: daten.verfuegbar === true,
        fehler: text(daten.fehler),
        tasten: daten.tasten
            .filter((t) => t && Number.isInteger(t.id))
            .map((t) => ({
                id: t.id,
                name: typeof t.name === "string" ? t.name : "",
                icon: text(t.icon),
                art: typeof t.art === "string" ? t.art : "",
                aktiv: t.aktiv === true ? true : t.aktiv === false ? false : null,
            })),
        fremde_weiterleitung: text(daten.fremde_weiterleitung),
        umgeleitet: daten.umgeleitet === true,
    };
}

/**
 * Zustand und Bedienung der Schnelltasten (Weiterleitung, Nicht stören, AB).
 *
 * Gelesen wird beim Start einmal und danach jede Minute, aber nur im Tab, der das Telefon
 * hält (``telefon.state.halter``); die anderen Tabs bekommen den Zustand über den Bus. Eine
 * Bus-Meldung wird nur gespeichert, nie mit einer neuen Abfrage beantwortet. Beim Schalten
 * geht der Wunschzustand mit (zwei Tabs schalten nicht hin und her).
 */
export class TastenDienst {
    constructor({ orm, busService, telefon, aktiv, ort }) {
        this.orm = orm;
        this.state = reactive({
            ort: ort || false,
            geladen: false,
            verfuegbar: false,
            fehler: false,
            tasten: [],
            fremde_weiterleitung: false,
            umgeleitet: false,
            schaltet: false,
        });
        this._nr = 0; // jede Antwort darf nur eine ältere überholen
        this._zuletzt = 0;
        this._timer = null;
        this._angefordert = false;
        this._laedt = false;
        if (aktiv) {
            busService.subscribe(BUS_TASTEN, (nutzlast) => {
                this._nr++;
                this._speichern(nutzlast);
            });
            effect((telefonZustand) => this._halter(Boolean(telefonZustand.halter)), [telefon.state]);
        }
    }

    /** Takt starten oder beenden, je nachdem, ob dieser Tab das Telefon hält. */
    _halter(haelt) {
        if (this._timer !== null) {
            clearInterval(this._timer);
            this._timer = null;
        }
        if (haelt) {
            this._angefordert = true;
            // beim Start kommt der Halter kurz nach dem ersten Lesen: nicht noch einmal fragen
            if (!this._kuerzlichGelesen()) {
                this.laden();
            }
            this._timer = setInterval(() => this._takt(), TAKT_MS);
        } else if (!this._angefordert) {
            // Nebentab: einmal beim Start, danach nur über den Bus
            this._angefordert = true;
            this.laden();
        }
    }

    /** Wurde gerade eben (oder läuft noch) gelesen, ist ein zweites Lesen beim Start überflüssig. */
    _kuerzlichGelesen() {
        return this._laedt || (this._zuletzt > 0 && Date.now() - this._zuletzt < KURZ_MS);
    }

    _takt() {
        const ruhe = this.state.geladen && !this.state.verfuegbar && !this.state.fehler;
        if (ruhe && Date.now() - this._zuletzt < TAKT_RUHE_MS) {
            return;
        }
        this.laden();
    }

    /** Legt den Zustand ab (Antwort oder Bus). Ruft nie den Server. */
    _speichern(daten) {
        const zustand = bereinigen(daten);
        if (zustand) {
            Object.assign(this.state, zustand, { geladen: true });
        }
    }

    /** Liest den Zustand vom Server; eine ältere Antwort überholt keine neuere. */
    async laden() {
        if (this._laedt) {
            return; // ein Lesen ist unterwegs, dessen Antwort gilt
        }
        this._laedt = true;
        this._zuletzt = Date.now();
        const nr = ++this._nr;
        try {
            const antwort = await this.orm.call("res.users", "wb_voip_tasten_zustand", []);
            if (nr === this._nr) {
                this._speichern(antwort);
            }
        } catch {
            // der alte Zustand bleibt; der nächste Takt versucht es erneut
        } finally {
            this._laedt = false;
        }
    }

    /** Gewünschter Zustand beim Klick: das Gegenteil des gezeigten, bei grauer Taste ``null`` (umschalten). */
    wunschzustand(taste) {
        return taste.aktiv === null ? null : !taste.aktiv;
    }

    schalten(tasteId, an = null) {
        return this._aktion("wb_voip_taste_schalten", [tasteId, an]);
    }

    weiterleitungAus() {
        return this._aktion("wb_voip_weiterleitung_aus", []);
    }

    async _aktion(methode, args) {
        if (this.state.schaltet) {
            return;
        }
        this.state.schaltet = true;
        // eine noch laufende Leseantwort ist damit überholt
        this._nr++;
        try {
            this._speichern(await this.orm.call("res.users", methode, args));
        } catch {
            this.state.fehler = _t("Schnelltasten sind gerade nicht verfügbar.");
        } finally {
            this.state.schaltet = false;
        }
    }
}

export const tastenDienst = {
    dependencies: ["orm", "bus_service", "wb_voip_telefon"],
    start(env, { orm, bus_service, wb_voip_telefon }) {
        return new TastenDienst({
            orm,
            busService: bus_service,
            telefon: wb_voip_telefon,
            // nur für eingerichtete VoIP-Benutzer, und nur mit Einstellung (ir.http.session_info)
            aktiv: Boolean(session.wb_voip_aktiv && session.wb_voip_ansitel_tastenleiste),
            ort: session.wb_voip_ansitel_tastenleiste,
        });
    },
};

registry.category("services").add("wb_voip_ansitel_tasten", tastenDienst);
