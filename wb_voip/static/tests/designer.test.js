import { describe, expect, test } from "@odoo/hoot";
import { click, edit, queryAll, queryAllTexts, select } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    contains,
    defineModels,
    mountWithCleanup,
    onRpc,
    serverState,
} from "@web/../tests/web_test_helpers";

import { LayoutDesigner } from "@wb_voip/designer/designer";

import { WbVoipProtokoll } from "./protokoll_modell";

describe.current.tags("desktop");

defineMailModels();
defineModels([WbVoipProtokoll]);

const ARTEN_FENSTER = [
    ["kontaktkopf", "Kontaktkopf"],
    ["felder", "Kontaktfelder"],
    ["protokolle", "Letzte Protokolle"],
    ["aufgaben", "Offene Aufgaben"],
    ["notiz", "Notiz"],
    ["buttons", "Buttons"],
    ["text", "Text"],
].map(([art, label]) => ({ art, label }));

/** Block wie ``wb_voip_designer_laden`` ihn liefert. */
function block(art, extra = {}) {
    return { art, titel: false, spalte: "1", anzahl: false, text: false, feld_ids: [], ...extra };
}

function ladeDaten({ layout = {}, bloecke } = {}) {
    return {
        layout: {
            id: 5,
            name: "Vertrieb",
            ort: "anrufer_fenster",
            breite: "normal",
            ist_standard: false,
            group_ids: [],
            bloecke: bloecke || [
                block("kontaktkopf"),
                block("text", { titel: "Hinweis", text: "Erst fragen" }),
                block("notiz"),
            ],
            ...layout,
        },
        arten: {
            anrufer_fenster: ARTEN_FENSTER,
            protokoll_popup: ARTEN_FENSTER.filter((a) =>
                ["kontaktkopf", "felder", "protokolle", "text"].includes(a.art)
            ),
        },
        felder: [
            { id: 11, name: "city", label: "Ort", typ: "char" },
            { id: 12, name: "email", label: "E-Mail", typ: "char" },
            { id: 13, name: "ref", label: "Referenz", typ: "char" },
        ],
        orte: [
            ["anrufer_fenster", "Anrufer-Fenster"],
            ["protokoll_popup", "Protokoll-Popup"],
        ],
        breiten: [
            ["normal", "Normal"],
            ["breit", "Breit"],
            ["sehr_breit", "Sehr breit"],
        ],
        layouts: [{ id: 5, name: "Vertrieb", ort: "anrufer_fenster", ist_standard: false }],
        beispielkontakt_id: serverState.partnerId,
    };
}

const WERTE = { city: "Musterstadt", email: "erika@example.com", ref: "K-1" };
const LABEL = { city: "Ort", email: "E-Mail", ref: "Referenz" };
const NAME = { 11: "city", 12: "email", 13: "ref" };

/** Vorschau wie der Server sie liefert: Feldwerte der Feldblöcke des Entwurfs. */
function vorschau(entwurf) {
    return {
        layout: {
            id: 5,
            breite: entwurf?.breite || "normal",
            bloecke: (entwurf?.bloecke || []).map((b) => ({
                ...b,
                felder: (b.feld_ids || []).map((id) => ({
                    name: NAME[id],
                    label: LABEL[NAME[id]],
                    wert: WERTE[NAME[id]],
                })),
            })),
        },
        kontakt: { id: 3, name: "Erika Muster", firma: false, telefon: "+49 170 5555", email: false },
        protokolle: [],
        aufgaben: [],
    };
}

async function designerMit({
    laden = () => ladeDaten(),
    speichern,
    ladenSchritte = false,
    vorschauSchritte = false,
    vorschauFn = vorschau,
} = {}) {
    onRpc("wb.voip.layout", "wb_voip_designer_laden", ({ args }) => {
        if (ladenSchritte) {
            expect.step(`laden ${args[0]}`);
        }
        return laden(args[0]);
    });
    onRpc("wb.voip.layout", "wb_voip_designer_vorschau", ({ args }) => {
        if (vorschauSchritte) {
            const felder = (args[2]?.bloecke || [])
                .filter((b) => b.art === "felder")
                .map((b) => b.feld_ids.join("+"));
            expect.step(`vorschau ${args[0]} ${args[1]} ${felder.join(",")}`.trim());
        }
        return vorschauFn(args[2]);
    });
    if (speichern) {
        onRpc("wb.voip.layout", "wb_voip_designer_speichern", speichern);
    }
    await mountWithCleanup(LayoutDesigner, { props: { action: { params: { layout_id: 5 } } } });
    await animationFrame();
    await animationFrame();
}

/** Arten der Blöcke in der Mitte (DOM-Reihenfolge). */
function blockArten(spalte = "") {
    return queryAll(`${spalte} .o_wb_voip_ds_block`.trim()).map((el) => el.dataset.art);
}

/** Arten der Blöcke in der Vorschau. */
function vorschauArten() {
    return queryAll(".o_wb_voip_ds_vorschau .o_wb_voip_lb_block").map((el) =>
        [...el.classList].find((k) => k.startsWith("o_wb_voip_lb_") && k !== "o_wb_voip_lb_block")
    );
}

test("lädt das Layout: Palette je Ort, Blöcke und Vorschau", async () => {
    await designerMit({ ladenSchritte: true, vorschauSchritte: true });
    expect.verifySteps(["laden 5", `vorschau 5 ${serverState.partnerId}`]);
    expect(".o_wb_voip_ds_name").toHaveValue("Vertrieb");
    expect(".o_wb_voip_ds_neu").toHaveCount(7);
    // Notiz gibt es höchstens einmal
    expect(".o_wb_voip_ds_neu[data-art=notiz]").toHaveAttribute("disabled");
    expect(blockArten()).toEqual(["kontaktkopf", "text", "notiz"]);
    expect(".o_wb_voip_ds_vorschau .o_wb_voip_lb_text_inhalt").toHaveText("Erst fragen");
    expect(".o_wb_voip_ds_vorschau .o_wb_voip_lb_kontaktkopf").toHaveText(/\+49 170 5555/);
    // eigenes Layout: kein Zurücksetzen
    expect(".o_wb_voip_ds_zuruecksetzen").toHaveCount(0);
    expect(".o_wb_voip_ds_speichern").toHaveAttribute("disabled");
    // Auswahlfelder von Gruppen und Beispielkontakt haben einen Namen
    expect(".o_wb_voip_ds_gruppen input").toHaveAttribute("aria-label", "Gruppen");
    expect(".o_wb_voip_ds_beispiel input").toHaveAttribute("aria-label", "Beispielkontakt");
    expect(".o_wb_voip_ds_gruppen").toHaveAttribute("role", "group");
    expect(".o_wb_voip_ds_status").toHaveAttribute("role", "status");
    expect(".o_wb_voip_ds_vorschau_hinweis").toHaveCount(0);
    // Popup: nur vier Arten, die Notiz ist dort nicht erlaubt
    await select("protokoll_popup", { target: ".o_wb_voip_ds_ort" });
    await animationFrame();
    expect(queryAll(".o_wb_voip_ds_neu").map((el) => el.dataset.art)).toEqual([
        "kontaktkopf",
        "felder",
        "protokolle",
        "text",
    ]);
    expect(".o_wb_voip_ds_block.o_wb_voip_ds_unerlaubt").toHaveCount(1);
    expect(".o_wb_voip_ds_block.o_wb_voip_ds_unerlaubt").toHaveAttribute("data-art", "notiz");
    expect(".o_wb_voip_ds_speichern").toHaveAttribute("disabled");
});

test("Block hinzufügen und Eigenschaften ändern, Vorschau folgt sofort", async () => {
    await designerMit();
    await click(".o_wb_voip_ds_neu[data-art=text]");
    await animationFrame();
    expect(blockArten()).toEqual(["kontaktkopf", "text", "notiz", "text"]);
    // der neue Block ist gewählt
    expect(".o_wb_voip_ds_block:eq(3)").toHaveClass("o_wb_voip_ds_gewaehlt");
    expect(".o_wb_voip_ds_eigenschaften .o_wb_voip_ds_text").toHaveCount(1);
    expect(".o_wb_voip_ds_eigenschaften .o_wb_voip_ds_anzahl").toHaveCount(0);
    await click(".o_wb_voip_ds_text");
    await edit("Neu hier\n<b>x</b>");
    await animationFrame();
    expect(".o_wb_voip_ds_vorschau .o_wb_voip_lb_text_inhalt:eq(1)").toHaveText(/Neu hier/);
    expect(".o_wb_voip_ds_vorschau b").toHaveCount(0);
    // Titel am ersten Block
    await click(".o_wb_voip_ds_block:eq(0) .o_wb_voip_ds_block_wahl");
    await animationFrame();
    await click(".o_wb_voip_ds_titel");
    await edit("Kunde");
    await animationFrame();
    expect(".o_wb_voip_ds_vorschau .o_wb_voip_lb_kontaktkopf .o_wb_voip_lb_titel").toHaveText("Kunde");
    // Protokolle mit Anzahl
    await click(".o_wb_voip_ds_neu[data-art=protokolle]");
    await animationFrame();
    expect(".o_wb_voip_ds_anzahl").toHaveValue(5);
    expect(".o_wb_voip_ds_speichern").not.toHaveAttribute("disabled");
    // Entfernen
    await click(".o_wb_voip_ds_block:eq(4) .o_wb_voip_ds_entfernen");
    await animationFrame();
    expect(blockArten()).toEqual(["kontaktkopf", "text", "notiz", "text"]);
});

test("Feldblock: Suche, Auswahl, Vorschau lädt die Werte", async () => {
    await designerMit({ ladenSchritte: true, vorschauSchritte: true });
    expect.verifySteps(["laden 5", `vorschau 5 ${serverState.partnerId}`]);
    await click(".o_wb_voip_ds_neu[data-art=felder]");
    await animationFrame();
    // ohne Feld nicht speicherbar, kein Vorschau-Aufruf für den leeren Block
    expect(".o_wb_voip_ds_speichern").toHaveAttribute("disabled");
    expect(".o_wb_voip_ds_fehlermeldung").toHaveText(/Kontaktfelder: Bitte mindestens ein Feld/);
    expect(".o_wb_voip_ds_feld").toHaveCount(3);
    await click(".o_wb_voip_ds_feldsuche");
    await edit("mail");
    await animationFrame();
    expect(queryAllTexts(".o_wb_voip_ds_feld")).toEqual(["E-Mail"]);
    await click(".o_wb_voip_ds_feld input");
    await animationFrame();
    await animationFrame();
    expect.verifySteps([`vorschau 5 ${serverState.partnerId} 12`]);
    expect(".o_wb_voip_ds_vorschau .o_wb_voip_lb_label").toHaveText("E-Mail");
    expect(".o_wb_voip_ds_vorschau .o_wb_voip_lb_wert").toHaveText("erika@example.com");
    expect(".o_wb_voip_ds_speichern").not.toHaveAttribute("disabled");
    // Titel ändern lädt nicht neu
    await click(".o_wb_voip_ds_titel");
    await edit("Daten");
    await animationFrame();
    expect.verifySteps([]);
    expect(".o_wb_voip_ds_vorschau .o_wb_voip_lb_felder .o_wb_voip_lb_titel").toHaveText("Daten");
});

test("Verschieben per Knopf (Tastatur), Fokus bleibt am Block", async () => {
    await designerMit();
    expect(".o_wb_voip_ds_block:eq(0) .o_wb_voip_ds_hoch").toHaveAttribute("disabled");
    expect(".o_wb_voip_ds_block:eq(2) .o_wb_voip_ds_runter").toHaveAttribute("disabled");
    expect(".o_wb_voip_ds_block:eq(0) .o_wb_voip_ds_runter").toHaveAttribute("aria-label");
    await click(".o_wb_voip_ds_block:eq(0) .o_wb_voip_ds_runter");
    await animationFrame();
    expect(blockArten()).toEqual(["text", "kontaktkopf", "notiz"]);
    expect(".o_wb_voip_ds_meldung").toHaveText("Block Kontaktkopf an Position 2");
    expect(vorschauArten()).toEqual(["o_wb_voip_lb_text", "o_wb_voip_lb_kontaktkopf", "o_wb_voip_lb_notiz"]);
    expect(".o_wb_voip_ds_block:eq(1) .o_wb_voip_ds_runter").toBeFocused();
    await click(".o_wb_voip_ds_block:eq(2) .o_wb_voip_ds_hoch");
    await animationFrame();
    expect(blockArten()).toEqual(["text", "notiz", "kontaktkopf"]);
});

test("Verschieben per Maus, auch in die zweite Spalte", async () => {
    let gesendet;
    await designerMit({
        laden: () =>
            ladeDaten({
                layout: { breite: "breit" },
                bloecke: [
                    block("kontaktkopf"),
                    block("text", { text: "Erst fragen" }),
                    block("notiz"),
                    block("protokolle", { spalte: "2", anzahl: 5 }),
                ],
            }),
        speichern: ({ args }) => {
            gesendet = args[1];
            return ladeDaten();
        },
    });
    expect(".o_wb_voip_ds_spalte").toHaveCount(2);
    expect(blockArten(".o_wb_voip_ds_spalte:eq(0)")).toEqual(["kontaktkopf", "text", "notiz"]);
    expect(blockArten(".o_wb_voip_ds_spalte:eq(1)")).toEqual(["protokolle"]);
    await contains(".o_wb_voip_ds_block:eq(0) .o_wb_voip_ds_griff").dragAndDrop(
        ".o_wb_voip_ds_block:eq(2)"
    );
    await animationFrame();
    expect(blockArten(".o_wb_voip_ds_spalte:eq(0)")).toEqual(["text", "notiz", "kontaktkopf"]);
    await contains(".o_wb_voip_ds_block[data-art=text] .o_wb_voip_ds_griff").dragAndDrop(
        ".o_wb_voip_ds_block[data-art=protokolle]"
    );
    await animationFrame();
    expect(blockArten(".o_wb_voip_ds_spalte:eq(0)")).toEqual(["notiz", "kontaktkopf"]);
    // auf einen Block abgelegt: davor
    expect(blockArten(".o_wb_voip_ds_spalte:eq(1)")).toEqual(["text", "protokolle"]);
    await click(".o_wb_voip_ds_speichern");
    await animationFrame();
    expect(gesendet.bloecke.map((b) => [b.art, b.spalte])).toEqual([
        ["notiz", "1"],
        ["kontaktkopf", "1"],
        ["text", "2"],
        ["protokolle", "2"],
    ]);
});

test("Speichern sendet die erwarteten Daten und lädt den Stand des Servers", async () => {
    let gesendet;
    await designerMit({
        ladenSchritte: true,
        speichern: ({ args }) => {
            expect.step(`speichern ${args[0]}`);
            gesendet = args[1];
            return ladeDaten({ layout: { name: "Vom Server" } });
        },
    });
    await click(".o_wb_voip_ds_name");
    await edit("Vertrieb Nord");
    await select("breit", { target: ".o_wb_voip_ds_breite" });
    await animationFrame();
    await click(".o_wb_voip_ds_neu[data-art=protokolle]");
    await animationFrame();
    await click(".o_wb_voip_ds_anzahl");
    await edit("3");
    await animationFrame();
    await select("2", { target: ".o_wb_voip_ds_spalte_wahl" });
    await animationFrame();
    await click(".o_wb_voip_ds_block:eq(1) .o_wb_voip_ds_block_wahl");
    await animationFrame();
    await click(".o_wb_voip_ds_titel");
    await edit("");
    await animationFrame();
    await click(".o_wb_voip_ds_speichern");
    await animationFrame();
    expect.verifySteps(["laden 5", "speichern 5"]);
    expect(gesendet).toEqual({
        name: "Vertrieb Nord",
        ort: "anrufer_fenster",
        breite: "breit",
        group_ids: [],
        bloecke: [
            { art: "kontaktkopf", titel: false, spalte: "1", anzahl: false, text: false, feld_ids: [] },
            { art: "text", titel: false, spalte: "1", anzahl: false, text: "Erst fragen", feld_ids: [] },
            { art: "notiz", titel: false, spalte: "1", anzahl: false, text: false, feld_ids: [] },
            { art: "protokolle", titel: false, spalte: "2", anzahl: 3, text: false, feld_ids: [] },
        ],
    });
    expect(".o_wb_voip_ds_name").toHaveValue("Vom Server");
    expect(".o_notification").toHaveText(/gespeichert/);
});

test("Fehler beim Speichern als Meldung, der Entwurf bleibt", async () => {
    await designerMit({
        speichern: () => {
            throw new Error("Ungültige Angabe");
        },
    });
    await click(".o_wb_voip_ds_neu[data-art=text]");
    await animationFrame();
    await click(".o_wb_voip_ds_speichern");
    await animationFrame();
    expect(".o_notification").toHaveText(/Ungültige Angabe/);
    expect(blockArten()).toEqual(["kontaktkopf", "text", "notiz", "text"]);
});

test("Verwerfen stellt den geladenen Stand her", async () => {
    await designerMit();
    await click(".o_wb_voip_ds_block:eq(0) .o_wb_voip_ds_entfernen");
    await animationFrame();
    expect(blockArten()).toEqual(["text", "notiz"]);
    expect(".o_wb_voip_ds_verwerfen").not.toHaveAttribute("disabled");
    await click(".o_wb_voip_ds_verwerfen");
    await animationFrame();
    expect(blockArten()).toEqual(["kontaktkopf", "text", "notiz"]);
    expect(".o_wb_voip_ds_verwerfen").toHaveAttribute("disabled");
});

test("Standard-Layout: Ort fest, Zurücksetzen nach Rückfrage", async () => {
    onRpc("wb.voip.layout", "action_auf_standard_zuruecksetzen", ({ args }) => {
        expect.step(`zuruecksetzen ${args[0][0]}`);
        return true;
    });
    await designerMit({
        ladenSchritte: true,
        laden: () => ladeDaten({ layout: { ist_standard: true } }),
    });
    expect(".o_wb_voip_ds_ort").toHaveAttribute("disabled");
    // Gruppen gibt es nur an eigenen Layouts
    expect(".o_wb_voip_ds_gruppen").toHaveCount(0);
    await click(".o_wb_voip_ds_zuruecksetzen");
    await animationFrame();
    expect.verifySteps(["laden 5"]);
    await click(".modal .btn-primary");
    await animationFrame();
    await animationFrame();
    expect.verifySteps(["zuruecksetzen 5", "laden 5"]);
});

test("Layout wechseln fragt bei ungespeicherten Änderungen nach", async () => {
    await designerMit({
        ladenSchritte: true,
        laden: (id) => {
            const daten = ladeDaten({ layout: { id: id || 5, name: `Layout ${id}` } });
            daten.layouts = [
                { id: 5, name: "Layout 5", ort: "anrufer_fenster", ist_standard: false },
                { id: 6, name: "Layout 6", ort: "anrufer_fenster", ist_standard: false },
            ];
            return daten;
        },
    });
    expect.verifySteps(["laden 5"]);
    await click(".o_wb_voip_ds_block:eq(0) .o_wb_voip_ds_entfernen");
    await animationFrame();
    await select("6", { target: ".o_wb_voip_ds_layout" });
    await animationFrame();
    expect(".modal").toHaveCount(1);
    await click(".modal .btn-secondary");
    await animationFrame();
    expect.verifySteps([]);
    expect(".o_wb_voip_ds_layout").toHaveValue("5");
    expect(blockArten()).toEqual(["text", "notiz"]);
    await select("6", { target: ".o_wb_voip_ds_layout" });
    await animationFrame();
    await click(".modal .btn-primary");
    await animationFrame();
    await animationFrame();
    expect.verifySteps(["laden 6"]);
    expect(".o_wb_voip_ds_name").toHaveValue("Layout 6");
    // ohne Änderungen kein Rückfrage-Dialog
    await select("5", { target: ".o_wb_voip_ds_layout" });
    await animationFrame();
    await animationFrame();
    expect(".modal").toHaveCount(0);
    expect.verifySteps(["laden 5"]);
});

test("Vorschau: nicht lesbarer Beispielkontakt ergibt einen Hinweis", async () => {
    await designerMit({
        vorschauFn: () => {
            throw new Error("Kontakt nicht lesbar");
        },
    });
    expect(".o_wb_voip_ds_vorschau_hinweis").toHaveText(/Kontakt nicht lesbar/);
    // Platzhalter statt Kontaktdaten
    expect(".o_wb_voip_ds_vorschau .o_wb_voip_lb_kontaktkopf").toHaveText(/\+49 …/);
});
