import { describe, expect, test } from "@odoo/hoot";
import { click, edit, hover, leave, press, queryOne, queryRect } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { Component, xml } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    defineModels,
    mockService,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";
import { deserializeDate, formatDate } from "@web/core/l10n/dates";
import { session } from "@web/session";

import { AnruferFenster } from "@wb_voip/anrufer_fenster/anrufer_fenster";
import { MAX_NOTIZ, TelefonDienst } from "@wb_voip/dienste/telefon_dienst";

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

const NEUER_KONTAKT = {
    id: 1,
    name: "Neuer Kontakt",
    icon: "fa-user-plus",
    farbe: "primary",
    art: "neuer_kontakt",
    orte: ["anrufer_fenster"],
};

/** Kontext wie ``wb_voip_kontext`` ihn liefert. */
function kontext(anrufId, { kontakt = false, aktionen = [NEUER_KONTAKT], aufgaben = [] } = {}) {
    return {
        anruf: { id: anrufId, richtung: "eingehend", nummer: "0170 5555", zustand: "klingelt" },
        kontakt,
        protokolle: kontakt
            ? [
                  {
                      id: 4,
                      datum: "2026-11-20 09:30:00",
                      ergebnis_label: "Erreicht",
                      dauer_sek: 65,
                      beschreibung_text: "<b>Angebot</b> besprochen",
                  },
              ]
            : [],
        aufgaben,
        aktionen,
    };
}

function erika(id = 3) {
    return {
        id,
        name: "Erika Muster",
        firma: "Muster GmbH",
        telefon: "+49 170 5555",
        email: "erika@example.com",
        bild_url: `/web/image/res.partner/${id}/avatar_128`,
    };
}

/** Welt mit gemeinsamer Sperre, Kanal und Uhr; ``dienst(tabId)`` baut einen Tab. */
function welt() {
    const uhr = new FakeUhr();
    const sperren = new FakeSperren();
    const netz = new FakeKanalNetz();
    const fabrik = new FakeAgentFabrik(uhr);
    const orm = new FakeOrm();
    // ohne Protokoll: kein Popup nach dem Auflegen
    orm.antworten["wb.voip.anruf.wb_voip_beendet"] = (args) => ({
        id: args[0][0],
        zustand: "beendet",
        protokoll_id: false,
    });
    const dienste = [];
    const dienst = (tabId) => {
        const d = new TelefonDienst({
            orm,
            sperren,
            kanal: netz.kanal(),
            uhr,
            tabId,
            agentFabrik: fabrik.fabrik,
            klaenge: new FakeKlaenge(),
        });
        dienste.push(d);
        return d;
    };
    return { uhr, fabrik, orm, dienst, aufraeumen: () => dienste.forEach((d) => d.stop()) };
}

class Huelle extends Component {
    static template = xml`<button class="o_test_vorher">vorher</button><input class="o_test_eingabe" type="text"/><div class="o_test_editierbar" contenteditable="true">x</div><AnruferFenster/>`;
    static components = { AnruferFenster };
    static props = ["*"];
}

/** Telefon-Tab mit Anrufer-Fenster; Bus-Abos werden mitgeschrieben. */
async function fensterMit(dienst) {
    patchWithCleanup(session, { wb_voip_aktiv: true });
    const abos = [];
    mockService("bus_service", {
        subscribe(typ, rueckruf) {
            abos.push([typ, rueckruf]);
        },
    });
    const aktionen = [];
    mockService("action", {
        doAction(aktion, optionen) {
            aktionen.push(aktion);
            return Promise.resolve(true);
        },
    });
    const meldungen = [];
    mockService("notification", {
        add(text, optionen) {
            meldungen.push([String(text), optionen?.type]);
            return () => {};
        },
    });
    mockService("wb_voip_telefon", () => dienst);
    await mountWithCleanup(Huelle, { noMainContainer: true });
    const busSenden = (payload) =>
        abos.filter(([typ]) => typ === "wb_voip/kontext").forEach(([, r]) => r(payload));
    return { aktionen, abos, busSenden, meldungen };
}

async function verbunden(w, tabId = "tab-a") {
    const dienst = w.dienst(tabId);
    dienst.start();
    await w.uhr.vorspulen(0);
    return dienst;
}

async function weiter(uhr, ms = 0) {
    await uhr.vorspulen(ms);
    await animationFrame();
}

test("Dienst meldet anruf-aktiv einmal je Anruf", async () => {
    const w = welt();
    const dienst = await verbunden(w);
    const gemeldet = [];
    dienst.bus.addEventListener("anruf-aktiv", ({ detail }) => gemeldet.push(detail.anrufId));
    await dienst.waehlen("0170 1234567");
    await w.uhr.vorspulen(0);
    expect(gemeldet).toEqual([]); // wählt noch
    const sitzung = w.fabrik.letzterAgent.invites[0];
    sitzung.fortschritt(180);
    await w.uhr.vorspulen(0);
    sitzung.gegenstelleNimmtAn();
    await w.uhr.vorspulen(0);
    expect(gemeldet).toEqual([10]);
    w.aufraeumen();
});

test("öffnet bei anruf-aktiv im Telefon-Tab, Fokus und Escape", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => {
        expect.step(`kontext ${args[0][0]}`);
        // mit Kontakt liefert der Server „Neuer Kontakt“ nicht mit
        return kontext(args[0][0], { kontakt: erika(), aktionen: [] });
    });
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(0);
    queryOne(".o_test_vorher").focus();
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    await animationFrame();
    expect.verifySteps(["kontext 10"]);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(1);
    expect(".o_wb_voip_anrufer_fenster").toHaveAttribute("role", "dialog");
    expect(".o_wb_voip_anrufer_fenster").toHaveAttribute("aria-label", "Anrufer-Fenster");
    expect(".o_wb_voip_anrufer_fenster").toBeFocused();
    expect(".o_wb_voip_af_name").toHaveText("Erika Muster");
    expect(".o_wb_voip_af_firma").toHaveText("Muster GmbH");
    // Hoot ersetzt Bildquellen; das Bild gibt es nur mit Kontakt
    expect(".o_wb_voip_af_bild").toHaveCount(1);
    // Serverdaten nur als Text
    expect(".o_wb_voip_af_protokolle").toHaveText(/<b>Angebot<\/b> besprochen/);
    expect(".o_wb_voip_af_protokolle b").toHaveCount(0);
    // Kontakt bekannt: weder „Zuordnen“ noch „Neuer Kontakt“
    expect(".o_wb_voip_af_zuordnen").toHaveCount(0);
    expect(".o_wb_voip_af_aktion").toHaveCount(0);
    await press("Escape");
    await animationFrame();
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(0);
    expect(".o_test_vorher").toBeFocused();
    w.aufraeumen();
});

test("öffnet sich nicht in einem Tab, der nicht telefoniert", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", () => {
        expect.step("kontext");
        return kontext(10);
    });
    const w = welt();
    const halter = await verbunden(w, "tab-a");
    const neben = await verbunden(w, "tab-b");
    expect(neben.state.status).toBe("anderer_tab");
    const imHalter = [];
    halter.bus.addEventListener("anruf-aktiv", ({ detail }) => imHalter.push(detail.anrufId));
    await fensterMit(neben);
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect(imHalter).toEqual([10]);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(0);
    expect.verifySteps([]);
    w.aufraeumen();
});

test("zwei Anrufe hintereinander zeigen jeweils den richtigen Kontext", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => {
        const id = args[0][0];
        return kontext(id, {
            kontakt: id === 10 ? erika() : { ...erika(4), name: "Max Beispiel", firma: false },
        });
    });
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    const erste = w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect(".o_wb_voip_af_name").toHaveText("Erika Muster");
    erste.gegenstelleLegtAuf("487");
    await weiter(w.uhr);
    // nach dem Ende bleibt das Fenster noch 10 s offen
    await weiter(w.uhr, 9000);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(1);
    // neuer Anruf vor Ablauf: Zeitgeber verfällt, Fenster zeigt den zweiten Anruf
    w.fabrik.letzterAgent.eingehend("0171 6666");
    await weiter(w.uhr);
    expect(".o_wb_voip_af_name").toHaveText("Max Beispiel");
    await weiter(w.uhr, 5000);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(1);
    expect(".o_wb_voip_af_name").toHaveText("Max Beispiel");
    w.aufraeumen();
});

test("schließt sich 10 s nach Anrufende", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => kontext(args[0][0]));
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    const sitzung = w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    sitzung.gegenstelleLegtAuf("487");
    await weiter(w.uhr, 9999);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(1);
    await weiter(w.uhr, 1);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(0);
    w.aufraeumen();
});

test("unbekannte Nummer: „Zuordnen“ und „Neuer Kontakt“", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) =>
        kontext(args[0][0], {
            aktionen: [
                NEUER_KONTAKT,
                { id: 2, name: "Nur Softphone", icon: false, farbe: "danger", art: "url", orte: ["softphone"] },
            ],
        })
    );
    onRpc("wb.voip.anruf", "action_wb_voip_zuordnen", ({ args }) => {
        expect.step(`zuordnen ${args[0][0]}`);
        return { type: "ir.actions.act_window", res_model: "wb.voip.zuordnen", target: "new" };
    });
    onRpc("wb.voip.anruf", "action_wb_voip_neuer_kontakt", ({ args }) => {
        expect.step(`neuer kontakt ${args[0][0]}`);
        return { type: "ir.actions.act_window", res_model: "res.partner", target: "current" };
    });
    const w = welt();
    const dienst = await verbunden(w);
    const { aktionen } = await fensterMit(dienst);
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect(".o_wb_voip_af_name").toHaveText("Unbekannte Nummer");
    expect(".o_wb_voip_af_bild").toHaveCount(0);
    expect(".o_wb_voip_af_zuordnen").toHaveCount(1);
    expect(".o_wb_voip_af_neuer_kontakt").toHaveCount(1);
    expect(".o_wb_voip_af_neuer_kontakt").toHaveText("Neuer Kontakt");
    expect(".o_wb_voip_af_neuer_kontakt").toHaveClass("btn-primary");
    expect(".o_wb_voip_af_neuer_kontakt i").toHaveClass("fa-user-plus");
    // der eingerichtete Button gleicher Art erscheint nicht doppelt
    expect(".o_wb_voip_af_aktion").toHaveCount(0);
    await click(".o_wb_voip_af_zuordnen");
    await animationFrame();
    expect.verifySteps(["zuordnen 10"]);
    expect(aktionen.at(-1).res_model).toBe("wb.voip.zuordnen");
    await click(".o_wb_voip_af_neuer_kontakt");
    await animationFrame();
    expect.verifySteps(["neuer kontakt 10"]);
    expect(aktionen.at(-1).res_model).toBe("res.partner");
    w.aufraeumen();
});

test("Button führt wb_voip_aktion_ausfuehren aus, Aufgabe öffnet Datensatz, Fehler als Meldung", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) =>
        kontext(args[0][0], {
            kontakt: erika(),
            aktionen: [
                { id: 7, name: "Angebot", icon: "fa-file", farbe: "success", art: "fenster", orte: ["anrufer_fenster"] },
                { id: 8, name: "Kaputt", icon: "fa-x\" onclick=\"x", farbe: "<b>", art: "serveraktion", orte: ["anrufer_fenster", "softphone"] },
                { id: 9, name: "Still", icon: false, farbe: "secondary", art: "serveraktion", orte: ["anrufer_fenster"] },
            ],
            aufgaben: [
                { id: 5, titel: "Rückruf", faellig: "2026-11-25", res_model: "crm.lead", res_id: 12 },
            ],
        })
    );
    onRpc("wb.voip.anruf", "wb_voip_aktion_ausfuehren", ({ args }) => {
        expect.step(`ausfuehren ${args[1]}`);
        if (args[1] === 7) {
            return { type: "ir.actions.act_window", res_model: "sale.order", target: "current" };
        }
        if (args[1] === 8) {
            throw new Error("Keine Rechte für diesen Button");
        }
        return false;
    });
    const w = welt();
    const dienst = await verbunden(w);
    const { aktionen, meldungen } = await fensterMit(dienst);
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    // Reihenfolge wie vom Server (sequence)
    expect(".o_wb_voip_af_aktion").toHaveCount(3);
    expect(".o_wb_voip_af_aktion:eq(0)").toHaveText("Angebot");
    expect(".o_wb_voip_af_aktion:eq(0)").toHaveClass("btn-success");
    // unbekannte Farbe/ungültiges Symbol: Standard statt Serverwert
    expect(".o_wb_voip_af_aktion:eq(1)").toHaveClass("btn-secondary");
    expect(".o_wb_voip_af_aktion:eq(1) i").toHaveCount(0);
    await click(".o_wb_voip_af_aktion:eq(0)");
    await animationFrame();
    expect.verifySteps(["ausfuehren 7"]);
    expect(aktionen.at(-1).res_model).toBe("sale.order");
    const anzahl = aktionen.length;
    await click(".o_wb_voip_af_aktion:eq(2)");
    await animationFrame();
    expect.verifySteps(["ausfuehren 9"]);
    expect(aktionen.length).toBe(anzahl);
    await click(".o_wb_voip_af_aktion:eq(1)");
    await animationFrame();
    await animationFrame();
    expect.verifySteps(["ausfuehren 8"]);
    expect(meldungen.length).toBe(1);
    expect(meldungen[0][0]).toMatch(/Keine Rechte für diesen Button/);
    expect(meldungen[0][1]).toBe("danger");
    await click(".o_wb_voip_af_aufgabe");
    await animationFrame();
    expect(aktionen.at(-1)).toEqual({
        type: "ir.actions.act_window",
        res_model: "crm.lead",
        res_id: 12,
        views: [[false, "form"]],
        target: "current",
    });
    w.aufraeumen();
});

test("Bus-Nachricht wb_voip/kontext lädt nur den gezeigten Anruf neu", async () => {
    let name = "Erika Muster";
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => {
        expect.step(`kontext ${args[0][0]}`);
        return kontext(args[0][0], { kontakt: { ...erika(), name } });
    });
    const w = welt();
    const dienst = await verbunden(w);
    const { abos, busSenden } = await fensterMit(dienst);
    expect(abos.filter(([typ]) => typ === "wb_voip/kontext").length).toBe(1);
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect.verifySteps(["kontext 10"]);
    name = "Erika Neu";
    busSenden({ anruf_id: 99 });
    await animationFrame();
    expect.verifySteps([]);
    busSenden({ anruf_id: 10 });
    await animationFrame();
    await animationFrame();
    expect.verifySteps(["kontext 10"]);
    expect(".o_wb_voip_af_name").toHaveText("Erika Neu");
    // Aufräumen beendet den Anruf: das Ende lädt noch einmal
    w.aufraeumen();
    await weiter(w.uhr);
    expect.verifySteps(["kontext 10"]);
});

test("Breite auf dem Desktop mindestens 720 px", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => kontext(args[0][0]));
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect(window.innerWidth).toBeGreaterThan(720);
    expect(queryRect(".o_wb_voip_anrufer_fenster").width).toBeGreaterThan(719);
    w.aufraeumen();
});

test("Notiz ist dieselbe wie in der Anrufansicht", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => kontext(args[0][0]));
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect(".o_wb_voip_af_notiz").toHaveAttribute("maxlength", String(MAX_NOTIZ));
    await click(".o_wb_voip_af_notiz");
    await edit("Will Angebot");
    await animationFrame();
    expect(dienst.state.anruf.notiz).toBe("Will Angebot");
    dienst.state.anruf.notiz = "Aus der Anrufansicht";
    await animationFrame();
    expect(".o_wb_voip_af_notiz").toHaveValue("Aus der Anrufansicht");
    w.aufraeumen();
});

// --- Barrierefreiheit ----------------------------------------------------------------------

async function fokusTest(wahl) {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => kontext(args[0][0]));
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    queryOne(wahl).focus();
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(1);
    expect(wahl).toBeFocused();
    w.aufraeumen();
}

test("neuer Anruf stiehlt den Fokus nicht aus einem Eingabefeld", async () => {
    await fokusTest(".o_test_eingabe");
});

test("neuer Anruf stiehlt den Fokus nicht aus einem editierbaren Bereich", async () => {
    await fokusTest(".o_test_editierbar");
});

test("ohne Eingabefokus zieht das Fenster den Fokus an sich", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => kontext(args[0][0]));
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect(".o_wb_voip_anrufer_fenster").toBeFocused();
    w.aufraeumen();
});

test("Kopf-Status steht in einem Live-Bereich", async () => {
    let fertig;
    const gesperrt = new Promise((r) => (fertig = r));
    onRpc("wb.voip.anruf", "wb_voip_kontext", async ({ args }) => {
        await gesperrt;
        return kontext(args[0][0]);
    });
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    const sitzung = w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect(".o_wb_voip_af_kopf [role=status]").toHaveCount(1);
    expect(".o_wb_voip_af_kopf [role=status]").toHaveText(/Lädt/);
    fertig();
    await weiter(w.uhr);
    sitzung.gegenstelleLegtAuf("487");
    await weiter(w.uhr);
    expect(".o_wb_voip_af_kopf [role=status]").toHaveCount(1);
    expect(".o_wb_voip_af_kopf [role=status]").toHaveText(/Anruf beendet/);
    w.aufraeumen();
});

test("Zeitgeber pausiert, solange der Fokus im Panel ist, und läuft danach neu", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) =>
        kontext(args[0][0], { kontakt: erika() })
    );
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    const sitzung = w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    sitzung.gegenstelleLegtAuf("487");
    await weiter(w.uhr);
    queryOne(".o_wb_voip_af_kontakt_oeffnen").focus();
    await animationFrame();
    await weiter(w.uhr, 30_000);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(1);
    queryOne(".o_test_vorher").focus();
    await animationFrame();
    await weiter(w.uhr, 9999);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(1);
    await weiter(w.uhr, 1);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(0);
    w.aufraeumen();
});

test("veralteter Fokus-Merker (Element entfernt) hält das Fenster nicht offen", async () => {
    let mitKontakt = true;
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) =>
        kontext(args[0][0], { kontakt: mitKontakt ? erika() : false })
    );
    const w = welt();
    const dienst = await verbunden(w);
    const { busSenden } = await fensterMit(dienst);
    const sitzung = w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    queryOne(".o_wb_voip_af_kontakt_oeffnen").focus();
    await animationFrame();
    // Kontakt verschwindet: Button samt Fokus wird entfernt, ohne dass der Panel-Handler es merkt
    mitKontakt = false;
    busSenden({ anruf_id: 10 });
    await weiter(w.uhr);
    expect(".o_wb_voip_af_kontakt_oeffnen").toHaveCount(0);
    sitzung.gegenstelleLegtAuf("487");
    await weiter(w.uhr);
    await weiter(w.uhr, 10_000);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(0);
    w.aufraeumen();
});

test("Fokus im „Mehr“-Menü außerhalb des Panels hält das Fenster offen", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) =>
        kontext(args[0][0], { kontakt: erika() })
    );
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    const sitzung = w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    const menue = document.createElement("div");
    menue.className = "o-dropdown--menu";
    menue.innerHTML = '<button type="button" class="o_test_menue">Eintrag</button>';
    document.body.appendChild(menue);
    try {
        queryOne(".o_wb_voip_af_kontakt_oeffnen").focus();
        await animationFrame();
        sitzung.gegenstelleLegtAuf("487");
        await weiter(w.uhr);
        menue.querySelector(".o_test_menue").focus();
        await animationFrame();
        await weiter(w.uhr, 30_000);
        expect(".o_wb_voip_anrufer_fenster").toHaveCount(1);
        // Fokus verlässt das Menü: Frist läuft neu
        queryOne(".o_test_vorher").focus();
        await animationFrame();
        await weiter(w.uhr, 10_000);
        expect(".o_wb_voip_anrufer_fenster").toHaveCount(0);
    } finally {
        menue.remove();
    }
    w.aufraeumen();
});

test("Zeitgeber pausiert, solange der Zeiger über dem Panel steht", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => kontext(args[0][0]));
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    const sitzung = w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    await hover(".o_wb_voip_anrufer_fenster");
    sitzung.gegenstelleLegtAuf("487");
    await weiter(w.uhr);
    await weiter(w.uhr, 30_000);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(1);
    await leave();
    await animationFrame();
    await weiter(w.uhr, 9999);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(1);
    await weiter(w.uhr, 1);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(0);
    w.aufraeumen();
});

// --- Neu laden bei Zustandswechsel ----------------------------------------------------------

test("Annahme lädt die Buttons neu (Zustand „Während des Gesprächs“)", async () => {
    let zustand = "klingelt";
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => {
        expect.step(`kontext ${args[0][0]} ${zustand}`);
        return kontext(args[0][0], {
            aktionen: [{ ...NEUER_KONTAKT, art: "url", id: zustand === "laeuft" ? 2 : 1, name: zustand }],
        });
    });
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect.verifySteps(["kontext 10 klingelt"]);
    expect(".o_wb_voip_af_aktion").toHaveText("klingelt");
    zustand = "laeuft";
    await dienst.annehmen();
    await weiter(w.uhr);
    await animationFrame();
    expect.verifySteps(["kontext 10 laeuft"]);
    expect(".o_wb_voip_af_aktion").toHaveText("laeuft");
    // Aufräumen beendet den Anruf: das Ende lädt noch einmal
    w.aufraeumen();
    await weiter(w.uhr);
    expect.verifySteps(["kontext 10 laeuft"]);
});

test("Anrufende lädt die Buttons einmal neu (Zustand „Nach dem Anruf“)", async () => {
    let zustand = "klingelt";
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => {
        expect.step(`kontext ${args[0][0]} ${zustand}`);
        return kontext(args[0][0], {
            aktionen: [{ ...NEUER_KONTAKT, art: "url", id: zustand === "beendet" ? 3 : 1, name: zustand }],
        });
    });
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    const sitzung = w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect.verifySteps(["kontext 10 klingelt"]);
    zustand = "beendet";
    sitzung.gegenstelleLegtAuf("487");
    await weiter(w.uhr);
    await animationFrame();
    expect.verifySteps(["kontext 10 beendet"]);
    expect(".o_wb_voip_af_aktion").toHaveText("beendet");
    // genau einmal, auch während der 10 s nicht erneut
    await weiter(w.uhr, 9000);
    expect.verifySteps([]);
    expect(".o_wb_voip_anrufer_fenster").toHaveCount(1);
    w.aufraeumen();
});

// --- Gesprächsknöpfe im Kopf -----------------------------------------------------------------

/** Ersetzt eine Dienst-Methode durch eine Hülle, die einen Schritt meldet. */
function mitSchritt(dienst, name) {
    const echt = dienst[name].bind(dienst);
    dienst[name] = (...args) => {
        expect.step(name);
        return echt(...args);
    };
}

test("Kopf: Annehmen beim Klingeln, danach Stumm und Auflegen", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => kontext(args[0][0]));
    const w = welt();
    const dienst = await verbunden(w);
    for (const name of ["annehmen", "ablehnen", "auflegen", "stumm"]) {
        mitSchritt(dienst, name);
    }
    await fensterMit(dienst);
    const sitzung = w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    expect(".o_wb_voip_af_kopf .o_wb_voip_af_annehmen").toHaveCount(1);
    expect(".o_wb_voip_af_annehmen").toHaveAttribute("title", "Annehmen");
    expect(".o_wb_voip_af_annehmen").toHaveAttribute("aria-label", "Annehmen");
    expect(".o_wb_voip_af_ablehnen").toHaveAttribute("aria-label", "Ablehnen");
    expect(".o_wb_voip_af_auflegen").toHaveCount(0);
    await click(".o_wb_voip_af_annehmen");
    await weiter(w.uhr);
    await animationFrame();
    expect.verifySteps(["annehmen"]);
    expect(sitzung.hat("accept").length).toBe(1);
    expect(".o_wb_voip_af_annehmen").toHaveCount(0);
    expect(".o_wb_voip_af_kopf .o_wb_voip_af_stumm").toHaveAttribute("aria-label", "Stumm");
    expect(".o_wb_voip_af_stumm").toHaveAttribute("aria-pressed", "false");
    await click(".o_wb_voip_af_stumm");
    await animationFrame();
    expect.verifySteps(["stumm"]);
    expect(dienst.state.anruf.stumm).toBe(true);
    expect(".o_wb_voip_af_stumm").toHaveAttribute("aria-pressed", "true");
    expect(".o_wb_voip_af_auflegen").toHaveAttribute("title", "Auflegen");
    await click(".o_wb_voip_af_auflegen");
    await weiter(w.uhr);
    expect.verifySteps(["auflegen"]);
    expect(sitzung.hat("bye").length).toBe(1);
    // nach dem Ende keine Gesprächsknöpfe mehr
    expect(".o_wb_voip_af_auflegen").toHaveCount(0);
    expect(".o_wb_voip_af_stumm").toHaveCount(0);
    w.aufraeumen();
});

test("Kopf: Ablehnen beim Klingeln, ausgehend nur Auflegen", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) => kontext(args[0][0]));
    const w = welt();
    const dienst = await verbunden(w);
    mitSchritt(dienst, "ablehnen");
    mitSchritt(dienst, "auflegen");
    await fensterMit(dienst);
    const sitzung = w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    await click(".o_wb_voip_af_ablehnen");
    await weiter(w.uhr);
    expect.verifySteps(["ablehnen"]);
    expect(sitzung.hat("reject")[0][1]).toEqual({ statusCode: 603 });
    // ausgehend, klingelt beim Gegenüber: Auflegen, Stumm erst im Gespräch
    await dienst.waehlen("0170 1234567");
    const raus = w.fabrik.letzterAgent.invites[0];
    raus.fortschritt(180);
    await weiter(w.uhr);
    expect(".o_wb_voip_af_annehmen").toHaveCount(0);
    expect(".o_wb_voip_af_stumm").toHaveAttribute("disabled");
    await click(".o_wb_voip_af_auflegen");
    await weiter(w.uhr);
    expect.verifySteps(["auflegen"]);
    expect(raus.hat("cancel").length).toBe(1);
    w.aufraeumen();
});

test("Fälligkeit offener Aufgaben im Datumsformat des Benutzers", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) =>
        kontext(args[0][0], {
            kontakt: erika(),
            aufgaben: [{ id: 5, titel: "Rückruf", faellig: "2026-11-25", res_model: "crm.lead", res_id: 12 }],
        })
    );
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    const erwartet = formatDate(deserializeDate("2026-11-25"));
    expect(erwartet).not.toBe("2026-11-25");
    // Titel und Datum stehen in getrennten Spans (ohne Leerzeichen im Text)
    expect(".o_wb_voip_af_aufgabe").toHaveText(`Rückruf${erwartet}`);
    w.aufraeumen();
});

test("Buttons nutzen die gemeinsame Leiste, „Mehr“ erst ab dem fünften", async () => {
    onRpc("wb.voip.anruf", "wb_voip_kontext", ({ args }) =>
        kontext(args[0][0], {
            kontakt: erika(),
            aktionen: [1, 2, 3, 4, 5, 6].map((id) => ({
                id,
                name: `Taste ${id}`,
                icon: false,
                farbe: "secondary",
                art: "serveraktion",
                orte: ["anrufer_fenster"],
            })),
        })
    );
    const w = welt();
    const dienst = await verbunden(w);
    await fensterMit(dienst);
    w.fabrik.letzterAgent.eingehend("0170 5555");
    await weiter(w.uhr);
    // im Anrufer-Fenster ist Platz: alle Buttons sichtbar, kein „Mehr“
    expect(".o_wb_voip_af_aktion").toHaveCount(6);
    expect(".o_wb_voip_aktion_mehr").toHaveCount(0);
    w.aufraeumen();
});
