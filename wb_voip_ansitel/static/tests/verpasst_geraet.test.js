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

const GERAET = {
    schluessel: "+4930111",
    anruf_id: 11,
    anzahl: 1,
    art: "anderswo",
    nummer: "+49 30 111",
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
    schluessel: "+4930222",
    anruf_id: 12,
    geraet: false,
    geraet_dauer_sek: false,
    bemerkung_moeglich: false,
    kollege: { name: "Hans Kollege", dauer_sek: 90, notiz: "", geraet: "Tisch B" },
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
    await click(".o_wb_voip_reiter button:contains(Verpasst)");
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
