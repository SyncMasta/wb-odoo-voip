import { afterEach, beforeEach, describe, expect, test } from "@odoo/hoot";
import { registry } from "@web/core/registry";
import { translatedTerms, translationLoaded } from "@web/core/l10n/translation";

import { erzeugeAgentFabrik, SipJsAgent, TestAgent } from "@wb_voip/dienste/sip_agent";
import { anruferKennung, TelefonDienst, waehlbar } from "@wb_voip/dienste/telefon_dienst";

import {
    ZUGANG_PROD,
    FakeAgentFabrik,
    FakeKanalNetz,
    FakeKlaenge,
    FakeOrm,
    FakeSperren,
    FakeUhr,
    lizenzFehler,
    userError,
} from "./sip_fake";

describe.current.tags("headless");

// Der Dienst übersetzt seine Texte mit _t; ohne Web-Umgebung gelten sie als geladen (Quelltext)
let uebersetzungVorher;
beforeEach(() => {
    uebersetzungVorher = translatedTerms[translationLoaded];
    translatedTerms[translationLoaded] = true;
});
afterEach(() => {
    translatedTerms[translationLoaded] = uebersetzungVorher;
});

/** Baut eine Testumgebung mit gemeinsamer Sperre, Kanal, Uhr und Agent-Fabrik. */
function umgebung() {
    const uhr = new FakeUhr();
    const welt = {
        uhr,
        sperren: new FakeSperren(),
        netz: new FakeKanalNetz(),
        fabrik: new FakeAgentFabrik(uhr),
        orm: new FakeOrm(),
        dienste: [],
    };
    welt.dienst = (tabId, extra = {}) => {
        const dienst = new TelefonDienst({
            orm: welt.orm,
            sperren: welt.sperren,
            kanal: welt.netz.kanal(),
            uhr,
            tabId,
            agentFabrik: welt.fabrik.fabrik,
            klaenge: new FakeKlaenge(),
            ...extra,
        });
        welt.dienste.push(dienst);
        return dienst;
    };
    welt.aufraeumen = () => welt.dienste.forEach((d) => d.stop());
    return welt;
}

/** Ein verbundener Halter-Dienst. */
async function verbundenerDienst(welt, tabId = "tab-1") {
    const dienst = welt.dienst(tabId);
    dienst.start();
    await welt.uhr.vorspulen(0);
    expect(dienst.state.status).toBe("verbunden");
    return dienst;
}

test("waehlbar: + wird 00, sonst nur Ziffern, * und #", () => {
    expect(waehlbar("+49 170 1234567")).toBe("00491701234567");
    expect(waehlbar("0170/123-45 67")).toBe("01701234567");
    expect(waehlbar(" *21# ")).toBe("*21#");
    expect(waehlbar("")).toBe("");
    expect(waehlbar(null)).toBe("");
});

test("Dienst ist in der Registry", () => {
    expect(registry.category("services").contains("wb_voip_telefon")).toBe(true);
});

test("nur Halter registriert", async () => {
    const welt = umgebung();
    const eins = welt.dienst("tab-1");
    const zwei = welt.dienst("tab-2");
    eins.start();
    zwei.start();
    await welt.uhr.vorspulen(0);
    expect(welt.fabrik.anzahlRegister).toBe(1);
    expect(eins.state.status).toBe("verbunden");
    expect(zwei.state.status).toBe("anderer_tab");
    welt.aufraeumen();
});

test("Übernahme nach Schließen", async () => {
    const welt = umgebung();
    const eins = welt.dienst("tab-1");
    const zwei = welt.dienst("tab-2");
    eins.start();
    zwei.start();
    await welt.uhr.vorspulen(0);
    expect(zwei.state.status).toBe("anderer_tab");
    eins.stop();
    await welt.uhr.vorspulen(5000);
    expect(welt.fabrik.anzahlRegister).toBe(2);
    expect(zwei.state.status).toBe("verbunden");
    welt.aufraeumen();
});

test("Wählen aus anderem Tab", async () => {
    const welt = umgebung();
    const eins = welt.dienst("tab-1");
    eins.start();
    await welt.uhr.vorspulen(0);
    const zwei = welt.dienst("tab-2");
    zwei.start();
    await welt.uhr.vorspulen(0);
    expect(zwei.state.status).toBe("anderer_tab");
    await zwei.waehlen("+49 170 1234567", { resModel: "res.partner", resId: 5 });
    await welt.uhr.vorspulen(0);
    const agent = welt.fabrik.agenten[0];
    expect(agent.invites.length).toBe(1);
    expect(agent.invites[0].ziel).toBe("00491701234567");
    // gespeichert wird die Nummer wie eingegeben, mit Datensatz
    const beginnen = welt.orm.letzter("wb_voip_beginnen");
    expect(beginnen.args).toEqual([
        "ausgehend",
        "+49 170 1234567",
        agent.invites[0].callId,
        "res.partner",
        5,
    ]);
    expect(eins.state.anruf.richtung).toBe("ausgehend");
    welt.aufraeumen();
});

test("veraltete Weiterleitung verworfen", async () => {
    const welt = umgebung();
    const eins = await verbundenerDienst(welt);
    const fremd = welt.netz.kanal();
    fremd.postMessage({
        art: "waehlen",
        ziel: "tab-1",
        nummer: "0170 1234567",
        resModel: false,
        resId: false,
        zeit: welt.uhr.jetzt() - 3000,
    });
    // fremdes Ziel
    fremd.postMessage({
        art: "waehlen",
        ziel: "tab-x",
        nummer: "0170 1234567",
        resModel: false,
        resId: false,
        zeit: welt.uhr.jetzt(),
    });
    await welt.uhr.vorspulen(0);
    expect(welt.fabrik.agenten[0].invites.length).toBe(0);
    // frische Nachricht an den Halter wird ausgeführt
    fremd.postMessage({
        art: "waehlen",
        ziel: "tab-1",
        nummer: "0170 1234567",
        resModel: false,
        resId: false,
        zeit: welt.uhr.jetzt() - 1000,
    });
    await welt.uhr.vorspulen(0);
    expect(welt.fabrik.agenten[0].invites.length).toBe(1);
    expect(eins.state.anruf).not.toBe(null);
    welt.aufraeumen();
});

test("Backoff", async () => {
    const welt = umgebung();
    welt.fabrik.registerFehler = true;
    const dienst = welt.dienst("tab-1");
    const start = welt.uhr.jetzt();
    dienst.start();
    await welt.uhr.vorspulen(0);
    expect(dienst.state.status).toBe("getrennt");
    expect(dienst.state.fehler).toInclude("403");
    await welt.uhr.vorspulen(170_000);
    const abstaende = [];
    const zeiten = welt.fabrik.registrierungen.map((t) => t - start);
    for (let i = 1; i < zeiten.length; i++) {
        abstaende.push(zeiten[i] - zeiten[i - 1]);
    }
    // Abstände zwischen den Versuchen: 3 s, 10 s, 30 s, dann jede Minute
    expect(zeiten[0]).toBe(0);
    expect(abstaende).toEqual([3000, 10_000, 30_000, 60_000, 60_000]);
    // alte Agenten werden vor dem nächsten Versuch beendet
    expect(welt.fabrik.agenten.slice(0, -1).every((a) => a.gestoppt)).toBe(true);
    // „Neu verbinden“ setzt den Zähler zurück
    const vorher = welt.fabrik.anzahlRegister;
    await dienst.neuVerbinden();
    await welt.uhr.vorspulen(0);
    expect(welt.fabrik.anzahlRegister).toBe(vorher + 1);
    await welt.uhr.vorspulen(3000);
    expect(welt.fabrik.anzahlRegister).toBe(vorher + 2);
    // Erfolg setzt den Zähler ebenfalls zurück
    welt.fabrik.registerFehler = false;
    await welt.uhr.vorspulen(10_000);
    expect(dienst.state.status).toBe("verbunden");
    expect(dienst.state.fehler).toBe(null);
    welt.fabrik.letzterAgent.netzWeg();
    await welt.uhr.vorspulen(0);
    expect(dienst.state.status).toBe("getrennt");
    const nachAbbruch = welt.fabrik.anzahlRegister;
    await welt.uhr.vorspulen(2999);
    expect(welt.fabrik.anzahlRegister).toBe(nachAbbruch);
    await welt.uhr.vorspulen(1);
    expect(welt.fabrik.anzahlRegister).toBe(nachAbbruch + 1);
    expect(dienst.state.status).toBe("verbunden");
    welt.aufraeumen();
});

test("Ablehnen sendet 603", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    const sitzung = welt.fabrik.agenten[0].eingehend("+49 30 1234");
    await welt.uhr.vorspulen(0);
    expect(dienst.state.anruf.richtung).toBe("eingehend");
    expect(dienst.state.anruf.zustand).toBe("klingelt");
    expect(dienst.klaenge.klingelnAn).toBe(true);
    await dienst.ablehnen();
    await welt.uhr.vorspulen(0);
    expect(sitzung.hat("reject")).toEqual([["reject", { statusCode: 603 }]]);
    expect(dienst.klaenge.klingelnAn).toBe(false);
    expect(dienst.state.anruf).toBe(null);
    const beendet = welt.orm.letzter("wb_voip_beendet");
    expect(beendet.args[2]).toBe("603");
    welt.aufraeumen();
});

test("Ohne Lizenz keine Registrierung", async () => {
    const welt = umgebung();
    welt.orm.antworten["res.users.wb_voip_status"] = () => ({
        aktiv: true,
        lizenz_gueltig: false,
        meldung: "Lizenz fehlt oder abgelaufen",
        eingerichtet: true,
        konfiguriert: true,
    });
    welt.orm.antworten["res.users.wb_voip_zugang_lesen"] = () => {
        throw lizenzFehler();
    };
    const dienst = welt.dienst("tab-1");
    dienst.start();
    await welt.uhr.vorspulen(0);
    expect(dienst.state.status).toBe("ohne_lizenz");
    expect(dienst.state.fehler).toBe("Lizenz fehlt oder abgelaufen");
    expect(welt.fabrik.anzahlRegister).toBe(0);
    const rpcVorher = welt.orm.aufrufe.length;
    await welt.uhr.vorspulen(120_000);
    expect(welt.fabrik.anzahlRegister).toBe(0);
    expect(welt.orm.aufrufe.length).toBe(rpcVorher);
    expect(welt.uhr.anzahlTimer).toBe(0);
    welt.aufraeumen();
});

test("UserError beim Zugang lesen: getrennt mit Meldung und Wiederholung", async () => {
    const welt = umgebung();
    welt.orm.antworten["res.users.wb_voip_zugang_lesen"] = () => {
        throw userError("SIP-Passwort nicht lesbar, bitte neu eingeben.");
    };
    const dienst = welt.dienst("tab-1");
    dienst.start();
    await welt.uhr.vorspulen(0);
    // Lizenz und Konfiguration bestimmt nur wb_voip_status
    expect(dienst.state.status).toBe("getrennt");
    expect(dienst.state.fehler).toBe("SIP-Passwort nicht lesbar, bitte neu eingeben.");
    expect(welt.fabrik.anzahlRegister).toBe(0);
    expect(welt.orm.zaehle("wb_voip_zugang_lesen")).toBe(1);
    await welt.uhr.vorspulen(3000);
    expect(welt.orm.zaehle("wb_voip_zugang_lesen")).toBe(2);
    welt.orm.antworten["res.users.wb_voip_zugang_lesen"] = () => ({ ...ZUGANG_PROD });
    await welt.uhr.vorspulen(10_000);
    expect(dienst.state.status).toBe("verbunden");
    welt.aufraeumen();
});

test("Schlüssel fehlt: nicht konfiguriert statt ohne Lizenz, kein Takt", async () => {
    const welt = umgebung();
    welt.orm.antworten["res.users.wb_voip_status"] = () => ({
        aktiv: true,
        lizenz_gueltig: false,
        meldung: "WB VoIP: Schlüssel WB_VOIP_FERNET_KEY fehlt auf dem Server.",
        eingerichtet: true,
        konfiguriert: false,
    });
    const dienst = welt.dienst("tab-1");
    dienst.start();
    await welt.uhr.vorspulen(120_000);
    expect(dienst.state.status).toBe("nicht_konfiguriert");
    expect(dienst.state.fehler).toBe(
        "WB VoIP: Schlüssel WB_VOIP_FERNET_KEY fehlt auf dem Server."
    );
    expect(welt.orm.zaehle("wb_voip_status")).toBe(1);
    expect(welt.orm.zaehle("wb_voip_zugang_lesen")).toBe(0);
    expect(welt.uhr.anzahlTimer).toBe(0);
    welt.aufraeumen();
});

test("kein VoIP-Benutzer: aus, kein Zugang", async () => {
    const welt = umgebung();
    welt.orm.antworten["res.users.wb_voip_status"] = () => ({ aktiv: false });
    const dienst = welt.dienst("tab-1");
    dienst.start();
    await welt.uhr.vorspulen(120_000);
    expect(dienst.state.status).toBe("aus");
    expect(welt.orm.zaehle("wb_voip_zugang_lesen")).toBe(0);
    expect(welt.uhr.anzahlTimer).toBe(0);
    welt.aufraeumen();
});

test("nicht eingerichtet: kein Zugang, kein Takt", async () => {
    const welt = umgebung();
    welt.orm.antworten["res.users.wb_voip_status"] = () => ({
        aktiv: true,
        lizenz_gueltig: true,
        meldung: "",
        eingerichtet: false,
        konfiguriert: true,
    });
    const dienst = welt.dienst("tab-1");
    dienst.start();
    await welt.uhr.vorspulen(120_000);
    expect(dienst.state.status).toBe("aus");
    expect(welt.orm.zaehle("wb_voip_zugang_lesen")).toBe(0);
    expect(welt.fabrik.anzahlRegister).toBe(0);
    welt.aufraeumen();
});

test("Beenden meldet Server", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    const ereignisse = [];
    dienst.bus.addEventListener("anruf-beendet", (ev) => ereignisse.push(ev.detail));
    await dienst.waehlen("0170 1234567");
    await welt.uhr.vorspulen(0);
    const sitzung = welt.fabrik.agenten[0].invites[0];
    sitzung.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    expect(dienst.state.anruf.zustand).toBe("laeuft");
    expect(welt.orm.zaehle("wb_voip_angenommen")).toBe(1);
    const anrufId = dienst.state.anruf.id;
    expect(anrufId).toBe(10);
    await dienst.auflegen();
    await welt.uhr.vorspulen(0);
    expect(sitzung.hat("bye").length).toBe(1);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(1);
    expect(welt.orm.letzter("wb_voip_beendet").args[0]).toEqual([10]);
    expect(ereignisse).toEqual([{ anrufId: 10, protokollId: 77, eigenerTab: true }]);
    expect(dienst.state.anruf).toBe(null);
    welt.aufraeumen();
});

test("Gegenstelle legt auf: genau einmal beendet", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    const ereignisse = [];
    dienst.bus.addEventListener("anruf-beendet", (ev) => ereignisse.push(ev.detail));
    await dienst.waehlen("0170 1234567");
    const sitzung = welt.fabrik.agenten[0].invites[0];
    sitzung.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    sitzung.gegenstelleLegtAuf("bye");
    sitzung.gegenstelleLegtAuf("bye");
    await welt.uhr.vorspulen(0);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(1);
    expect(ereignisse.length).toBe(1);
    welt.aufraeumen();
});

test("Lebenszeichen alle 60 s während läuft", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    await dienst.waehlen("0170 1234567");
    const sitzung = welt.fabrik.agenten[0].invites[0];
    await welt.uhr.vorspulen(30_000);
    expect(welt.orm.zaehle("wb_voip_lebenszeichen")).toBe(0);
    sitzung.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    expect(welt.orm.zaehle("wb_voip_lebenszeichen")).toBe(1);
    await welt.uhr.vorspulen(60_000);
    expect(welt.orm.zaehle("wb_voip_lebenszeichen")).toBe(2);
    await welt.uhr.vorspulen(60_000);
    expect(welt.orm.zaehle("wb_voip_lebenszeichen")).toBe(3);
    await dienst.auflegen();
    await welt.uhr.vorspulen(180_000);
    expect(welt.orm.zaehle("wb_voip_lebenszeichen")).toBe(3);
    welt.aufraeumen();
});

test("Freizeichen erst bei 180, nicht bei Early Media", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    await dienst.waehlen("0170 1234567");
    const sitzung = welt.fabrik.agenten[0].invites[0];
    await welt.uhr.vorspulen(0);
    expect(dienst.klaenge.freizeichenAn).toBe(false);
    sitzung.fortschritt(100);
    expect(dienst.klaenge.freizeichenAn).toBe(false);
    sitzung.fortschritt(183, true);
    expect(dienst.klaenge.freizeichenAn).toBe(false);
    sitzung.fortschritt(180, false);
    expect(dienst.klaenge.freizeichenAn).toBe(true);
    expect(dienst.state.anruf.zustand).toBe("klingelt");
    sitzung.fortschritt(183, true);
    expect(dienst.klaenge.freizeichenAn).toBe(false);
    sitzung.fortschritt(180, false);
    expect(dienst.klaenge.freizeichenAn).toBe(true);
    sitzung.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    expect(dienst.klaenge.freizeichenAn).toBe(false);
    welt.aufraeumen();
});

test("Auflegen vor Annahme sendet CANCEL", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    await dienst.waehlen("0170 1234567");
    const sitzung = welt.fabrik.agenten[0].invites[0];
    sitzung.fortschritt(180);
    await welt.uhr.vorspulen(0);
    await dienst.auflegen();
    await welt.uhr.vorspulen(0);
    expect(sitzung.hat("cancel").length).toBe(1);
    expect(sitzung.hat("bye").length).toBe(0);
    expect(dienst.klaenge.freizeichenAn).toBe(false);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(1);
    welt.aufraeumen();
});

test("Annehmen, Stumm, Halten, DTMF, Weiterleiten", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    const sitzung = welt.fabrik.agenten[0].eingehend("0170 1234567");
    await welt.uhr.vorspulen(0);
    await dienst.annehmen();
    await welt.uhr.vorspulen(0);
    expect(sitzung.hat("accept").length).toBe(1);
    expect(dienst.state.anruf.zustand).toBe("laeuft");
    expect(dienst.klaenge.klingelnAn).toBe(false);
    dienst.stumm(true);
    expect(sitzung.hat("mute")).toEqual([["mute", true]]);
    expect(dienst.state.anruf.stumm).toBe(true);
    await dienst.halten(true);
    expect(dienst.state.anruf.gehalten).toBe(true);
    await dienst.halten(false);
    expect(sitzung.hat("hold").length).toBe(1);
    expect(sitzung.hat("unhold").length).toBe(1);
    expect(dienst.state.anruf.gehalten).toBe(false);
    dienst.dtmf("5");
    dienst.dtmf("x");
    expect(sitzung.hat("dtmf")).toEqual([["dtmf", "5"]]);
    await dienst.weiterleiten("+49 170 999");
    expect(sitzung.hat("refer")).toEqual([["refer", "0049170999"]]);
    welt.aufraeumen();
});

test("zweiter eingehender Anruf während Gespräch: 486", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    welt.fabrik.agenten[0].eingehend("0170 1");
    await welt.uhr.vorspulen(0);
    const zweite = welt.fabrik.agenten[0].eingehend("0170 2");
    await welt.uhr.vorspulen(0);
    expect(zweite.hat("reject")).toEqual([["reject", { statusCode: 486 }]]);
    expect(dienst.state.anruf.nummer).toBe("0170 1");
    welt.aufraeumen();
});

test("Modus test: simulierter Agent ohne SIP.js", async () => {
    const welt = umgebung();
    welt.orm.antworten["res.users.wb_voip_zugang_lesen"] = () => ({
        anlage_id: 1,
        modus: "test",
        sip_domain: false,
        ws_url: false,
        sip_benutzer: "100",
        sip_passwort: false,
        land: "DE",
    });
    let geladen = 0;
    const fabrik = erzeugeAgentFabrik({
        bibliothekLaden: async () => {
            geladen++;
        },
    });
    const dienst = welt.dienst("tab-1", { agentFabrik: fabrik });
    dienst.start();
    await welt.uhr.vorspulen(0);
    expect(dienst.state.status).toBe("verbunden");
    expect(geladen).toBe(0);
    expect(dienst._agent instanceof TestAgent).toBe(true);
    await dienst.waehlen("0170 1234567");
    await welt.uhr.vorspulen(0);
    expect(dienst.state.anruf.zustand).toBe("klingelt");
    await welt.uhr.vorspulen(1000);
    expect(dienst.state.anruf.zustand).toBe("laeuft");
    await dienst.auflegen();
    await welt.uhr.vorspulen(0);
    expect(dienst.state.anruf).toBe(null);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(1);
    welt.aufraeumen();
});

test("Modus prod lädt das SIP-Bündel", async () => {
    let geladen = 0;
    const fabrik = erzeugeAgentFabrik({
        bibliothekLaden: async () => {
            geladen++;
        },
        sipBibliothek: () => null,
    });
    // ohne SIP-Global schlägt die Anlage fehl, aber das Bündel wurde angefordert
    await expect(fabrik({ modus: "prod" }, { uhr: new FakeUhr() })).rejects.toThrow();
    expect(geladen).toBe(1);
});

test("pagehide: sofort abmelden und Sperre freigeben", async () => {
    const welt = umgebung();
    const fenster = new EventTarget();
    const eins = welt.dienst("tab-1", { fenster });
    const zwei = welt.dienst("tab-2");
    eins.start();
    zwei.start();
    await welt.uhr.vorspulen(0);
    const agent = welt.fabrik.agenten[0];
    expect(agent.abmeldungen).toBe(0);
    fenster.dispatchEvent(new Event("pagehide"));
    // synchron, bevor die Seite verschwindet
    expect(agent.abmeldungen).toBe(1);
    expect(agent.gestoppt).toBe(true);
    await welt.uhr.vorspulen(0);
    expect(zwei.state.status).toBe("verbunden");
    expect(welt.fabrik.anzahlRegister).toBe(2);
    welt.aufraeumen();
});

test("Weiterleitung mit NaN als Zeit verworfen", async () => {
    const welt = umgebung();
    const eins = await verbundenerDienst(welt);
    for (const zeit of [NaN, Infinity, -Infinity, "1"]) {
        eins._nachricht({
            art: "waehlen",
            ziel: "tab-1",
            nummer: "0170 1234567",
            resModel: false,
            resId: false,
            zeit,
        });
    }
    await welt.uhr.vorspulen(0);
    expect(welt.fabrik.agenten[0].invites.length).toBe(0);
    expect(eins.state.anruf).toBe(null);
    welt.aufraeumen();
});

test("Neu verbinden während eines Anrufs wird abgelehnt", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    await dienst.waehlen("0170 1234567");
    const sitzung = welt.fabrik.agenten[0].invites[0];
    sitzung.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    const anruf = dienst.state.anruf;
    await dienst.neuVerbinden();
    await welt.uhr.vorspulen(0);
    expect(dienst.state.fehler).toBe("Bitte erst auflegen.");
    expect(dienst.state.status).toBe("verbunden");
    expect(dienst.state.anruf).toBe(anruf);
    expect(dienst.state.anruf.zustand).toBe("laeuft");
    expect(welt.fabrik.anzahlRegister).toBe(1);
    expect(welt.fabrik.agenten[0].gestoppt).toBe(false);
    expect(sitzung.hat("bye").length).toBe(0);
    welt.aufraeumen();
});

test("wb_voip_beendet scheitert: drei neue Versuche nach 2, 5 und 10 s", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    const ereignisse = [];
    dienst.bus.addEventListener("anruf-beendet", (ev) => ereignisse.push(ev.detail));
    welt.orm.antworten["wb.voip.anruf.wb_voip_beendet"] = () => {
        throw new Error("Netz weg");
    };
    await dienst.waehlen("0170 1234567");
    await welt.uhr.vorspulen(0);
    await dienst.auflegen();
    await welt.uhr.vorspulen(0);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(1);
    await welt.uhr.vorspulen(1999);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(1);
    await welt.uhr.vorspulen(1);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(2);
    await welt.uhr.vorspulen(5000);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(3);
    await welt.uhr.vorspulen(10_000);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(4);
    await welt.uhr.vorspulen(600_000);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(4);
    expect(ereignisse.length).toBe(0);
    expect(dienst.state.fehler).toBe("Netz weg");
    welt.aufraeumen();
});

test("wb_voip_beendet klappt beim zweiten Versuch", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    const ereignisse = [];
    dienst.bus.addEventListener("anruf-beendet", (ev) => ereignisse.push(ev.detail));
    let fehlschlaege = 1;
    welt.orm.antworten["wb.voip.anruf.wb_voip_beendet"] = (args) => {
        if (fehlschlaege-- > 0) {
            throw new Error("Netz weg");
        }
        return { id: args[0][0], zustand: "beendet", protokoll_id: 77 };
    };
    await dienst.waehlen("0170 1234567");
    await welt.uhr.vorspulen(0);
    await dienst.auflegen();
    await welt.uhr.vorspulen(2000);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(2);
    expect(ereignisse).toEqual([{ anrufId: 10, protokollId: 77, eigenerTab: true }]);
    await welt.uhr.vorspulen(60_000);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(2);
    welt.aufraeumen();
});

test("wb_voip_beendet mit UserError: kein neuer Versuch", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    welt.orm.antworten["wb.voip.anruf.wb_voip_beendet"] = () => {
        throw userError("Ungültige Angabe: Notiz.");
    };
    await dienst.waehlen("0170 1234567");
    await welt.uhr.vorspulen(0);
    await dienst.auflegen();
    await welt.uhr.vorspulen(60_000);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(1);
    expect(dienst.state.fehler).toBe("Ungültige Angabe: Notiz.");
    welt.aufraeumen();
});

test("Notiz wird auf 10 000 Zeichen gekürzt", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    await dienst.waehlen("0170 1234567");
    await welt.uhr.vorspulen(0);
    dienst.state.anruf.notiz = "x".repeat(10_050);
    await dienst.auflegen();
    await welt.uhr.vorspulen(0);
    expect(welt.orm.letzter("wb_voip_beendet").args[1].length).toBe(10_000);
    welt.aufraeumen();
});

// --- Abschlussprüfung Phase 1 ---------------------------------------------------------

/** Minimales SIP.js-Double: merkt sich die Optionen des UserAgent. */
function sipAttrappe() {
    const sip = { optionen: null };
    sip.UserAgent = class {
        constructor(optionen) {
            sip.optionen = optionen;
        }
        static makeURI(text) {
            return { text };
        }
    };
    return sip;
}

test("ICE: ohne Eintrag an der Anlage kein STUN-Server", () => {
    const sip = sipAttrappe();
    new SipJsAgent({ ...ZUGANG_PROD }, sip);
    const konfig = sip.optionen.sessionDescriptionHandlerFactoryOptions.peerConnectionConfiguration;
    expect(konfig.iceServers).toEqual([]);
    new SipJsAgent({ ...ZUGANG_PROD, ice_server_urls: [] }, sip);
    expect(
        sip.optionen.sessionDescriptionHandlerFactoryOptions.peerConnectionConfiguration.iceServers
    ).toEqual([]);
});

test("ICE: Server aus der Anlage", () => {
    const sip = sipAttrappe();
    const urls = ["stun:stun.example.org:3478", "stun:stun2.example.org"];
    new SipJsAgent({ ...ZUGANG_PROD, ice_server_urls: urls }, sip);
    expect(
        sip.optionen.sessionDescriptionHandlerFactoryOptions.peerConnectionConfiguration.iceServers
    ).toEqual([{ urls }]);
});

test("ICE: TURN-Einträge werden nicht an iceServers übergeben", () => {
    const sip = sipAttrappe();
    const urls = ["turn:turn.example.org:3478", "stun:stun.example.org:3478", "turns:t.example.org"];
    new SipJsAgent({ ...ZUGANG_PROD, ice_server_urls: urls }, sip);
    expect(
        sip.optionen.sessionDescriptionHandlerFactoryOptions.peerConnectionConfiguration.iceServers
    ).toEqual([{ urls: ["stun:stun.example.org:3478"] }]);
    new SipJsAgent({ ...ZUGANG_PROD, ice_server_urls: ["turns:t.example.org"] }, sip);
    expect(
        sip.optionen.sessionDescriptionHandlerFactoryOptions.peerConnectionConfiguration.iceServers
    ).toEqual([]);
});

test("bfcache: pagehide setzt aus, pageshow mit persisted verbindet neu", async () => {
    const welt = umgebung();
    const fenster = new EventTarget();
    const dienst = welt.dienst("tab-1", { fenster, kanalFabrik: () => welt.netz.kanal() });
    dienst.start();
    await welt.uhr.vorspulen(0);
    expect(dienst.state.status).toBe("verbunden");
    fenster.dispatchEvent(new Event("pagehide"));
    expect(dienst.state.status).toBe("aus");
    expect(welt.fabrik.agenten[0].gestoppt).toBe(true);
    // pageshow ohne persisted (normales Laden) startet nichts
    fenster.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: false }));
    await welt.uhr.vorspulen(0);
    expect(welt.fabrik.anzahlRegister).toBe(1);
    fenster.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    await welt.uhr.vorspulen(0);
    expect(dienst.state.status).toBe("verbunden");
    expect(welt.fabrik.anzahlRegister).toBe(2);
    // neuer Kanal: der zweite Tab erfährt den Halter
    const zwei = welt.dienst("tab-2");
    zwei.start();
    await welt.uhr.vorspulen(0);
    expect(zwei.state.status).toBe("anderer_tab");
    expect(await zwei._halterErfragen()).toBe("tab-1");
    welt.aufraeumen();
});

test("bfcache: wartender Neben-Tab übernimmt nach pageshow nur einmal", async () => {
    const welt = umgebung();
    const fenster = new EventTarget();
    const eins = welt.dienst("tab-1");
    const zwei = welt.dienst("tab-2", { fenster, kanalFabrik: () => welt.netz.kanal() });
    eins.start();
    zwei.start();
    await welt.uhr.vorspulen(0);
    expect(zwei.state.status).toBe("anderer_tab");
    fenster.dispatchEvent(new Event("pagehide"));
    fenster.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    await welt.uhr.vorspulen(0);
    expect(zwei.state.status).toBe("anderer_tab");
    eins.stop();
    await welt.uhr.vorspulen(0);
    expect(zwei.state.status).toBe("verbunden");
    // eine Registrierung von Tab 1, genau eine von Tab 2
    expect(welt.fabrik.anzahlRegister).toBe(2);
    welt.aufraeumen();
});

test("pagehide während eines Anrufs meldet das Ende", async () => {
    const welt = umgebung();
    const fenster = new EventTarget();
    const dienst = welt.dienst("tab-1", { fenster, kanalFabrik: () => welt.netz.kanal() });
    dienst.start();
    await welt.uhr.vorspulen(0);
    await dienst.waehlen("0170 1234567");
    welt.fabrik.agenten[0].invites[0].gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    fenster.dispatchEvent(new Event("pagehide"));
    await welt.uhr.vorspulen(0);
    expect(dienst.state.anruf).toBe(null);
    expect(welt.orm.zaehle("wb_voip_beendet")).toBe(1);
    expect(welt.orm.letzter("wb_voip_beendet").args[3]).toBe(true);
    fenster.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    await welt.uhr.vorspulen(0);
    expect(await dienst.waehlen("0170 7654321")).toBe(true);
    welt.aufraeumen();
});

test("beendet meldet angenommen mit (Wettlauf mit wb_voip_angenommen)", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    // wb_voip_angenommen kommt nie an
    welt.orm.antworten["wb.voip.anruf.wb_voip_angenommen"] = () => new Promise(() => {});
    await dienst.waehlen("0170 1234567");
    await welt.uhr.vorspulen(0);
    const sitzung = welt.fabrik.agenten[0].invites[0];
    sitzung.gegenstelleNimmtAn();
    await welt.uhr.vorspulen(0);
    sitzung.gegenstelleLegtAuf("bye");
    await welt.uhr.vorspulen(0);
    expect(welt.orm.letzter("wb_voip_beendet").args[3]).toBe(true);
    // nicht angenommen: false
    await dienst.waehlen("0170 1234567");
    await welt.uhr.vorspulen(0);
    await dienst.auflegen();
    await welt.uhr.vorspulen(0);
    expect(welt.orm.letzter("wb_voip_beendet").args[3]).toBe(false);
    welt.aufraeumen();
});

test("Anrufer-Kennung: höchstens 64 Zeichen, ohne Steuerzeichen", async () => {
    expect(anruferKennung("0170\u0000 12\u001b34\u007f5\u202e6")).toBe("0170 123456");
    expect(anruferKennung("1".repeat(100))).toBe("1".repeat(64));
    expect(anruferKennung("\u0001\u0002")).toBe("anonymous");
    expect(anruferKennung(null)).toBe("anonymous");
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    welt.fabrik.agenten[0].eingehend("\u0007" + "9".repeat(80) + "\n");
    await welt.uhr.vorspulen(0);
    const nummer = welt.orm.letzter("wb_voip_beginnen").args[1];
    expect(nummer).toBe("9".repeat(64));
    expect(dienst.state.anruf.nummer).toBe("9".repeat(64));
    welt.aufraeumen();
});

test("Lizenz läuft während der Registrierung ab: ohne_lizenz und abmelden", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    welt.orm.antworten["wb.voip.anruf.wb_voip_beginnen"] = () => {
        throw lizenzFehler();
    };
    welt.orm.antworten["res.users.wb_voip_status"] = () => ({
        aktiv: true,
        lizenz_gueltig: false,
        meldung: "Lizenz fehlt oder abgelaufen",
        eingerichtet: true,
        konfiguriert: true,
    });
    const agent = welt.fabrik.agenten[0];
    const sitzung = agent.eingehend("0170 1234567");
    await welt.uhr.vorspulen(0);
    expect(welt.orm.zaehle("wb_voip_status")).toBe(2);
    expect(dienst.state.status).toBe("ohne_lizenz");
    expect(dienst.state.fehler).toBe("Lizenz fehlt oder abgelaufen");
    expect(agent.gestoppt).toBe(true);
    expect(agent.abmeldungen).toBe(1);
    expect(dienst.state.anruf).toBe(null);
    expect(sitzung.hat("reject").length).toBe(1);
    await welt.uhr.vorspulen(120_000);
    expect(welt.fabrik.anzahlRegister).toBe(1);
    welt.aufraeumen();
});

test("beginnen scheitert, Lizenz aber gültig: verbunden bleiben", async () => {
    const welt = umgebung();
    const dienst = await verbundenerDienst(welt);
    welt.orm.antworten["wb.voip.anruf.wb_voip_beginnen"] = () => {
        throw userError("Ungültige Angabe: Nummer.");
    };
    await dienst.waehlen("0170 1234567");
    await welt.uhr.vorspulen(0);
    expect(welt.orm.zaehle("wb_voip_status")).toBe(2);
    expect(dienst.state.status).toBe("verbunden");
    expect(welt.fabrik.agenten[0].gestoppt).toBe(false);
    welt.aufraeumen();
});
