import { describe, expect, test } from "@odoo/hoot";
import { waitFor } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { EventBus, reactive } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { defineModels, mockService, mountWithCleanup } from "@web/../tests/web_test_helpers";
import { MainComponentsContainer } from "@web/core/main_components_container";

import "@wb_voip/protokoll_popup";

import { WbVoipProtokoll } from "./protokoll_modell";

describe.current.tags("desktop");

defineMailModels();
defineModels([WbVoipProtokoll]);

function telefonAttrappe() {
    return { state: reactive({ status: "verbunden", anruf: null, fehler: null }), bus: new EventBus() };
}

async function mitDienst() {
    const telefon = telefonAttrappe();
    mockService("wb_voip_telefon", () => telefon);
    await mountWithCleanup(MainComponentsContainer);
    return telefon;
}

test("Popup nur im eigenen Tab", async () => {
    const telefon = await mitDienst();
    telefon.bus.trigger("anruf-beendet", { anrufId: 5, protokollId: 1, eigenerTab: false });
    await animationFrame();
    expect(".modal").toHaveCount(0);
});

test("Popup nicht ohne Protokoll", async () => {
    const telefon = await mitDienst();
    telefon.bus.trigger("anruf-beendet", { anrufId: 5, protokollId: false, eigenerTab: true });
    await animationFrame();
    expect(".modal").toHaveCount(0);
});

test("Popup ist breit", async () => {
    const telefon = await mitDienst();
    telefon.bus.trigger("anruf-beendet", { anrufId: 5, protokollId: 1, eigenerTab: true });
    await animationFrame();
    await animationFrame();
    expect(".modal").toHaveCount(1);
    expect(".modal-dialog").toHaveClass("modal-xl");
});

test("Popup zeigt Ergebnis erreicht", async () => {
    const telefon = await mitDienst();
    telefon.bus.trigger("anruf-beendet", { anrufId: 5, protokollId: 1, eigenerTab: true });
    await waitFor(".modal .o_field_widget[name=ergebnis] input");
    expect(".modal .o_field_widget[name=ergebnis] input").toHaveValue("Erreicht");
});

test("Popup hat Platz für Aktionen", async () => {
    const telefon = await mitDienst();
    telefon.bus.trigger("anruf-beendet", { anrufId: 5, protokollId: 1, eigenerTab: true });
    await waitFor(".modal .o_form_view");
    expect(".modal .o_wb_voip_popup_aktionen").toHaveCount(1);
});
