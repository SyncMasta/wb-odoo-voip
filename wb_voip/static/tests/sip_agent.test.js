import { describe, expect, test } from "@odoo/hoot";

import { SipJsSitzung, sipfragStatus } from "@wb_voip/dienste/sip_agent";

import { FakeUhr } from "./sip_fake";

describe.current.tags("desktop");

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

test("Verbinden: ohne NOTIFY, aber Anlage hat die gehaltene Leitung beendet – erfolgreich", async () => {
    const { uhr, gehalten, ergebnis, angenommen } = verbindenMitUhr();
    await Promise.resolve();
    angenommen();
    gehalten.beenden();
    await uhr.vorspulen(15_000);
    expect(await ergebnis).toBe("ok");
});
