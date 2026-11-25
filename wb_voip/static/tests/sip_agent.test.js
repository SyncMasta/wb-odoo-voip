import { describe, expect, test } from "@odoo/hoot";

import { SipJsSitzung } from "@wb_voip/dienste/sip_agent";

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
