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

describe.current.tags("desktop");

defineMailModels();

// Zeilen in der Form, die der Server liefert (wb_voip_verpasst_liste mit wb_voip_ansitel).
// Rufnummern aus dem Bereich der Bundesnetzagentur für fiktive Nutzung (030 23125 000–999).
const GERAET = {
    schluessel: "+493023125011",
    anruf_id: 11,
    anzahl: 1,
    art: "anderswo",
    nummer: "030 23125 011",
    partner: false,
    letzter_beginn: "2026-11-25 08:00:00",
    kollege: false,
    geraet: "Tisch A",
    geraet_dauer_sek: 240,
    bemerkung_moeglich: true,
    bemerkung: false,
};

const KOLLEGE = {
    ...GERAET,
    schluessel: "+493023125012",
    anruf_id: 12,
    nummer: "030 23125 012",
    geraet: false,
    geraet_dauer_sek: false,
    bemerkung_moeglich: false,
    bemerkung: false,
    kollege: { name: "Hans Kollege", dauer_sek: 90, notiz: "", geraet: "Tisch B" },
};

// Kollege hat mit seinem Softphone angenommen: kein Gerät beim Kollegen (Kernform)
const KOLLEGE_SOFTPHONE = {
    ...KOLLEGE,
    schluessel: "+493023125013",
    anruf_id: 13,
    nummer: "030 23125 013",
    kollege: { name: "Hans Kollege", dauer_sek: 30, notiz: "" },
};

function vorbereiten(zeilen) {
    patchWithCleanup(session, { wb_voip_aktiv: true });
    mockService("bus_service", { subscribe() {} });
    mockService("wb_voip_telefon", () => ({
        state: reactive({ status: "verbunden", anruf: null, fehler: null }),
        bus: new EventBus(),
        waehlen: async () => true,
    }));
    const zustand = { zeilen };
    onRpc("wb.voip.anruf", "wb_voip_verpasst_zaehler", () => 0);
    onRpc("wb.voip.anruf", "wb_voip_verpasst_liste", () => {
        expect.step("liste");
        return zustand.zeilen;
    });
    onRpc("wb.voip.anruf", "wb_voip_verpasst_gesehen", () => 0);
    return zustand;
}

async function reiterOeffnen() {
    await click(".o_wb_voip_reiter button:contains(Missed)");
    await animationFrame();
}

test("Angenommen auf einem Gerät: Hinweis mit Gerät und Dauer", async () => {
    vorbereiten([GERAET, KOLLEGE]);
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["liste"]);
    const hinweise = [...document.querySelectorAll(".o_wb_voip_anderswo")].map((e) =>
        e.textContent.trim()
    );
    expect(hinweise).toEqual([
        "↪ angenommen auf Tisch A (4:00)",
        "↪ angenommen von Hans Kollege · Tisch B (1:30)",
    ]);
    // „Bemerkung“ nur beim eigenen Gerät, nicht beim Kollegen
    expect(".o_wb_voip_ansitel_bemerkung").toHaveCount(1);
});

test("Kollege geht vor: auch wenn die Zeile ein Gerät hätte, steht der Kollege da", async () => {
    vorbereiten([{ ...KOLLEGE, geraet: "Tisch B", geraet_dauer_sek: 90 }, KOLLEGE_SOFTPHONE]);
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["liste"]);
    const hinweise = [...document.querySelectorAll(".o_wb_voip_anderswo")].map((e) =>
        e.textContent.trim()
    );
    expect(hinweise).toEqual([
        "↪ angenommen von Hans Kollege · Tisch B (1:30)",
        "↪ angenommen von Hans Kollege (0:30)",
    ]);
});

test("Bemerkung speichern ruft den Server und lädt die Liste neu", async () => {
    const zustand = vorbereiten([GERAET]);
    onRpc("wb.voip.anruf", "wb_voip_bemerkung_speichern", ({ args }) => {
        expect.step(`bemerkung ${JSON.stringify(args)}`);
        zustand.zeilen = [{ ...GERAET, bemerkung: "Angebot folgt" }];
        return true;
    });
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["liste"]);
    await click(".o_wb_voip_ansitel_bemerkung");
    await animationFrame();
    expect(".o_wb_voip_ansitel_bemerkung_eingabe").toHaveAttribute("maxlength", "10000");
    await click(".o_wb_voip_ansitel_bemerkung_eingabe");
    await edit("Angebot folgt");
    await click(".o_wb_voip_ansitel_bemerkung_speichern");
    await animationFrame();
    expect.verifySteps(['bemerkung [[11],"Angebot folgt"]', "liste"]);
    expect(".o_wb_voip_ansitel_bemerkung_eingabe").toHaveCount(0);
    expect(".o_wb_voip_ansitel_bemerkung_text").toHaveText("Angebot folgt");
});

test("Bemerkung: Fehler bleibt sichtbar, Abbrechen ruft den Server nicht", async () => {
    vorbereiten([GERAET]);
    onRpc("wb.voip.anruf", "wb_voip_bemerkung_speichern", () => {
        expect.step("bemerkung");
        throw new Error("kaputt");
    });
    await mountWithCleanup(Softphone);
    await reiterOeffnen();
    expect.verifySteps(["liste"]);
    await click(".o_wb_voip_ansitel_bemerkung");
    await animationFrame();
    await click(".o_wb_voip_ansitel_bemerkung_speichern");
    await animationFrame();
    expect.verifySteps(["bemerkung"]);
    expect(".o_wb_voip_ansitel_bemerkung_feld .alert").toHaveCount(1);
    await click(".o_wb_voip_ansitel_bemerkung_abbrechen");
    await animationFrame();
    expect(".o_wb_voip_ansitel_bemerkung_eingabe").toHaveCount(0);
    expect.verifySteps([]);
});
