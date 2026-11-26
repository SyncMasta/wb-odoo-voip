// SIP-Agenten für den Telefon-Dienst. Der Dienst kennt nur diese kleine Schnittstelle:
//
//   Agent:   register(), unregister(), stop(), invite(ziel) -> Sitzung
//            Ereignisse: "invitation" (Sitzung), "getrennt" (Text)
//   Sitzung: callId, remoteNummer, accept(), reject({statusCode}), cancel(), bye(),
//            hold(), unhold(), mute(an), dtmf(ziffer), refer(ziel), konferenzMedien()
//            refer(ziel): ziel ist eine Nummer (blindes Übergeben, danach legt die Sitzung
//            selbst auf) oder eine andere Sitzung (Verbinden: Refer-To mit Replaces,
//            RFC 3891; Auflegen übernimmt der Dienst)
//            konferenzMedien(): {sender, empfang, mikrofon} für die Mischung (konferenz.js)
//            oder null; die Simulation hat ``simuliert = true`` und mischt nichts
//            Ereignisse: "fortschritt" ({statusCode, mitSdp}), "angenommen",
//            "beendet" ({grund})
//
// Umsetzungen: SipJsAgent (SIP.js 0.21.2, Modus „prod“), TestAgent (simuliert, Modus „test“),
// in den Tests FakeAgent (static/tests/sip_fake.js).

import { _t } from "@web/core/l10n/translation";

export class Sender {
    constructor() {
        this._wbHandler = {};
    }
    on(name, rueckruf) {
        (this._wbHandler[name] ||= []).push(rueckruf);
        return () => {
            this._wbHandler[name] = this._wbHandler[name].filter((r) => r !== rueckruf);
        };
    }
    emit(name, ...args) {
        for (const rueckruf of [...(this._wbHandler[name] || [])]) {
            rueckruf(...args);
        }
    }
}

const REGISTER_TIMEOUT_MS = 30_000;
/**
 * Wartezeit auf das abschließende NOTIFY nach 202 beim Verbinden. Danach gilt es als
 * gescheitert, außer die Anlage hat die gehaltene Leitung schon beendet (BYE).
 */
export const VERBINDEN_NOTIFY_MS = 15_000;

/** Uhr ohne Test-Ersatz (für Agenten, denen keine Uhr übergeben wurde). */
const STANDARD_UHR = {
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (id) => clearTimeout(id),
};
const REGISTER_EXPIRES_S = 300;

function zufall() {
    return Math.random().toString(36).slice(2, 12);
}

/** RTCIceServer-Liste aus den URLs der Anlage (leer = kein STUN/TURN). */
export function iceServer(urls) {
    const liste = Array.isArray(urls) ? urls.filter((url) => typeof url === "string" && /^stun:/i.test(url)) : [];
    return liste.length ? [{ urls: liste }] : [];
}

// --- SIP.js ---------------------------------------------------------------------------

/** Zerlegt einen Header-Wert an Kommas außerhalb von Anführungszeichen (mehrere Reason-Einträge). */
function headerEintraege(wert) {
    const eintraege = [];
    let aktuell = "";
    let inText = false;
    for (let i = 0; i < wert.length; i++) {
        const zeichen = wert[i];
        if (zeichen === "\\" && inText && i + 1 < wert.length) {
            aktuell += zeichen + wert[++i];
            continue;
        }
        if (zeichen === '"') {
            inText = !inText;
        } else if (zeichen === "," && !inText) {
            eintraege.push(aktuell);
            aktuell = "";
            continue;
        }
        aktuell += zeichen;
    }
    eintraege.push(aktuell);
    return eintraege.map((e) => e.trim()).filter(Boolean);
}

/** True für einen Reason-Eintrag des Protokolls SIP mit cause=200 oder „Call completed elsewhere“. */
function anderswoEintrag(eintrag) {
    const teile = eintrag.split(";");
    if (teile[0].trim().toUpperCase() !== "SIP") {
        return false;
    }
    return teile.slice(1).some((teil) => {
        const [name, ...rest] = teil.split("=");
        const wert = rest.join("=").trim();
        const schluessel = name.trim().toLowerCase();
        if (schluessel === "cause") {
            return wert === "200";
        }
        return schluessel === "text" && /call completed elsewhere/i.test(wert);
    });
}

/**
 * Grund eines CANCEL vor der Annahme: „anderswo“, wenn ein anderes Gerät angenommen hat
 * (Reason-Header nach RFC 3326: ein Eintrag des Protokolls SIP mit cause=200 bzw. Text
 * „Call completed elsewhere“), sonst „487“. Alle Reason-Header und alle Einträge je Header
 * zählen; Einträge anderer Protokolle (z. B. Q.850) werden ignoriert.
 */
export function cancelGrund(nachricht) {
    let werte = [];
    try {
        if (typeof nachricht?.getHeaders === "function") {
            werte = nachricht.getHeaders("Reason") || [];
        } else if (typeof nachricht?.getHeader === "function") {
            werte = [nachricht.getHeader("Reason")];
        }
    } catch {
        werte = [];
    }
    const eintraege = (Array.isArray(werte) ? werte : [werte])
        .filter((w) => w !== undefined && w !== null)
        .flatMap((w) => headerEintraege(String(w)));
    return eintraege.some(anderswoEintrag) ? "anderswo" : "487";
}

/**
 * Mikrofon einer Leitung: gesendet wird nur, wenn sie weder stumm noch gehalten ist. Alle
 * Spuren werden nur hier gesetzt (``enabled = !stumm && !gehalten``).
 *
 * Halten ist bei SIP.js 0.21.2 nur ein Re-INVITE mit ``sendonly``: Der Browser schickt das
 * Mikrofon weiter, die Anlage spielt es dem gehaltenen Anrufer vor. Deshalb geht das Mikrofon
 * vor dem Halte-Re-INVITE aus und erst nach angenommenem Fortsetzen wieder an; scheitert das
 * Re-INVITE, gilt der Zustand davor.
 */
export class Mikrofon {
    /** @param {() => Iterable<MediaStreamTrack>} spuren gerade gesendete Spuren */
    constructor(spuren) {
        this._spuren = spuren;
        this.stumm = false;
        this.gehalten = false;
    }
    setzen() {
        const an = !this.stumm && !this.gehalten;
        for (const spur of this._spuren() || []) {
            if (spur) {
                spur.enabled = an;
            }
        }
    }
    /** Stumm an/aus; eine gehaltene Leitung bleibt trotzdem aus. */
    stummSchalten(an) {
        this.stumm = Boolean(an);
        this.setzen();
    }
    /** Halten/Fortsetzen über ``reInvite()`` (Promise); bei Ablehnung der Zustand davor. */
    async halten(an, reInvite) {
        const vorher = this.gehalten;
        if (an) {
            this.gehalten = true;
            this.setzen();
        }
        try {
            await reInvite();
        } catch (fehler) {
            this.gehalten = vorher;
            this.setzen();
            throw fehler;
        }
        this.gehalten = Boolean(an);
        this.setzen();
    }
}

export class SipJsSitzung extends Sender {
    constructor(agent, session, { ausgehend }) {
        super();
        this._agent = agent;
        this._session = session;
        this._ausgehend = ausgehend;
        this._mikrofon = new Mikrofon(() =>
            (this._sdh?.peerConnection?.getSenders?.() || []).map((sender) => sender.track)
        );
        this._grund = "";
        this._angenommen = false;
        this._vorbei = false; // SIP-Dialog beendet (z. B. BYE der Anlage)
        this._audio = null;
        this.callId = session.request?.callId || `wb-voip-${zufall()}`;
        this.remoteNummer = session.remoteIdentity?.uri?.user || "";
        const { SessionState } = agent.SIP;
        session.stateChange.addListener((zustand) => {
            if (zustand === SessionState.Established) {
                this._angenommen = true;
                this._audioAnhaengen();
                this.emit("angenommen");
            } else if (zustand === SessionState.Terminated) {
                this._vorbei = true;
                this._audioLoesen();
                // Grund aus onReject kann im selben Takt noch kommen
                Promise.resolve().then(() =>
                    this.emit("beendet", { grund: this._grund || (this._angenommen ? "bye" : "") })
                );
            }
        });
        if (!ausgehend) {
            // Anrufer legt vor der Annahme auf (CANCEL)
            session.delegate = {
                ...(session.delegate || {}),
                onCancel: (nachricht) => {
                    this._grund = cancelGrund(nachricht);
                },
            };
        }
    }
    get _sdh() {
        return this._session.sessionDescriptionHandler;
    }
    _fortschritt(antwort) {
        const nachricht = antwort?.message || {};
        const mitSdp = Boolean(nachricht.body);
        if (mitSdp) {
            // Early Media: Ton der Gegenstelle hörbar machen
            this._audioAnhaengen();
        }
        this.emit("fortschritt", { statusCode: nachricht.statusCode, mitSdp });
    }
    _audioAnhaengen() {
        const strom = this._sdh?.remoteMediaStream;
        if (!strom) {
            return;
        }
        if (!this._audio) {
            this._audio = new Audio();
            this._audio.autoplay = true;
        }
        if (this._audio.srcObject !== strom) {
            this._audio.srcObject = strom;
        }
        this._audio.play().catch(() => {});
    }
    _audioLoesen() {
        if (this._audio) {
            this._audio.pause();
            this._audio.srcObject = null;
            this._audio = null;
        }
    }
    accept() {
        return this._session.accept({
            sessionDescriptionHandlerOptions: { constraints: { audio: true, video: false } },
        });
    }
    reject({ statusCode = 603 } = {}) {
        this._grund = String(statusCode);
        return this._session.reject({ statusCode });
    }
    cancel() {
        this._grund = "487";
        return this._session.cancel();
    }
    bye() {
        this._grund = "bye";
        return this._session.bye();
    }
    _halten(an) {
        return this._mikrofon.halten(an, () => {
            const optionen = this._session.sessionDescriptionHandlerOptionsReInvite || {};
            this._session.sessionDescriptionHandlerOptionsReInvite = { ...optionen, hold: an };
            return new Promise((resolve, reject) => {
                this._session
                    .invite({
                        requestDelegate: {
                            onAccept: () => resolve(),
                            onReject: (antwort) =>
                                reject(
                                    new Error(`Re-INVITE abgelehnt (${antwort.message.statusCode})`)
                                ),
                        },
                    })
                    .catch(reject);
            });
        });
    }
    hold() {
        return this._halten(true);
    }
    unhold() {
        return this._halten(false);
    }
    /** Stumm an/aus; schaltet das Mikrofon einer gehaltenen Leitung nie ein. */
    mute(an) {
        this._mikrofon.stummSchalten(an);
    }
    /**
     * Audio of this session for the browser conference: the audio RTCRtpSender, the remote
     * stream and the local microphone stream; null if the session has no media (yet).
     */
    konferenzMedien() {
        const sdh = this._sdh;
        const sender = (sdh?.peerConnection?.getSenders?.() || []).find(
            (s) => s.track?.kind === "audio"
        );
        const empfang = sdh?.remoteMediaStream;
        const mikrofon = sdh?.localMediaStream;
        if (!sender || !empfang || !mikrofon) {
            return null;
        }
        return { sender, empfang, mikrofon };
    }
    dtmf(ziffer) {
        // RFC 4733 über RTP; sonst SIP INFO
        if (this._sdh?.sendDtmf?.(ziffer)) {
            return;
        }
        this._session
            .info({
                requestOptions: {
                    body: {
                        contentDisposition: "render",
                        contentType: "application/dtmf-relay",
                        content: `Signal=${ziffer}\r\nDuration=160`,
                    },
                },
            })
            .catch(() => {});
    }
    refer(ziel) {
        if (ziel instanceof SipJsSitzung) {
            return this._verbinden(ziel);
        }
        const uri = this._agent._uri(ziel);
        return new Promise((resolve, reject) => {
            this._session
                .refer(uri, {
                    requestDelegate: {
                        onAccept: () => {
                            resolve();
                            // Blindes Weiterleiten: nach 202 selbst auflegen
                            this.bye().catch(() => {});
                        },
                        onReject: (antwort) =>
                            reject(new Error(`REFER abgelehnt (${antwort.message.statusCode})`)),
                    },
                })
                .catch(reject);
        });
    }
    /**
     * Verbinden (Übergeben mit Rückfrage): REFER mit der anderen Sitzung als Ziel, SIP.js baut
     * Refer-To mit Replaces. Erfüllt, sobald das NOTIFY ein 2xx meldet; nach 202 und
     * ``VERBINDEN_NOTIFY_MS`` ohne Abschluss nur, wenn die Anlage diese Leitung schon beendet
     * hat. Abgelehnt bei REFER- oder NOTIFY-Fehler und sonst nach Ablauf der Frist.
     */
    _verbinden(andere) {
        const uhr = this._agent._uhr || STANDARD_UHR;
        return new Promise((resolve, reject) => {
            let fertig = false;
            let timer = null;
            const ende = (fn, wert) => {
                if (fertig) {
                    return;
                }
                fertig = true;
                if (timer !== null) {
                    uhr.clearTimeout(timer);
                }
                fn(wert);
            };
            this._session
                .refer(andere._session, {
                    requestDelegate: {
                        onAccept: () => {
                            timer = uhr.setTimeout(() => {
                                timer = null;
                                if (this._vorbei) {
                                    ende(resolve); // Anlage hat die gehaltene Leitung übernommen
                                } else {
                                    ende(
                                        reject,
                                        new Error(_t("Connecting: no confirmation from the PBX."))
                                    );
                                }
                            }, VERBINDEN_NOTIFY_MS);
                        },
                        onReject: (antwort) =>
                            ende(
                                reject,
                                new Error(`REFER abgelehnt (${antwort.message.statusCode})`)
                            ),
                    },
                    onNotify: (meldung) => {
                        try {
                            Promise.resolve(meldung.accept?.()).catch(() => {});
                        } catch {
                            // NOTIFY schon beantwortet
                        }
                        const code = sipfragStatus(meldung.request?.body);
                        if (code >= 200 && code < 300) {
                            ende(resolve);
                        } else if (code >= 300) {
                            ende(reject, new Error(_t("Connecting failed (%s)", code)));
                        }
                    },
                })
                .catch((fehler) => ende(reject, fehler));
        });
    }
}

/** Statuscode aus einem message/sipfrag-Body („SIP/2.0 200 OK“), sonst 0. */
export function sipfragStatus(body) {
    const treffer = /^\s*SIP\/2\.0\s+(\d{3})/.exec(String(body ?? ""));
    return treffer ? Number(treffer[1]) : 0;
}

export class SipJsAgent extends Sender {
    /**
     * @param {object} zugang Ergebnis von res.users.wb_voip_zugang_lesen
     * @param {object} SIP    globales SIP.js-Objekt
     * @param {object} [optionen.uhr] Uhr für Fristen (Tests: FakeUhr)
     */
    constructor(zugang, SIP, { uhr = null } = {}) {
        super();
        this.SIP = SIP;
        this._uhr = uhr;
        this._domain = zugang.sip_domain;
        this._gestoppt = false;
        this._registerer = null;
        const { UserAgent } = SIP;
        this._ua = new UserAgent({
            uri: this._uri(zugang.sip_benutzer),
            transportOptions: { server: zugang.ws_url },
            // Nur ICE-Server der Anlage; ohne Eintrag keiner. SIP.js fragt sonst
            // stun.l.google.com (Daten an Dritte, DSGVO Art. 5/44).
            sessionDescriptionHandlerFactoryOptions: {
                peerConnectionConfiguration: { iceServers: iceServer(zugang.ice_server_urls) },
            },
            authorizationUsername: zugang.sip_benutzer,
            authorizationPassword: zugang.sip_passwort || "",
            displayName: zugang.sip_benutzer,
            userAgentString: "WB VoIP",
            logLevel: "error",
            delegate: {
                onInvite: (invitation) => this._eingehend(invitation),
                onDisconnect: (fehler) => {
                    if (!this._gestoppt) {
                        this.emit("getrennt", fehler?.message || _t("Connection to the PBX lost"));
                    }
                },
            },
        });
    }
    _uri(benutzer) {
        const uri = this.SIP.UserAgent.makeURI(`sip:${benutzer}@${this._domain}`);
        if (!uri) {
            throw new Error(_t("Invalid SIP address."));
        }
        return uri;
    }
    async register() {
        const { Registerer, RegistererState } = this.SIP;
        await this._ua.start();
        this._registerer = new Registerer(this._ua, { expires: REGISTER_EXPIRES_S });
        await new Promise((resolve, reject) => {
            const timer = setTimeout(
                () => reject(new Error(_t("The PBX does not respond to REGISTER."))),
                REGISTER_TIMEOUT_MS
            );
            const fertig = (fn) => (arg) => {
                clearTimeout(timer);
                fn(arg);
            };
            this._registerer
                .register({
                    requestDelegate: {
                        onAccept: fertig(() => resolve()),
                        onReject: fertig((antwort) =>
                            reject(
                                new Error(
                                    `REGISTER abgelehnt (${antwort.message.statusCode} ${antwort.message.reasonPhrase || ""})`.trim()
                                )
                            )
                        ),
                    },
                })
                .catch(fertig(reject));
        });
        // Registrierung geht später verloren (Auffrischung scheitert)
        this._registerer.stateChange.addListener((zustand) => {
            if (zustand === RegistererState.Unregistered && !this._gestoppt) {
                this.emit("getrennt", _t("Registration on the PBX lost"));
            }
        });
    }
    async unregister() {
        if (this._registerer) {
            await this._registerer.unregister().catch(() => {});
        }
    }
    async stop() {
        if (this._gestoppt) {
            return;
        }
        this._gestoppt = true;
        // unregister() schickt REGISTER mit Ablauf 0 noch im selben Takt (vor dem ersten
        // await), auch wenn die Seite gerade geschlossen wird
        await this.unregister();
        await this._ua.stop().catch(() => {});
    }
    invite(ziel) {
        const { Inviter } = this.SIP;
        const inviter = new Inviter(this._ua, this._uri(ziel), {
            earlyMedia: true,
            sessionDescriptionHandlerOptions: { constraints: { audio: true, video: false } },
        });
        const sitzung = new SipJsSitzung(this, inviter, { ausgehend: true });
        sitzung.remoteNummer = ziel;
        inviter
            .invite({
                requestDelegate: {
                    onProgress: (antwort) => sitzung._fortschritt(antwort),
                    onReject: (antwort) => {
                        sitzung._grund = String(antwort.message.statusCode);
                    },
                },
            })
            .catch(() => {
                sitzung._grund ||= "fehler";
            });
        return sitzung;
    }
    _eingehend(invitation) {
        this.emit("invitation", new SipJsSitzung(this, invitation, { ausgehend: false }));
    }
}

// --- Simulation (Modus „test“) ----------------------------------------------------------

const TEST_KLINGELN_MS = 1000;

class TestSitzung extends Sender {
    constructor(uhr, { remoteNummer }) {
        super();
        this._uhr = uhr;
        this.callId = `wb-voip-test-${zufall()}`;
        this.remoteNummer = remoteNummer;
        this._timer = [];
        this._vorbei = false;
        this.simuliert = true; // Konferenz ohne Ton (kein WebRTC)
        this._mikrofon = new Mikrofon(() => []); // ohne Spuren, nur der Zustand
    }
    _spaeter(ms, fn) {
        this._timer.push(this._uhr.setTimeout(() => !this._vorbei && fn(), ms));
    }
    _ende(grund) {
        if (this._vorbei) {
            return;
        }
        this._vorbei = true;
        this._timer.forEach((id) => this._uhr.clearTimeout(id));
        this.emit("beendet", { grund });
    }
    accept() {
        this._spaeter(0, () => this.emit("angenommen"));
        return Promise.resolve();
    }
    reject({ statusCode = 603 } = {}) {
        this._ende(String(statusCode));
        return Promise.resolve();
    }
    cancel() {
        this._ende("487");
        return Promise.resolve();
    }
    bye() {
        this._ende("bye");
        return Promise.resolve();
    }
    hold() {
        return this._mikrofon.halten(true, () => Promise.resolve());
    }
    unhold() {
        return this._mikrofon.halten(false, () => Promise.resolve());
    }
    mute(an) {
        this._mikrofon.stummSchalten(an);
    }
    dtmf() {}
    konferenzMedien() {
        return null;
    }
    refer(ziel) {
        // Verbinden: der Dienst legt beide Leitungen selbst auf
        if (!(ziel instanceof TestSitzung)) {
            this._spaeter(0, () => this._ende("bye"));
        }
        return Promise.resolve();
    }
}

/** Simulierter Agent: keine SIP-Verbindung; ein Anruf klingelt 1 s und wird angenommen. */
export class TestAgent extends Sender {
    constructor(zugang, { uhr }) {
        super();
        this._uhr = uhr;
    }
    register() {
        return Promise.resolve();
    }
    unregister() {
        return Promise.resolve();
    }
    stop() {
        return Promise.resolve();
    }
    invite(ziel) {
        const sitzung = new TestSitzung(this._uhr, { remoteNummer: ziel });
        sitzung._spaeter(0, () => sitzung.emit("fortschritt", { statusCode: 180, mitSdp: false }));
        sitzung._spaeter(TEST_KLINGELN_MS, () => sitzung.emit("angenommen"));
        return sitzung;
    }
    /** Eingehenden Anruf simulieren (Demo). */
    simuliereEingehend(nummer) {
        const sitzung = new TestSitzung(this._uhr, { remoteNummer: nummer });
        this.emit("invitation", sitzung);
        return sitzung;
    }
}

/**
 * Liefert die Agent-Fabrik des Dienstes: Modus „test“ simuliert, sonst wird das Bündel
 * ``wb_voip.sip_lib`` (SIP.js) erst jetzt nachgeladen.
 */
export function erzeugeAgentFabrik({ bibliothekLaden, sipBibliothek = () => window.SIP } = {}) {
    return async (zugang, { uhr }) => {
        if (zugang.modus === "test") {
            return new TestAgent(zugang, { uhr });
        }
        await bibliothekLaden();
        const SIP = sipBibliothek();
        if (!SIP?.UserAgent) {
            throw new Error(_t("The SIP library could not be loaded."));
        }
        if (!zugang.ws_url || !zugang.sip_domain || !zugang.sip_benutzer) {
            throw new Error(_t("SIP access incomplete (WebSocket, domain or user is missing)."));
        }
        return new SipJsAgent(zugang, SIP, { uhr });
    };
}
