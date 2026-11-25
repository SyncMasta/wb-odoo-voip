import { EventBus, reactive } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { session } from "@web/session";

import { Klaenge } from "./klaenge";
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
    }) {
        this.orm = orm;
        this.sperren = sperren;
        this._kanalFabrik = kanalFabrik;
        this.kanal = kanal ?? kanalFabrik?.() ?? null;
        this.uhr = uhr;
        this.tabId = tabId;
        this.agentFabrik = agentFabrik;
        this.klaenge = klaenge;
        this.bus = new EventBus();
        /**
         * status: aus | verbinde | verbunden | getrennt | anderer_tab | ohne_lizenz
         *         | nicht_konfiguriert
         */
        /**
         * anruf: aktive Leitung; gehaltener: zweite, gehaltene Leitung (Rückfrage oder
         * angenommener zweiter Anruf); klopfend: zweiter eingehender Anruf, der noch klingelt.
         * Höchstens zwei Leitungen zugleich.
         */
        this.state = reactive({
            status: "aus",
            anruf: null,
            gehaltener: null,
            klopfend: null,
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
        this._verbindet = null; // {aktiv, gehalten} während „Verbinden“
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

    /** Meldet „angemeldet“ und wiederholt es alle 60 s, solange dieser Tab registriert ist. */
    _teamStarten() {
        this._teamStoppenTimer();
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
        if (this._leitungen().length) {
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
        let platz = "aktiv";
        if (this._aktiv) {
            // Zweite Leitung nur neben einem laufenden Gespräch; sonst (oder als dritter
            // Anruf) 480 wie bisher, der Anruf zählt als verpasst
            if (!this._laufend() || this._gehalten || this._klopfend || this._rueckfrageLaeuft) {
                sitzung.reject({ statusCode: 480 });
                return;
            }
            platz = "klopfend";
        }
        // Kennung kommt von außen (SIP-Header): kürzen und Steuerzeichen entfernen
        const kennung = anruferKennung(sitzung.remoteNummer);
        this._anrufBeginnen(sitzung, "eingehend", kennung, false, false, platz);
        this.klaenge.klingeln(true);
    }

    /** Alle belegten Leitungen; die aktive zuletzt (beim Beenden wird so nichts zurückgeholt). */
    _leitungen() {
        return [this._klopfend, this._gehalten, this._aktiv].filter(Boolean);
    }

    /** Beendet alle Leitungen mit ``fn``, ohne die gehaltene zurückzuholen. */
    _alleBeenden(fn) {
        this._verbindet = null;
        for (const kontext of this._leitungen()) {
            fn(kontext);
        }
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
        const verbinden = this._verbindet;
        if (verbinden && (kontext === verbinden.aktiv || kontext === verbinden.gehalten)) {
            // Während „Verbinden“ legt die Anlage eine Leitung auf: Übergabe läuft, beide enden
            this._verbindet = null;
            this._verbindenAbschliessen(verbinden);
            return;
        }
        kontext.beendet = true;
        this._lebenszeichenStoppen(kontext);
        if (this._aktiv === kontext) {
            this._aktiv = null;
            this.klaenge.aus();
            this.state.anruf = null;
            if (this._gehalten) {
                // Review Focus 3: Rückfrage endet (Kollege legt auf, besetzt, nicht erreicht)
                this._fortsetzen();
            } else if (this._klopfend) {
                // der wartende Anruf klingelt jetzt als einziger weiter
                const wartend = this._klopfend;
                this._klopfend = null;
                this.state.klopfend = null;
                this._aktiv = wartend;
                this.state.anruf = wartend.anruf;
                this.klaenge.klingeln(true);
                this._aktivMelden(wartend);
            }
        } else if (this._gehalten === kontext) {
            this._gehalten = null;
            this.state.gehaltener = null;
        } else if (this._klopfend === kontext) {
            this._klopfend = null;
            this.state.klopfend = null;
            this.klaenge.klingeln(false);
        }
        // Protokoll-Popup nur, wenn keine andere Leitung weiterläuft (keine gestapelten Popups)
        const popup = !this._aktiv && !this._gehalten;
        const id = await kontext.idVersprechen;
        if (!id) {
            return;
        }
        const notiz = String(kontext.anruf.notiz || "").slice(0, MAX_NOTIZ) || false;
        // angenommen mitschicken: wb_voip_angenommen kann noch unterwegs oder verloren sein
        await this._beendetMelden(id, notiz, grund ? String(grund) : false, kontext.angenommen, popup);
    }

    /** Macht die gehaltene Leitung wieder zur aktiven und nimmt sie aus dem Halten. */
    _fortsetzen() {
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
    async _beendetMelden(id, notiz, grund, angenommen, popup = true, versuch = 0) {
        try {
            const daten = await this.orm.call("wb.voip.anruf", "wb_voip_beendet", [
                [id],
                notiz,
                grund,
                Boolean(angenommen),
            ]);
            this.bus.trigger("anruf-beendet", {
                anrufId: id,
                protokollId: daten?.protokoll_id || false,
                eigenerTab: true,
                popup,
            });
        } catch (fehler) {
            if (!istOdooFehler(fehler) && versuch < BEENDET_WIEDERHOLUNG_MS.length) {
                this.uhr.setTimeout(
                    () => this._beendetMelden(id, notiz, grund, angenommen, popup, versuch + 1),
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
        this.klaenge.klingeln(false);
        const laufend = this._laufend();
        if (laufend && !laufend.anruf.gehalten) {
            try {
                await laufend.sitzung.hold();
                laufend.anruf.gehalten = true;
            } catch (fehler) {
                this.state.fehler = fehlertext(fehler);
                if (this._klopfend === wartend) {
                    this.klaenge.klingeln(true);
                }
                return;
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
        const kontext = this._aktiv;
        if (!kontext) {
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
        const kontext = this._laufend();
        if (!kontext) {
            return;
        }
        kontext.sitzung.mute(Boolean(an));
        kontext.anruf.stumm = Boolean(an);
    }

    async halten(an) {
        const kontext = this._laufend();
        if (!kontext) {
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
        if (!kontext || !DTMF.test(String(ziffer))) {
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
        this._anrufBeginnen(sitzung, "ausgehend", String(nummer).trim(), false, false);
        return true;
    }

    /** Legt die aktive Leitung (Rückfrage) auf; die gehaltene kommt zurück. */
    async zurueck() {
        if (!this._gehalten || !this._aktiv) {
            return false;
        }
        await this.auflegen();
        return true;
    }

    /**
     * Verbindet die gehaltene mit der aktiven Leitung: REFER der gehaltenen mit
     * ``Replaces`` auf die aktive (RFC 3891). Danach enden beide lokal wie beim Auflegen.
     */
    async verbinden() {
        const aktiv = this._laufend();
        const gehalten = this._gehalten;
        if (!aktiv || !gehalten || gehalten.beendet || this._verbindet) {
            this.state.fehler = _t("Verbinden geht erst, wenn die Rückfrage angenommen ist.");
            return false;
        }
        const verbinden = { aktiv, gehalten };
        this._verbindet = verbinden;
        try {
            await gehalten.sitzung.refer(aktiv.sitzung);
        } catch (fehler) {
            if (this._verbindet !== verbinden) {
                return true; // die Anlage hat beide Leitungen schon beendet
            }
            this._verbindet = null;
            this.state.fehler = fehlertext(fehler);
            return false;
        }
        if (this._verbindet === verbinden) {
            this._verbindet = null;
            this._verbindenAbschliessen(verbinden);
        }
        return true;
    }

    /** Beendet nach dem Verbinden beide Leitungen; die gehaltene zuerst (nichts zurückholen). */
    _verbindenAbschliessen({ aktiv, gehalten }) {
        for (const kontext of [gehalten, aktiv]) {
            if (kontext.beendet) {
                continue;
            }
            try {
                Promise.resolve(kontext.sitzung.bye()).catch(() => {});
            } catch {
                // Sitzung war schon beendet
            }
        }
        this._beenden(gehalten, "bye");
        this._beenden(aktiv, "bye");
    }

    /** Übergibt die aktive Leitung direkt (ohne Rückfrage) per REFER an ``nummer``. */
    uebergeben(nummer) {
        return this.weiterleiten(nummer);
    }

    async weiterleiten(nummer) {
        const kontext = this._laufend();
        const ziel = waehlbar(nummer);
        if (!kontext || !ziel) {
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
    dependencies: ["orm"],
    start(env, { orm }) {
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
