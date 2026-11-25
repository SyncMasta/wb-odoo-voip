import { describe, expect, test } from "@odoo/hoot";
import { click, queryAll } from "@odoo/hoot-dom";
import { Deferred, animationFrame } from "@odoo/hoot-mock";
import { Component, reactive, xml } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { mockService, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";

import { LayoutBloecke, artLabel } from "@wb_voip/layout/bloecke";
import "@wb_voip_crm/bloecke_vorgaenge";

describe.current.tags("desktop");

defineMailModels();

const VORGAENGE = [
    {
        id: 11,
        name: "Angebot <b>Dach</b>",
        stufe: "Neu",
        erwarteter_umsatz: "1.500,00 €",
        zugeordnet: true,
    },
    { id: 12, name: "Zweiter Vorgang", stufe: "Qualifiziert", erwarteter_umsatz: false, zugeordnet: false },
    { id: 13, name: "Dritter Vorgang", stufe: "", erwarteter_umsatz: false, zugeordnet: false },
];

function block(daten, extra = {}) {
    return {
        art: "vorgaenge",
        titel: false,
        spalte: "1",
        anzahl: 5,
        text: false,
        felder: [],
        daten,
        ...extra,
    };
}

async function zeigen(daten, { anruf = { id: 10 }, extra = {} } = {}) {
    await mountWithCleanup(LayoutBloecke, {
        props: {
            layout: { id: 9, breite: "normal", bloecke: [block(daten, extra)] },
            ort: "anrufer_fenster",
            ...(anruf ? { anruf } : {}),
        },
    });
    await animationFrame();
}

test("Block ist registriert und heißt Vorgänge", async () => {
    expect(artLabel("vorgaenge")).toBe("Vorgänge");
});

test("Zeilen mit Name, Stufe und Umsatz nur als Text, zugeordneter markiert", async () => {
    await zeigen(VORGAENGE);
    expect(".o_wb_voip_lb_vorgang").toHaveCount(3);
    expect(".o_wb_voip_lb_vorgang_name:first").toHaveText("Angebot <b>Dach</b>");
    expect(".o_wb_voip_lb_vorgang b").toHaveCount(0);
    expect(".o_wb_voip_lb_vorgang_stufe:first").toHaveText("Neu");
    expect(".o_wb_voip_lb_vorgang_umsatz").toHaveCount(1);
    expect(".o_wb_voip_lb_vorgang_umsatz").toHaveText("1.500,00 €");
    // nur der erste ist zugeordnet: markiert, ohne Zuordnen-Knopf
    expect(".o_wb_voip_lb_vorgang_aktiv").toHaveCount(1);
    expect(".o_wb_voip_lb_vorgang_marke").toHaveCount(1);
    expect(".o_wb_voip_lb_vorgang_zuordnen").toHaveCount(2);
    expect(".o_wb_voip_lb_vorgang_aktiv .o_wb_voip_lb_vorgang_zuordnen").toHaveCount(0);
    expect(".o_wb_voip_lb_vorgang_oeffnen").toHaveCount(3);
});

test("Zuordnen ruft wb_voip_vorgang_setzen am Anruf und markiert den Vorgang", async () => {
    const aufrufe = [];
    onRpc("wb.voip.anruf", "wb_voip_vorgang_setzen", ({ args }) => {
        aufrufe.push(args);
        return true;
    });
    await zeigen(VORGAENGE);
    await click(queryAll(".o_wb_voip_lb_vorgang_zuordnen")[0]);
    await animationFrame();
    expect(aufrufe).toEqual([[[10], 12]]);
    // jetzt ist der zweite zugeordnet, der erste wieder zuordenbar
    expect(".o_wb_voip_lb_vorgang_aktiv").toHaveCount(1);
    expect(".o_wb_voip_lb_vorgang_aktiv .o_wb_voip_lb_vorgang_name").toHaveText("Zweiter Vorgang");
    expect(".o_wb_voip_lb_vorgang_zuordnen").toHaveCount(2);
});

test("Zuordnen läuft nur einmal gleichzeitig", async () => {
    const warten = new Deferred();
    let aufrufe = 0;
    onRpc("wb.voip.anruf", "wb_voip_vorgang_setzen", async () => {
        aufrufe++;
        await warten;
        return true;
    });
    await zeigen(VORGAENGE);
    const knoepfe = queryAll(".o_wb_voip_lb_vorgang_zuordnen");
    click(knoepfe[0]);
    await animationFrame();
    click(knoepfe[1]);
    await animationFrame();
    expect(aufrufe).toBe(1);
    warten.resolve();
    await animationFrame();
});

test("Fehler beim Zuordnen: Meldung als Text, nichts markiert", async () => {
    onRpc("wb.voip.anruf", "wb_voip_vorgang_setzen", () => {
        throw new Error("kaputt");
    });
    await zeigen(VORGAENGE);
    await click(queryAll(".o_wb_voip_lb_vorgang_zuordnen")[0]);
    await animationFrame();
    expect(".o_wb_voip_lb_vorgang_fehler").toHaveCount(1);
    expect(".o_wb_voip_lb_vorgang_aktiv .o_wb_voip_lb_vorgang_name").toHaveText("Angebot <b>Dach</b>");
});

test("Öffnen zeigt das Formular des Vorgangs", async () => {
    const aktionen = [];
    mockService("action", { doAction: (aktion) => aktionen.push(aktion) });
    await zeigen(VORGAENGE);
    await click(queryAll(".o_wb_voip_lb_vorgang_oeffnen")[1]);
    expect(aktionen).toEqual([
        {
            type: "ir.actions.act_window",
            res_model: "crm.lead",
            res_id: 12,
            views: [[false, "form"]],
            target: "current",
        },
    ]);
});

test("Vorschau ohne Anruf: Zeilen, aber kein Zuordnen-Knopf", async () => {
    await zeigen(VORGAENGE.map((v) => ({ ...v, zugeordnet: false })), { anruf: false });
    expect(".o_wb_voip_lb_vorgang").toHaveCount(3);
    expect(".o_wb_voip_lb_vorgang_zuordnen").toHaveCount(0);
    expect(".o_wb_voip_lb_vorgang_oeffnen").toHaveCount(3);
});

test("Anzahl begrenzt die Zeilen", async () => {
    await zeigen(VORGAENGE, { extra: { anzahl: 2 } });
    expect(".o_wb_voip_lb_vorgang").toHaveCount(2);
});

test("Leere Liste: der Block fehlt ganz", async () => {
    await zeigen([]);
    expect(".o_wb_voip_lb_vorgaenge").toHaveCount(0);
});

test("Ungültige Serverdaten werden ignoriert", async () => {
    await zeigen([null, { id: "x", name: "kaputt" }, { id: 5, name: 7, stufe: 1 }]);
    expect(".o_wb_voip_lb_vorgang").toHaveCount(1);
    expect(".o_wb_voip_lb_vorgang_name").toHaveText("");
});

test("Neue Server-Daten setzen die eigene Auswahl zurück", async () => {
    onRpc("wb.voip.anruf", "wb_voip_vorgang_setzen", () => true);
    const zustand = reactive({ daten: VORGAENGE });
    class Eltern extends Component {
        static components = { LayoutBloecke };
        static props = {};
        static template = xml`<LayoutBloecke layout="layout" ort="'anrufer_fenster'" anruf="{ id: 10 }"/>`;
        get layout() {
            return { id: 9, breite: "normal", bloecke: [block(zustand.daten)] };
        }
    }
    const eltern = await mountWithCleanup(Eltern);
    // zugeordnet wird der zweite Vorgang (lokale Auswahl)
    await click(queryAll(".o_wb_voip_lb_vorgang_zuordnen")[0]);
    await animationFrame();
    expect(".o_wb_voip_lb_vorgang_aktiv .o_wb_voip_lb_vorgang_name").toHaveText("Zweiter Vorgang");
    // anderswo wurde der dritte zugeordnet: Bus-Neuladen liefert neue Daten
    zustand.daten = VORGAENGE.map((v) => ({ ...v, zugeordnet: v.id === 13 }));
    eltern.render(true);
    await animationFrame();
    expect(".o_wb_voip_lb_vorgang_aktiv").toHaveCount(1);
    expect(".o_wb_voip_lb_vorgang_aktiv .o_wb_voip_lb_vorgang_name").toHaveText("Dritter Vorgang");
});
