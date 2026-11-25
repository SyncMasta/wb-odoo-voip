import { afterEach, beforeEach, describe, expect, test } from "@odoo/hoot";
import { translatedTerms, translationLoaded } from "@web/core/l10n/translation";

import { SipJsSitzung, sipfragStatus } from "@wb_voip/dienste/sip_agent";

import { FakeUhr } from "./sip_fake";

describe.current.tags("desktop");

// Fehlermeldungen laufen über _t: dafür gelten die Übersetzungen im Test als geladen
let uebersetzungVorher;
beforeEach(() => {
    uebersetzungVorher = translatedTerms[translationLoaded];
    translatedTerms[translationLoaded] = true;
});
afterEach(() => {
    translatedTerms[translationLoaded] = uebersetzungVorher;
});

/** Minimale SIP.js-Sitzung: stateChange und Delegate wie in SIP.js 0.21.2. */
function fakeSitzung() {
    const hoerer = [];
    return {
        request: { callId: "abc" },
        remoteIdentity: { uri: { user: "+4917012345" } },
        stateChange: { addListener: (rueckruf) => hoerer.push(rueckruf) },
        delegate: {},
        beenden() {
            hoerer.forEach((rueckruf) => rueckruf("Terminated"));
        },
    };
}

const FAKE_AGENT = { SIP: { SessionState: { Established: "Established", Terminated: "Terminated" } } };

/** Legt eine eingehende Sitzung an, lässt den Anrufer abbrechen und liefert den Grund. */
async function grundNachCancel(nachricht) {
    const session = fakeSitzung();
    const sitzung = new SipJsSitzung(FAKE_AGENT, session, { ausgehend: false });
    const gemeldet = [];
    sitzung.on("beendet", (daten) => gemeldet.push(daten.grund));
    session.delegate.onCancel(nachricht);
    session.beenden();
    await Promise.resolve();
    await Promise.resolve();
    return gemeldet[0];
}

const mitReason = (wert) => ({ getHeader: (name) => (name === "Reason" ? wert : undefined) });

test("CANCEL mit Reason cause=200 ist „anderswo“", async () => {
    expect(await grundNachCancel(mitReason('SIP;cause=200;text="Call completed elsewhere"'))).toBe("anderswo");
});

test("CANCEL mit Text „Call completed elsewhere“ ohne cause ist „anderswo“", async () => {
    expect(await grundNachCancel(mitReason('SIP;text="call COMPLETED elsewhere"'))).toBe("anderswo");
});

test("CANCEL ohne Reason-Header ist 487", async () => {
    expect(await grundNachCancel(mitReason(undefined))).toBe("487");
});

test("CANCEL mit anderem Reason (cause=487) ist 487", async () => {
    expect(await grundNachCancel(mitReason('SIP;cause=487;text="Request Terminated"'))).toBe("487");
});

test("CANCEL ohne Nachricht oder ohne getHeader ist 487", async () => {
    expect(await grundNachCancel(undefined)).toBe("487");
    expect(await grundNachCancel({})).toBe("487");
});

const mitReasons = (werte) => ({ getHeaders: (name) => (name === "Reason" ? werte : []) });

test("Zwei Reason-Header: der SIP-Eintrag mit cause=200 zählt", async () => {
    expect(
        await grundNachCancel(mitReasons(["Q.850;cause=16", 'SIP;cause=200;text="Call completed elsewhere"']))
    ).toBe("anderswo");
});

test("Q.850;cause=200 allein ist 487 (nur Protokoll SIP zählt)", async () => {
    expect(await grundNachCancel(mitReasons(["Q.850;cause=200"]))).toBe("487");
    expect(await grundNachCancel(mitReasons(['Q.850;cause=16;text="Call completed elsewhere"']))).toBe("487");
});

test("Mehrere Einträge in einem Reason-Header, auch mit Komma im Text", async () => {
    expect(await grundNachCancel(mitReasons(['Q.850;cause=16;text="a, b", SIP;cause=200']))).toBe("anderswo");
    expect(await grundNachCancel(mitReasons(['SIP;cause=2000', 'SIP;cause=487']))).toBe("487");
});

test("Verbinden: REFER mit der anderen Sitzung, Abschluss über NOTIFY (sipfrag)", async () => {
    expect(sipfragStatus("SIP/2.0 200 OK\r\n")).toBe(200);
    expect(sipfragStatus("Unsinn")).toBe(0);
    const versuch = async (body) => {
        const gehalten = fakeSitzung();
        const andere = fakeSitzung();
        let referiert;
        gehalten.refer = (ziel, optionen) => {
            referiert = { ziel, optionen };
            return Promise.resolve();
        };
        const sitzung = new SipJsSitzung(FAKE_AGENT, gehalten, { ausgehend: false });
        const zweite = new SipJsSitzung(FAKE_AGENT, andere, { ausgehend: true });
        const ergebnis = sitzung.refer(zweite).then(
            () => "ok",
            (fehler) => fehler.message
        );
        await Promise.resolve();
        expect(referiert.ziel).toBe(andere);
        referiert.optionen.requestDelegate.onAccept();
        referiert.optionen.onNotify({ request: { body: "SIP/2.0 100 Trying" }, accept: () => Promise.resolve() });
        referiert.optionen.onNotify({ request: { body }, accept: () => Promise.resolve() });
        return ergebnis;
    };
    expect(await versuch("SIP/2.0 200 OK")).toBe("ok");
    expect(await versuch("SIP/2.0 503 Service Unavailable")).toMatch(/503/);
});

/** Verbinden mit steuerbarem REFER; liefert Ergebnis-Versprechen und Hilfen. */
function verbindenMitUhr() {
    const uhr = new FakeUhr();
    const agent = { ...FAKE_AGENT, _uhr: uhr };
    const gehalten = fakeSitzung();
    let referiert;
    gehalten.refer = (ziel, optionen) => {
        referiert = optionen;
        return Promise.resolve();
    };
    const sitzung = new SipJsSitzung(agent, gehalten, { ausgehend: false });
    const zweite = new SipJsSitzung(agent, fakeSitzung(), { ausgehend: true });
    const ergebnis = sitzung.refer(zweite).then(
        () => "ok",
        (fehler) => fehler.message
    );
    return { uhr, gehalten, ergebnis, angenommen: () => referiert.requestDelegate.onAccept() };
}

test("Verbinden: ohne NOTIFY nach 15 s gilt es als gescheitert", async () => {
    const { uhr, ergebnis, angenommen } = verbindenMitUhr();
    await Promise.resolve();
    angenommen();
    await uhr.vorspulen(14_000);
    let fertig = false;
    ergebnis.then(() => (fertig = true));
    await uhr.vorspulen(0);
    expect(fertig).toBe(false);
    await uhr.vorspulen(1000);
    expect(await ergebnis).toMatch(/Anlage/);
});

// --- Halten und Stumm: Mikrofon-Spur (Fake-peerConnection) -----------------------------------

/** Sitzung mit Fake-peerConnection; Re-INVITEs werden gesammelt und von Hand beantwortet. */
function sitzungMitTon() {
    const session = fakeSitzung();
    const spur = { kind: "audio", enabled: true };
    const reInvites = [];
    session.sessionDescriptionHandler = {
        peerConnection: { getSenders: () => [{ track: spur }] },
    };
    session.invite = (optionen) => {
        reInvites.push({
            hold: session.sessionDescriptionHandlerOptionsReInvite.hold,
            spurBeimSenden: spur.enabled,
            annehmen: () => optionen.requestDelegate.onAccept(),
            ablehnen: (code = 491) => optionen.requestDelegate.onReject({ message: { statusCode: code } }),
        });
        return Promise.resolve();
    };
    const sitzung = new SipJsSitzung(FAKE_AGENT, session, { ausgehend: true });
    return { sitzung, spur, reInvites };
}

test("Halten: Mikrofon aus, schon bevor das Re-INVITE rausgeht", async () => {
    const { sitzung, spur, reInvites } = sitzungMitTon();
    const ergebnis = sitzung.hold();
    expect(reInvites.length).toBe(1);
    expect(reInvites[0].hold).toBe(true);
    expect(reInvites[0].spurBeimSenden).toBe(false);
    reInvites[0].annehmen();
    await ergebnis;
    expect(spur.enabled).toBe(false);
});

test("Halten: Stumm aus schaltet das Mikrofon einer gehaltenen Leitung nicht ein", async () => {
    const { sitzung, spur, reInvites } = sitzungMitTon();
    const ergebnis = sitzung.hold();
    reInvites[0].annehmen();
    await ergebnis;
    sitzung.mute(false);
    expect(spur.enabled).toBe(false);
    sitzung.mute(true);
    sitzung.mute(false);
    expect(spur.enabled).toBe(false);
});

test("Fortsetzen: Mikrofon erst nach angenommenem Re-INVITE und nur, wenn nicht stumm", async () => {
    const { sitzung, spur, reInvites } = sitzungMitTon();
    let ergebnis = sitzung.hold();
    reInvites[0].annehmen();
    await ergebnis;
    ergebnis = sitzung.unhold();
    expect(reInvites[1].hold).toBe(false);
    expect(spur.enabled).toBe(false); // noch nicht angenommen
    reInvites[1].annehmen();
    await ergebnis;
    expect(spur.enabled).toBe(true);
    // stumm gehalten: nach dem Fortsetzen weiter stumm
    sitzung.mute(true);
    ergebnis = sitzung.hold();
    reInvites[2].annehmen();
    await ergebnis;
    ergebnis = sitzung.unhold();
    reInvites[3].annehmen();
    await ergebnis;
    expect(spur.enabled).toBe(false);
    sitzung.mute(false);
    expect(spur.enabled).toBe(true);
});

test("Halten abgelehnt: Mikrofon wieder wie vorher; Fortsetzen abgelehnt: bleibt aus", async () => {
    const { sitzung, spur, reInvites } = sitzungMitTon();
    let ergebnis = sitzung.hold();
    expect(spur.enabled).toBe(false);
    reInvites[0].ablehnen(491);
    await expect(ergebnis).rejects.toThrow(/491/);
    expect(spur.enabled).toBe(true);
    // stumm bleibt stumm, auch nach abgelehntem Halten
    sitzung.mute(true);
    ergebnis = sitzung.hold();
    reInvites[1].ablehnen(500);
    await expect(ergebnis).rejects.toThrow(/500/);
    expect(spur.enabled).toBe(false);
    sitzung.mute(false);
    ergebnis = sitzung.hold();
    reInvites[2].annehmen();
    await ergebnis;
    ergebnis = sitzung.unhold();
    reInvites[3].ablehnen(488);
    await expect(ergebnis).rejects.toThrow(/488/);
    expect(spur.enabled).toBe(false);
});

test("Verbinden: ohne NOTIFY, aber Anlage hat die gehaltene Leitung beendet – erfolgreich", async () => {
    const { uhr, gehalten, ergebnis, angenommen } = verbindenMitUhr();
    await Promise.resolve();
    angenommen();
    gehalten.beenden();
    await uhr.vorspulen(15_000);
    expect(await ergebnis).toBe("ok");
});
