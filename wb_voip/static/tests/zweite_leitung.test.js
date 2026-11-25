import { afterEach, beforeEach, describe, expect, test } from "@odoo/hoot";
import { click, edit } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { EventBus, reactive } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { mockService, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";
import { translatedTerms, translationLoaded } from "@web/core/l10n/translation";

import { Benachrichtiger } from "@wb_voip/benachrichtigung";
import { TelefonDienst } from "@wb_voip/dienste/telefon_dienst";
import { protokollPopupDienst } from "@wb_voip/protokoll_popup";
import { Softphone } from "@wb_voip/softphone/softphone";

import {
    FakeAgentFabrik,
    FakeKanalNetz,
    FakeKlaenge,
    FakeOrm,
    FakeSperren,
    FakeUhr,
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

/** Verbundener Telefon-Dienst mit Fakes; ``beendet`` ohne Protokoll (kein Popup im UI-Test). */
async function verbunden() {
    const uhr = new FakeUhr();
    const fabrik = new FakeAgentFabrik(uhr);
    const orm = new FakeOrm();
    const dienst = new TelefonDienst({
        orm,
        sperren: new FakeSperren(),
        kanal: new FakeKanalNetz().kanal(),
        uhr,
        tabId: "tab-zwei",
        agentFabrik: fabrik.fabrik,
        klaenge: new FakeKlaenge(),
    });
    dienst.start();
    await uhr.vorspulen(0);
    expect(dienst.state.status).toBe("verbunden");
    const beendet = [];
    dienst.bus.addEventListener("anruf-beendet", ({ detail }) => beendet.push(detail));
    return { dienst, uhr, fabrik, orm, agent: fabrik.letzterAgent, beendet };
}

/** Laufendes Gespräch (ausgehend, angenommen). */
async function imGespraech(welt, nummer = "0170 1111") {
    await welt.dienst.waehlen(nummer);
    const sitzung = welt.agent.invites.at(-1);
    sitzung.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    expect(welt.dienst.state.anruf.zustand).toBe("laeuft");
    return sitzung;
}

/** Laufendes Gespräch plus Rückfrage an ``nummer`` (angenommen, wenn ``annehmen``). */
async function mitRueckfrage(welt, { annehmen = true, nummer = "102" } = {}) {
    const erste = await imGespraech(welt);
    expect(await welt.dienst.rueckfrage(nummer)).toBe(true);
    await welt.uhr.vorspulen(0);
    const zweite = welt.agent.invites.at(-1);
    if (annehmen) {
        zweite.gegenstelleNimmtAn();
        await welt.uhr.vorspulen(0);
    }
    return { erste, zweite };
}

// --- Rückfrage ----------------------------------------------------------------------------

test("Rückfrage hält das Gespräch und wählt die zweite Leitung", async () => {
    const welt = await verbunden();
    const erste = await imGespraech(welt);
    const ersterAnruf = welt.dienst.state.anruf;
    expect(await welt.dienst.rueckfrage("+49 30 102")).toBe(true);
    await welt.uhr.vorspulen(0);
    expect(erste.hat("hold").length).toBe(1);
    expect(welt.agent.invites.length).toBe(2);
    expect(welt.agent.invites[1].ziel).toBe("004930102");
    expect(welt.dienst.state.gehaltener.nummer).toBe(ersterAnruf.nummer);
    expect(welt.dienst.state.gehaltener.gehalten).toBe(true);
    expect(welt.dienst.state.anruf.nummer).toBe("+49 30 102");
    // je Leitung ein eigener Anruf auf dem Server
    expect(welt.orm.zaehle("wb_voip_beginnen")).toBe(2);
    // Lebenszeichen für beide Leitungen
    welt.agent.invites[1].gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    const vorher = welt.orm.zaehle("wb_voip_lebenszeichen");
    await welt.uhr.vorspulen(60_000);
    expect(welt.orm.zaehle("wb_voip_lebenszeichen") - vorher).toBe(2);
    welt.dienst.stop();
});

test("Rückfrage nur im laufenden Gespräch und höchstens zwei Leitungen", async () => {
    const welt = await verbunden();
    expect(await welt.dienst.rueckfrage("102")).toBe(false);
    await mitRueckfrage(welt);
    expect(await welt.dienst.rueckfrage("103")).toBe(false);
    expect(welt.agent.invites.length).toBe(2);
    expect(Boolean(welt.dienst.state.fehler)).toBe(true);
    welt.dienst.stop();
});

test("Zurück legt die Rückfrage auf und holt das Gespräch zurück", async () => {
    const welt = await verbunden();
    const { erste, zweite } = await mitRueckfrage(welt);
    const ersteId = welt.dienst.state.gehaltener.id;
    expect(await welt.dienst.zurueck()).toBe(true);
    await welt.uhr.vorspulen(0);
    expect(zweite.hat("bye").length).toBe(1);
    expect(erste.hat("unhold").length).toBe(1);
    expect(welt.dienst.state.gehaltener).toBe(null);
    expect(welt.dienst.state.anruf.id).toBe(ersteId);
    expect(welt.dienst.state.anruf.gehalten).toBe(false);
    expect(welt.dienst.state.anruf.zustand).toBe("laeuft");
    // Ende der Rückfrage gemeldet, aber ohne Popup (das Gespräch läuft weiter)
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(1);
    expect(welt.beendet.map((d) => d.popup)).toEqual([false]);
    welt.dienst.stop();
});

test("Review Focus 3: Kollege nimmt nicht ab – Gespräch kommt aus dem Halten zurück", async () => {
    const welt = await verbunden();
    const { erste, zweite } = await mitRueckfrage(welt, { annehmen: false });
    zweite.fortschritt(180);
    zweite.gegenstelleLegtAuf("486");
    await welt.uhr.vorspulen(0);
    expect(erste.hat("unhold").length).toBe(1);
    expect(welt.dienst.state.gehaltener).toBe(null);
    expect(welt.dienst.state.anruf.zustand).toBe("laeuft");
    expect(welt.dienst.state.anruf.gehalten).toBe(false);
    // nichts geht verloren: das erste Gespräch läuft weiter und wird nicht beendet
    expect(erste.hat("bye").length).toBe(0);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(1);
    expect(welt.dienst.klaenge.freizeichenAn).toBe(false);
    dienstStumm(welt);
    welt.dienst.stop();
});

/** Stumm und DTMF wirken auf die aktive (zurückgeholte) Leitung. */
function dienstStumm(welt) {
    const erste = welt.agent.invites[0];
    welt.dienst.stumm(true);
    welt.dienst.dtmf("7");
    expect(erste.hat("mute")).toEqual([["mute", true]]);
    expect(erste.hat("dtmf")).toEqual([["dtmf", "7"]]);
}

test("Review Focus 3: Kollege legt nach der Annahme auf – Gespräch kommt zurück", async () => {
    const welt = await verbunden();
    const { erste, zweite } = await mitRueckfrage(welt);
    zweite.gegenstelleLegtAuf("bye");
    await welt.uhr.vorspulen(0);
    expect(erste.hat("unhold").length).toBe(1);
    expect(welt.dienst.state.gehaltener).toBe(null);
    expect(welt.dienst.state.anruf.nummer).toBe("0170 1111");
    expect(welt.dienst.state.anruf.zustand).toBe("laeuft");
    welt.dienst.stop();
});

test("Gehaltene Leitung legt auf: Leiste verschwindet, Rückfrage läuft weiter", async () => {
    const welt = await verbunden();
    const { erste, zweite } = await mitRueckfrage(welt);
    erste.gegenstelleLegtAuf("bye");
    await welt.uhr.vorspulen(0);
    expect(welt.dienst.state.gehaltener).toBe(null);
    expect(welt.dienst.state.anruf.nummer).toBe("102");
    expect(welt.beendet.map((d) => d.popup)).toEqual([false]);
    zweite.gegenstelleLegtAuf("bye");
    await welt.uhr.vorspulen(0);
    expect(welt.dienst.state.anruf).toBe(null);
    expect(welt.beendet.map((d) => d.popup)).toEqual([false, true]);
    welt.dienst.stop();
});

// --- Verbinden und Übergeben -----------------------------------------------------------

test("Verbinden: REFER der gehaltenen Leitung mit der anderen Sitzung, beide enden", async () => {
    const welt = await verbunden();
    const { erste, zweite } = await mitRueckfrage(welt);
    expect(await welt.dienst.verbinden()).toBe(true);
    await welt.uhr.vorspulen(0);
    expect(erste.hat("refer").length).toBe(1);
    expect(erste.hat("refer")[0][1]).toBe(zweite);
    expect(zweite.hat("refer").length).toBe(0);
    expect(erste.hat("unhold").length).toBe(0);
    expect(welt.dienst.state.anruf).toBe(null);
    expect(welt.dienst.state.gehaltener).toBe(null);
    // Protokolle wie bei normalem Auflegen, Popup nur für die zuletzt aktive Leitung
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(2);
    expect(welt.beendet.map((d) => d.popup)).toEqual([false, true]);
    // Server-IDs des Fakes: Gespräch 10, Rückfrage 11
    expect(welt.beendet.map((d) => d.anrufId)).toEqual([10, 11]);
    welt.dienst.stop();
});

test("Verbinden ohne angenommene Rückfrage oder abgelehnt: nichts geht verloren", async () => {
    const welt = await verbunden();
    const { erste, zweite } = await mitRueckfrage(welt, { annehmen: false });
    expect(await welt.dienst.verbinden()).toBe(false);
    expect(erste.hat("refer").length).toBe(0);
    zweite.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    erste.refer = (ziel) => {
        erste.aufrufe.push(["refer", ziel]);
        return Promise.reject(new Error("REFER abgelehnt (403)"));
    };
    expect(await welt.dienst.verbinden()).toBe(false);
    await welt.uhr.vorspulen(0);
    expect(welt.dienst.state.gehaltener).not.toBe(null);
    expect(welt.dienst.state.anruf.nummer).toBe("102");
    expect(welt.dienst.state.fehler).toMatch(/REFER/);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(0);
    welt.dienst.stop();
});

test("Übergeben direkt per REFER an die wählbare Nummer", async () => {
    const welt = await verbunden();
    const erste = await imGespraech(welt);
    expect(await welt.dienst.uebergeben("+49 30 102")).toBe(true);
    expect(erste.hat("refer")).toEqual([["refer", "004930102"]]);
    expect(await welt.dienst.uebergeben("abc")).toBe(false);
    welt.dienst.stop();
});

// --- Zweiter eingehender Anruf ------------------------------------------------------------

test("Zweiter eingehender Anruf klingelt als zweite Leitung, Annehmen hält das erste", async () => {
    const welt = await verbunden();
    const erste = await imGespraech(welt);
    const zweite = welt.agent.eingehend("0170 2222");
    await welt.uhr.vorspulen(0);
    expect(zweite.hat("reject").length).toBe(0);
    expect(welt.dienst.state.klopfend.nummer).toBe("0170 2222");
    expect(welt.dienst.state.anruf.nummer).toBe("0170 1111");
    expect(welt.dienst.klaenge.klingelnAn).toBe(true);
    expect(welt.orm.zaehle("wb_voip_beginnen")).toBe(2);
    await welt.dienst.annehmen();
    await welt.uhr.vorspulen(0);
    expect(erste.hat("hold").length).toBe(1);
    expect(zweite.hat("accept").length).toBe(1);
    expect(welt.dienst.state.klopfend).toBe(null);
    expect(welt.dienst.state.anruf.nummer).toBe("0170 2222");
    expect(welt.dienst.state.anruf.zustand).toBe("laeuft");
    expect(welt.dienst.state.gehaltener.nummer).toBe("0170 1111");
    expect(welt.dienst.klaenge.klingelnAn).toBe(false);
    welt.dienst.stop();
});

test("Dritter eingehender Anruf wird mit 480 abgewiesen", async () => {
    const welt = await verbunden();
    await imGespraech(welt);
    welt.agent.eingehend("0170 2222");
    await welt.uhr.vorspulen(0);
    const dritte = welt.agent.eingehend("0170 3333");
    await welt.uhr.vorspulen(0);
    expect(dritte.hat("reject")).toEqual([["reject", { statusCode: 480 }]]);
    await welt.dienst.annehmen();
    await welt.uhr.vorspulen(0);
    const vierte = welt.agent.eingehend("0170 4444");
    await welt.uhr.vorspulen(0);
    expect(vierte.hat("reject")).toEqual([["reject", { statusCode: 480 }]]);
    expect(welt.orm.zaehle("wb_voip_beginnen")).toBe(2);
    welt.dienst.stop();
});

test("Wartender Anrufer legt auf oder wird abgelehnt: erstes Gespräch unberührt", async () => {
    const welt = await verbunden();
    const erste = await imGespraech(welt);
    const zweite = welt.agent.eingehend("0170 2222");
    await welt.uhr.vorspulen(0);
    zweite.gegenstelleLegtAuf("487");
    await welt.uhr.vorspulen(0);
    expect(welt.dienst.state.klopfend).toBe(null);
    expect(welt.dienst.klaenge.klingelnAn).toBe(false);
    const dritte = welt.agent.eingehend("0170 3333");
    await welt.uhr.vorspulen(0);
    await welt.dienst.ablehnen();
    await welt.uhr.vorspulen(0);
    expect(dritte.hat("reject")).toEqual([["reject", { statusCode: 603 }]]);
    expect(welt.dienst.state.klopfend).toBe(null);
    expect(erste.hat("hold").length).toBe(0);
    expect(welt.dienst.state.anruf.nummer).toBe("0170 1111");
    expect(welt.dienst.state.anruf.zustand).toBe("laeuft");
    expect(welt.beendet.map((d) => d.popup)).toEqual([false, false]);
    welt.dienst.stop();
});

test("Abbruch (Verbindung weg) beendet beide Leitungen ohne Zurückholen", async () => {
    const welt = await verbunden();
    const { erste } = await mitRueckfrage(welt);
    welt.agent.netzWeg();
    await welt.uhr.vorspulen(0);
    expect(erste.hat("unhold").length).toBe(0);
    expect(welt.dienst.state.anruf).toBe(null);
    expect(welt.dienst.state.gehaltener).toBe(null);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(2);
    welt.dienst.stop();
});

test("Desktop-Benachrichtigung auch für den wartenden Anruf", async () => {
    const gemeldet = [];
    const benachrichtiger = new Benachrichtiger({ api: null, hatFokus: () => false });
    benachrichtiger.eingehend = (daten) => gemeldet.push(daten.nummer);
    const state = reactive({ anruf: { richtung: "ausgehend", annahme: 1, nummer: "1" }, klopfend: null });
    benachrichtiger.beobachten(state);
    state.klopfend = { richtung: "eingehend", annahme: false, nummer: "0170 2222", beginn: 5 };
    await animationFrame();
    expect(gemeldet).toEqual(["0170 2222"]);
});

test("Protokoll-Popup nicht für eine Leitung, neben der eine andere weiterläuft", async () => {
    const geoeffnet = [];
    const bus = new EventBus();
    protokollPopupDienst.start({}, {
        dialog: { add: (_k, props) => geoeffnet.push(props.resId) },
        wb_voip_telefon: { bus },
    });
    bus.trigger("anruf-beendet", { anrufId: 1, protokollId: 5, eigenerTab: true, popup: false });
    bus.trigger("anruf-beendet", { anrufId: 2, protokollId: 6, eigenerTab: true, popup: true });
    expect(geoeffnet).toEqual([6]);
});

// --- Anrufansicht ---------------------------------------------------------------------------

async function ansicht() {
    const welt = await verbunden();
    welt.orm.antworten["wb.voip.anruf.wb_voip_beendet"] = (args) => ({
        id: args[0][0],
        zustand: "beendet",
        protokoll_id: false,
    });
    onRpc("res.users", "wb_voip_team", () => ({
        heranholen_praefix: false,
        kollegen: [{ user_id: 2, name: "Anna Frei", nebenstelle: "102", status: "frei", seit: false }],
    }));
    onRpc("wb.voip.anruf", "wb_voip_aktionen", () => []);
    mockService("wb_voip_telefon", () => welt.dienst);
    await mountWithCleanup(Softphone);
    return welt;
}

test("Anrufansicht: Leiste „Gehalten“ mit Zurück und Verbinden", async () => {
    const welt = await ansicht();
    const { erste, zweite } = await mitRueckfrage(welt);
    await animationFrame();
    expect(".o_wb_voip_gehalten").toHaveText(/Gehalten: 0170 1111/);
    expect(".o_wb_voip_name").toHaveText("102");
    await click(".o_wb_voip_verbinden");
    await welt.uhr.vorspulen(0);
    await animationFrame();
    expect(erste.hat("refer")[0][1]).toBe(zweite);
    expect(".o_wb_voip_anrufansicht").toHaveCount(0);
    // zweiter Durchlauf: Zurück
    const weiter = await mitRueckfrage(welt);
    await animationFrame();
    await click(".o_wb_voip_zurueck");
    await welt.uhr.vorspulen(0);
    await animationFrame();
    expect(weiter.zweite.hat("bye").length).toBe(1);
    expect(".o_wb_voip_gehalten").toHaveCount(0);
    expect(".o_wb_voip_name").toHaveText("0170 1111");
    welt.dienst.stop();
});

test("Anrufansicht: wartender Anruf sichtbar, Annehmen hält das Gespräch", async () => {
    const welt = await ansicht();
    const erste = await imGespraech(welt);
    welt.agent.eingehend("0170 2222");
    await welt.uhr.vorspulen(0);
    await animationFrame();
    expect(".o_wb_voip_klopfend").toHaveText(/0170 2222/);
    await click(".o_wb_voip_klopfend_annehmen");
    await welt.uhr.vorspulen(0);
    await animationFrame();
    expect(erste.hat("hold").length).toBe(1);
    expect(".o_wb_voip_klopfend").toHaveCount(0);
    expect(".o_wb_voip_gehalten").toHaveText(/0170 1111/);
    expect(".o_wb_voip_name").toHaveText("0170 2222");
    welt.dienst.stop();
});

test("Anrufansicht: Team-Auswahl mit Übergeben und Mit Rückfrage", async () => {
    const welt = await ansicht();
    const erste = await imGespraech(welt);
    await animationFrame();
    await click(".o_wb_voip_team_umschalten");
    await animationFrame();
    expect(".o_wb_voip_anrufansicht .o_wb_voip_team_zeile").toHaveCount(1);
    // im Gespräch nur Übergeben und Rückfrage, nicht Anrufen
    expect(".o_wb_voip_anrufansicht .o_wb_voip_team_anrufen").toHaveCount(0);
    await click(".o_wb_voip_anrufansicht .o_wb_voip_team_rueckfrage");
    await welt.uhr.vorspulen(0);
    await animationFrame();
    expect(welt.agent.invites.at(-1).ziel).toBe("102");
    expect(".o_wb_voip_gehalten").toHaveCount(1);
    // mit zweiter Leitung keine weitere Team-Auswahl
    expect(".o_wb_voip_team_umschalten").toHaveCount(0);
    await welt.dienst.zurueck();
    await welt.uhr.vorspulen(0);
    await animationFrame();
    await click(".o_wb_voip_team_umschalten");
    await animationFrame();
    await click(".o_wb_voip_anrufansicht .o_wb_voip_team_uebergeben");
    await welt.uhr.vorspulen(0);
    expect(erste.hat("refer")).toEqual([["refer", "102"]]);
    welt.dienst.stop();
});

test("Anrufansicht: freie Nummer mit Rückfrage", async () => {
    const welt = await ansicht();
    const erste = await imGespraech(welt);
    await animationFrame();
    await click(".o_wb_voip_weiterleiten_umschalten");
    await animationFrame();
    await click(".o_wb_voip_weiterleiten_ziel");
    await edit("+49 30 555");
    await click(".o_wb_voip_rueckfrage");
    await welt.uhr.vorspulen(0);
    await animationFrame();
    expect(erste.hat("hold").length).toBe(1);
    expect(welt.agent.invites.at(-1).ziel).toBe("004930555");
    expect(".o_wb_voip_gehalten").toHaveText(/0170 1111/);
    welt.dienst.stop();
});
