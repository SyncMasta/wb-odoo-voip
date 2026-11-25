// Test-Doubles für den Telefon-Dienst: Uhr, Web-Sperren, BroadcastChannel, ORM, SIP-Agent, Klänge.
// Alle Fakes sind deterministisch; Zeit läuft nur über FakeUhr.vorspulen().

/** Leert die Microtask-Warteschlange (mehrere Runden für verkettete Promises). */
export async function leeren(runden = 30) {
    for (let i = 0; i < runden; i++) {
        await Promise.resolve();
    }
}

export class FakeUhr {
    constructor(start = 1_000_000) {
        this.t = start;
        this._naechsteId = 1;
        this._timer = new Map();
    }
    jetzt() {
        return this.t;
    }
    setTimeout(fn, ms = 0) {
        const id = this._naechsteId++;
        this._timer.set(id, { fn, faellig: this.t + ms, intervall: null });
        return id;
    }
    clearTimeout(id) {
        this._timer.delete(id);
    }
    setInterval(fn, ms) {
        const id = this._naechsteId++;
        this._timer.set(id, { fn, faellig: this.t + ms, intervall: ms });
        return id;
    }
    clearInterval(id) {
        this._timer.delete(id);
    }
    get anzahlTimer() {
        return this._timer.size;
    }
    /** Lässt die Zeit um ``ms`` laufen und führt fällige Timer in Reihenfolge aus. */
    async vorspulen(ms = 0) {
        const ziel = this.t + ms;
        await leeren();
        for (;;) {
            let naechster = null;
            for (const [id, timer] of this._timer) {
                if (timer.faellig <= ziel && (!naechster || timer.faellig < naechster[1].faellig)) {
                    naechster = [id, timer];
                }
            }
            if (!naechster) {
                break;
            }
            const [id, timer] = naechster;
            this.t = Math.max(this.t, timer.faellig);
            if (timer.intervall) {
                timer.faellig += timer.intervall;
            } else {
                this._timer.delete(id);
            }
            timer.fn();
            await leeren();
        }
        this.t = ziel;
        await leeren();
    }
}

/** Nachbau von navigator.locks (request mit ifAvailable, exklusiv, Warteschlange). */
export class FakeSperren {
    constructor() {
        this._gehalten = new Set();
        this._warten = new Map();
    }
    request(name, optionen, rueckruf) {
        if (typeof optionen === "function") {
            rueckruf = optionen;
            optionen = {};
        }
        if (this._gehalten.has(name)) {
            if (optionen.ifAvailable) {
                return Promise.resolve().then(() => rueckruf(null));
            }
            return new Promise((resolve, reject) => {
                const liste = this._warten.get(name) || [];
                liste.push({ rueckruf, resolve, reject });
                this._warten.set(name, liste);
            });
        }
        return this._gewaehren(name, rueckruf);
    }
    _gewaehren(name, rueckruf) {
        this._gehalten.add(name);
        return Promise.resolve()
            .then(() => rueckruf({ name, mode: "exclusive" }))
            .finally(() => this._freigeben(name));
    }
    _freigeben(name) {
        this._gehalten.delete(name);
        const liste = this._warten.get(name) || [];
        const naechster = liste.shift();
        if (naechster) {
            this._gewaehren(name, naechster.rueckruf).then(naechster.resolve, naechster.reject);
        }
    }
}

/** Mehrere BroadcastChannel-Enden; Nachrichten gehen asynchron an alle anderen Enden. */
export class FakeKanalNetz {
    constructor() {
        this.enden = [];
        this.verlauf = [];
    }
    kanal() {
        const netz = this;
        const ende = {
            onmessage: null,
            geschlossen: false,
            postMessage(nachricht) {
                if (ende.geschlossen) {
                    return;
                }
                const kopie = JSON.parse(JSON.stringify(nachricht));
                netz.verlauf.push(kopie);
                for (const anderes of netz.enden) {
                    if (anderes !== ende && !anderes.geschlossen) {
                        Promise.resolve().then(() => anderes.onmessage?.({ data: kopie }));
                    }
                }
            },
            close() {
                ende.geschlossen = true;
            },
        };
        this.enden.push(ende);
        return ende;
    }
}

export const ZUGANG_PROD = Object.freeze({
    anlage_id: 1,
    modus: "prod",
    sip_domain: "anlage.test",
    ws_url: "wss://anlage.test/ws",
    sip_benutzer: "100",
    sip_passwort: "geheim",
    land: "DE",
});

export function lizenzFehler() {
    return userError("Lizenz fehlt oder abgelaufen");
}

/** Fehler wie ein UserError aus einem RPC. */
export function userError(meldung) {
    const fehler = new Error(meldung);
    fehler.data = { name: "odoo.exceptions.UserError", message: meldung };
    return fehler;
}

/** ORM-Fake: protokolliert Aufrufe, Antworten je "modell.methode" überschreibbar. */
export class FakeOrm {
    constructor() {
        this.aufrufe = [];
        this._naechsteAnrufId = 10;
        this.antworten = {
            "res.users.wb_voip_status": () => ({
                aktiv: true,
                lizenz_gueltig: true,
                meldung: "",
                eingerichtet: true,
                konfiguriert: true,
            }),
            "res.users.wb_voip_zugang_lesen": () => ({ ...ZUGANG_PROD }),
            "wb.voip.anruf.wb_voip_beginnen": (args) => ({
                id: this._naechsteAnrufId++,
                richtung: args[0],
                nummer: args[1],
                zustand: "klingelt",
                partner: false,
                protokoll_id: false,
            }),
            "wb.voip.anruf.wb_voip_angenommen": (args) => ({ id: args[0][0], zustand: "laeuft" }),
            "wb.voip.anruf.wb_voip_beendet": (args) => ({
                id: args[0][0],
                zustand: "beendet",
                protokoll_id: 77,
            }),
            "wb.voip.anruf.wb_voip_lebenszeichen": () => true,
            "res.users.wb_voip_lebenszeichen_melden": () => true,
        };
    }
    async call(modell, methode, args = [], kwargs = {}) {
        this.aufrufe.push({ modell, methode, args, kwargs });
        const antwort = this.antworten[`${modell}.${methode}`];
        if (!antwort) {
            throw new Error(`Unerwarteter RPC ${modell}.${methode}`);
        }
        return antwort(args, kwargs);
    }
    zaehle(methode) {
        return this.aufrufe.filter((a) => a.methode === methode).length;
    }
    letzter(methode) {
        return this.aufrufe.filter((a) => a.methode === methode).at(-1);
    }
}

class Sender {
    constructor() {
        this._h = {};
    }
    on(name, rueckruf) {
        (this._h[name] ||= []).push(rueckruf);
    }
    emit(name, ...args) {
        for (const rueckruf of this._h[name] || []) {
            rueckruf(...args);
        }
    }
}

let sitzungsZaehler = 0;

// --- WebAudio/WebRTC für die Konferenz ----------------------------------------------------

let spurZaehler = 0;

/** MediaStreamTrack-Ersatz. */
export class FakeSpur {
    constructor(name = `spur-${++spurZaehler}`) {
        this.name = name;
        this.kind = "audio";
        this.enabled = true;
        this.gestoppt = false;
    }
    stop() {
        this.gestoppt = true;
    }
}

/** RTCRtpSender-Ersatz: merkt sich jede ersetzte Spur. */
export class FakeSender {
    constructor(track = new FakeSpur()) {
        this.track = track;
        this.ersetzungen = [];
        this.fehler = null;
    }
    replaceTrack(spur) {
        if (this.fehler) {
            return Promise.reject(this.fehler);
        }
        this.ersetzungen.push(spur);
        this.track = spur;
        return Promise.resolve();
    }
}

class FakeKnoten {
    constructor(art, extra = {}) {
        this.art = art;
        this.ziele = [];
        this.getrennt = false;
        Object.assign(this, extra);
    }
    connect(ziel) {
        this.ziele.push(ziel);
        return ziel;
    }
    disconnect() {
        this.ziele = [];
        this.getrennt = true;
    }
}

/** AudioContext-Ersatz mit nachverfolgbarem Graphen. */
export class FakeAudioContext {
    constructor() {
        this.knoten = [];
        this.geschlossen = false;
        this.state = "suspended"; // wie ein neuer Kontext vor resume()
        this.destination = new FakeKnoten("lautsprecher");
    }
    _neu(knoten) {
        this.knoten.push(knoten);
        return knoten;
    }
    createMediaStreamSource(strom) {
        return this._neu(new FakeKnoten("quelle", { strom }));
    }
    createGain() {
        return this._neu(new FakeKnoten("verstaerkung", { gain: { value: 1 } }));
    }
    createMediaStreamDestination() {
        const spur = new FakeSpur();
        return this._neu(
            new FakeKnoten("ziel", { spur, stream: { getAudioTracks: () => [spur] } })
        );
    }
    resume() {
        if (!this.geschlossen) {
            this.state = "running";
        }
        return Promise.resolve();
    }
    close() {
        this.geschlossen = true;
        this.state = "closed";
        return Promise.resolve();
    }
    /**
     * Ströme, die hörbar (Verstärkung > 0, nicht getrennt) bei ``ziel`` ankommen.
     */
    quellenVon(ziel) {
        const ergebnis = new Set();
        const besuchen = (knoten, faktor) => {
            for (const kandidat of this.knoten) {
                if (kandidat.ziele.includes(knoten)) {
                    const f = faktor * (kandidat.gain ? kandidat.gain.value : 1);
                    if (kandidat.art === "quelle") {
                        if (f > 0) {
                            ergebnis.add(kandidat.strom);
                        }
                    } else {
                        besuchen(kandidat, f);
                    }
                }
            }
        };
        besuchen(ziel, 1);
        return ergebnis;
    }
}

export class FakeSitzung extends Sender {
    constructor({ ziel = null, remoteNummer = null } = {}) {
        super();
        this.callId = `fake-call-${++sitzungsZaehler}`;
        this.ziel = ziel;
        this.remoteNummer = remoteNummer || ziel;
        this.aufrufe = [];
        const n = sitzungsZaehler;
        // Ton der Leitung (wie SipJsSitzung.konferenzMedien)
        this.medien = {
            mikrofon: { name: `mikrofon-${n}` },
            empfang: { name: `empfang-${n}` },
            sender: new FakeSender(new FakeSpur(`mik-spur-${n}`)),
        };
    }
    konferenzMedien() {
        return this.medien;
    }
    _merke(name, ...args) {
        this.aufrufe.push([name, ...args]);
    }
    hat(name) {
        return this.aufrufe.filter((a) => a[0] === name);
    }
    accept() {
        this._merke("accept");
        Promise.resolve().then(() => this.emit("angenommen"));
        return Promise.resolve();
    }
    reject(optionen) {
        this._merke("reject", optionen);
        return Promise.resolve();
    }
    cancel() {
        this._merke("cancel");
        return Promise.resolve();
    }
    bye() {
        this._merke("bye");
        Promise.resolve().then(() => this.emit("beendet", { grund: "bye" }));
        return Promise.resolve();
    }
    hold() {
        this._merke("hold");
        return Promise.resolve();
    }
    unhold() {
        this._merke("unhold");
        return Promise.resolve();
    }
    mute(an) {
        this._merke("mute", an);
        // wie SipJsSitzung: schaltet die gerade gesendete Spur
        if (this.medien?.sender?.track) {
            this.medien.sender.track.enabled = !an;
        }
    }
    dtmf(ziffer) {
        this._merke("dtmf", ziffer);
    }
    refer(ziel) {
        this._merke("refer", ziel);
        return Promise.resolve();
    }
    // Gegenstelle simulieren
    fortschritt(statusCode, mitSdp = false) {
        this.emit("fortschritt", { statusCode, mitSdp });
    }
    gegenstelleNimmtAn() {
        this.emit("angenommen");
    }
    gegenstelleLegtAuf(grund = "bye") {
        this.emit("beendet", { grund });
    }
}

export class FakeAgent extends Sender {
    constructor(fabrik, zugang) {
        super();
        this.fabrik = fabrik;
        this.zugang = zugang;
        this.invites = [];
        this.gestoppt = false;
        this.abmeldungen = 0;
    }
    register() {
        this.fabrik.registrierungen.push(this.fabrik.uhr ? this.fabrik.uhr.jetzt() : 0);
        if (this.fabrik.registerFehler) {
            return Promise.reject(new Error("REGISTER abgelehnt (403 Forbidden)"));
        }
        return Promise.resolve();
    }
    unregister() {
        this.abmeldungen++;
        return Promise.resolve();
    }
    /** Wie SipJsAgent: meldet sofort (synchron) ab, dann Ende. */
    stop() {
        if (!this.gestoppt) {
            this.gestoppt = true;
            this.unregister();
        }
        return Promise.resolve();
    }
    invite(ziel) {
        const sitzung = new FakeSitzung({ ziel });
        this.invites.push(sitzung);
        return sitzung;
    }
    /** Eingehenden Anruf simulieren. */
    eingehend(nummer) {
        const sitzung = new FakeSitzung({ remoteNummer: nummer });
        this.emit("invitation", sitzung);
        return sitzung;
    }
    netzWeg() {
        this.emit("getrennt", "Verbindung getrennt");
    }
}

/** Fabrik für FakeAgent; gemeinsam für alle Dienst-Instanzen eines Tests. */
export class FakeAgentFabrik {
    constructor(uhr = null) {
        this.uhr = uhr;
        this.agenten = [];
        this.registrierungen = [];
        this.registerFehler = false;
        this.fabrik = async (zugang) => {
            const agent = new FakeAgent(this, zugang);
            this.agenten.push(agent);
            return agent;
        };
    }
    get anzahlRegister() {
        return this.registrierungen.length;
    }
    get letzterAgent() {
        return this.agenten.at(-1);
    }
}

export class FakeKlaenge {
    constructor() {
        this.freizeichenAn = false;
        this.klingelnAn = false;
        this.verlauf = [];
    }
    freizeichen(an) {
        this.freizeichenAn = !!an;
        this.verlauf.push(["freizeichen", !!an]);
    }
    klingeln(an) {
        this.klingelnAn = !!an;
        this.verlauf.push(["klingeln", !!an]);
    }
    aus() {
        this.freizeichen(false);
        this.klingeln(false);
    }
}
