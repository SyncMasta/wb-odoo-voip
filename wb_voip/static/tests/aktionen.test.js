import { describe, expect, test } from "@odoo/hoot";
import { click, waitFor } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { EventBus, reactive } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    defineModels,
    editSelectMenu,
    mockService,
    mountWithCleanup,
    patchWithCleanup,
    onRpc,
} from "@web/../tests/web_test_helpers";
import { MainComponentsContainer } from "@web/core/main_components_container";

import "@wb_voip/aktionen/popup_aktionen";
import "@wb_voip/protokoll_popup";
import { Anrufansicht } from "@wb_voip/softphone/anrufansicht";
import { Softphone } from "@wb_voip/softphone/softphone";
import { TelefonDienst } from "@wb_voip/dienste/telefon_dienst";

import { WbVoipProtokoll } from "./protokoll_modell";
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
defineModels([WbVoipProtokoll]);

function taste(id, name, orte, extra = {}) {
    return { id, name, icon: false, farbe: "secondary", art: "serveraktion", orte, ...extra };
}

// --- Protokoll-Popup ---------------------------------------------------------------------

async function popupOeffnen() {
    const telefon = { state: reactive({ status: "verbunden", anruf: null, fehler: null }), bus: new EventBus() };
    mockService("wb_voip_telefon", () => telefon);
    const aktionen = [];
    mockService("action", {
        doAction(aktion) {
            aktionen.push(aktion);
            return Promise.resolve(true);
        },
    });
    await mountWithCleanup(MainComponentsContainer);
    telefon.bus.trigger("anruf-beendet", { anrufId: 5, protokollId: 1, eigenerTab: true });
    await waitFor(".modal .o_form_view");
    return aktionen;
}

test("Popup zeigt nur die Buttons, die der Server fürs Popup liefert", async () => {
    onRpc("wb.voip.protokoll", "wb_voip_popup_aktionen", ({ args }) => {
        expect.step(`popup ${args[0][0]}`);
        return [
            taste(1, "Angebot", ["protokoll_popup"], { icon: "fa-file", farbe: "success" }),
            taste(2, "Nur Softphone", ["softphone"]),
            taste(3, "Kaputt", ["protokoll_popup"], { icon: 'fa-x" onclick="x', farbe: "<b>" }),
        ];
    });
    await popupOeffnen();
    await animationFrame();
    expect.verifySteps(["popup 1"]);
    expect(".modal .o_wb_voip_popup_aktion").toHaveCount(2);
    expect(".modal .o_wb_voip_popup_aktion:eq(0)").toHaveText("Angebot");
    expect(".modal .o_wb_voip_popup_aktion:eq(0)").toHaveClass("btn-success");
    expect(".modal .o_wb_voip_popup_aktion:eq(0) i").toHaveClass("fa-file");
    expect(".modal .o_wb_voip_popup_aktion:eq(1)").toHaveClass("btn-secondary");
    expect(".modal .o_wb_voip_popup_aktion:eq(1) i").toHaveCount(0);
});

test("Popup: Button führt wb_voip_aktion_ausfuehren auf dem Anruf des Protokolls aus", async () => {
    onRpc("wb.voip.protokoll", "wb_voip_popup_aktionen", () => [
        taste(7, "Angebot", ["protokoll_popup"]),
    ]);
    onRpc("wb.voip.anruf", "wb_voip_aktion_ausfuehren", ({ args }) => {
        expect.step(`ausfuehren ${args[0][0]} ${args[1]}`);
        return { type: "ir.actions.act_window", res_model: "sale.order", target: "current" };
    });
    const aktionen = await popupOeffnen();
    await animationFrame();
    await click(".modal .o_wb_voip_popup_aktion");
    await animationFrame();
    expect.verifySteps(["ausfuehren 5 7"]);
    expect(aktionen.at(-1).res_model).toBe("sale.order");
});

test("Popup: speichert Ergebnis und Notiz, bevor ein Button läuft", async () => {
    onRpc("wb.voip.protokoll", "wb_voip_popup_aktionen", () => [
        taste(7, "Angebot", ["protokoll_popup"]),
    ]);
    onRpc("wb.voip.protokoll", "web_save", ({ args }) => {
        expect.step(`speichern ${args[0][0]} ${args[1].ergebnis}`);
    });
    onRpc("wb.voip.anruf", "wb_voip_aktion_ausfuehren", ({ args }) => {
        expect.step(`ausfuehren ${args[0][0]} ${args[1]}`);
        return false;
    });
    await popupOeffnen();
    await animationFrame();
    await editSelectMenu(".modal .o_field_widget[name=ergebnis] input", { value: "Mailbox" });
    await click(".modal .o_wb_voip_popup_aktion");
    await animationFrame();
    expect.verifySteps(["speichern 1 mailbox", "ausfuehren 5 7"]);
});

test("Popup: ohne Buttons keine Leiste, Fehler beim Laden stört nicht", async () => {
    onRpc("wb.voip.protokoll", "wb_voip_popup_aktionen", () => {
        throw new Error("kaputt");
    });
    await popupOeffnen();
    await animationFrame();
    expect(".modal .o_wb_voip_popup_aktionen_leiste").toHaveCount(0);
    expect(".modal .o_form_view").toHaveCount(1);
});

// --- Softphone-Anrufansicht ----------------------------------------------------------------

async function softphoneMitAnruf() {
    const uhr = new FakeUhr();
    const fabrik = new FakeAgentFabrik(uhr);
    const dienst = new TelefonDienst({
        orm: new FakeOrm(),
        sperren: new FakeSperren(),
        kanal: new FakeKanalNetz().kanal(),
        uhr,
        tabId: "tab-ui",
        agentFabrik: fabrik.fabrik,
        klaenge: new FakeKlaenge(),
    });
    dienst.start();
    await uhr.vorspulen(0);
    mockService("wb_voip_telefon", () => dienst);
    mockService("action", {
        doAction(aktion) {
            return Promise.resolve(aktion);
        },
    });
    await mountWithCleanup(Softphone);
    return { dienst, uhr, fabrik };
}

/** Kontext fürs Anrufer-Fenster, das im Softphone-Test mitläuft (ohne Buttons). */
function leererKontext({ args }) {
    return { anruf: { id: args[0][0] }, kontakt: false, protokolle: [], aufgaben: [], aktionen: [] };
}

test("Softphone: nur Softphone-Buttons, höchstens 4, Rest unter „Mehr“, einmal je Anruf", async () => {
    let abrufe = 0;
    onRpc("wb.voip.anruf", "wb_voip_kontext", leererKontext);
    // leichte Abfrage nur der Buttons statt des ganzen Anrufer-Kontexts
    onRpc("wb.voip.anruf", "wb_voip_aktionen", ({ args }) => {
        abrufe++;
        expect(args[1]).toBe("softphone");
        return [
            taste(1, "Eins", ["softphone"]),
            taste(2, "Zwei", ["softphone", "anrufer_fenster"]),
            taste(3, "Nur Fenster", ["anrufer_fenster"]),
            taste(4, "Drei", ["softphone"]),
            taste(5, "Vier", ["softphone"]),
            taste(6, "Fünf", ["softphone"]),
            taste(7, "Sechs", ["softphone"]),
        ];
    });
    onRpc("wb.voip.anruf", "wb_voip_aktion_ausfuehren", ({ args }) => {
        expect.step(`ausfuehren ${args[0][0]} ${args[1]}`);
        return false;
    });
    const { dienst, uhr, fabrik } = await softphoneMitAnruf();
    // Ladevorgänge der Anrufansicht selbst (das Anrufer-Fenster fragt den Kontext zusätzlich ab)
    let ladungen = 0;
    patchWithCleanup(Anrufansicht.prototype, {
        aktionenLaden() {
            ladungen++;
            return super.aktionenLaden(...arguments);
        },
    });
    await dienst.waehlen("0170 1234567");
    await uhr.vorspulen(0);
    await animationFrame();
    expect(ladungen).toBe(1);
    fabrik.letzterAgent.invites[0].gegenstelleNimmtAn();
    await uhr.vorspulen(0);
    await animationFrame();
    await animationFrame();
    // nach der Annahme genau ein weiteres Laden (Buttons „während des Gesprächs“)
    expect(ladungen).toBe(2);
    expect(".o_wb_voip_anrufansicht .o_wb_voip_aktion").toHaveCount(4);
    expect(".o_wb_voip_anrufansicht .o_wb_voip_aktion:eq(3)").toHaveText("Vier");
    expect(".o_wb_voip_anrufansicht .o_wb_voip_aktion_mehr").toHaveCount(1);
    // zugänglicher Name = sichtbarer Text (kein abweichendes aria-label)
    expect(".o_wb_voip_anrufansicht .o_wb_voip_aktion_mehr").not.toHaveAttribute("aria-label");
    expect(".o_wb_voip_anrufansicht .o_wb_voip_aktion_mehr").toHaveText("Mehr");
    expect(abrufe).toBe(2);
    // kein Polling: Anruf ohne Änderung fragt nicht noch einmal
    const vorher = abrufe;
    await uhr.vorspulen(30_000);
    await animationFrame();
    expect(abrufe).toBe(vorher);
    expect(ladungen).toBe(2);
    expect(".o_wb_voip_anrufansicht .o_wb_voip_aktion").toHaveCount(4);
    await click(".o_wb_voip_anrufansicht .o_wb_voip_aktion_mehr");
    await animationFrame();
    expect(".o-dropdown-item").toHaveCount(2);
    expect(".o-dropdown-item:eq(0)").toHaveText("Fünf");
    await click(".o-dropdown-item:eq(1)");
    await animationFrame();
    expect.verifySteps(["ausfuehren 10 7"]);
    dienst.stop();
});

test("Softphone: ohne Softphone-Buttons weder Leiste noch „Mehr“", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", leererKontext);
    onRpc("wb.voip.anruf", "wb_voip_aktionen", () => [
        taste(3, "Nur Fenster", ["anrufer_fenster"]),
    ]);
    const { dienst, uhr, fabrik } = await softphoneMitAnruf();
    await dienst.waehlen("0170 1234567");
    fabrik.letzterAgent.invites[0].gegenstelleNimmtAn();
    await uhr.vorspulen(0);
    await animationFrame();
    expect(".o_wb_voip_anrufansicht").toHaveCount(1);
    expect(".o_wb_voip_anrufansicht .o_wb_voip_aktion").toHaveCount(0);
    expect(".o_wb_voip_anrufansicht .o_wb_voip_aktion_mehr").toHaveCount(0);
    dienst.stop();
});
