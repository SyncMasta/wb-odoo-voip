import { afterEach, beforeEach, describe, expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { mockService, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";
import { translatedTerms, translationLoaded } from "@web/core/l10n/translation";

import { konferenzAbbauen, konferenzAufbauen } from "@wb_voip/dienste/konferenz";
import { SipJsSitzung } from "@wb_voip/dienste/sip_agent";
import { TelefonDienst } from "@wb_voip/dienste/telefon_dienst";
import { Softphone } from "@wb_voip/softphone/softphone";

import {
    FakeAgentFabrik,
    FakeAudioContext,
    FakeKanalNetz,
    FakeKlaenge,
    FakeOrm,
    FakeSender,
    FakeSperren,
    FakeSpur,
    FakeUhr,
    lizenzFehler,
} from "./sip_fake";

describe.current.tags("desktop");

defineMailModels();

let uebersetzungVorher;
beforeEach(() => {
    uebersetzungVorher = translatedTerms[translationLoaded];
    translatedTerms[translationLoaded] = true;
});
afterEach(() => {
    translatedTerms[translationLoaded] = uebersetzungVorher;
});

// --- Mischung (reine Funktionen) -------------------------------------------------------------

function seiten() {
    return {
        ctx: new FakeAudioContext(),
        mikrofon: { name: "mikrofon" },
        a: { empfang: { name: "von A" }, sender: new FakeSender(new FakeSpur("mik A")) },
        b: { empfang: { name: "von B" }, sender: new FakeSender(new FakeSpur("mik B")) },
    };
}

/** Namen der Ströme, die hörbar an der Spur ankommen, die ``sender`` gerade sendet. */
function hoertAn(ctx, sender) {
    const ziel = ctx.knoten.find((k) => k.art === "ziel" && k.spur === sender.track);
    return [...ctx.quellenVon(ziel)].map((s) => s.name).sort();
}

test("Mischung: an A Mikrofon + B, an B Mikrofon + A, nichts doppelt im Lautsprecher", async () => {
    const p = seiten();
    const handle = await konferenzAufbauen(p);
    expect(p.a.sender.ersetzungen.length).toBe(1);
    expect(p.b.sender.ersetzungen.length).toBe(1);
    expect(hoertAn(p.ctx, p.a.sender)).toEqual(["mikrofon", "von B"]);
    expect(hoertAn(p.ctx, p.b.sender)).toEqual(["mikrofon", "von A"]);
    // lokal spielen die Audio-Elemente der Leitungen, die Mischung gibt nichts aus
    expect(p.ctx.quellenVon(p.ctx.destination).size).toBe(0);
    await konferenzAbbauen(handle);
});

test("Mischung: Stumm wirkt in beide Richtungen nur auf das Mikrofon", async () => {
    const p = seiten();
    const handle = await konferenzAufbauen(p);
    handle.stumm(true);
    expect(hoertAn(p.ctx, p.a.sender)).toEqual(["von B"]);
    expect(hoertAn(p.ctx, p.b.sender)).toEqual(["von A"]);
    handle.stumm(false);
    expect(hoertAn(p.ctx, p.a.sender)).toEqual(["mikrofon", "von B"]);
    await konferenzAbbauen(handle);
});

test("Mischung: Abbau stellt die Mikrofon-Spuren wieder her und räumt auf", async () => {
    const p = seiten();
    const mikA = p.a.sender.track;
    const mikB = p.b.sender.track;
    const handle = await konferenzAufbauen(p);
    const gemischt = [p.a.sender.track, p.b.sender.track];
    // eine Leitung ist schon weg: replaceTrack scheitert, der Abbau läuft trotzdem durch
    p.b.sender.fehler = new Error("closed");
    await konferenzAbbauen(handle, { stumm: true });
    expect(p.a.sender.track).toBe(mikA);
    expect(mikA.enabled).toBe(false);
    expect(mikB.enabled).toBe(false);
    expect(gemischt.every((spur) => spur.gestoppt)).toBe(true);
    expect(p.ctx.knoten.every((k) => k.getrennt)).toBe(true);
    expect(p.ctx.geschlossen).toBe(true);
    // zweiter Abbau ohne Wirkung
    await konferenzAbbauen(handle);
    expect(p.a.sender.ersetzungen.length).toBe(2);
});

test("Mischung: scheitert replaceTrack beim Aufbau, wird alles zurückgenommen", async () => {
    const p = seiten();
    const mikA = p.a.sender.track;
    p.b.sender.fehler = new Error("InvalidStateError");
    await expect(konferenzAufbauen(p)).rejects.toThrow("InvalidStateError");
    expect(p.a.sender.track).toBe(mikA);
    expect(p.ctx.geschlossen).toBe(true);
    await expect(konferenzAufbauen({ ...p, b: { empfang: null, sender: null } })).rejects.toThrow();
});

test("SipJsSitzung.konferenzMedien: Audio-Sender, Ton der Gegenstelle und Mikrofon", () => {
    const sender = { track: { kind: "audio" } };
    const session = {
        request: { callId: "abc" },
        remoteIdentity: { uri: { user: "1" } },
        stateChange: { addListener: () => {} },
        sessionDescriptionHandler: {
            peerConnection: { getSenders: () => [{ track: { kind: "video" } }, sender] },
            remoteMediaStream: { name: "fern" },
            localMediaStream: { name: "lokal" },
        },
    };
    const agent = { SIP: { SessionState: {} } };
    const sitzung = new SipJsSitzung(agent, session, { ausgehend: true });
    expect(sitzung.konferenzMedien()).toEqual({
        sender,
        empfang: { name: "fern" },
        mikrofon: { name: "lokal" },
    });
    session.sessionDescriptionHandler = undefined;
    expect(sitzung.konferenzMedien()).toBe(null);
});

// --- Telefon-Dienst ---------------------------------------------------------------------------

async function verbunden() {
    const uhr = new FakeUhr();
    const fabrik = new FakeAgentFabrik(uhr);
    const orm = new FakeOrm();
    orm.antworten["wb.voip.anruf.wb_voip_beendet"] = (args) => ({
        id: args[0][0],
        zustand: "beendet",
        protokoll_id: 100 + args[0][0],
    });
    const kontexte = [];
    const dienst = new TelefonDienst({
        orm,
        sperren: new FakeSperren(),
        kanal: new FakeKanalNetz().kanal(),
        uhr,
        tabId: "tab-konferenz",
        agentFabrik: fabrik.fabrik,
        klaenge: new FakeKlaenge(),
        audioKontext: () => {
            const ctx = new FakeAudioContext();
            kontexte.push(ctx);
            return ctx;
        },
    });
    dienst.start();
    await uhr.vorspulen(0);
    const popups = [];
    dienst.bus.addEventListener("anruf-beendet", ({ detail }) => {
        if (detail.popup) {
            popups.push(detail.anrufId);
        }
    });
    dienst.bus.addEventListener("protokoll-oeffnen", ({ detail }) => popups.push(detail.anrufId));
    return { dienst, uhr, fabrik, orm, agent: fabrik.letzterAgent, kontexte, popups };
}

/** Kunde (aktiv, ausgehend) plus angenommene Rückfrage an 102. */
async function zweiLeitungen(welt) {
    await welt.dienst.waehlen("0170 1111");
    const erste = welt.agent.invites.at(-1);
    erste.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    expect(await welt.dienst.rueckfrage("102")).toBe(true);
    await welt.uhr.vorspulen(0);
    const zweite = welt.agent.invites.at(-1);
    zweite.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    return { erste, zweite };
}

/** Zwei Kundengespräche: erster Anruf gehalten, zweiter eingehender angenommen. */
async function zweiKunden(welt) {
    await welt.dienst.waehlen("0170 1111");
    const erste = welt.agent.invites.at(-1);
    erste.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    const zweite = welt.agent.eingehend("0170 2222");
    await welt.uhr.vorspulen(0);
    await welt.dienst.annehmen();
    await welt.uhr.vorspulen(0);
    return { erste, zweite };
}

async function inKonferenz(welt, leitungen = zweiLeitungen) {
    const paar = await leitungen(welt);
    expect(await welt.dienst.konferenz()).toBe(true);
    await welt.uhr.vorspulen(0);
    expect(welt.dienst.state.konferenz).toBe(true);
    return paar;
}

test("Konferenz: gehaltene Leitung kommt zurück, beide gemischt", async () => {
    const welt = await verbunden();
    expect(await welt.dienst.konferenz()).toBe(false);
    const { erste, zweite } = await inKonferenz(welt);
    // aktiv war die Rückfrage (zweite), gehalten der Kunde (erste)
    expect(erste.hat("unhold").length).toBe(1);
    expect(welt.dienst.state.gehaltener.gehalten).toBe(false);
    expect(welt.dienst.state.anruf.gehalten).toBe(false);
    const ctx = welt.kontexte[0];
    // Mikrofon der aktiven Leitung ist die Quelle
    expect(hoertAn(ctx, zweite.medien.sender)).toEqual(["empfang-" + nr(erste), "mikrofon-" + nr(zweite)].sort());
    expect(hoertAn(ctx, erste.medien.sender)).toEqual(["empfang-" + nr(zweite), "mikrofon-" + nr(zweite)].sort());
    // zweite Konferenz geht nicht
    expect(await welt.dienst.konferenz()).toBe(false);
    welt.dienst.stop();
});

/** Laufende Nummer einer FakeSitzung (aus der Call-ID). */
function nr(sitzung) {
    return sitzung.callId.split("-").at(-1);
}

test("Konferenz nur mit zwei angenommenen Leitungen und verbundenem Telefon", async () => {
    const welt = await verbunden();
    await welt.dienst.waehlen("0170 1111");
    welt.agent.invites[0].gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    await welt.dienst.rueckfrage("102");
    await welt.uhr.vorspulen(0);
    // Rückfrage klingelt noch
    expect(await welt.dienst.konferenz()).toBe(false);
    expect(welt.kontexte.length).toBe(0);
    expect(welt.agent.invites[0].hat("unhold").length).toBe(0);
    welt.dienst.stop();
});

test("Review Focus 4: Kunde legt in der Konferenz auf – Kollege läuft mit Mikrofon allein weiter", async () => {
    const welt = await verbunden();
    const { erste, zweite } = await inKonferenz(welt);
    const gemischt = zweite.medien.sender.track;
    erste.gegenstelleLegtAuf("bye");
    await welt.uhr.vorspulen(0);
    expect(welt.dienst.state.konferenz).toBe(false);
    expect(welt.dienst.state.gehaltener).toBe(null);
    expect(welt.dienst.state.anruf.nummer).toBe("102");
    expect(welt.dienst.state.anruf.zustand).toBe("laeuft");
    expect(welt.dienst.state.anruf.gehalten).toBe(false);
    // wieder nur das eigene Mikrofon, Mischung aufgelöst
    expect(zweite.medien.sender.track).not.toBe(gemischt);
    expect(zweite.medien.sender.track.name).toBe(`mik-spur-${nr(zweite)}`);
    expect(welt.kontexte[0].geschlossen).toBe(true);
    expect(zweite.hat("bye").length).toBe(0);
    expect(zweite.hat("hold").length).toBe(0);
    // Bedienung wirkt auf die verbleibende Leitung
    welt.dienst.stumm(true);
    welt.dienst.dtmf("5");
    expect(zweite.hat("mute")).toEqual([["mute", true]]);
    expect(zweite.hat("dtmf")).toEqual([["dtmf", "5"]]);
    welt.dienst.stop();
});

test("Review Focus 4: Kollege legt in der Konferenz auf – Kunde läuft mit Mikrofon allein weiter", async () => {
    const welt = await verbunden();
    const { erste, zweite } = await inKonferenz(welt);
    zweite.gegenstelleLegtAuf("bye");
    await welt.uhr.vorspulen(0);
    expect(welt.dienst.state.konferenz).toBe(false);
    expect(welt.dienst.state.gehaltener).toBe(null);
    expect(welt.dienst.state.anruf.nummer).toBe("0170 1111");
    expect(welt.dienst.state.anruf.zustand).toBe("laeuft");
    expect(welt.dienst.state.anruf.gehalten).toBe(false);
    expect(erste.medien.sender.track.name).toBe(`mik-spur-${nr(erste)}`);
    expect(welt.kontexte[0].geschlossen).toBe(true);
    // schon aus dem Halten geholt: kein zweites Re-INVITE
    expect(erste.hat("unhold").length).toBe(1);
    expect(erste.hat("bye").length).toBe(0);
    // Popup erst, wenn auch der Kunde fertig ist
    expect(welt.popups).toEqual([]);
    erste.gegenstelleLegtAuf("bye");
    await welt.uhr.vorspulen(0);
    expect(welt.popups).toEqual([10]);
    welt.dienst.stop();
});

test("Konferenz: Stumm über die Mischung, bleibt nach dem Auflösen erhalten", async () => {
    const welt = await verbunden();
    const { erste, zweite } = await inKonferenz(welt);
    const ctx = welt.kontexte[0];
    const vorher = [erste.hat("mute").length, zweite.hat("mute").length];
    welt.dienst.stumm(true);
    expect(welt.dienst.state.anruf.stumm).toBe(true);
    expect(welt.dienst.state.gehaltener.stumm).toBe(true);
    expect(hoertAn(ctx, erste.medien.sender)).toEqual([`empfang-${nr(zweite)}`]);
    expect(hoertAn(ctx, zweite.medien.sender)).toEqual([`empfang-${nr(erste)}`]);
    // die gemischte Spur wird nicht abgeschaltet
    expect([erste.hat("mute").length, zweite.hat("mute").length]).toEqual(vorher);
    erste.gegenstelleLegtAuf("bye");
    await welt.uhr.vorspulen(0);
    expect(welt.dienst.state.anruf.stumm).toBe(true);
    expect(zweite.medien.sender.track.enabled).toBe(false);
    welt.dienst.stop();
});

test("Konferenz beenden legt beide auf, jedes Kundengespräch bekommt sein Popup", async () => {
    const welt = await verbunden();
    const { erste, zweite } = await inKonferenz(welt, zweiKunden);
    expect(await welt.dienst.konferenzBeenden()).toBe(true);
    await welt.uhr.vorspulen(0);
    expect(erste.hat("bye").length).toBe(1);
    expect(zweite.hat("bye").length).toBe(1);
    expect(welt.dienst.state.anruf).toBe(null);
    expect(welt.dienst.state.gehaltener).toBe(null);
    expect(welt.dienst.state.konferenz).toBe(false);
    expect(welt.kontexte[0].geschlossen).toBe(true);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(2);
    expect([...welt.popups].sort()).toEqual([10, 11]);
    expect(await welt.dienst.konferenzBeenden()).toBe(false);
    welt.dienst.stop();
});

test("In der Konferenz gesperrt: Halten, Verbinden, Zurück, Rückfrage, Übergeben; dritter Anruf 480", async () => {
    const welt = await verbunden();
    const { erste, zweite } = await inKonferenz(welt);
    welt.dienst.state.fehler = null;
    await welt.dienst.halten(true);
    expect(await welt.dienst.verbinden()).toBe(false);
    expect(await welt.dienst.zurueck()).toBe(false);
    expect(await welt.dienst.rueckfrage("103")).toBe(false);
    expect(await welt.dienst.weiterleiten("103")).toBe(false);
    const dritte = welt.agent.eingehend("0170 3333");
    await welt.uhr.vorspulen(0);
    expect(dritte.hat("reject")).toEqual([["reject", { statusCode: 480 }]]);
    for (const s of [erste, zweite]) {
        expect(s.hat("hold").length).toBe(s === erste ? 1 : 0);
        expect(s.hat("refer").length).toBe(0);
        expect(s.hat("bye").length).toBe(0);
    }
    expect(welt.agent.invites.length).toBe(2);
    expect(welt.dienst.state.konferenz).toBe(true);
    // Auflegen beendet die Konferenz (beide Leitungen)
    await welt.dienst.auflegen();
    await welt.uhr.vorspulen(0);
    expect(erste.hat("bye").length).toBe(1);
    expect(zweite.hat("bye").length).toBe(1);
    welt.dienst.stop();
});

test("Konferenz: Lizenzverlust, Verbindungsabbruch und stop lösen auf und beenden alle Leitungen", async () => {
    for (const ausloeser of ["lizenz", "netz", "stop"]) {
        const welt = await verbunden();
        const { erste, zweite } = await inKonferenz(welt);
        if (ausloeser === "lizenz") {
            welt.orm.antworten["res.users.wb_voip_status"] = () => ({
                aktiv: true,
                lizenz_gueltig: false,
                meldung: "Lizenz abgelaufen",
                eingerichtet: true,
                konfiguriert: true,
            });
            welt.orm.antworten["wb.voip.anruf.wb_voip_beginnen"] = () => {
                throw lizenzFehler();
            };
            // ein abgewiesener Anruf prüft die Lizenz nicht; der Lizenzverlust kommt hier
            // über die Nachprüfung
            await welt.dienst._lizenzNachpruefen();
        } else if (ausloeser === "netz") {
            welt.agent.netzWeg();
        } else {
            welt.dienst.stop();
        }
        await welt.uhr.vorspulen(0);
        expect(welt.dienst.state.konferenz).toBe(false);
        expect(welt.dienst.state.anruf).toBe(null);
        expect(welt.dienst.state.gehaltener).toBe(null);
        expect(welt.kontexte[0].geschlossen).toBe(true);
        expect(erste.medien.sender.track.name).toBe(`mik-spur-${nr(erste)}`);
        expect(zweite.medien.sender.track.name).toBe(`mik-spur-${nr(zweite)}`);
        expect(welt.orm.zaehle("wb_voip_beendet")).toBe(2);
        welt.dienst.stop();
    }
});

test("Konferenz mit Kollegen (Team): Rückfrage, nach der Annahme automatisch gemischt", async () => {
    const welt = await verbunden();
    await welt.dienst.waehlen("0170 1111");
    const erste = welt.agent.invites.at(-1);
    erste.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    expect(await welt.dienst.konferenz("102")).toBe(true);
    await welt.uhr.vorspulen(0);
    const zweite = welt.agent.invites.at(-1);
    expect(zweite.ziel).toBe("102");
    expect(erste.hat("hold").length).toBe(1);
    expect(welt.dienst.state.konferenz).toBe(false);
    zweite.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    expect(welt.dienst.state.konferenz).toBe(true);
    expect(erste.hat("unhold").length).toBe(1);
    welt.dienst.stop();
    // Kollege nimmt nicht ab: Kunde kommt zurück, keine Konferenz
    const welt2 = await verbunden();
    await welt2.dienst.waehlen("0170 1111");
    const kunde = welt2.agent.invites.at(-1);
    kunde.gegenstelleNimmtAn();
    await welt2.uhr.vorspulen(0);
    await welt2.dienst.konferenz("102");
    await welt2.uhr.vorspulen(0);
    welt2.agent.invites.at(-1).gegenstelleLegtAuf("486");
    await welt2.uhr.vorspulen(0);
    expect(welt2.dienst.state.konferenz).toBe(false);
    expect(welt2.dienst.state.anruf.nummer).toBe("0170 1111");
    expect(kunde.hat("unhold").length).toBe(1);
    expect(welt2.kontexte.length).toBe(0);
    welt2.dienst.stop();
});

// --- Anrufansicht -----------------------------------------------------------------------------

async function ansicht() {
    const welt = await verbunden();
    welt.orm.antworten["wb.voip.anruf.wb_voip_beendet"] = (args) => ({
        id: args[0][0],
        zustand: "beendet",
        protokoll_id: false,
    });
    onRpc("res.users", "wb_voip_team", () => ({ heranholen_praefix: false, kollegen: [] }));
    onRpc("wb.voip.anruf", "wb_voip_aktionen", () => []);
    mockService("wb_voip_telefon", () => welt.dienst);
    await mountWithCleanup(Softphone);
    return welt;
}

test("Anrufansicht: Konferenz-Knopf, Leiste mit beiden, Stumm und Konferenz beenden", async () => {
    const welt = await ansicht();
    await welt.dienst.waehlen("0170 1111");
    welt.agent.invites[0].gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    await welt.dienst.rueckfrage("102");
    await welt.uhr.vorspulen(0);
    await animationFrame();
    // Rückfrage klingelt noch: Knopf gesperrt
    expect(".o_wb_voip_konferenz_starten").toHaveAttribute("disabled");
    const { erste, zweite } = { erste: welt.agent.invites[0], zweite: welt.agent.invites[1] };
    zweite.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    await animationFrame();
    expect(".o_wb_voip_konferenz_starten").not.toHaveAttribute("disabled");
    expect(".o_wb_voip_konferenz_starten").toHaveAttribute("aria-label");
    await click(".o_wb_voip_konferenz_starten");
    await welt.uhr.vorspulen(0);
    await animationFrame();
    expect(".o_wb_voip_konferenz").toHaveText(/0170 1111/);
    expect(".o_wb_voip_konferenz").toHaveText(/102/);
    expect(".o_wb_voip_gehalten").toHaveCount(0);
    for (const knopf of [".o_wb_voip_halten", ".o_wb_voip_weiterleiten_umschalten", ".o_wb_voip_tastatur_umschalten"]) {
        expect(knopf).toHaveAttribute("disabled");
    }
    expect(".o_wb_voip_team_umschalten").toHaveCount(0);
    expect(".o_wb_voip_auflegen").toHaveCount(0);
    await click(".o_wb_voip_stumm");
    await animationFrame();
    expect(".o_wb_voip_stumm").toHaveAttribute("aria-pressed", "true");
    expect(welt.kontexte[0].knoten.find((k) => k.gain).gain.value).toBe(0);
    await click(".o_wb_voip_konferenz_beenden");
    await welt.uhr.vorspulen(0);
    await animationFrame();
    expect(erste.hat("bye").length).toBe(1);
    expect(zweite.hat("bye").length).toBe(1);
    expect(".o_wb_voip_anrufansicht").toHaveCount(0);
    welt.dienst.stop();
});
