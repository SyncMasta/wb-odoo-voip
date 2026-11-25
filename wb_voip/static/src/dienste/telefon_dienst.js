import { EventBus, reactive } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { session } from "@web/session";

import { Klaenge } from "./klaenge";
import { konferenzAbbauen, konferenzAufbauen } from "./konferenz";
import { erzeugeAgentFabrik } from "./sip_agent";

// Die Anlage nimmt je Nebenstelle nur eine Registrierung an: Nur der Tab, der die
// Web-Sperre hält, meldet sich an. Andere Tabs reichen Wählen über den Kanal weiter.
export const SPERRE = "wb_voip_telefon";
export const KANAL = "wb_voip_anrufe";
/** Weitergereichte Wählaufträge, die älter sind, verwirft der Halter. */
export const WEITERLEITUNG_MAX_ALTER_MS = 2500;
/** Abstände zwischen Verbindungsversuchen; danach bleibt es beim letzten Wert. */
export const WIEDERHOLUNG_MS = [3000, 10_000, 30_000, 60_000];
export const LEBENSZEICHEN_MS = 60_000;
/** Takt, in dem der Telefon-Tab dem Team „angemeldet“ meldet (Server: gültig 150 s). */
export const TEAM_LEBENSZEICHEN_MS = 60_000;
/** Neue Versuche für wb_voip_beendet; danach schließt der Server-Cron den Anruf. */
export const BEENDET_WIEDERHOLUNG_MS = [2000, 5000, 10_000];
/** Höchstlänge der Notiz (wie auf dem Server geprüft). */
export const MAX_NOTIZ = 10_000;
/** Höchstlänge der Anrufer-Kennung (wie die Nummer auf dem Server geprüft). */
export const MAX_NUMMER = 64;
/** Meldung, wenn dem Benutzer Anlage oder SIP-Benutzer fehlt (Zustand „aus“). */
export const NICHT_EINGERICHTET = _t("Telefon ist für diesen Benutzer nicht eingerichtet.");
/** Höchstens so viele mit 480 abgewiesene Anrufe je Minute an den Server melden. */
export const ABGEWIESEN_MAX_JE_MINUTE = 10;
const ABGEWIESEN_FENSTER_MS = 60_000;
/** So viele Call-IDs abgewiesener Anrufe merkt sich der Dienst (je Call-ID nur eine Meldung). */
const ABGEWIESEN_MERKEN = 100;
const HALTER_FRAGE_MS = 1000;
const DTMF = /^[0-9*#]$/;

/** Wählbare Form: führendes „+“ wird „00“, sonst nur Ziffern, „*“ und „#“. */
export function waehlbar(nummer) {
    const roh = String(nummer ?? "").trim();
    return (roh.startsWith("+") ? "00" : "") + roh.replace(/[^0-9*#]/g, "");
}

// Steuerzeichen (C0, DEL, C1) und Bidi-Steuerzeichen, die eine Anzeige verfälschen können
// eslint-disable-next-line no-control-regex
const STEUERZEICHEN = /[\u0000-\u001f\u007f-\u009f\u200e\u200f\u202a-\u202e\u2066-\u2069]/g;

/**
 * Anrufer-Kennung einer eingehenden Sitzung, bereinigt für Server und Anzeige: ohne
 * Steuerzeichen, höchstens ``MAX_NUMMER`` Zeichen, leer wird „anonymous“.
 */
export function anruferKennung(roh) {
    const text = String(roh ?? "").replace(STEUERZEICHEN, "").trim().slice(0, MAX_NUMMER).trim();
    return text || "anonymous";
}

/** Fachlicher Fehler des Servers (UserError, AccessError …): neuer Versuch zwecklos. */
function istOdooFehler(fehler) {
    return String(fehler?.data?.name || "").startsWith("odoo.exceptions.");
}

function fehlertext(fehler) {
    return (
        fehler?.data?.message ||
        fehler?.message ||
        (fehler ? String(fehler) : _t("Unbekannter Fehler"))
    );
}

export const browserUhr = {
    jetzt: () => Date.now(),
    setTimeout: (fn, ms) => window.setTimeout(fn, ms),
    clearTimeout: (id) => window.clearTimeout(id),
    setInterval: (fn, ms) => window.setInterval(fn, ms),
    clearInterval: (id) => window.clearInterval(id),
};

/**
 * Telefon-Dienst eines Browser-Tabs. Alle Außenabhängigkeiten werden übergeben,
 * damit Tests sie ersetzen können.
 */
export class TelefonDienst {
    /**
     * @param {object} deps
     * @param {{call: Function}} deps.orm
     * @param {LockManager} deps.sperren          navigator.locks oder Fake
     * @param {BroadcastChannel} deps.kanal
     * @param {typeof browserUhr} deps.uhr
     * @param {string} deps.tabId
     * @param {(zugang: object, hilfen: object) => Promise<object>} deps.agentFabrik
     * @param {{freizeichen: Function, klingeln: Function, aus: Function}} deps.klaenge
     * @param {EventTarget} [deps.fenster]       window: bei „pagehide“ abmelden, bei
     *                                           „pageshow“ aus dem bfcache neu starten
     * @param {() => BroadcastChannel} [deps.kanalFabrik] neuer Kanal für den Neustart
     * @param {() => AudioContext} [deps.audioKontext] neuer AudioContext für die Konferenz
     * @param {(text: string) => void} [deps.meldung] Hinweis an den Benutzer (Benachrichtigung)
     */
    constructor({
        orm,
        sperren,
        kanal,
        uhr,
        tabId,
        agentFabrik,
        klaenge,
        fenster = null,
        kanalFabrik = null,
        audioKontext = null,
        meldung = null,
    }) {
        this.orm = orm;
        this.sperren = sperren;
        this._kanalFabrik = kanalFabrik;
        this.kanal = kanal ?? kanalFabrik?.() ?? null;
        this.uhr = uhr;
        this.tabId = tabId;
        this.agentFabrik = agentFabrik;
        this.klaenge = klaenge;
        this._audioKontext =
            audioKontext || (() => new (window.AudioContext || window.webkitAudioContext)());
        this._meldung = meldung;
        this.bus = new EventBus();
        /**
         * status: aus | verbinde | verbunden | getrennt | anderer_tab | ohne_lizenz
         *         | nicht_konfiguriert
         */
        /**
         * anruf: aktive Leitung; gehaltener: zweite, gehaltene Leitung (Rückfrage oder
         * angenommener zweiter Anruf); klopfend: zweiter eingehender Anruf, der noch klingelt.
         * Höchstens zwei Leitungen zugleich. konferenz: beide Leitungen laufen gemischt
         * (keine gehalten).
         */
        this.state = reactive({
            status: "aus",
            anruf: null,
            gehaltener: null,
            klopfend: null,
            verbindet: false, // „Verbinden“ wartet auf die Anlage
            konferenz: false,
            // Team-Übersicht der aktiven Firma eingeschaltet (aus wb_voip_status); nur dann
            // Lebenszeichen und Team-Auswahl in der Anrufansicht
            teamAktiv: false,
            fehler: null,
        });

        this._istHalter = false;
        this._halterTabId = null;
        this._halterWarten = [];
        this._freigeben = null;
        this._gestoppt = false;
        this._gestartet = false;
        this._seiteVersteckt = false;
        this._generation = 0; // je start(); alte Sperr-Rückrufe verfallen
        this._lauf = 0;
        this._versuch = 0;
        this._wiederTimer = null;
        this._agent = null;
        this._aktiv = null; // Kontext der aktiven Leitung
        this._gehalten = null; // Kontext der gehaltenen Leitung
        this._klopfend = null; // Kontext des wartenden eingehenden Anrufs
        this._rueckfrageLaeuft = false; // Halten vor der Rückfrage läuft noch
        this._verbindet = null; // {aktiv, gehalten, beideBeendet} während „Verbinden“
        this._konferenz = null; // {aktiv, gehalten, handle} ab dem Aufbau der Konferenz
        this._abgewiesenIds = new Set(); // Call-IDs schon gemeldeter 480-Anrufe
        this._abgewiesenZeiten = []; // Meldezeiten im laufenden Minutenfenster
        this._popupWarten = []; // Protokoll-Popups, bis keine Leitung mehr läuft
        this._teamTimer = null; // Takt des Team-Lebenszeichens (nur im Telefon-Tab)
        this._teamGemeldet = false; // dem Server zuletzt „angemeldet“ gemeldet

        this._kanalVerbinden();
        // Tab wird geschlossen, neu geladen oder in den bfcache gelegt: sofort an der Anlage
        // abmelden und die Sperre freigeben, damit ein anderer Tab übernimmt. Kommt die Seite
        // aus dem bfcache zurück (pageshow mit persisted), startet der Dienst neu.
        this._fenster = fenster;
        this._beiPagehide = () => {
            if (!this._gestoppt) {
                this._seiteVersteckt = this._gestartet;
                this.stop();
            }
        };
        this._beiPageshow = (ereignis) => {
            if (ereignis?.persisted && this._seiteVersteckt) {
                this._neustart();
            }
        };
        this._fenster?.addEventListener("pagehide", this._beiPagehide);
        this._fenster?.addEventListener("pageshow", this._beiPageshow);
    }

    _kanalVerbinden() {
        if (this.kanal) {
            this.kanal.onmessage = (ereignis) => this._nachricht(ereignis?.data);
        }
    }

    // --- Haupt-Tab ------------------------------------------------------------------

    /** Bewirbt sich um die Sperre; die Antwort kommt, sobald klar ist, wer hält. */
    start() {
        this._gestartet = true;
        const generation = ++this._generation;
        return new Promise((bereit) => {
            this.sperren
                .request(SPERRE, { ifAvailable: true }, (sperre) => {
                    if (generation !== this._generation) {
                        bereit();
                        return;
                    }
                    if (!sperre) {
                        this._alsNebenTab(generation);
                        bereit();
                        return;
                    }
                    return this._halten(bereit);
                })
                .catch((fehler) => {
                    this.state.fehler = fehlertext(fehler);
                    bereit();
                });
        });
    }

    _alsNebenTab(generation) {
        if (this._gestoppt) {
            return;
        }
        this.state.status = "anderer_tab";
        this._senden({ art: "wer_haelt" });
        // Wartet, bis der Halter-Tab geschlossen wird, und übernimmt dann. Ein Rückruf aus
        // einer früheren Generation (vor bfcache) gibt die Sperre sofort wieder frei.
        this.sperren
            .request(SPERRE, (sperre) =>
                sperre && generation === this._generation ? this._halten(() => {}) : undefined
            )
            .catch(() => {});
    }

    _halten(bereit) {
        if (this._gestoppt) {
            bereit();
            return;
        }
        this._istHalter = true;
        this._halterTabId = this.tabId;
        this._senden({ art: "halter", tabId: this.tabId });
        const freigabe = new Promise((resolve) => {
            this._freigeben = resolve;
        });
        this._verbinden().finally(bereit);
        return freigabe;
    }

    /** Beendet den Dienst dieses Tabs und gibt die Sperre frei (Zustand „aus“). */
    stop() {
        if (this._gestoppt) {
            return;
        }
        this._gestoppt = true;
        this._lauf++;
        this._wiederholungStoppen();
        this._teamBeenden();
        this.klaenge?.aus();
        // Sitzungen enden mit dem Agenten; Ende noch melden (sonst schließt der Cron)
        this._alleBeenden((kontext) => this._beenden(kontext, "fehler"));
        this._agentBeenden();
        this._istHalter = false;
        this._halterTabId = null;
        this._halterWarten = [];
        this._freigeben?.();
        this._freigeben = null;
        this.kanal?.close?.();
        this.state.status = "aus";
    }

    /** Neustart nach Rückkehr aus dem bfcache: neuer Kanal, Sperre neu anfordern. */
    _neustart() {
        this._seiteVersteckt = false;
        this._gestoppt = false;
        this._versuch = 0;
        this.state.fehler = null;
        if (this._kanalFabrik) {
            this.kanal = this._kanalFabrik();
            this._kanalVerbinden();
        }
        return this.start();
    }

    // --- Verbindung -----------------------------------------------------------------

    /** Neuer Verbindungsversuch sofort; setzt die Wartezeiten zurück. */
    async neuVerbinden() {
        if (this._leitungen().length) {
            // Ein neuer Agent würde den laufenden Anruf abbrechen
            this.state.fehler = _t("Bitte erst auflegen.");
            return;
        }
        this._versuch = 0;
        if (this._istHalter) {
            await this._verbinden();
        } else if (!this._gestoppt) {
            this._senden({ art: "wer_haelt" });
        }
    }

    async _verbinden() {
        const lauf = ++this._lauf;
        const aktuell = () => lauf === this._lauf && !this._gestoppt;
        this._wiederholungStoppen();
        this._agentBeenden();
        this.state.status = "verbinde";
        this.state.fehler = null;

        let status;
        try {
            status = await this.orm.call("res.users", "wb_voip_status", []);
        } catch (fehler) {
            return aktuell() && this._fehlschlag(lauf, fehlertext(fehler));
        }
        if (!aktuell()) {
            return;
        }
        // Nur wb_voip_status bestimmt „aus“, „nicht konfiguriert“ und „ohne Lizenz“;
        // in diesen Fällen kein Wiederholungstakt (nur Neuladen oder „Neu verbinden“)
        if (!status?.aktiv) {
            this.state.status = "aus";
            return;
        }
        if (status.konfiguriert === false) {
            this.state.status = "nicht_konfiguriert";
            this.state.fehler =
                status.meldung || _t("Softphone ist auf dem Server nicht konfiguriert.");
            return;
        }
        if (!status.lizenz_gueltig) {
            this._ohneLizenz(status.meldung);
            return;
        }
        if (!status.eingerichtet) {
            this.state.status = "aus";
            this.state.fehler = NICHT_EINGERICHTET;
            return;
        }
        // fehlt die Angabe, gilt aus (Datensparsamkeit)
        this.state.teamAktiv = status.team_aktiv === true;

        let agent;
        try {
            let zugang = await this.orm.call("res.users", "wb_voip_zugang_lesen", []);
            if (!aktuell()) {
                return;
            }
            agent = await this.agentFabrik(zugang, { uhr: this.uhr });
            zugang = null; // Passwort nicht länger als nötig halten
        } catch (fehler) {
            if (!aktuell()) {
                return;
            }
            // Auch ein UserError (z. B. Passwort nicht lesbar): getrennt mit Meldung und
            // normalem Wiederverbinden
            return this._fehlschlag(lauf, fehlertext(fehler));
        }
        if (!aktuell()) {
            agent.stop?.();
            return;
        }
        this._agent = agent;
        agent.on("invitation", (sitzung) => {
            if (this._agent === agent) {
                this._eingehend(sitzung);
            } else {
                sitzung.reject({ statusCode: 480 });
            }
        });
        agent.on("getrennt", (grund) => {
            if (this._agent === agent) {
                this._fehlschlag(this._lauf, grund || _t("Verbindung zur Anlage getrennt"));
            }
        });
        try {
            await agent.register();
        } catch (fehler) {
            return aktuell() && this._fehlschlag(lauf, fehlertext(fehler));
        }
        if (!aktuell()) {
            return;
        }
        this._versuch = 0;
        this.state.status = "verbunden";
        this.state.fehler = null;
        this._teamStarten();
    }

    // --- Team-Lebenszeichen -----------------------------------------------------------

    /**
     * Meldet „angemeldet“ und wiederholt es alle 60 s, solange dieser Tab registriert ist; bei
     * ausgeschalteter Team-Übersicht nichts.
     */
    _teamStarten() {
        this._teamStoppenTimer();
        if (!this.state.teamAktiv) {
            return;
        }
        this._teamMelden(true);
        this._teamTimer = this.uhr.setInterval(() => this._teamMelden(true), TEAM_LEBENSZEICHEN_MS);
    }

    /** Beendet den Takt und meldet „abgemeldet“, falls zuvor „angemeldet“ gemeldet wurde. */
    _teamBeenden() {
        this._teamStoppenTimer();
        if (this._teamGemeldet) {
            this._teamMelden(false);
        }
    }

    _teamStoppenTimer() {
        if (this._teamTimer !== null) {
            this.uhr.clearInterval(this._teamTimer);
            this._teamTimer = null;
        }
    }

    /** Best effort: ein verlorener Aufruf wird beim nächsten Takt oder durch Ablauf (150 s) ausgeglichen. */
    _teamMelden(registriert) {
        this._teamGemeldet = registriert;
        try {
            Promise.resolve(
                this.orm.call("res.users", "wb_voip_lebenszeichen_melden", [registriert])
            ).catch(() => {});
        } catch {
            // Aufruf nicht möglich (z. B. beim Schließen des Tabs)
        }
    }

    _ohneLizenz(meldung) {
        this._lauf++; // laufende Verbindungsversuche verwerfen
        this._wiederholungStoppen();
        this._alleBeenden((kontext) => this._anrufAbbrechen(kontext));
        this._agentBeenden();
        this.state.status = "ohne_lizenz";
        this.state.fehler = meldung || _t("Lizenz fehlt oder abgelaufen");
    }

    /**
     * Nach einem fachlichen Fehler von ``wb_voip_beginnen``: Lizenz neu prüfen (sie kann
     * abgelaufen sein, während das Telefon registriert war) und dann abmelden.
     */
    async _lizenzNachpruefen() {
        let status;
        try {
            status = await this.orm.call("res.users", "wb_voip_status", []);
        } catch {
            return;
        }
        if (
            !this._gestoppt &&
            status?.aktiv &&
            status.konfiguriert !== false &&
            status.lizenz_gueltig === false
        ) {
            this._ohneLizenz(status.meldung);
        }
    }

    /** Legt den Anruf ohne Rückfrage auf (Lizenz weg) und schließt ihn lokal. */
    _anrufAbbrechen(kontext) {
        try {
            let ergebnis;
            if (kontext.angenommen) {
                ergebnis = kontext.sitzung.bye();
            } else if (kontext.anruf.richtung === "eingehend") {
                ergebnis = kontext.sitzung.reject({ statusCode: 480 });
            } else {
                ergebnis = kontext.sitzung.cancel();
            }
            Promise.resolve(ergebnis).catch(() => {});
        } catch {
            // Sitzung war schon beendet
        }
        this._beenden(kontext, "fehler");
    }

    _fehlschlag(lauf, text) {
        if (lauf !== this._lauf || this._gestoppt) {
            return;
        }
        this._lauf++; // spätere Antworten des alten Versuchs ignorieren
        this._agentBeenden();
        this._alleBeenden((kontext) => this._beenden(kontext, "fehler"));
        this.state.status = "getrennt";
        this.state.fehler = text;
        const warten = WIEDERHOLUNG_MS[Math.min(this._versuch, WIEDERHOLUNG_MS.length - 1)];
        this._versuch++;
        this._wiederholungStoppen();
        this._wiederTimer = this.uhr.setTimeout(() => {
            this._wiederTimer = null;
            this._verbinden();
        }, warten);
    }

    _wiederholungStoppen() {
        if (this._wiederTimer !== null) {
            this.uhr.clearTimeout(this._wiederTimer);
            this._wiederTimer = null;
        }
    }

    _agentBeenden() {
        const agent = this._agent;
        this._agent = null;
        // ohne Registrierung gilt der Tab im Team als abgemeldet
        this._teamBeenden();
        if (agent) {
            // synchron aufrufen: beim Schließen des Tabs muss die Abmeldung sofort raus
            try {
                Promise.resolve(agent.stop()).catch(() => {});
            } catch {
                // Agent war schon kaputt
            }
        }
    }

    // --- Tab-Kanal --------------------------------------------------------------------

    _senden(nachricht) {
        try {
            this.kanal?.postMessage(nachricht);
        } catch {
            // Kanal geschlossen
        }
    }

    _nachricht(nachricht) {
        if (!nachricht || typeof nachricht !== "object" || this._gestoppt) {
            return;
        }
        switch (nachricht.art) {
            case "wer_haelt":
                if (this._istHalter) {
                    this._senden({ art: "halter", tabId: this.tabId });
                }
                break;
            case "halter":
                if (typeof nachricht.tabId === "string" && nachricht.tabId !== this.tabId) {
                    this._halterTabId = nachricht.tabId;
                    const warten = this._halterWarten;
                    this._halterWarten = [];
                    warten.forEach((fertig) => fertig(nachricht.tabId));
                }
                break;
            case "waehlen":
                this._weitergereichtWaehlen(nachricht);
                break;
        }
    }

    _weitergereichtWaehlen(nachricht) {
        if (!this._istHalter || nachricht.ziel !== this.tabId) {
            return;
        }
        if (!Number.isFinite(nachricht.zeit) || typeof nachricht.nummer !== "string") {
            return;
        }
        const alter = this.uhr.jetzt() - nachricht.zeit;
        if (alter > WEITERLEITUNG_MAX_ALTER_MS || alter < -WEITERLEITUNG_MAX_ALTER_MS) {
            return;
        }
        const resModel = typeof nachricht.resModel === "string" ? nachricht.resModel : false;
        const resId = Number.isInteger(nachricht.resId) ? nachricht.resId : false;
        this.waehlen(nachricht.nummer, { resModel, resId });
    }

    _halterErfragen() {
        if (this._halterTabId && this._halterTabId !== this.tabId) {
            return Promise.resolve(this._halterTabId);
        }
        return new Promise((resolve) => {
            const timer = this.uhr.setTimeout(() => {
                this._halterWarten = this._halterWarten.filter((f) => f !== fertig);
                resolve(null);
            }, HALTER_FRAGE_MS);
            const fertig = (tabId) => {
                this.uhr.clearTimeout(timer);
                resolve(tabId);
            };
            this._halterWarten.push(fertig);
            this._senden({ art: "wer_haelt" });
        });
    }

    async _weiterreichen(nummer, resModel, resId) {
        if (this._gestoppt) {
            return false;
        }
        const ziel = await this._halterErfragen();
        if (!ziel) {
            this.state.fehler = _t("Kein Telefon-Tab gefunden.");
            return false;
        }
        this._senden({
            art: "waehlen",
            ziel,
            nummer: String(nummer),
            resModel: resModel || false,
            resId: resId || false,
            zeit: this.uhr.jetzt(),
        });
        return true;
    }

    // --- Anrufe -----------------------------------------------------------------------

    /**
     * Wählt ``nummer``; in einem Neben-Tab wird der Auftrag an den Telefon-Tab gereicht.
     * Gespeichert wird die Nummer wie eingegeben, gewählt die wählbare Form.
     */
    async waehlen(nummer, { resModel = false, resId = false } = {}) {
        if (!this._istHalter) {
            return this._weiterreichen(nummer, resModel, resId);
        }
        const ziel = waehlbar(nummer);
        if (!ziel) {
            this.state.fehler = _t("Keine wählbare Nummer.");
            return false;
        }
        if (this.state.status !== "verbunden" || !this._agent) {
            this.state.fehler = _t("Telefon ist nicht verbunden.");
            return false;
        }
        if (this._leitungen().length || this._verbindet) {
            this.state.fehler = _t("Es läuft bereits ein Anruf.");
            return false;
        }
        let sitzung;
        try {
            sitzung = this._agent.invite(ziel);
        } catch (fehler) {
            this.state.fehler = fehlertext(fehler);
            return false;
        }
        this.state.fehler = null;
        this._anrufBeginnen(sitzung, "ausgehend", String(nummer).trim(), resModel, resId);
        return true;
    }

    _eingehend(sitzung) {
        // Kennung kommt von außen (SIP-Header): kürzen und Steuerzeichen entfernen
        const kennung = anruferKennung(sitzung.remoteNummer);
        let platz = "aktiv";
        if (this._aktiv || this._gehalten || this._verbindet) {
            // Zweite Leitung nur neben einem laufenden Gespräch; sonst (oder als dritter
            // Anruf) 480 wie bisher, auf dem Server als verpasst gemeldet
            if (
                !this._laufend() ||
                this._gehalten ||
                this._klopfend ||
                this._rueckfrageLaeuft ||
                this._verbindet
            ) {
                try {
                    Promise.resolve(sitzung.reject({ statusCode: 480 })).catch(() => {});
                } catch {
                    // Anrufer hat schon aufgelegt
                }
                this._abgewiesenMelden(sitzung, kennung);
                return;
            }
            platz = "klopfend";
        }
        this._anrufBeginnen(sitzung, "eingehend", kennung, false, false, platz);
        if (platz === "klopfend") {
            // während eines Gesprächs nur ein kurzer Hinweiston, nicht der volle Klingelton
            this.klaenge.anklopfen(true);
        } else {
            this.klaenge.klingeln(true);
        }
    }

    /**
     * Meldet einen mit 480 abgewiesenen Anruf, damit er als verpasst zählt: der Server legt ihn
     * direkt als verpasst an (kein kurzes „klingelt“ in der Team-Übersicht). Best effort, ohne
     * Oberfläche und ohne Fehlermeldung. Je Call-ID nur einmal und höchstens
     * ``ABGEWIESEN_MAX_JE_MINUTE`` je Minute (eine Flut von INVITEs erzeugt sonst je Anruf einen
     * Server-Aufruf).
     */
    _abgewiesenMelden(sitzung, kennung) {
        // ohne Call-ID keine Doppelprüfung (sonst fielen alle zusammen); die Minutengrenze gilt
        const callId = String(sitzung.callId || "");
        if (callId && this._abgewiesenIds.has(callId)) {
            return;
        }
        const jetzt = this.uhr.jetzt();
        this._abgewiesenZeiten = this._abgewiesenZeiten.filter(
            (zeit) => jetzt - zeit < ABGEWIESEN_FENSTER_MS
        );
        if (this._abgewiesenZeiten.length >= ABGEWIESEN_MAX_JE_MINUTE) {
            return;
        }
        this._abgewiesenZeiten.push(jetzt);
        if (callId) {
            this._abgewiesenIds.add(callId);
        }
        if (this._abgewiesenIds.size > ABGEWIESEN_MERKEN) {
            this._abgewiesenIds.delete(this._abgewiesenIds.values().next().value);
        }
        Promise.resolve()
            .then(() =>
                this.orm.call("wb.voip.anruf", "wb_voip_abgewiesen_melden", [kennung, sitzung.callId])
            )
            .catch((fehler) => console.debug("wb_voip: abgewiesener Anruf nicht gemeldet", fehler));
    }

    /** Alle belegten Leitungen; die aktive zuletzt (beim Beenden wird so nichts zurückgeholt). */
    _leitungen() {
        return [this._klopfend, this._gehalten, this._aktiv].filter(Boolean);
    }

    /** Beendet alle Leitungen mit ``fn``, ohne die gehaltene zurückzuholen. */
    _alleBeenden(fn) {
        this._konferenzAufloesen();
        const verbinden = this._verbindet;
        this._verbindet = null;
        this.state.verbindet = false;
        for (const kontext of this._leitungen()) {
            fn(kontext);
        }
        verbinden?.beideBeendet?.(); // wartendes verbinden() gibt auf
    }

    _anrufBeginnen(sitzung, richtung, nummer, resModel, resId, platz = "aktiv") {
        const anruf = {
            id: false,
            richtung,
            nummer,
            zustand: richtung === "ausgehend" ? "waehlt" : "klingelt",
            partner: false,
            stumm: false,
            gehalten: false,
            notiz: "",
            beginn: this.uhr.jetzt(),
            annahme: false,
            rueckfrage: false, // true: per Rückfrage gewählt (kein Kundengespräch, kein Popup)
        };
        const kontext = { sitzung, anruf: null, beendet: false, angenommen: false, lebenTimer: null };
        if (platz === "klopfend") {
            this.state.klopfend = anruf;
            kontext.anruf = this.state.klopfend;
            this._klopfend = kontext;
        } else {
            this.state.anruf = anruf;
            kontext.anruf = this.state.anruf;
            this._aktiv = kontext;
        }
        kontext.idVersprechen = this.orm
            .call("wb.voip.anruf", "wb_voip_beginnen", [
                richtung,
                nummer,
                sitzung.callId,
                resModel || false,
                resId || false,
            ])
            .then((daten) => {
                kontext.anruf.id = daten.id;
                kontext.anruf.partner = daten.partner || false;
                this._aktivMelden(kontext);
                return daten.id;
            })
            .catch(async (fehler) => {
                if (this._leitungen().includes(kontext)) {
                    this.state.fehler = fehlertext(fehler);
                }
                if (istOdooFehler(fehler)) {
                    await this._lizenzNachpruefen();
                }
                return false;
            });
        sitzung.on("fortschritt", (info) => this._fortschritt(kontext, info || {}));
        sitzung.on("angenommen", () => this._angenommen(kontext));
        sitzung.on("beendet", (info) => this._beenden(kontext, info?.grund));
        return kontext;
    }

    _fortschritt(kontext, { statusCode, mitSdp }) {
        if (kontext.beendet || kontext.angenommen) {
            return;
        }
        if (mitSdp) {
            // Early Media: die Gegenstelle liefert den Ton selbst
            this.klaenge.freizeichen(false);
        } else if (statusCode === 180) {
            this.klaenge.freizeichen(true);
        }
        if (statusCode >= 180 && statusCode < 200) {
            kontext.anruf.zustand = "klingelt";
            this._aktivMelden(kontext);
        }
    }

    /**
     * Meldet ``anruf-aktiv`` einmal je Anruf, sobald er eine Server-ID hat und klingelt
     * oder läuft. Nur der Telefon-Tab führt Anrufe, andere Tabs melden nie.
     */
    _aktivMelden(kontext) {
        // ein wartender Anruf meldet sich erst, wenn er die aktive Leitung wird
        if (kontext.beendet || kontext.aktivGemeldet || !kontext.anruf.id || this._aktiv !== kontext) {
            return;
        }
        if (!["klingelt", "laeuft"].includes(kontext.anruf.zustand)) {
            return;
        }
        kontext.aktivGemeldet = true;
        this.bus.trigger("anruf-aktiv", { anrufId: kontext.anruf.id, eigenerTab: true });
    }

    async _angenommen(kontext) {
        if (kontext.beendet || kontext.angenommen) {
            return;
        }
        kontext.angenommen = true;
        this.klaenge.aus();
        kontext.anruf.zustand = "laeuft";
        kontext.anruf.annahme = this.uhr.jetzt();
        this._aktivMelden(kontext);
        if (kontext.konferenzNachAnnahme && this._aktiv === kontext && this._gehalten) {
            // Konferenz mit einem Kollegen (Team): mischen, sobald er abnimmt
            kontext.konferenzNachAnnahme = false;
            this.konferenz().then(
                (ok) => {
                    // läuft schon eine Konferenz, gibt es nichts zu melden
                    if (!ok && !kontext.beendet && !this._konferenz) {
                        // feste Meldung statt Fehlertext: „bitte erneut klicken“ passt hier nicht
                        const text = _t(
                            "Konferenz konnte nicht aufgebaut werden – bitte im Gespräch „Konferenz“ drücken."
                        );
                        this.state.fehler = text;
                        this._melden(text);
                    }
                },
                () => {}
            );
        }
        // Lebenszeichen je Leitung, auch für die gehaltene
        this._lebenszeichenStoppen(kontext);
        kontext.lebenTimer = this.uhr.setInterval(
            () => this._lebenszeichen(kontext),
            LEBENSZEICHEN_MS
        );
        const id = await kontext.idVersprechen;
        if (!id || kontext.beendet) {
            return;
        }
        try {
            await this.orm.call("wb.voip.anruf", "wb_voip_angenommen", [[id]]);
        } catch (fehler) {
            this.state.fehler = fehlertext(fehler);
        }
        // Buttons mit Zustand „Während des Gesprächs“ erst jetzt vom Server holen lassen
        if (!kontext.beendet) {
            this.bus.trigger("anruf-angenommen", { anrufId: id });
        }
        await this._lebenszeichen(kontext);
    }

    async _lebenszeichen(kontext) {
        if (kontext.beendet) {
            return;
        }
        const id = await kontext.idVersprechen;
        if (!id || kontext.beendet) {
            return;
        }
        try {
            await this.orm.call("wb.voip.anruf", "wb_voip_lebenszeichen", [[id]]);
        } catch {
            // nächster Takt versucht es erneut
        }
    }

    _lebenszeichenStoppen(kontext) {
        if (kontext.lebenTimer !== null) {
            this.uhr.clearInterval(kontext.lebenTimer);
            kontext.lebenTimer = null;
        }
    }

    async _beenden(kontext, grund) {
        if (kontext.beendet) {
            return;
        }
        // Während „Verbinden“ endet nur die betroffene Leitung; über die andere entscheidet
        // das Ergebnis des REFER (verbinden())
        const waehrendVerbinden = Boolean(this._verbindet);
        const konferenz = this._konferenz;
        const inKonferenz = Boolean(
            konferenz && (konferenz.aktiv === kontext || konferenz.gehalten === kontext)
        );
        kontext.beendet = true;
        this._lebenszeichenStoppen(kontext);
        if (inKonferenz) {
            // Review Focus 4: Mischung auflösen, die andere Leitung läuft allein weiter
            this._konferenzAufloesen();
        }
        if (this._aktiv === kontext) {
            this._aktiv = null;
            this.klaenge.aus();
            this.state.anruf = null;
            if (waehrendVerbinden) {
                // nichts zurückholen
            } else if (this._gehalten) {
                // Review Focus 3: Rückfrage endet (Kollege legt auf, besetzt, nicht erreicht);
                // aus der Konferenz ist die andere Leitung schon aus dem Halten geholt
                this._fortsetzen({
                    unhold:
                        !inKonferenz || (this._gehalten.anruf.gehalten && !konferenz.unholdLaeuft),
                });
            } else if (this._klopfend) {
                // der wartende Anruf klingelt jetzt als einziger weiter
                const wartend = this._klopfend;
                this._klopfend = null;
                this.state.klopfend = null;
                this._aktiv = wartend;
                this.state.anruf = wartend.anruf;
                // jetzt der einzige Anruf: voller Klingelton statt Anklopfton
                this.klaenge.anklopfen(false);
                this.klaenge.klingeln(true);
                this._aktivMelden(wartend);
            }
        } else if (this._gehalten === kontext) {
            this._gehalten = null;
            this.state.gehaltener = null;
        } else if (this._klopfend === kontext) {
            this._klopfend = null;
            this.state.klopfend = null;
            this.klaenge.anklopfen(false);
        }
        const verbinden = this._verbindet;
        if (verbinden && verbinden.aktiv.beendet && verbinden.gehalten.beendet) {
            // die Anlage hat beide Dialoge beendet: Übergabe gelungen, ohne auf NOTIFY zu warten
            verbinden.beideBeendet?.();
        }
        this._popupsFreigeben();
        // Protokoll-Popup für jedes Kundengespräch, nie für eine Rückfrage
        const kunde = !kontext.anruf.rueckfrage;
        const id = await kontext.idVersprechen;
        if (!id) {
            return;
        }
        const notiz = String(kontext.anruf.notiz || "").slice(0, MAX_NOTIZ) || false;
        // angenommen mitschicken: wb_voip_angenommen kann noch unterwegs oder verloren sein
        await this._beendetMelden(id, notiz, grund ? String(grund) : false, kontext.angenommen, kunde);
    }

    /** True, wenn keine Leitung mehr belegt ist und kein „Verbinden“ läuft. */
    _alleFrei() {
        return !this._leitungen().length && !this._verbindet;
    }

    /** Öffnet die wartenden Protokoll-Popups der Reihe nach, sobald keine Leitung mehr läuft. */
    _popupsFreigeben() {
        if (!this._alleFrei()) {
            return;
        }
        const warten = this._popupWarten;
        this._popupWarten = [];
        for (const eintrag of warten) {
            this.bus.trigger("protokoll-oeffnen", { ...eintrag, eigenerTab: true });
        }
    }

    /**
     * Macht die gehaltene Leitung wieder zur aktiven und nimmt sie aus dem Halten (mit
     * ``unhold: false`` nur zur aktiven, z. B. nach einer Konferenz).
     */
    _fortsetzen({ unhold = true } = {}) {
        const kontext = this._gehalten;
        this._gehalten = null;
        this.state.gehaltener = null;
        this._aktiv = kontext;
        this.state.anruf = kontext.anruf;
        if (kontext.anruf.id) {
            // Anrufer-Fenster zeigt wieder dieses Gespräch
            kontext.aktivGemeldet = true;
            this.bus.trigger("anruf-aktiv", { anrufId: kontext.anruf.id, eigenerTab: true });
        }
        if (!unhold) {
            kontext.anruf.gehalten = false;
            return;
        }
        let ergebnis;
        try {
            ergebnis = kontext.sitzung.unhold();
        } catch (fehler) {
            ergebnis = Promise.reject(fehler);
        }
        Promise.resolve(ergebnis).then(
            () => {
                if (!kontext.beendet) {
                    kontext.anruf.gehalten = false;
                }
            },
            (fehler) => {
                // bleibt gehalten; „Halten“ in der Anrufansicht versucht es erneut
                if (!kontext.beendet) {
                    this.state.fehler = fehlertext(fehler);
                }
            }
        );
    }

    /**
     * Meldet das Ende an den Server; bei Netz- oder Serverfehlern bis zu drei neue
     * Versuche, bei fachlichen Fehlern (UserError, AccessError …) keiner.
     */
    async _beendetMelden(id, notiz, grund, angenommen, kunde = true, versuch = 0) {
        try {
            const daten = await this.orm.call("wb.voip.anruf", "wb_voip_beendet", [
                [id],
                notiz,
                grund,
                Boolean(angenommen),
            ]);
            const protokollId = daten?.protokoll_id || false;
            let popup = false;
            if (kunde && Number.isInteger(protokollId)) {
                if (this._alleFrei()) {
                    // erst die früher beendeten Kundengespräche, dann dieses
                    this._popupsFreigeben();
                    popup = true;
                } else {
                    this._popupWarten.push({ anrufId: id, protokollId });
                }
            }
            this.bus.trigger("anruf-beendet", {
                anrufId: id,
                protokollId,
                eigenerTab: true,
                popup,
            });
        } catch (fehler) {
            if (!istOdooFehler(fehler) && versuch < BEENDET_WIEDERHOLUNG_MS.length) {
                this.uhr.setTimeout(
                    () => this._beendetMelden(id, notiz, grund, angenommen, kunde, versuch + 1),
                    BEENDET_WIEDERHOLUNG_MS[versuch]
                );
            } else {
                // danach schließt der Server-Cron den Anruf (keine Lebenszeichen mehr)
                this.state.fehler = fehlertext(fehler);
            }
        }
    }

    _laufend() {
        return this._aktiv && this._aktiv.angenommen && !this._aktiv.beendet ? this._aktiv : null;
    }

    async annehmen() {
        if (this._klopfend) {
            return this._klopfendAnnehmen(this._klopfend);
        }
        const kontext = this._aktiv;
        if (!kontext || kontext.angenommen || kontext.anruf.richtung !== "eingehend") {
            return;
        }
        this.klaenge.klingeln(false);
        try {
            await kontext.sitzung.accept();
        } catch (fehler) {
            this.state.fehler = fehlertext(fehler);
        }
    }

    /** Nimmt den wartenden Anruf an; das laufende Gespräch wird vorher gehalten. */
    async _klopfendAnnehmen(wartend) {
        this.klaenge.anklopfen(false);
        const laufend = this._laufend();
        if (laufend && !laufend.anruf.gehalten) {
            try {
                await laufend.sitzung.hold();
                laufend.anruf.gehalten = true;
            } catch (fehler) {
                if (this._aktiv !== wartend || wartend.beendet) {
                    this.state.fehler = fehlertext(fehler);
                    if (this._klopfend === wartend) {
                        this.klaenge.anklopfen(true);
                    }
                    return;
                }
                // das erste Gespräch ist während des Haltens beendet: direkt annehmen
            }
        }
        if (wartend.beendet) {
            // Anrufer hat inzwischen aufgelegt: Gespräch wieder aufnehmen
            if (laufend && this._aktiv === laufend) {
                await this.halten(false);
            }
            return;
        }
        if (this._klopfend === wartend) {
            this._klopfend = null;
            this.state.klopfend = null;
            if (laufend && !laufend.beendet && this._aktiv === laufend) {
                this._gehalten = laufend;
                this.state.gehaltener = laufend.anruf;
            }
            this._aktiv = wartend;
            this.state.anruf = wartend.anruf;
        }
        // sonst ist der wartende nach dem Ende des ersten schon die aktive Leitung
        try {
            await wartend.sitzung.accept();
        } catch (fehler) {
            this.state.fehler = fehlertext(fehler);
        }
    }

    async ablehnen() {
        const kontext = this._klopfend || this._aktiv;
        if (!kontext || kontext.angenommen || kontext.anruf.richtung !== "eingehend") {
            return;
        }
        try {
            await kontext.sitzung.reject({ statusCode: 603 });
        } catch {
            // Anruf ist ohnehin vorbei
        }
        await this._beenden(kontext, "603");
    }

    async auflegen() {
        if (this._konferenz) {
            // in der Konferenz legt „Auflegen“ beide Leitungen auf
            return this.konferenzBeenden();
        }
        const kontext = this._aktiv;
        if (!kontext || this._verbindet) {
            return;
        }
        if (!kontext.angenommen && kontext.anruf.richtung === "eingehend") {
            return this.ablehnen();
        }
        let grund = "bye";
        try {
            if (kontext.angenommen) {
                await kontext.sitzung.bye();
            } else {
                grund = "487";
                await kontext.sitzung.cancel();
            }
        } catch {
            // Sitzung war schon beendet
        }
        await this._beenden(kontext, grund);
    }

    stumm(an) {
        const konferenz = this._konferenz;
        if (konferenz) {
            konferenz.aktiv.anruf.stumm = Boolean(an);
            konferenz.gehalten.anruf.stumm = Boolean(an);
            if (konferenz.handle) {
                // nur das Mikrofon in der Mischung; die gemischte Spur bleibt an
                konferenz.handle.stumm(Boolean(an));
            } else {
                // Aufbau läuft noch: beide Leitungen direkt stumm schalten, damit das Mikrofon
                // währenddessen nicht gesendet wird (die Mischung liest den Stand am Ende)
                for (const kontext of [konferenz.aktiv, konferenz.gehalten]) {
                    if (!kontext.beendet) {
                        kontext.sitzung.mute(Boolean(an));
                    }
                }
            }
            return;
        }
        const kontext = this._laufend();
        if (!kontext) {
            return;
        }
        kontext.sitzung.mute(Boolean(an));
        kontext.anruf.stumm = Boolean(an);
    }

    async halten(an) {
        const kontext = this._laufend();
        if (!kontext || this._verbindet || this._konferenz) {
            return;
        }
        try {
            await (an ? kontext.sitzung.hold() : kontext.sitzung.unhold());
            kontext.anruf.gehalten = Boolean(an);
        } catch (fehler) {
            this.state.fehler = fehlertext(fehler);
        }
    }

    dtmf(ziffer) {
        const kontext = this._laufend();
        if (!kontext || this._konferenz || !DTMF.test(String(ziffer))) {
            return;
        }
        kontext.sitzung.dtmf(String(ziffer));
    }

    /**
     * Rückfrage: hält die aktive Leitung und wählt ``nummer`` als neue aktive Leitung.
     * Endet die Rückfrage von selbst, kommt die gehaltene Leitung automatisch zurück.
     */
    async rueckfrage(nummer) {
        const kontext = this._laufend();
        const ziel = waehlbar(nummer);
        if (!kontext) {
            this.state.fehler = _t("Rückfrage nur während eines Gesprächs.");
            return false;
        }
        if (this._gehalten || this._klopfend || this._rueckfrageLaeuft) {
            this.state.fehler = _t("Es sind bereits zwei Leitungen belegt.");
            return false;
        }
        if (!ziel) {
            this.state.fehler = _t("Keine wählbare Nummer.");
            return false;
        }
        if (this.state.status !== "verbunden" || !this._agent) {
            this.state.fehler = _t("Telefon ist nicht verbunden.");
            return false;
        }
        this._rueckfrageLaeuft = true;
        try {
            if (!kontext.anruf.gehalten) {
                await kontext.sitzung.hold();
                kontext.anruf.gehalten = true;
            }
        } catch (fehler) {
            this.state.fehler = fehlertext(fehler);
            return false;
        } finally {
            this._rueckfrageLaeuft = false;
        }
        if (kontext.beendet || this._aktiv !== kontext || !this._agent) {
            this.state.fehler = _t("Das Gespräch ist inzwischen beendet.");
            return false;
        }
        let sitzung;
        try {
            sitzung = this._agent.invite(ziel);
        } catch (fehler) {
            this.state.fehler = fehlertext(fehler);
            await this.halten(false);
            return false;
        }
        this.state.fehler = null;
        this._gehalten = kontext;
        this.state.gehaltener = kontext.anruf;
        this._aktiv = null;
        const neu = this._anrufBeginnen(sitzung, "ausgehend", String(nummer).trim(), false, false);
        neu.anruf.rueckfrage = true;
        return true;
    }

    /** Legt die aktive Leitung (Rückfrage) auf; die gehaltene kommt zurück. */
    async zurueck() {
        if (!this._gehalten || !this._aktiv || this._verbindet || this._konferenz) {
            return false;
        }
        await this.auflegen();
        return true;
    }

    /**
     * Verbindet die gehaltene mit der aktiven Leitung: REFER der gehaltenen mit
     * ``Replaces`` auf die aktive (RFC 3891). Erfolg: beide enden lokal wie beim Auflegen.
     * Scheitert es, kommt die gehaltene Leitung zurück, falls die aktive inzwischen fehlt.
     * Endet eine Leitung während des Wartens, endet nur diese.
     */
    async verbinden() {
        if (this._verbindet || this._konferenz) {
            return false; // läuft schon bzw. Konferenz (Knopf ist gesperrt)
        }
        const aktiv = this._laufend();
        const gehalten = this._gehalten;
        if (!aktiv || !gehalten || gehalten.beendet) {
            this.state.fehler = _t("Verbinden geht erst, wenn die Rückfrage angenommen ist.");
            return false;
        }
        const verbinden = { aktiv, gehalten, beideBeendet: null };
        // erfüllt, sobald die Anlage beide Dialoge beendet hat (BYE vor dem NOTIFY) oder der
        // Dienst abbricht; dann wird nicht länger auf das REFER gewartet
        const beideBeendet = new Promise((resolve) => (verbinden.beideBeendet = resolve));
        this._verbindet = verbinden;
        this.state.verbindet = true;
        let refer;
        try {
            refer = Promise.resolve(gehalten.sitzung.refer(aktiv.sitzung)).then(
                () => null,
                (f) => f || new Error(_t("Verbinden fehlgeschlagen."))
            );
        } catch (f) {
            refer = Promise.resolve(f);
        }
        const fehler = await Promise.race([refer, beideBeendet.then(() => null)]);
        if (this._verbindet !== verbinden) {
            return false; // inzwischen abgebrochen (stop, Lizenz, Verbindung weg)
        }
        this._verbindet = null;
        this.state.verbindet = false;
        if (fehler) {
            this.state.fehler = fehlertext(fehler);
            if (!gehalten.beendet && !this._aktiv && this._gehalten === gehalten) {
                this._fortsetzen();
            }
            this._popupsFreigeben();
            return false;
        }
        // gehaltene zuerst, damit nichts zurückgeholt wird
        for (const kontext of [gehalten, aktiv]) {
            if (kontext.beendet) {
                continue;
            }
            try {
                Promise.resolve(kontext.sitzung.bye()).catch(() => {});
            } catch {
                // Sitzung war schon beendet
            }
            this._beenden(kontext, "bye");
        }
        this._popupsFreigeben();
        return true;
    }

    /**
     * Dreier-Konferenz. Ohne ``nummer``: verbindet die aktive mit der gehaltenen Leitung
     * (beide angenommen); die gehaltene kommt aus dem Halten, der Ton wird im Browser
     * gemischt (konferenz.js). Mit ``nummer`` (Team): Rückfrage an ``nummer``, gemischt wird,
     * sobald der Kollege abnimmt; nimmt er nicht ab, kommt das Gespräch zurück.
     */
    async konferenz(nummer) {
        if (nummer !== undefined && nummer !== null) {
            const ok = await this.rueckfrage(nummer);
            if (ok && this._aktiv) {
                this._aktiv.konferenzNachAnnahme = true;
            }
            return ok;
        }
        if (this._konferenz || this._verbindet || this._rueckfrageLaeuft) {
            return false; // läuft schon (Knopf ist gesperrt)
        }
        const aktiv = this._laufend();
        const gehalten = this._gehalten;
        if (!aktiv || !gehalten || gehalten.beendet || !gehalten.angenommen || this._klopfend) {
            this.state.fehler = _t("Konferenz geht erst mit zwei angenommenen Leitungen.");
            return false;
        }
        if (this.state.status !== "verbunden" || !this._agent) {
            this.state.fehler = _t("Telefon ist nicht verbunden.");
            return false;
        }
        // Simulation (Modus „test“): Konferenz ohne Ton
        const simuliert = Boolean(aktiv.sitzung.simuliert && gehalten.sitzung.simuliert);
        const medienA = simuliert ? null : aktiv.sitzung.konferenzMedien?.();
        const medienB = simuliert ? null : gehalten.sitzung.konferenzMedien?.();
        if (!simuliert && (!medienA || !medienB)) {
            this.state.fehler = _t("Konferenz nicht möglich: Ton einer Leitung fehlt.");
            return false;
        }
        // AudioContext noch im Klick anlegen und starten (Autoplay-Regeln der Browser);
        // konferenzAufbauen prüft danach, dass er wirklich läuft
        let ctx = null;
        if (!simuliert) {
            try {
                ctx = this._audioKontext();
                Promise.resolve(ctx.resume?.()).catch(() => {});
            } catch (fehler) {
                Promise.resolve(ctx?.close?.()).catch(() => {});
                this.state.fehler = fehlertext(fehler);
                return false;
            }
        }
        const schliessen = () => Promise.resolve(ctx?.close?.()).catch(() => {});
        const konferenz = { aktiv, gehalten, handle: null, unholdLaeuft: false };
        this._konferenz = konferenz;
        this.state.fehler = null;
        try {
            // Die gehaltene Leitung kommt vor dem Mischen aus dem Halten. Bei der Team-Konferenz
            // (konferenz(nummer)) ist das der Kunde: Er wird kurz vor dem Mischen fortgesetzt und
            // hört bis dahin nur das eigene Mikrofon, den Kollegen erst mit der Mischung.
            if (gehalten.anruf.gehalten) {
                // endet die aktive Leitung währenddessen, holt _beenden nicht noch einmal zurück
                konferenz.unholdLaeuft = true;
                await gehalten.sitzung.unhold();
            }
            if (!gehalten.beendet) {
                gehalten.anruf.gehalten = false;
            }
        } catch (fehler) {
            if (this._konferenz === konferenz) {
                this._konferenz = null;
            }
            if (!gehalten.beendet) {
                if (this._aktiv === gehalten) {
                    // Die aktive Leitung endete während des unhold: _beenden hat diese schon zur
                    // aktiven gemacht (nicht gehalten), das Re-INVITE ist aber gescheitert
                    await this._unholdWiederholen(gehalten);
                } else {
                    this.state.fehler = fehlertext(fehler);
                }
            }
            schliessen();
            this._konferenzStummZurueck(konferenz);
            return false;
        } finally {
            konferenz.unholdLaeuft = false;
        }
        if (this._konferenz !== konferenz) {
            // eine Leitung ist inzwischen beendet
            schliessen();
            this._konferenzStummZurueck(konferenz);
            return false;
        }
        if (!simuliert) {
            // Mikrofon-Quelle ist der lokale Strom der aktiven Leitung. Die eigene Aufnahme der
            // zweiten Leitung bleibt offen (nicht gestoppt): Sie ist die Spur, die ihr Sender nach
            // dem Abbau wieder sendet. Gestoppt hätte sie nach der Konferenz kein Mikrofon mehr,
            // und ein neues getUserMedia mitten im Gespräch könnte nachfragen oder scheitern.
            let handle;
            try {
                handle = await konferenzAufbauen({
                    ctx,
                    mikrofon: medienA.mikrofon,
                    // beim Abbau schaltet die Mikrofon-Klasse der Leitung die Spur wieder ein
                    a: {
                        empfang: medienA.empfang,
                        sender: medienA.sender,
                        zurueck: () => this._mikrofonZurueck(aktiv),
                    },
                    b: {
                        empfang: medienB.empfang,
                        sender: medienB.sender,
                        zurueck: () => this._mikrofonZurueck(gehalten),
                    },
                    // aktueller Stand, auch wenn während des Aufbaus umgeschaltet
                    stumm: () => Boolean(aktiv.anruf.stumm),
                });
            } catch (fehler) {
                if (this._konferenz === konferenz) {
                    this._konferenz = null;
                    this.state.fehler = fehlertext(fehler);
                    this._konferenzZurueckHalten(gehalten);
                }
                this._konferenzStummZurueck(konferenz);
                return false;
            }
            if (this._konferenz !== konferenz) {
                // eine Leitung endete während des Aufbaus
                await konferenzAbbauen(handle, { stumm: Boolean(aktiv.anruf.stumm) });
                this._konferenzStummZurueck(konferenz);
                return false;
            }
            konferenz.handle = handle;
        }
        // Stumm gilt für beide Richtungen (ggf. während des Aufbaus geändert)
        konferenz.handle?.stumm(Boolean(aktiv.anruf.stumm));
        gehalten.anruf.stumm = Boolean(aktiv.anruf.stumm);
        this.state.konferenz = true;
        return true;
    }

    /** Zweiter Versuch, die Leitung aus dem Halten zu holen; scheitert er, gilt sie als gehalten. */
    async _unholdWiederholen(kontext) {
        try {
            await kontext.sitzung.unhold();
            if (!kontext.beendet) {
                kontext.anruf.gehalten = false;
            }
        } catch (fehler) {
            if (!kontext.beendet) {
                kontext.anruf.gehalten = true;
                this.state.fehler = fehlertext(fehler);
            }
        }
    }

    /**
     * Nach einem gescheiterten Aufbau: jede noch laufende Leitung sendet ihr Mikrofon wieder
     * genau so, wie ihr Stumm-Zustand es anzeigt (gehalten: nie).
     */
    _konferenzStummZurueck(konferenz) {
        for (const kontext of [konferenz.aktiv, konferenz.gehalten]) {
            this._mikrofonZurueck(kontext);
        }
    }

    /** Mikrofon einer laufenden Leitung nach ihrem Stumm-Zustand (über die Mikrofon-Klasse). */
    _mikrofonZurueck(kontext) {
        if (kontext.beendet) {
            return;
        }
        try {
            kontext.sitzung.mute(Boolean(kontext.anruf.stumm));
        } catch {
            // Sitzung ohne Ton
        }
    }

    /** Hinweis an den Benutzer (Benachrichtigung), z. B. wenn die Team-Konferenz scheitert. */
    _melden(text) {
        try {
            this._meldung?.(text);
        } catch {
            // ohne Benachrichtigungsdienst
        }
    }

    /** Aufbau gescheitert: die zweite Leitung wieder halten (best effort). */
    _konferenzZurueckHalten(kontext) {
        if (kontext.beendet || this._gehalten !== kontext) {
            return;
        }
        Promise.resolve()
            .then(() => kontext.sitzung.hold())
            .then(
                () => {
                    if (!kontext.beendet) {
                        kontext.anruf.gehalten = true;
                    }
                },
                () => {}
            );
    }

    /** Legt beide Leitungen der Konferenz auf; jedes Kundengespräch bekommt sein Popup. */
    async konferenzBeenden() {
        const konferenz = this._konferenz;
        if (!konferenz) {
            return false;
        }
        // gehaltene zuerst, damit die andere nicht kurz zur aktiven wird
        for (const kontext of [konferenz.gehalten, konferenz.aktiv]) {
            if (kontext.beendet) {
                continue;
            }
            try {
                Promise.resolve(kontext.sitzung.bye()).catch(() => {});
            } catch {
                // Sitzung war schon beendet
            }
            this._beenden(kontext, "bye");
        }
        return true;
    }

    /** Löst die Mischung auf (ohne Leitungen zu beenden); jede sendet wieder ihr Mikrofon. */
    _konferenzAufloesen() {
        const konferenz = this._konferenz;
        if (!konferenz) {
            return;
        }
        this._konferenz = null;
        this.state.konferenz = false;
        if (konferenz.handle) {
            const stumm = Boolean(konferenz.aktiv.anruf.stumm);
            // Ein Klick auf Stumm während des Abbaus trifft noch die gemischte Spur; danach
            // gilt der aktuelle Stand wieder für das Mikrofon jeder laufenden Leitung
            konferenzAbbauen(konferenz.handle, { stumm })
                .catch(() => {})
                .then(() => this._konferenzStummZurueck(konferenz));
        }
    }

    /** Übergibt die aktive Leitung direkt (ohne Rückfrage) per REFER an ``nummer``. */
    uebergeben(nummer) {
        return this.weiterleiten(nummer);
    }

    async weiterleiten(nummer) {
        const kontext = this._laufend();
        const ziel = waehlbar(nummer);
        if (!kontext || !ziel || this._verbindet || this._konferenz) {
            return false;
        }
        try {
            await kontext.sitzung.refer(ziel);
            return true;
        } catch (fehler) {
            this.state.fehler = fehlertext(fehler);
            return false;
        }
    }
}

function neueTabId() {
    if (window.crypto?.randomUUID) {
        return window.crypto.randomUUID();
    }
    return `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export const telefonDienst = {
    dependencies: ["orm", "notification"],
    start(env, { orm, notification }) {
        const unterstuetzt = Boolean(navigator.locks && window.BroadcastChannel);
        const dienst = new TelefonDienst({
            orm,
            sperren: navigator.locks,
            kanalFabrik: unterstuetzt ? () => new BroadcastChannel(KANAL) : null,
            uhr: browserUhr,
            tabId: neueTabId(),
            agentFabrik: erzeugeAgentFabrik({
                bibliothekLaden: () => loadBundle("wb_voip.sip_lib", { css: false }),
            }),
            klaenge: new Klaenge(),
            fenster: window,
            meldung: (text) => notification.add(text, { type: "danger" }),
        });
        // Nur für eingerichtete VoIP-Benutzer (ir.http.session_info: wb_voip_aktiv)
        if (session.wb_voip_aktiv) {
            if (unterstuetzt) {
                dienst.start();
            } else {
                dienst.state.fehler = _t("Dieser Browser unterstützt das Softphone nicht.");
            }
        }
        return dienst;
    },
};

registry.category("services").add("wb_voip_telefon", telefonDienst);
