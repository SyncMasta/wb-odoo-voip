import { describe, expect, test } from "@odoo/hoot";
import { click, queryAll, queryOne } from "@odoo/hoot-dom";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";
import { EventBus, reactive } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    makeMockEnv,
    mockService,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";
import { registry } from "@web/core/registry";
import { session } from "@web/session";

import { Softphone } from "@wb_voip/softphone/softphone";
import { WbVoipSystray } from "@wb_voip/softphone/systray";
import { WbVoipTastenleiste } from "@wb_voip_ansitel/tasten/tastenleiste";
import { tastenSichtbar } from "@wb_voip_ansitel/tasten/tasten_dienst";

describe.current.tags("desktop");

defineMailModels();

function zustand(extra = {}) {
    return {
        verfuegbar: true,
        fehler: false,
        tasten: [
            { id: 1, name: "→ Handy", icon: "fa-mobile", art: "weiterleitung", aktiv: false },
            { id: 2, name: "Nicht stören", icon: "fa-bell-slash", art: "anrufschutz", aktiv: true },
            { id: 3, name: "AB", icon: false, art: "anrufbeantworter", aktiv: null },
        ],
        fremde_weiterleitung: false,
        umgeleitet: false,
        ...extra,
    };
}

/**
 * Bereitet Session, Bus, Telefon-Attrappe und Server vor. ``antworten`` ersetzt je Methode
 * die Antwort (Wert oder Funktion); jeder Aufruf steht in ``w.aufrufe``.
 */
function vorbereiten({ ort = "softphone", aktiv = true, halter = false, antworten = {} } = {}) {
    patchWithCleanup(session, { wb_voip_aktiv: aktiv, wb_voip_ansitel_tastenleiste: ort });
    const abos = [];
    mockService("bus_service", {
        subscribe(typ, rueckruf) {
            abos.push([typ, rueckruf]);
        },
    });
    const telefon = {
        state: reactive({ status: "verbunden", halter, anruf: null, gehaltener: null, fehler: null }),
        bus: new EventBus(),
    };
    mockService("wb_voip_telefon", () => telefon);
    const w = {
        aufrufe: [],
        telefon,
        abos,
        antworten: { wb_voip_tasten_zustand: zustand(), ...antworten },
        busSenden: (nutzlast) =>
            abos.filter(([typ]) => typ === "wb_voip/tasten").forEach(([, r]) => r(nutzlast)),
    };
    for (const methode of [
        "wb_voip_tasten_zustand",
        "wb_voip_taste_schalten",
        "wb_voip_weiterleitung_aus",
    ]) {
        onRpc("res.users", methode, ({ args }) => {
            w.aufrufe.push([methode, ...args]);
            const antwort = w.antworten[methode] ?? w.antworten.wb_voip_tasten_zustand;
            return typeof antwort === "function" ? antwort(...args) : antwort;
        });
    }
    return w;
}

async function dienstStarten() {
    const env = await makeMockEnv();
    await animationFrame();
    return env.services.wb_voip_ansitel_tasten;
}

const namen = () => queryAll(".o_wb_voip_taste").map((t) => t.dataset.id);

// --- Sichtbarkeit nach Einstellung ------------------------------------------------------

test("tastenSichtbar: Einstellung softphone, rand, beide; nur bei verfügbarer Leiste", () => {
    const z = zustand();
    expect(tastenSichtbar(z, "softphone", "softphone")).toBe(true);
    expect(tastenSichtbar(z, "softphone", "rand")).toBe(false);
    expect(tastenSichtbar(z, "rand", "rand")).toBe(true);
    expect(tastenSichtbar(z, "rand", "softphone")).toBe(false);
    expect(tastenSichtbar(z, "beide", "softphone")).toBe(true);
    expect(tastenSichtbar(z, "beide", "rand")).toBe(true);
    expect(tastenSichtbar(zustand({ verfuegbar: false }), "beide", "rand")).toBe(false);
    expect(tastenSichtbar(null, "beide", "rand")).toBe(false);
    expect(tastenSichtbar(z, false, "softphone")).toBe(false);
});

// --- Dienst: Laden, Bus, Takt -------------------------------------------------------------

test("Dienst lädt beim Start einmal und speichert den Zustand", async () => {
    const w = vorbereiten();
    const dienst = await dienstStarten();
    expect(w.aufrufe).toEqual([["wb_voip_tasten_zustand"]]);
    expect(dienst.state.tasten.map((t) => t.id)).toEqual([1, 2, 3]);
    expect(dienst.state.verfuegbar).toBe(true);
});

test("ohne aktives Telefon oder ohne Einstellung: kein Abruf, kein Bus-Abo", async () => {
    const w = vorbereiten({ aktiv: false });
    await dienstStarten();
    expect(w.aufrufe).toEqual([]);
    expect(w.abos.filter(([typ]) => typ === "wb_voip/tasten").length).toBe(0);
});

test("Bus-Meldung speichert nur und fragt nie zurück", async () => {
    const w = vorbereiten({ halter: true });
    const dienst = await dienstStarten();
    expect(w.aufrufe.length).toBe(1);
    w.busSenden(zustand({ umgeleitet: true, fremde_weiterleitung: "+49 30 1" }));
    await animationFrame();
    expect(dienst.state.umgeleitet).toBe(true);
    expect(dienst.state.fremde_weiterleitung).toBe("+49 30 1");
    expect(w.aufrufe.length).toBe(1);
});

test("Bus-Nutzlast: nur bekannte Felder werden übernommen (keine Zugangsdaten)", async () => {
    const w = vorbereiten();
    const dienst = await dienstStarten();
    w.busSenden({
        ...zustand(),
        token: "geheim",
        tasten: [{ id: 5, name: "X", icon: false, art: "weiterleitung", aktiv: true, token: "geheim" }],
    });
    await animationFrame();
    expect(JSON.stringify(dienst.state)).not.toMatch(/geheim/);
    expect(dienst.state.tasten.map((t) => t.id)).toEqual([5]);
    // Unsinn wird verworfen, der alte Zustand bleibt
    w.busSenden("kaputt");
    w.busSenden(null);
    expect(dienst.state.tasten.map((t) => t.id)).toEqual([5]);
});

test("Takt: nur im Telefon-Tab alle 60 s, nicht im Nebentab", async () => {
    const w = vorbereiten({ halter: false });
    await dienstStarten();
    expect(w.aufrufe.length).toBe(1);
    await advanceTime(180_000);
    expect(w.aufrufe.length).toBe(1);
    // Übernahme der Sperre: sofort neu lesen, danach jede Minute
    w.telefon.state.halter = true;
    await animationFrame();
    expect(w.aufrufe.length).toBe(2);
    await advanceTime(60_000);
    expect(w.aufrufe.length).toBe(3);
    await advanceTime(60_000);
    expect(w.aufrufe.length).toBe(4);
    // Sperre abgegeben (Tab schließt/wechselt): Takt stoppt
    w.telefon.state.halter = false;
    await animationFrame();
    await advanceTime(300_000);
    expect(w.aufrufe.length).toBe(4);
});

test("Ohne Schnelltasten (nicht verfügbar, kein Fehler) fragt der Takt nur alle 10 Minuten", async () => {
    const w = vorbereiten({
        halter: true,
        antworten: {
            wb_voip_tasten_zustand: zustand({ verfuegbar: false, tasten: [] }),
        },
    });
    await dienstStarten();
    const start = w.aufrufe.length;
    await advanceTime(300_000);
    expect(w.aufrufe.length).toBe(start);
    await advanceTime(300_000);
    expect(w.aufrufe.length).toBe(start + 1);
});

test("Start: der Halter-Wechsel gleich nach dem ersten Lesen liest nicht ein zweites Mal", async () => {
    const w = vorbereiten({ halter: false });
    await dienstStarten();
    expect(w.aufrufe.length).toBe(1);
    w.telefon.state.halter = true;
    await animationFrame();
    expect(w.aufrufe.length).toBe(1);
    // der Takt läuft danach im Halter-Tab weiter
    await advanceTime(60_000);
    expect(w.aufrufe.length).toBe(2);
});

test("Wird der Tab später Halter, liest er neu", async () => {
    const w = vorbereiten({ halter: false });
    await dienstStarten();
    await advanceTime(30_000);
    w.telefon.state.halter = true;
    await animationFrame();
    expect(w.aufrufe.length).toBe(2);
});

test("Lesefehler: Zustand bleibt, Dienst läuft weiter", async () => {
    const w = vorbereiten({
        halter: true,
        antworten: {
            wb_voip_tasten_zustand: () => {
                throw new Error("kaputt");
            },
        },
    });
    const dienst = await dienstStarten();
    expect(dienst.state.verfuegbar).toBe(false);
    w.antworten.wb_voip_tasten_zustand = zustand();
    await advanceTime(60_000);
    expect(dienst.state.verfuegbar).toBe(true);
});

// --- Dienst: Schalten ---------------------------------------------------------------------

test("Schalten schickt den Wunschzustand, bei grauer Taste null", async () => {
    const w = vorbereiten({
        antworten: { wb_voip_taste_schalten: zustand({ umgeleitet: true }) },
    });
    const dienst = await dienstStarten();
    await dienst.schalten(1, false);
    await dienst.schalten(2, true);
    await dienst.schalten(3, null);
    expect(w.aufrufe.filter(([m]) => m === "wb_voip_taste_schalten")).toEqual([
        ["wb_voip_taste_schalten", 1, false],
        ["wb_voip_taste_schalten", 2, true],
        ["wb_voip_taste_schalten", 3, null],
    ]);
    expect(dienst.state.umgeleitet).toBe(true);
});

test("Tastenklick: Wunschzustand ist das Gegenteil des gezeigten Zustands", async () => {
    const w = vorbereiten();
    const dienst = await dienstStarten();
    expect(dienst.wunschzustand({ aktiv: true })).toBe(false);
    expect(dienst.wunschzustand({ aktiv: false })).toBe(true);
    expect(dienst.wunschzustand({ aktiv: null })).toBe(null);
    expect(w.aufrufe.length).toBe(1);
});

test("Während des Schaltens sind weitere Klicks gesperrt", async () => {
    let freigabe;
    const gate = new Promise((r) => (freigabe = r));
    const w = vorbereiten({
        antworten: {
            wb_voip_taste_schalten: async () => {
                await gate;
                return zustand({ umgeleitet: true });
            },
        },
    });
    const dienst = await dienstStarten();
    const erste = dienst.schalten(1, true);
    expect(dienst.state.schaltet).toBe(true);
    await dienst.schalten(2, false);
    freigabe();
    await erste;
    expect(dienst.state.schaltet).toBe(false);
    expect(w.aufrufe.filter(([m]) => m === "wb_voip_taste_schalten").length).toBe(1);
});

test("Fehler beim Schalten: Tasten bleiben sichtbar, Hinweis gesetzt, Sperre gelöst", async () => {
    const w = vorbereiten({
        antworten: {
            wb_voip_taste_schalten: () => {
                throw new Error("kaputt");
            },
        },
    });
    const dienst = await dienstStarten();
    await dienst.schalten(1, true);
    expect(dienst.state.schaltet).toBe(false);
    expect(dienst.state.fehler).toBe("Quick keys are currently unavailable.");
    expect(dienst.state.tasten.length).toBe(3);
    expect(w.aufrufe.length).toBe(2);
});

test("Eine ältere Leseantwort überholt die neuere Schaltantwort nicht", async () => {
    let freigabe;
    const gate = new Promise((r) => (freigabe = r));
    let erster = true;
    const w = vorbereiten({
        halter: true,
        antworten: {
            wb_voip_tasten_zustand: async () => {
                if (erster) {
                    erster = false;
                    return zustand();
                }
                await gate;
                return zustand({ umgeleitet: false, tasten: [] });
            },
            wb_voip_taste_schalten: zustand({ umgeleitet: true }),
        },
    });
    const dienst = await dienstStarten();
    await advanceTime(60_000); // Takt startet ein Lesen, das hängt
    await dienst.schalten(1, true);
    expect(dienst.state.umgeleitet).toBe(true);
    freigabe();
    await animationFrame();
    expect(dienst.state.umgeleitet).toBe(true);
    expect(dienst.state.tasten.length).toBe(3);
    expect(w.aufrufe.length).toBeGreaterThan(2);
});

test("Fremde Weiterleitung ausschalten ruft wb_voip_weiterleitung_aus", async () => {
    const w = vorbereiten({
        antworten: { wb_voip_weiterleitung_aus: zustand({ fremde_weiterleitung: false }) },
    });
    const dienst = await dienstStarten();
    await dienst.weiterleitungAus();
    expect(w.aufrufe.at(-1)).toEqual(["wb_voip_weiterleitung_aus"]);
});

// --- Leiste ---------------------------------------------------------------------------------

test("Leiste im Softphone zeigt die Tasten mit Zustand, ARIA und Namen", async () => {
    vorbereiten();
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "softphone" } });
    await animationFrame();
    expect(namen()).toEqual(["1", "2", "3"]);
    expect(".o_wb_voip_taste[data-id='1']").toHaveAttribute("aria-pressed", "false");
    expect(".o_wb_voip_taste[data-id='1']").not.toHaveClass("o_wb_voip_taste_aktiv");
    expect(".o_wb_voip_taste[data-id='2']").toHaveAttribute("aria-pressed", "true");
    expect(".o_wb_voip_taste[data-id='2']").toHaveClass("o_wb_voip_taste_aktiv");
    expect(".o_wb_voip_taste[data-id='1']").toHaveText("→ Handy");
    // ohne Symbol: Standardsymbol
    expect(".o_wb_voip_taste[data-id='3'] i").toHaveClass("fa-circle-o");
});

test("Graue Taste: gestrichelt, aria-pressed false mit aria-describedby, Hinweistext sichtbar und im Titel", async () => {
    const hinweis = "Voicemail needs a personal Ansitel token or approval – please ask an administrator.";
    vorbereiten({ antworten: { wb_voip_tasten_zustand: zustand({ fehler: hinweis }) } });
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "softphone" } });
    await animationFrame();
    const grau = queryOne(".o_wb_voip_taste[data-id='3']");
    expect(grau).toHaveClass("o_wb_voip_taste_unbekannt");
    expect(grau).toHaveAttribute("aria-pressed", "false");
    expect(grau.title).toInclude(hinweis);
    expect(".o_wb_voip_tasten_fehler").toHaveText(hinweis);
    expect(grau.getAttribute("aria-describedby")).toBe(
        queryOne(".o_wb_voip_tasten_fehler").id
    );
    // die schaltbaren Tasten tragen den Hinweis nicht im Titel
    expect(queryOne(".o_wb_voip_taste[data-id='1']").title).not.toInclude(hinweis);
});

test("Klick schickt Wunschzustand: aus → true, an → false, grau → null", async () => {
    const w = vorbereiten();
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "softphone" } });
    await animationFrame();
    for (const id of ["1", "2", "3"]) {
        await click(`.o_wb_voip_taste[data-id='${id}']`);
        await animationFrame();
    }
    expect(w.aufrufe.filter(([m]) => m === "wb_voip_taste_schalten")).toEqual([
        ["wb_voip_taste_schalten", 1, true],
        ["wb_voip_taste_schalten", 2, false],
        ["wb_voip_taste_schalten", 3, null],
    ]);
});

test("Tasten sind gesperrt, solange der Server schaltet", async () => {
    let freigabe;
    const gate = new Promise((r) => (freigabe = r));
    vorbereiten({
        antworten: {
            wb_voip_taste_schalten: async () => {
                await gate;
                return zustand();
            },
        },
    });
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "softphone" } });
    await animationFrame();
    await click(".o_wb_voip_taste[data-id='1']");
    await animationFrame();
    expect(".o_wb_voip_taste[data-id='2']").toHaveAttribute("aria-disabled", "true");
    expect(".o_wb_voip_taste[data-id='2']").not.toHaveAttribute("disabled");
    freigabe();
    await animationFrame();
    expect(".o_wb_voip_taste[data-id='2']").not.toHaveAttribute("aria-disabled");
});

test("Beim Schalten bleibt der Fokus erhalten und weitere Klicks werden ignoriert", async () => {
    let freigabe;
    const gate = new Promise((r) => (freigabe = r));
    const w = vorbereiten({
        antworten: {
            wb_voip_taste_schalten: async () => {
                await gate;
                return zustand();
            },
        },
    });
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "softphone" } });
    await animationFrame();
    queryOne(".o_wb_voip_taste[data-id='1']").focus();
    await click(".o_wb_voip_taste[data-id='1']");
    await animationFrame();
    expect(document.activeElement).toBe(queryOne(".o_wb_voip_taste[data-id='1']"));
    await click(".o_wb_voip_taste[data-id='2']");
    await animationFrame();
    freigabe();
    await animationFrame();
    expect(w.aufrufe.filter(([m]) => m === "wb_voip_taste_schalten").length).toBe(1);
});

test("Aktive Taste hat zusätzlich zur Farbe einen Haken", async () => {
    vorbereiten();
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "softphone" } });
    await animationFrame();
    expect(".o_wb_voip_taste[data-id='2'] .o_wb_voip_taste_haken").toHaveCount(1);
    expect(".o_wb_voip_taste[data-id='1'] .o_wb_voip_taste_haken").toHaveCount(0);
    expect(".o_wb_voip_taste[data-id='3'] .o_wb_voip_taste_haken").toHaveCount(0);
});

test("Bei „beide“ trägt die Rand-Leiste einen eigenen Namen", async () => {
    vorbereiten({ ort: "beide" });
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "rand" } });
    await animationFrame();
    expect(".o_wb_voip_tasten_rand [role='group']:first").toHaveAttribute(
        "aria-label",
        "Quick keys on the edge"
    );
});

test("Im Softphone heißt die Gruppe „Schnelltasten“", async () => {
    vorbereiten({ ort: "beide" });
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "softphone" } });
    await animationFrame();
    expect(".o_wb_voip_tasten_softphone [role='group']:first").toHaveAttribute(
        "aria-label",
        "Quick keys"
    );
});

test("Fremde Weiterleitung: Zeile mit Knopf „aus“", async () => {
    const w = vorbereiten({
        antworten: {
            wb_voip_tasten_zustand: zustand({ fremde_weiterleitung: "+49 30 1234" }),
            wb_voip_weiterleitung_aus: zustand(),
        },
    });
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "softphone" } });
    await animationFrame();
    expect(".o_wb_voip_tasten_fremd").toHaveText(/Forward to \+49 30 1234/);
    expect(".o_wb_voip_tasten_fremd_aus").toHaveAttribute(
        "aria-label",
        "Turn off forwarding to +49 30 1234"
    );
    await click(".o_wb_voip_tasten_fremd_aus");
    await animationFrame();
    expect(w.aufrufe.at(-1)).toEqual(["wb_voip_weiterleitung_aus"]);
    expect(".o_wb_voip_tasten_fremd").toHaveCount(0);
});

test("Einstellung rand: im Softphone keine Leiste", async () => {
    vorbereiten({
        ort: "rand",
        antworten: { wb_voip_tasten_zustand: zustand({ fehler: "Ansitel is unreachable." }) },
    });
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "softphone" } });
    await animationFrame();
    expect(".o_wb_voip_tasten_softphone").toHaveCount(0);
});

test("Rand-Leiste: Einstellung rand zeigt sie, Fehler als Symbol mit Titel", async () => {
    vorbereiten({
        ort: "rand",
        antworten: { wb_voip_tasten_zustand: zustand({ fehler: "Ansitel is unreachable." }) },
    });
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "rand" } });
    await animationFrame();
    // die Testumgebung hängt die Hauptkomponente selbst ein; beide zeigen dasselbe
    expect(".o_wb_voip_tasten_rand").toHaveCount(2);
    expect(".o_wb_voip_tasten_fehler_icon:first").toHaveAttribute(
        "title",
        "Ansitel is unreachable."
    );
    expect(".o_wb_voip_tasten_fehler").toHaveCount(0);
    expect(document.body).toHaveClass("o_wb_voip_tasten_rand_platz");
});

test("Einstellung softphone: Rand-Leiste bleibt leer", async () => {
    vorbereiten({ ort: "softphone" });
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "rand" } });
    await animationFrame();
    expect(".o_wb_voip_tasten").toHaveCount(0);
    expect(document.body).not.toHaveClass("o_wb_voip_tasten_rand_platz");
});

test("Nicht verfügbar (keine Nebenstelle): keine Leiste", async () => {
    vorbereiten({
        ort: "beide",
        antworten: { wb_voip_tasten_zustand: zustand({ verfuegbar: false, tasten: [] }) },
    });
    await mountWithCleanup(WbVoipTastenleiste, { props: { lage: "softphone" } });
    await animationFrame();
    expect(".o_wb_voip_tasten").toHaveCount(0);
});

test("Rand-Leiste ist als Hauptkomponente und die Leiste im Softphone eingehängt", async () => {
    expect(registry.category("main_components").contains("wb_voip_ansitel.TastenleisteRand")).toBe(
        true
    );
    vorbereiten({ ort: "beide" });
    await mountWithCleanup(Softphone);
    await animationFrame();
    expect(".o_wb_voip_softphone .o_wb_voip_tasten_softphone").toHaveCount(1);
});

// --- Systray-Punkt ------------------------------------------------------------------------------

test("Punkt am Systray-Symbol bei umgeleitet, mit Hinweis im Titel", async () => {
    const w = vorbereiten({
        antworten: { wb_voip_tasten_zustand: zustand({ umgeleitet: true }) },
    });
    await mountWithCleanup(WbVoipSystray);
    await animationFrame();
    expect(".o_wb_voip_ansitel_umgeleitet_punkt").toHaveCount(1);
    expect(".o_wb_voip_systray_knopf").toHaveAttribute("title", /calls are being forwarded/);
    w.busSenden(zustand({ umgeleitet: false }));
    await animationFrame();
    expect(".o_wb_voip_ansitel_umgeleitet_punkt").toHaveCount(0);
    expect(".o_wb_voip_systray_knopf").not.toHaveAttribute("title", /forwarded/);
});
