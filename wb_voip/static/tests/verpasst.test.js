import { describe, expect, test } from "@odoo/hoot";
import { click, edit } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { EventBus, reactive } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    mockService,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";
import { session } from "@web/session";

import { Softphone } from "@wb_voip/softphone/softphone";
import { WbVoipSystray } from "@wb_voip/softphone/systray";
import { seite } from "@wb_voip/verpasst/verpasst_liste";

describe.current.tags("desktop");

defineMailModels();

const ZEILE = {
    schluessel: "+4917012345",
    anruf_id: 7,
    anzahl: 2,
    art: "verpasst",
    nummer: "+49 170 12345",
    partner: [1, "Erika Muster"],
    letzter_beginn: "2026-11-25 09:30:00",
    kollege: false,
};

const ANDERSWO = {
    schluessel: "+4930999",
    anruf_id: 9,
    anzahl: 1,
    art: "anderswo",
    nummer: "+49 30 999",
    partner: false,
    letzter_beginn: "2026-11-25 08:00:00",
    kollege: { name: "Hans Kollege", dauer_sek: 125, notiz: "<b>Angebot</b> folgt" },
};

/** Dienst-Attrappe des Telefons mit protokollierten Wählaufträgen. */
function telefon(status = "verbunden") {
    const aufrufe = [];
    return {
        state: reactive({ status, anruf: null, fehler: null }),
        bus: new EventBus(),
        aufrufe,
        waehlen: async (nummer, optionen) => {
            aufrufe.push(["waehlen", nummer, optionen]);
            return true;
        },
    };
}

/**
 * Bereitet Server, Bus und Telefon vor. ``zaehler`` wird nach jedem „gesehen“ auf 0 gesetzt.
 */
function vorbereiten({ zeilen = [ZEILE], zaehler = 2, status = "verbunden" } = {}) {
    patchWithCleanup(session, { wb_voip_aktiv: true });
    const zustand = { zeilen, zaehler };
    const abos = [];
    mockService("bus_service", {
        subscribe(typ, rueckruf) {
            abos.push([typ, rueckruf]);
        },
    });
    const dienst = telefon(status);
    mockService("wb_voip_telefon", () => dienst);
    onRpc("wb.voip.anruf", "wb_voip_verpasst_zaehler", () => {
        expect.step("zaehler");
        return zustand.zaehler;
    });
    onRpc("wb.voip.anruf", "wb_voip_verpasst_liste", () => {
        expect.step("liste");
        return zustand.zeilen;
    });
    onRpc("wb.voip.anruf", "wb_voip_verpasst_gesehen", () => {
        expect.step("gesehen");
        zustand.zaehler = 0;
        return 0;
    });
    const busSenden = () =>
        abos.filter(([typ]) => typ === "wb_voip/verpasst").forEach(([, rueckruf]) => rueckruf({}));
    return { zustand, dienst, abos, busSenden };
}

async function reiterOeffnen() {
    await click(".o_wb_voip_reiter button:contains(Verpasst)");
    await animationFrame();
}

test("Plakette am Systray-Symbol nur bei Zähler über 0", async () => {
    const w = vorbereiten({ zaehler: 3 });
    await mountWithCleanup(WbVoipSystray);
    await animationFrame();
    expect.verifySteps(["zaehler"]);
    expect(".o_wb_voip_plakette").toHaveCount(1);
    expect(".o_wb_voip_plakette").toHaveText("3");
    w.zustand.zaehler = 0;
    w.busSenden();
    await animationFrame();
    expect.verifySteps(["zaehler"]);
    expect(".o_wb_voip_plakette").toHaveCount(0);
});

test("Reiter „Verpasst“ steht nach „Anrufe“ und trägt die Plakette", async () => {
    vorbereiten({ zaehler: 2 });
    await mountWithCleanup(Softphone);
    await animationFrame();
    const titel = [...document.querySelectorAll(".o_wb_voip_reiter button")].map((b) =>
        b.textContent.trim()
    );
    const anrufe = titel.findIndex((t) => t.startsWith("Anrufe"));
    expect(titel[anrufe + 1]).toMatch(/^Verpasst/);
    expect(".o_wb_voip_reiter .o_wb_voip_plakette").toHaveText("2");
    expect.verifySteps(["zaehler"]);
});

test("Öffnen des Reiters lädt die Liste und markiert als gesehen", async () => {
    vorbereiten({ zaehler: 2 });
    await mountWithCleanup(Softphone);
    await animationFrame();
    expect.verifySteps(["zaehler"]);
    await reiterOeffnen();
    expect.verifySteps(["liste", "gesehen"]);
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").toHaveCount(1);
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").toHaveText(/Erika Muster/);
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").toHaveText(/×2/);
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").toHaveText(/25\.11\.2026|11\/25\/2026/);
    await animationFrame();
    expect(".o_wb_voip_reiter .o_wb_voip_plakette").toHaveCount(0);
});

test("Leere Liste zeigt einen Hinweis", async () => {
    vorbereiten({ zeilen: [], zaehler: 0 });
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["zaehler", "liste", "gesehen"]);
    expect(".o_wb_voip_verpasstliste").toHaveText(/Keine offenen verpassten Anrufe/);
});

test("Erledigen: Text eingeben, speichern, Zeile verschwindet", async () => {
    vorbereiten();
    onRpc("wb.voip.anruf", "wb_voip_verpasst_erledigen", ({ args }) => {
        expect.step(`erledigen ${JSON.stringify(args)}`);
        return false;
    });
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["zaehler", "liste", "gesehen"]);
    expect(".o_wb_voip_erledigen_text").toHaveCount(0);
    await click(".o_wb_voip_erledigen");
    await animationFrame();
    expect(".o_wb_voip_erledigen_text").toHaveCount(1);
    expect(".o_wb_voip_erledigen_text").toHaveAttribute("maxlength", "2000");
    expect(".o_wb_voip_erledigen_text").toHaveAttribute("aria-label");
    await click(".o_wb_voip_erledigen_text");
    await edit("Zurückgerufen, Termin vereinbart");
    await click(".o_wb_voip_erledigen_speichern");
    await animationFrame();
    expect.verifySteps(['erledigen [[7],"Zurückgerufen, Termin vereinbart"]']);
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").toHaveCount(0);
});

test("Erledigen: Abbrechen ruft den Server nicht", async () => {
    vorbereiten();
    onRpc("wb.voip.anruf", "wb_voip_verpasst_erledigen", () => {
        expect.step("erledigen");
        return false;
    });
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["zaehler", "liste", "gesehen"]);
    await click(".o_wb_voip_erledigen");
    await animationFrame();
    await click(".o_wb_voip_erledigen_abbrechen");
    await animationFrame();
    expect(".o_wb_voip_erledigen_text").toHaveCount(0);
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").toHaveCount(1);
    expect.verifySteps([]);
});

test("Erledigen: Rest der Gruppe ersetzt die Zeile", async () => {
    vorbereiten();
    onRpc("wb.voip.anruf", "wb_voip_verpasst_erledigen", () => ({
        ...ZEILE,
        anzahl: 1,
    }));
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["zaehler", "liste", "gesehen"]);
    await click(".o_wb_voip_erledigen");
    await animationFrame();
    await click(".o_wb_voip_erledigen_speichern");
    await animationFrame();
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").toHaveCount(1);
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").not.toHaveText(/×/);
    expect(".o_wb_voip_erledigen_text").toHaveCount(0);
});

test("Jetzt anrufen wählt mit Kontakt; nur wählbar und bei bereitem Telefon", async () => {
    const w = vorbereiten({ zeilen: [ZEILE, { ...ANDERSWO, nummer: "anonymous" }] });
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["zaehler", "liste", "gesehen"]);
    // „anonymous“ ist nicht wählbar: nur eine Zeile hat den Knopf
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").toHaveCount(2);
    expect(".o_wb_voip_jetzt_anrufen").toHaveCount(1);
    await click(".o_wb_voip_jetzt_anrufen");
    expect(w.dienst.aufrufe).toEqual([
        ["waehlen", "+49 170 12345", { resModel: "res.partner", resId: 1 }],
    ]);
    // Telefon nicht bereit: kein Knopf
    w.dienst.state.status = "getrennt";
    await animationFrame();
    expect(".o_wb_voip_jetzt_anrufen").toHaveCount(0);
});

test("Kollege hat angenommen: Hinweis und Notiz als Text", async () => {
    vorbereiten({ zeilen: [ZEILE, ANDERSWO] });
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["zaehler", "liste", "gesehen"]);
    expect(".o_wb_voip_anderswo").toHaveCount(1);
    expect(".o_wb_voip_anderswo").toHaveText("↪ angenommen von Hans Kollege (2:05)");
    // 📋 nur bei anderswo mit Notiz
    expect(".o_wb_voip_kollegen_notiz_knopf").toHaveCount(1);
    expect(".o_wb_voip_kollegen_notiz").toHaveCount(0);
    await click(".o_wb_voip_kollegen_notiz_knopf");
    await animationFrame();
    expect(".o_wb_voip_kollegen_notiz").toHaveText("<b>Angebot</b> folgt");
    expect(".o_wb_voip_kollegen_notiz b").toHaveCount(0);
    await click(".o_wb_voip_kollegen_notiz_knopf");
    await animationFrame();
    expect(".o_wb_voip_kollegen_notiz").toHaveCount(0);
});

test("Bus-Meldung lädt Zähler und bei offenem, sichtbarem Reiter die Liste", async () => {
    const w = vorbereiten({ zaehler: 0, zeilen: [] });
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["zaehler", "liste", "gesehen"]);
    w.zustand.zeilen = [ZEILE];
    w.zustand.zaehler = 1;
    w.busSenden();
    await animationFrame();
    // Zähler, Liste neu, dann als gesehen markiert (Reiter ist offen und sichtbar)
    expect.verifySteps(["zaehler", "liste", "gesehen"]);
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").toHaveCount(1);
});

test("Bus-Meldung bei verstecktem Tab lädt nur den Zähler; Sichtbarwerden lädt die Liste", async () => {
    const w = vorbereiten({ zaehler: 0, zeilen: [] });
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["zaehler", "liste", "gesehen"]);
    patchWithCleanup(seite, { sichtbar: () => false });
    w.zustand.zeilen = [ZEILE];
    w.zustand.zaehler = 1;
    w.busSenden();
    await animationFrame();
    expect.verifySteps(["zaehler"]);
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").toHaveCount(0);
    patchWithCleanup(seite, { sichtbar: () => true });
    document.dispatchEvent(new Event("visibilitychange"));
    await animationFrame();
    expect.verifySteps(["liste", "gesehen"]);
    expect(".o_wb_voip_verpasstliste .o_wb_voip_eintrag").toHaveCount(1);
});

test("Ohne VoIP-Benutzer kein Abruf und keine Plakette", async () => {
    patchWithCleanup(session, { wb_voip_aktiv: false });
    mockService("wb_voip_telefon", () => telefon());
    onRpc("wb.voip.anruf", "wb_voip_verpasst_zaehler", () => {
        expect.step("zaehler");
        return 5;
    });
    await mountWithCleanup(WbVoipSystray);
    await animationFrame();
    expect.verifySteps([]);
    expect(".o_wb_voip_plakette").toHaveCount(0);
});
