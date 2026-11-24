import { describe, expect, getFixture, test } from "@odoo/hoot";
import { click, queryAll, queryOne, queryRect, resize, waitFor } from "@odoo/hoot-dom";
import { Deferred, animationFrame } from "@odoo/hoot-mock";
import { Component, EventBus, reactive, useState, xml } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    defineModels,
    mockService,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";
import { MainComponentsContainer } from "@web/core/main_components_container";
import { session } from "@web/session";

import { AnruferFenster } from "@wb_voip/anrufer_fenster/anrufer_fenster";
import { LayoutBloecke, NotizBlock } from "@wb_voip/layout/bloecke";
import "@wb_voip/layout/popup_layout";
import "@wb_voip/protokoll_popup";

import { WbVoipProtokoll } from "./protokoll_modell";

describe.current.tags("desktop");

defineMailModels();
defineModels([WbVoipProtokoll]);

const ERIKA = {
    id: 3,
    name: "Erika Muster",
    firma: "Muster GmbH",
    telefon: "+49 170 5555",
    email: "erika@example.com",
    bild_url: "/web/image/res.partner/3/avatar_128",
};

/** Block wie ``_wb_voip_fuer_client`` ihn liefert. */
function block(art, extra = {}) {
    return { art, titel: false, spalte: "1", anzahl: false, text: false, felder: [], ...extra };
}

function layout(bloecke, breite = "normal") {
    return { id: 9, breite, bloecke };
}

const PROTOKOLL = {
    id: 4,
    datum: "2026-11-20 09:30:00",
    ergebnis_label: "Erreicht",
    dauer_sek: 65,
    beschreibung_text: "Angebot besprochen",
};

function kontext(anrufId, lay, extra = {}) {
    return {
        anruf: { id: anrufId, richtung: "eingehend", nummer: "0170 5555", zustand: "klingelt" },
        kontakt: ERIKA,
        protokolle: [PROTOKOLL],
        aufgaben: [],
        aktionen: [],
        layout: lay,
        ...extra,
    };
}

/** Telefon-Dienst als Attrappe: Zustand, Bus und Uhr reichen dem Anrufer-Fenster. */
function telefonAttrappe() {
    return {
        state: reactive({ status: "verbunden", anruf: null, fehler: null }),
        bus: new EventBus(),
        uhr: {
            setTimeout: (f, ms) => setTimeout(f, ms),
            clearTimeout: (t) => clearTimeout(t),
        },
        annehmen() {},
        ablehnen() {},
        auflegen() {},
        stumm() {},
    };
}

/** Anrufer-Fenster mit klingelndem Anruf ``anrufId`` öffnen. */
async function fensterOeffnen(telefon, anrufId = 10) {
    telefon.state.anruf = {
        id: anrufId,
        richtung: "eingehend",
        annahme: false,
        zustand: "klingelt",
        stumm: false,
        notiz: "",
    };
    telefon.bus.trigger("anruf-aktiv", { anrufId, eigenerTab: true });
    await animationFrame();
    await animationFrame();
}

async function fensterMit(lay, { breite = 1400, extra = () => ({}) } = {}) {
    await resize({ width: breite, height: 900 });
    patchWithCleanup(session, { wb_voip_aktiv: false });
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => kontext(args[0][0], lay(), extra()));
    const telefon = telefonAttrappe();
    mockService("wb_voip_telefon", () => telefon);
    await mountWithCleanup(AnruferFenster, { noMainContainer: true });
    await fensterOeffnen(telefon);
    return telefon;
}

/** Arten der gezeigten Blöcke in DOM-Reihenfolge, optional nur unter ``wurzel``. */
function arten(wurzel = "") {
    return queryAll(`${wurzel} .o_wb_voip_lb_block`.trim()).map((el) =>
        [...el.classList].find((k) => k.startsWith("o_wb_voip_lb_") && k !== "o_wb_voip_lb_block")
    );
}

const GEMISCHT = () => [
    block("text", { text: "Erster", spalte: "2" }),
    block("kontaktkopf"),
    block("protokolle", { spalte: "2", anzahl: 5 }),
    block("text", { text: "Letzter" }),
];

// --- Anrufer-Fenster ------------------------------------------------------------------------

test("Blöcke in Layout-Reihenfolge, bei „normal“ eine Spalte", async () => {
    await fensterMit(() => layout(GEMISCHT(), "normal"));
    expect(".o_wb_voip_lb_spalte").toHaveCount(1);
    expect(arten()).toEqual([
        "o_wb_voip_lb_text",
        "o_wb_voip_lb_kontaktkopf",
        "o_wb_voip_lb_protokolle",
        "o_wb_voip_lb_text",
    ]);
    expect(".o_wb_voip_lb_text_inhalt:eq(0)").toHaveText("Erster");
});

test("„breit“ ab 992 px: Spalte-2-Blöcke rechts", async () => {
    await fensterMit(() => layout(GEMISCHT(), "breit"));
    expect(".o_wb_voip_lb_zwei").toHaveCount(1);
    expect(".o_wb_voip_lb_spalte").toHaveCount(2);
    expect(arten(".o_wb_voip_lb_spalte:eq(0)")).toEqual([
        "o_wb_voip_lb_kontaktkopf",
        "o_wb_voip_lb_text",
    ]);
    expect(".o_wb_voip_lb_spalte:eq(0) .o_wb_voip_lb_text_inhalt").toHaveText("Letzter");
    expect(arten(".o_wb_voip_lb_spalte:eq(1)")).toEqual([
        "o_wb_voip_lb_text",
        "o_wb_voip_lb_protokolle",
    ]);
    expect(".o_wb_voip_lb_spalte:eq(1) .o_wb_voip_lb_text_inhalt").toHaveText("Erster");
    // Spalten nebeneinander
    const links = queryRect(".o_wb_voip_lb_spalte:eq(0)");
    const rechts = queryRect(".o_wb_voip_lb_spalte:eq(1)");
    expect(rechts.left).toBeGreaterThan(links.left + links.width - 1);
});

test("„sehr breit“ unter 992 px: eine Spalte in Block-Reihenfolge", async () => {
    await fensterMit(() => layout(GEMISCHT(), "sehr_breit"), { breite: 900 });
    expect(".o_wb_voip_lb_spalte").toHaveCount(1);
    expect(arten()).toEqual([
        "o_wb_voip_lb_text",
        "o_wb_voip_lb_kontaktkopf",
        "o_wb_voip_lb_protokolle",
        "o_wb_voip_lb_text",
    ]);
});

test("leeres Layout: Kopfzeile mit Name, Nummer und Gesprächsknöpfen bleibt", async () => {
    await fensterMit(() => layout([]));
    expect(".o_wb_voip_af_name").toHaveText("Erika Muster");
    expect(".o_wb_voip_af_nummer").toHaveText("0170 5555");
    expect(".o_wb_voip_af_kopf .o_wb_voip_af_annehmen").toHaveCount(1);
    expect(".o_wb_voip_af_kopf .o_wb_voip_af_ablehnen").toHaveCount(1);
    expect(".o_wb_voip_af_schliessen").toHaveCount(1);
    expect(".o_wb_voip_lb_block").toHaveCount(0);
    expect(".o_wb_voip_af_notiz").toHaveCount(0);
    expect(".o_wb_voip_af_protokolle").toHaveCount(0);
});

test("Feldblock zeigt Label und Wert nur als Text", async () => {
    await fensterMit(() =>
        layout([
            block("felder", {
                titel: "Daten <u>fett</u>",
                felder: [
                    { name: "ref", label: "Referenz <i>x</i>", wert: "<b>x</b>" },
                    { name: "comment", label: "Hinweis", wert: "Zeile 1\nZeile 2" },
                ],
            }),
        ])
    );
    expect(".o_wb_voip_lb_felder").toHaveCount(1);
    expect(".o_wb_voip_lb_felder .o_wb_voip_lb_titel").toHaveText("Daten <u>fett</u>");
    expect(".o_wb_voip_lb_label:eq(0)").toHaveText("Referenz <i>x</i>");
    expect(".o_wb_voip_lb_wert:eq(0)").toHaveText("<b>x</b>");
    expect(".o_wb_voip_lb_felder b, .o_wb_voip_lb_felder i, .o_wb_voip_lb_felder u").toHaveCount(0);
    expect(queryOne(".o_wb_voip_lb_wert:eq(1)").textContent).toBe("Zeile 1\nZeile 2");
});

test("Textblock: Klartext mit Zeilenumbrüchen, kein Markup", async () => {
    await fensterMit(() =>
        layout([block("text", { titel: "Hinweis", text: "Bitte Ausweis prüfen.\n<b>Wichtig</b>" })])
    );
    const inhalt = queryOne(".o_wb_voip_lb_text_inhalt");
    expect(inhalt.textContent).toBe("Bitte Ausweis prüfen.\n<b>Wichtig</b>");
    expect(".o_wb_voip_lb_text b").toHaveCount(0);
    expect(getComputedStyle(inhalt).whiteSpace).toBe("pre-line");
    expect(".o_wb_voip_lb_text .o_wb_voip_lb_titel").toHaveText("Hinweis");
});

test("unbekannte Art fällt ohne Fehler weg", async () => {
    await fensterMit(() =>
        layout([
            block("<img src=x onerror=alert(1)>"),
            block("gibt_es_nicht"),
            { titel: "ohne Art" },
            block("text", { text: "Bleibt" }),
        ])
    );
    expect(arten()).toEqual(["o_wb_voip_lb_text"]);
    expect(".o_wb_voip_anrufer_fenster img[onerror]").toHaveCount(0);
});

test("Breite des Fensters je Layout: 720/960/1200 px", async () => {
    let breite = "normal";
    const telefon = await fensterMit(() => layout([block("kontaktkopf")], breite));
    const erwartet = { normal: 720, breit: 960, sehr_breit: 1200 };
    let anrufId = 10;
    for (const [name, px] of Object.entries(erwartet)) {
        if (name !== "normal") {
            breite = name;
            await fensterOeffnen(telefon, ++anrufId);
        }
        expect(".o_wb_voip_anrufer_fenster").toHaveClass(`o_wb_voip_af_breite_${name}`);
        expect(document.body).toHaveClass(`o_wb_voip_af_offen_${name}`);
        // 100vw ist das echte Browserfenster des Testlaufs (1366 px), nicht die Hoot-Breite
        expect(Math.round(queryRect(".o_wb_voip_anrufer_fenster").width)).toBe(px);
    }
    // unbekannter Wert vom Server: normal, nie als Klasse übernommen
    breite = "riesig x";
    await fensterOeffnen(telefon, ++anrufId);
    expect(".o_wb_voip_anrufer_fenster").toHaveClass("o_wb_voip_af_breite_normal");
    expect(document.body).not.toHaveClass("o_wb_voip_af_offen_breit");
});

// --- Protokoll-Popup ------------------------------------------------------------------------

async function popupMit(antwort) {
    onRpc("wb.voip.protokoll", "wb_voip_popup_layout", ({ args }) => {
        expect.step(`layout ${args[0][0]}`);
        return antwort();
    });
    const telefon = telefonAttrappe();
    mockService("wb_voip_telefon", () => telefon);
    await mountWithCleanup(MainComponentsContainer);
    telefon.bus.trigger("anruf-beendet", { anrufId: 5, protokollId: 1, eigenerTab: true });
    await waitFor(".modal .o_form_view");
    await animationFrame();
}

test("Popup: rechte Spalte folgt dem Layout, linke bleibt fest", async () => {
    await popupMit(() => ({
        layout: layout(
            [
                block("text", { text: "Erst prüfen", spalte: "2" }),
                block("felder", { felder: [{ name: "ref", label: "Referenz", wert: "K-1" }] }),
                block("kontaktkopf"),
                block("protokolle", { anzahl: 5 }),
            ],
            "breit"
        ),
        kontakt: ERIKA,
        protokolle: [PROTOKOLL],
    }));
    // Daten einmal geladen (Größe und Spalte aus derselben Antwort)
    expect.verifySteps(["layout 1"]);
    expect(".modal-dialog").toHaveClass("modal-xl");
    // links unverändert
    expect(".o_wb_voip_popup_links .o_field_widget[name=ergebnis]").toHaveCount(1);
    expect(".o_wb_voip_popup_links .o_field_widget[name=beschreibung]").toHaveCount(1);
    expect(".o_wb_voip_popup_links .o_wb_voip_lb_block").toHaveCount(0);
    // rechts in Layout-Reihenfolge, im Popup immer einspaltig
    expect(".o_wb_voip_popup_rechts .o_wb_voip_lb_spalte").toHaveCount(1);
    expect(arten(".o_wb_voip_popup_rechts")).toEqual([
        "o_wb_voip_lb_text",
        "o_wb_voip_lb_felder",
        "o_wb_voip_lb_kontaktkopf",
        "o_wb_voip_lb_protokolle",
    ]);
    expect(".o_wb_voip_popup_rechts .o_wb_voip_lb_name").toHaveText("Erika Muster");
    expect(".o_wb_voip_popup_rechts .o_wb_voip_lb_wert").toHaveText("K-1");
    // im Popup kein Sprung aus dem Dialog
    expect(".o_wb_voip_popup_rechts .o_wb_voip_af_kontakt_oeffnen").toHaveCount(0);
});

test("Popup: „sehr breit“ ist Vollbild", async () => {
    await popupMit(() => ({ layout: layout([block("kontaktkopf")], "sehr_breit"), kontakt: ERIKA, protokolle: [] }));
    expect.verifySteps(["layout 1"]);
    expect(".modal-dialog").toHaveClass("modal-fs");
});

test("Popup: Fehler beim Laden des Layouts öffnet trotzdem (groß)", async () => {
    await popupMit(() => {
        throw new Error("weg");
    });
    expect(".modal-dialog").toHaveClass("modal-lg");
    expect(".o_wb_voip_popup_links .o_field_widget[name=ergebnis]").toHaveCount(1);
    expect(".o_wb_voip_popup_rechts .o_wb_voip_lb_block").toHaveCount(0);
    // nur das Widget lädt, einmal
    expect.verifySteps(["layout 1"]);
});

test("Popup öffnet sofort, das Widget lädt seine Daten und passt die Größe an", async () => {
    const antwort = new Deferred();
    onRpc("wb.voip.protokoll", "wb_voip_popup_layout", ({ args }) => {
        expect.step(`layout ${args[0][0]}`);
        return antwort;
    });
    const telefon = telefonAttrappe();
    mockService("wb_voip_telefon", () => telefon);
    await mountWithCleanup(MainComponentsContainer);
    telefon.bus.trigger("anruf-beendet", { anrufId: 5, protokollId: 1, eigenerTab: true });
    await waitFor(".modal .o_form_view");
    await animationFrame();
    // Dialog schon offen, obwohl die Antwort fehlt
    expect.verifySteps(["layout 1"]);
    expect(".modal-dialog").toHaveClass("modal-lg");
    expect(".o_wb_voip_popup_links .o_field_widget[name=ergebnis]").toHaveCount(1);
    antwort.resolve({
        layout: layout([block("text", { text: "Später da" })], "breit"),
        kontakt: ERIKA,
        protokolle: [],
    });
    await animationFrame();
    await animationFrame();
    expect(".o_wb_voip_popup_rechts .o_wb_voip_lb_text_inhalt").toHaveText("Später da");
    expect(".modal-dialog").toHaveClass("modal-xl");
    expect(".modal-dialog").not.toHaveClass("modal-lg");
    // das nächste Popup im selben Tab öffnet gleich in der zuletzt gesehenen Größe
    await click(".modal .btn-close");
    await animationFrame();
    telefon.bus.trigger("anruf-beendet", { anrufId: 6, protokollId: 1, eigenerTab: true });
    await waitFor(".modal .o_form_view");
    expect(".modal-dialog").toHaveClass("modal-xl");
    await animationFrame();
    expect.verifySteps(["layout 1"]);
});

// --- Kopfzeile, Barrierefreiheit, Softphone (Nacharbeiten) ----------------------------------

const NEUER_KONTAKT = {
    id: 1,
    name: "Neuer Kontakt",
    icon: "fa-user-plus",
    farbe: "primary",
    art: "neuer_kontakt",
    orte: ["anrufer_fenster"],
};
const WEB = { id: 2, name: "Web", icon: false, farbe: "secondary", art: "url", orte: ["anrufer_fenster"] };

test("unbekannte Nummer: „Zuordnen“ und „Neuer Kontakt“ fest im Kopf, auch ohne Buttons-Block", async () => {
    await fensterMit(() => layout([]), {
        extra: () => ({ kontakt: false, protokolle: [], aktionen: [NEUER_KONTAKT, WEB] }),
    });
    expect(".o_wb_voip_af_name").toHaveText("Unbekannte Nummer");
    expect(".o_wb_voip_af_kopf .o_wb_voip_af_zuordnen").toHaveCount(1);
    expect(".o_wb_voip_af_kopf .o_wb_voip_af_aktion").toHaveCount(1);
    expect(".o_wb_voip_af_kopf .o_wb_voip_af_aktion").toHaveText("Neuer Kontakt");
    // kein Buttons-Block: der Web-Button fehlt
    expect(".o_wb_voip_lb_buttons").toHaveCount(0);
});

test("Buttons-Block zeigt weder „Zuordnen“ noch „Neuer Kontakt“", async () => {
    await fensterMit(() => layout([block("buttons")]), {
        extra: () => ({ kontakt: false, protokolle: [], aktionen: [NEUER_KONTAKT, WEB] }),
    });
    expect(".o_wb_voip_lb_buttons .o_wb_voip_af_aktion").toHaveCount(1);
    expect(".o_wb_voip_lb_buttons .o_wb_voip_af_aktion").toHaveText("Web");
    expect(".o_wb_voip_lb_buttons .o_wb_voip_af_zuordnen").toHaveCount(0);
    expect(".o_wb_voip_af_kopf .o_wb_voip_af_zuordnen").toHaveCount(1);
    expect(".o_wb_voip_af_kopf .o_wb_voip_af_aktion").toHaveText("Neuer Kontakt");
});

test("bekannter Kontakt: kein „Zuordnen“ im Kopf", async () => {
    await fensterMit(() => layout([block("buttons")]), { extra: () => ({ aktionen: [WEB] }) });
    expect(".o_wb_voip_af_zuordnen").toHaveCount(0);
    expect(".o_wb_voip_af_kopf .o_wb_voip_af_aktion").toHaveCount(0);
    expect(".o_wb_voip_lb_buttons .o_wb_voip_af_aktion").toHaveText("Web");
});

test("Blöcke haben einen zugänglichen Namen, das Fenster eine Überschrift", async () => {
    await fensterMit(() =>
        layout([
            block("text", { titel: "Hinweis", text: "Bitte prüfen" }),
            block("kontaktkopf"),
            block("protokolle", { anzahl: 5 }),
        ])
    );
    const ueberschrift = queryOne(".o_wb_voip_anrufer_fenster .o_wb_voip_af_ueberschrift");
    expect(ueberschrift.tagName).toBe("H2");
    expect(ueberschrift).toHaveClass("visually-hidden");
    const text = queryOne(".o_wb_voip_lb_text");
    const titelId = text.getAttribute("aria-labelledby");
    expect(titelId).toMatch(/^wb_voip_lb_/);
    expect(queryOne(`#${titelId}`)).toHaveText("Hinweis");
    expect(queryOne(`#${titelId}`).tagName).toBe("H3");
    // ohne Titel: Name der Art
    expect(".o_wb_voip_lb_kontaktkopf").toHaveAttribute("aria-label", "Kontakt");
    expect(".o_wb_voip_lb_kontaktkopf").not.toHaveAttribute("aria-labelledby");
    // Protokolle haben immer eine Überschrift
    const protokolle = queryOne(".o_wb_voip_lb_protokolle");
    expect(queryOne(`#${protokolle.getAttribute("aria-labelledby")}`)).toHaveText("Letzte Gespräche");
});

test("Softphone wird nie aus dem Bild geschoben", async () => {
    const softphone = document.createElement("div");
    softphone.className = "o_wb_voip_fenster position-fixed";
    softphone.style.height = "80px";
    getFixture().appendChild(softphone);
    let breite = "normal";
    const telefon = await fensterMit(() => layout([block("kontaktkopf")], breite));
    let anrufId = 10;
    for (const name of ["normal", "breit", "sehr_breit"]) {
        if (name !== "normal") {
            breite = name;
            await fensterOeffnen(telefon, ++anrufId);
        }
        const panel = queryRect(".o_wb_voip_anrufer_fenster");
        const sp = softphone.getBoundingClientRect();
        // ganz sichtbar ...
        expect(sp.left).toBeGreaterThan(-0.5);
        // ... und entweder neben dem Panel oder gar nicht verschoben (Panel liegt darüber)
        const neben = sp.right <= panel.left + 0.5;
        const unverschoben = Math.abs(panel.right - 16 - sp.right) < 1;
        expect(neben || unverschoben).toBe(true, { message: name });
    }
});

class ZweiNotizen extends Component {
    static components = { LayoutBloecke };
    static props = ["anruf"];
    static template = xml`
        <div>
            <t t-if="state.zeigen">
                <LayoutBloecke layout="lay" ort="'anrufer_fenster'" anruf="props.anruf"/>
                <LayoutBloecke layout="lay" ort="'anrufer_fenster'" anruf="props.anruf"/>
            </t>
        </div>`;
    setup() {
        this.state = useState({ zeigen: true });
        this.lay = layout([block("notiz")]);
        ZweiNotizen.letzte = this;
    }
}

test("Notiz-Block: eindeutige Feld-ID, keine Beobachtung nach dem Abbau", async () => {
    const anruf = reactive({ id: 10, notiz: "" });
    await mountWithCleanup(ZweiNotizen, { props: { anruf } });
    const felder = queryAll(".o_wb_voip_af_notiz");
    expect(felder).toHaveLength(2);
    expect(felder[0].id).not.toBe(felder[1].id);
    for (const feld of felder) {
        expect(queryAll(`label[for="${feld.id}"]`)).toHaveLength(1);
    }
    // Änderung an anderer Stelle wird gezeigt
    anruf.notiz = "Von außen";
    await animationFrame();
    expect(".o_wb_voip_af_notiz:eq(0)").toHaveValue("Von außen");
    // nach dem Abbau zeichnet niemand mehr
    let gezeichnet = 0;
    patchWithCleanup(NotizBlock.prototype, {
        render(...args) {
            gezeichnet++;
            return super.render(...args);
        },
    });
    ZweiNotizen.letzte.state.zeigen = false;
    await animationFrame();
    gezeichnet = 0;
    anruf.notiz = "Nach dem Abbau";
    await animationFrame();
    expect(gezeichnet).toBe(0);
});
