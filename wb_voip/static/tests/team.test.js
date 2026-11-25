import { describe, expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";
import { EventBus, reactive } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    mockService,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";
import { serializeDateTime } from "@web/core/l10n/dates";
import { session } from "@web/session";

import { Softphone } from "@wb_voip/softphone/softphone";
import { TeamListe } from "@wb_voip/team/team_liste";
import { seite } from "@wb_voip/verpasst/verpasst_liste";

describe.current.tags("desktop");

defineMailModels();

const { DateTime } = luxon;

function kollege(user_id, name, nebenstelle, status, seit = false) {
    return { user_id, name, nebenstelle, status, seit };
}

const FREI = kollege(2, "Anna Frei", "102", "frei");
const KLINGELT = kollege(3, "Karl Klingelt", "103", "klingelt");
const TELEFONIERT = kollege(4, "Tina Telefon", "104", "telefoniert");
const ABWESEND = kollege(5, "Abel Abgemeldet", "105", "abgemeldet");

/** Eigener Anruf in der Form des Telefon-Dienstes. */
const ANRUF = (zustand) => ({
    id: 1,
    richtung: "ausgehend",
    nummer: "+49 30 1",
    zustand,
    partner: false,
    stumm: false,
    gehalten: false,
    notiz: "",
    beginn: Date.now(),
    annahme: zustand === "laeuft" ? Date.now() : false,
});

/** Dienst-Attrappe des Telefons mit protokollierten Aufträgen. */
function telefon(status = "verbunden", anruf = null, extra = {}) {
    const aufrufe = [];
    return {
        state: reactive({ status, anruf, fehler: null }),
        bus: new EventBus(),
        uhr: { jetzt: () => Date.now() },
        aufrufe,
        waehlen: async (nummer, optionen) => {
            aufrufe.push(["waehlen", nummer, optionen]);
            return true;
        },
        weiterleiten: async (nummer) => {
            aufrufe.push(["weiterleiten", nummer]);
            return true;
        },
        ...extra,
    };
}

function vorbereiten({
    kollegen = [FREI, KLINGELT, TELEFONIERT, ABWESEND],
    praefix = "**",
    status = "verbunden",
    anruf = null,
    extra = {},
} = {}) {
    patchWithCleanup(session, { wb_voip_aktiv: true });
    const zustand = { kollegen, praefix };
    const abos = [];
    mockService("bus_service", {
        subscribe(typ, rueckruf) {
            abos.push([typ, rueckruf]);
        },
    });
    const dienst = telefon(status, anruf, extra);
    mockService("wb_voip_telefon", () => dienst);
    onRpc("res.users", "wb_voip_team", () => {
        expect.step("team");
        if (zustand.fehler) {
            throw new Error("kaputt");
        }
        if (zustand.aus) {
            return { aktiv: false, heranholen_praefix: zustand.praefix, kollegen: [] };
        }
        return { heranholen_praefix: zustand.praefix, kollegen: zustand.kollegen };
    });
    // Zähler des Verpasst-Reiters (Softphone lädt ihn beim Start)
    onRpc("wb.voip.anruf", "wb_voip_verpasst_zaehler", () => 0);
    const busSenden = () =>
        abos.filter(([typ]) => typ === "wb_voip/team").forEach(([, rueckruf]) => rueckruf({}));
    return { zustand, dienst, abos, busSenden };
}

async function reiterOeffnen() {
    await click(".o_wb_voip_reiter button:contains(Team)");
    await animationFrame();
}

function zeile(name) {
    return [...document.querySelectorAll(".o_wb_voip_team_zeile")].find((z) =>
        z.textContent.includes(name)
    );
}

test("Reiter „Team“ steht nach „Verpasst“ und lädt beim Öffnen", async () => {
    vorbereiten();
    await mountWithCleanup(Softphone);
    await animationFrame();
    const titel = [...document.querySelectorAll(".o_wb_voip_reiter button")].map((b) =>
        b.textContent.trim()
    );
    const verpasst = titel.findIndex((t) => t.startsWith("Verpasst"));
    expect(titel[verpasst + 1]).toMatch(/^Team/);
    expect.verifySteps([]);
    await reiterOeffnen();
    expect.verifySteps(["team"]);
    expect(".o_wb_voip_team_zeile").toHaveCount(4);
});

test("Darstellung je Status: Text, Farbe und Dauer", async () => {
    const seit = serializeDateTime(DateTime.now().minus({ seconds: 125 }));
    vorbereiten({
        kollegen: [FREI, KLINGELT, { ...TELEFONIERT, seit }, ABWESEND],
    });
    await mountWithCleanup(TeamListe);
    await animationFrame();
    expect.verifySteps(["team"]);
    // Name, Nebenstelle und Status als Text (nicht nur Farbe)
    expect(zeile("Anna Frei")).toHaveText(/Anna Frei/);
    expect(zeile("Anna Frei")).toHaveText(/102/);
    expect(zeile("Anna Frei").querySelector(".o_wb_voip_team_status")).toHaveText("Frei");
    expect(zeile("Anna Frei").querySelector(".o_wb_voip_team_punkt")).toHaveClass("text-success");
    expect(zeile("Karl Klingelt").querySelector(".o_wb_voip_team_status")).toHaveText("Klingelt");
    expect(zeile("Karl Klingelt").querySelector(".o_wb_voip_team_punkt")).toHaveClass(
        "o_wb_voip_team_blinkt"
    );
    expect(zeile("Anna Frei").querySelector(".o_wb_voip_team_punkt")).not.toHaveClass(
        "o_wb_voip_team_blinkt"
    );
    expect(zeile("Tina Telefon").querySelector(".o_wb_voip_team_status")).toHaveText(
        /Telefoniert 2:0[5-6]/
    );
    expect(zeile("Tina Telefon").querySelector(".o_wb_voip_team_punkt")).toHaveClass("text-danger");
    expect(zeile("Abel Abgemeldet").querySelector(".o_wb_voip_team_status")).toHaveText(
        "Abgemeldet"
    );
    expect(zeile("Abel Abgemeldet").querySelector(".o_wb_voip_team_punkt")).toHaveClass(
        "text-muted"
    );
});

test("Name und Nebenstelle sind sichtbar getrennt (Liste und Auswahl)", async () => {
    vorbereiten();
    await mountWithCleanup(TeamListe);
    await animationFrame();
    expect.verifySteps(["team"]);
    const titel = (name) => zeile(name).querySelector(".fw-bold").textContent;
    expect(titel("Anna Frei")).not.toMatch(/Frei\s*102/);
    expect(titel("Anna Frei")).toMatch(/Anna Frei\s*·\s*102/);
    expect(zeile("Anna Frei").querySelector(".o_wb_voip_team_nebenstelle")).toHaveText("102");
});

test("Auch in der Auswahl steht ein Trenner zwischen Name und Nebenstelle", async () => {
    vorbereiten();
    await mountWithCleanup(TeamListe, { props: { auswahl: true, onFertig: () => {} } });
    await animationFrame();
    expect.verifySteps(["team"]);
    expect(zeile("Karl Klingelt").querySelector(".fw-bold").textContent).toMatch(
        /Karl Klingelt\s*·\s*103/
    );
});

test("Die Dauer zählt im Browser weiter", async () => {
    const seit = serializeDateTime(DateTime.now().minus({ seconds: 10 }));
    vorbereiten({ kollegen: [{ ...TELEFONIERT, seit }] });
    await mountWithCleanup(TeamListe);
    await animationFrame();
    expect.verifySteps(["team"]);
    expect(".o_wb_voip_team_status").toHaveText(/Telefoniert 0:1\d/);
    await advanceTime(60_000);
    await animationFrame();
    // das 60-s-Neuladen ist ein zweiter Abruf
    expect.verifySteps(["team"]);
    expect(".o_wb_voip_team_status").toHaveText(/Telefoniert 1:[1-2]\d/);
});

test("Knopf-Sichtbarkeit: Anrufen, Heranholen, Übergeben, Rückfrage, Konferenz", async () => {
    const w = vorbereiten();
    await mountWithCleanup(TeamListe);
    await animationFrame();
    expect.verifySteps(["team"]);
    // Anrufen: frei, klingelt und telefoniert, nicht bei abgemeldet
    expect(".o_wb_voip_team_anrufen").toHaveCount(3);
    expect(zeile("Abel Abgemeldet").querySelector(".o_wb_voip_team_anrufen")).toBe(null);
    // Heranholen nur bei klingelt (Präfix gesetzt)
    expect(".o_wb_voip_team_heranholen").toHaveCount(1);
    expect(zeile("Karl Klingelt").querySelector(".o_wb_voip_team_heranholen")).not.toBe(null);
    // ohne eigenes laufendes Gespräch keine Übergabe-Knöpfe
    expect(".o_wb_voip_team_uebergeben").toHaveCount(0);
    expect(".o_wb_voip_team_rueckfrage").toHaveCount(0);
    expect(".o_wb_voip_team_konferenz").toHaveCount(0);
    // Telefon nicht bereit: gar keine Wählknöpfe
    w.dienst.state.status = "getrennt";
    await animationFrame();
    expect(".o_wb_voip_team_anrufen").toHaveCount(0);
    expect(".o_wb_voip_team_heranholen").toHaveCount(0);
});

test("Ohne eingestellten Präfix kein Heranholen", async () => {
    vorbereiten({ praefix: false });
    await mountWithCleanup(TeamListe);
    await animationFrame();
    expect.verifySteps(["team"]);
    expect(".o_wb_voip_team_anrufen").toHaveCount(3);
    expect(".o_wb_voip_team_heranholen").toHaveCount(0);
});

test("Während eines laufenden Gesprächs: Übergeben; Rückfrage und Konferenz nur wenn der Dienst sie kann", async () => {
    const w = vorbereiten({ anruf: ANRUF("klingelt") });
    await mountWithCleanup(TeamListe);
    await animationFrame();
    expect.verifySteps(["team"]);
    // Anruf klingelt erst: noch kein laufendes Gespräch
    expect(".o_wb_voip_team_uebergeben").toHaveCount(0);
    w.dienst.state.anruf.zustand = "laeuft";
    await animationFrame();
    // nicht für abgemeldete Kollegen
    expect(".o_wb_voip_team_uebergeben").toHaveCount(3);
    expect(".o_wb_voip_team_rueckfrage").toHaveCount(0);
    expect(".o_wb_voip_team_konferenz").toHaveCount(0);
    await click(zeile("Anna Frei").querySelector(".o_wb_voip_team_uebergeben"));
    expect(w.dienst.aufrufe).toEqual([["weiterleiten", "102"]]);
});

test("Rückfrage und Konferenz erscheinen, sobald der Dienst sie kennt", async () => {
    const w = vorbereiten({
        anruf: ANRUF("laeuft"),
        extra: {
            rueckfrage: async (nummer) => {
                expect.step(`rueckfrage ${nummer}`);
                return true;
            },
            konferenz: async (nummer) => {
                expect.step(`konferenz ${nummer}`);
                return true;
            },
        },
    });
    await mountWithCleanup(TeamListe);
    await animationFrame();
    expect.verifySteps(["team"]);
    expect(".o_wb_voip_team_rueckfrage").toHaveCount(3);
    expect(".o_wb_voip_team_konferenz").toHaveCount(3);
    await click(zeile("Anna Frei").querySelector(".o_wb_voip_team_rueckfrage"));
    await click(zeile("Karl Klingelt").querySelector(".o_wb_voip_team_konferenz"));
    expect.verifySteps(["rueckfrage 102", "konferenz 103"]);
    expect(w.dienst.aufrufe).toEqual([]);
});

test("Anrufen wählt die Nebenstelle, Heranholen Präfix und Nebenstelle", async () => {
    const w = vorbereiten();
    await mountWithCleanup(TeamListe);
    await animationFrame();
    expect.verifySteps(["team"]);
    await click(zeile("Anna Frei").querySelector(".o_wb_voip_team_anrufen"));
    await click(zeile("Karl Klingelt").querySelector(".o_wb_voip_team_heranholen"));
    expect(w.dienst.aufrufe).toEqual([
        ["waehlen", "102", { resModel: false, resId: false }],
        ["waehlen", "**103", { resModel: false, resId: false }],
    ]);
    // Knöpfe tragen einen Namen für Bildschirmleser
    expect(".o_wb_voip_team_anrufen:eq(0)").toHaveAttribute("aria-label");
    expect(".o_wb_voip_team_heranholen").toHaveAttribute("aria-label");
});

test("Bus-Meldung lädt nur im sichtbaren Tab; Sichtbarwerden lädt nach", async () => {
    const w = vorbereiten({ kollegen: [] });
    await mountWithCleanup(TeamListe);
    await animationFrame();
    expect.verifySteps(["team"]);
    expect(".o_wb_voip_team_zeile").toHaveCount(0);
    w.zustand.kollegen = [FREI];
    w.busSenden();
    await animationFrame();
    expect.verifySteps(["team"]);
    expect(".o_wb_voip_team_zeile").toHaveCount(1);
    // versteckter Tab: kein Abruf
    patchWithCleanup(seite, { sichtbar: () => false });
    w.zustand.kollegen = [FREI, KLINGELT];
    w.busSenden();
    await animationFrame();
    expect.verifySteps([]);
    expect(".o_wb_voip_team_zeile").toHaveCount(1);
    patchWithCleanup(seite, { sichtbar: () => true });
    document.dispatchEvent(new Event("visibilitychange"));
    await animationFrame();
    expect.verifySteps(["team"]);
    expect(".o_wb_voip_team_zeile").toHaveCount(2);
});

test("Offener, sichtbarer Reiter lädt alle 60 s neu; versteckt oder geschlossen nicht", async () => {
    vorbereiten({ kollegen: [FREI] });
    onRpc("wb.voip.anruf", "wb_voip_verpasst_liste", () => []);
    onRpc("wb.voip.anruf", "wb_voip_verpasst_gesehen", () => 0);
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["team"]);
    await advanceTime(60_000);
    await animationFrame();
    expect.verifySteps(["team"]);
    patchWithCleanup(seite, { sichtbar: () => false });
    await advanceTime(60_000);
    await animationFrame();
    expect.verifySteps([]);
    patchWithCleanup(seite, { sichtbar: () => true });
    // anderen Reiter öffnen: der Team-Reiter ist weg, kein Takt mehr
    await click(".o_wb_voip_reiter button:contains(Verpasst)");
    await animationFrame();
    await advanceTime(120_000);
    await animationFrame();
    expect.verifySteps([]);
});

test("Ladefehler zeigt eine Meldung", async () => {
    const w = vorbereiten();
    w.zustand.fehler = true;
    await mountWithCleanup(TeamListe);
    await animationFrame();
    expect.verifySteps(["team"]);
    expect(".o_wb_voip_team .alert").toHaveCount(1);
});

test("Team-Übersicht ausgeschaltet: Hinweis statt Liste", async () => {
    const w = vorbereiten();
    w.zustand.aus = true;
    await mountWithCleanup(TeamListe);
    await animationFrame();
    expect.verifySteps(["team"]);
    expect(".o_wb_voip_team_aus").toHaveText("Die Team-Übersicht ist ausgeschaltet.");
    expect(".o_wb_voip_team_zeile").toHaveCount(0);
    expect(".o_wb_voip_team").not.toHaveText(/Keine Kollegen/);
});
