import { describe, expect, test } from "@odoo/hoot";
import { click, edit, press, queryAll, queryOne } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { Component, EventBus, reactive, xml } from "@odoo/owl";
import { defineMailModels, startServer } from "@mail/../tests/mail_test_helpers";
import {
    defineModels,
    mockService,
    mountView,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { session } from "@web/session";

import { Benachrichtiger } from "@wb_voip/benachrichtigung";
import { MAX_NOTIZ, TelefonDienst } from "@wb_voip/dienste/telefon_dienst";
import { Softphone, SoftphoneFenster } from "@wb_voip/softphone/softphone";
import { WbVoipSystray } from "@wb_voip/softphone/systray";
import "@wb_voip/protokoll_popup";

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
// Nach dem Auflegen öffnet der Popup-Dienst das Protokoll-Formular
defineModels([WbVoipProtokoll]);

async function kontaktAnlegen() {
    const pyEnv = await startServer();
    return pyEnv["res.partner"].create({ name: "Erika Muster", phone: "+49 170 1234567" });
}

/** Dienst-Attrappe nur mit Zustand und protokollierten Aufrufen. */
function attrappe(status = "verbunden", { fehler = null, waehlenErgebnis = true } = {}) {
    const aufrufe = [];
    const dienst = {
        state: reactive({ status, anruf: null, fehler }),
        bus: new EventBus(),
        uhr: { jetzt: () => 0 },
        aufrufe,
        waehlen: async (nummer, optionen) => {
            aufrufe.push(["waehlen", nummer, optionen]);
            if (!waehlenErgebnis) {
                dienst.state.fehler = "Kein Telefon-Tab gefunden.";
            }
            return waehlenErgebnis;
        },
        neuVerbinden: async () => aufrufe.push(["neuVerbinden"]),
    };
    return dienst;
}

/** Echter Telefon-Dienst mit Fakes, als Halter verbunden. */
async function echterDienst() {
    const uhr = new FakeUhr();
    const fabrik = new FakeAgentFabrik(uhr);
    const orm = new FakeOrm();
    const dienst = new TelefonDienst({
        orm,
        sperren: new FakeSperren(),
        kanal: new FakeKanalNetz().kanal(),
        uhr,
        tabId: "tab-ui",
        agentFabrik: fabrik.fabrik,
        klaenge: new FakeKlaenge(),
    });
    dienst.start();
    await uhr.vorspulen(0);
    expect(dienst.state.status).toBe("verbunden");
    return { dienst, uhr, fabrik, orm };
}

async function softphoneMit(dienst) {
    mockService("wb_voip_telefon", () => dienst);
    await mountWithCleanup(Softphone);
}

// --- Status -------------------------------------------------------------------------------

test("Statuspunkt: verbunden grün", async () => {
    mockService("wb_voip_telefon", () => attrappe("verbunden"));
    await mountWithCleanup(WbVoipSystray);
    expect(".o_wb_voip_punkt").toHaveClass("text-success");
});

test("Statuspunkt: aus grau", async () => {
    mockService("wb_voip_telefon", () => attrappe("aus"));
    await mountWithCleanup(WbVoipSystray);
    expect(".o_wb_voip_punkt").toHaveClass("text-muted");
});

test("Statuspunkt: anderer Tab gelb", async () => {
    mockService("wb_voip_telefon", () => attrappe("anderer_tab"));
    await mountWithCleanup(WbVoipSystray);
    expect(".o_wb_voip_punkt").toHaveClass("text-warning");
    await mountWithCleanup(Softphone);
    expect(".o_wb_voip_status").toHaveText(/another tab/);
});

test("Statuspunkt: getrennt rot mit „Neu verbinden“", async () => {
    const dienst = attrappe("getrennt", { fehler: "REGISTER abgelehnt" });
    mockService("wb_voip_telefon", () => dienst);
    await mountWithCleanup(WbVoipSystray);
    expect(".o_wb_voip_punkt").toHaveClass("text-danger");
    await mountWithCleanup(Softphone);
    expect(".o_wb_voip_status").toHaveText(/REGISTER abgelehnt/);
    await click(".o_wb_voip_neu_verbinden");
    expect(dienst.aufrufe).toEqual([["neuVerbinden"]]);
});

test("Statuspunkt: ohne Lizenz rot mit Hinweis und Aktivierung", async () => {
    patchWithCleanup(user, { isSystem: true });
    mockService("wb_voip_telefon", () =>
        attrappe("ohne_lizenz", { fehler: "Lizenz fehlt oder abgelaufen" })
    );
    const aktionen = [];
    mockService("action", { doAction: (aktion) => aktionen.push(aktion) });
    await mountWithCleanup(WbVoipSystray);
    expect(".o_wb_voip_punkt").toHaveClass("text-danger");
    await mountWithCleanup(Softphone);
    expect(".o_wb_voip_status").toHaveText(/License missing or expired/);
    expect(".o_wb_voip_neu_verbinden").toHaveCount(0);
    await click(".o_wb_voip_lizenz");
    expect(aktionen).toEqual(["wb_license_client.wb_license_activate_wizard_action"]);
});

test("ohne Lizenz: kein Aktivierungslink für Nicht-Administratoren", async () => {
    patchWithCleanup(user, { isSystem: false });
    mockService("wb_voip_telefon", () => attrappe("ohne_lizenz"));
    await mountWithCleanup(Softphone);
    expect(".o_wb_voip_status").toHaveText(/License missing or expired/);
    expect(".o_wb_voip_lizenz").toHaveCount(0);
});

test("nicht konfiguriert: Hinweis, für Administratoren mit WB_VOIP_FERNET_KEY", async () => {
    patchWithCleanup(session, { wb_voip_manager: false });
    mockService("wb_voip_telefon", () => attrappe("nicht_konfiguriert"));
    await mountWithCleanup(Softphone);
    expect(".o_wb_voip_punkt").toHaveClass("text-danger");
    expect(".o_wb_voip_status").toHaveText(/Telephony is not set up yet\./);
    expect(".o_wb_voip_status").not.toHaveText(/WB_VOIP_FERNET_KEY/);
});

test("nicht konfiguriert: Hinweis für Administratoren", async () => {
    patchWithCleanup(session, { wb_voip_manager: true });
    mockService("wb_voip_telefon", () => attrappe("nicht_konfiguriert"));
    await mountWithCleanup(Softphone);
    expect(".o_wb_voip_status").toHaveText(/WB_VOIP_FERNET_KEY/);
});

// --- Wählen -------------------------------------------------------------------------------

test("Wähltastatur: 0170 + Anrufen wählt", async () => {
    const dienst = attrappe("verbunden");
    await softphoneMit(dienst);
    for (const taste of ["0", "1", "7", "0"]) {
        await click(`.o_wb_voip_taste[data-taste='${taste}']`);
    }
    await animationFrame();
    expect(".o_wb_voip_eingabe").toHaveValue("0170");
    await click(".o_wb_voip_anrufen");
    expect(dienst.aufrufe).toEqual([["waehlen", "0170", { resModel: false, resId: false }]]);
});

test("Wähltastatur: Enter im Eingabefeld wählt", async () => {
    const dienst = attrappe("verbunden");
    await softphoneMit(dienst);
    await click(".o_wb_voip_eingabe");
    await edit("+49 170 1");
    await press("Enter");
    expect(dienst.aufrufe).toEqual([["waehlen", "+49 170 1", { resModel: false, resId: false }]]);
});

test("Neben-Tab: Hinweis „An das Telefon im anderen Tab übergeben“", async () => {
    const dienst = attrappe("anderer_tab");
    await softphoneMit(dienst);
    await click(".o_wb_voip_eingabe");
    await edit("0170");
    await animationFrame();
    await click(".o_wb_voip_anrufen");
    await animationFrame();
    expect(".o_notification").toHaveText(/Hand over to the phone in the other tab/);
});

test("Neben-Tab ohne Halter: Fehler wird angezeigt", async () => {
    const dienst = attrappe("anderer_tab", { waehlenErgebnis: false });
    await softphoneMit(dienst);
    await click(".o_wb_voip_eingabe");
    await edit("0170");
    await animationFrame();
    await click(".o_wb_voip_anrufen");
    await animationFrame();
    expect(".o_notification").toHaveText(/Kein Telefon-Tab gefunden/);
});

// --- Anrufansicht ---------------------------------------------------------------------------

test("eingehender Anruf zeigt Annehmen/Ablehnen", async () => {
    const { dienst, uhr, fabrik } = await echterDienst();
    await softphoneMit(dienst);
    const sitzung = fabrik.letzterAgent.eingehend("0170 5555");
    await uhr.vorspulen(0);
    await animationFrame();
    expect(".o_wb_voip_anrufansicht").toHaveText(/0170 5555/);
    expect(".o_wb_voip_annehmen").toHaveCount(1);
    expect(".o_wb_voip_ablehnen").toHaveCount(1);
    expect(".o_wb_voip_auflegen").toHaveCount(0);
    await click(".o_wb_voip_annehmen");
    await uhr.vorspulen(0);
    await animationFrame();
    expect(sitzung.hat("accept").length).toBe(1);
    expect(".o_wb_voip_annehmen").toHaveCount(0);
    expect(".o_wb_voip_auflegen").toHaveCount(1);
    dienst.stop();
});

test("Eingehend: Hinweis an der Notiz, dass Kollegen sie sehen", async () => {
    const { dienst, uhr, fabrik } = await echterDienst();
    await softphoneMit(dienst);
    fabrik.letzterAgent.eingehend("0170 5555");
    await uhr.vorspulen(0);
    await animationFrame();
    expect(".o_wb_voip_notiz_hinweis").toHaveCount(1);
    expect(".o_wb_voip_notiz_hinweis").toHaveText(
        "Colleagues who missed this call see this note."
    );
    expect(".o_wb_voip_notiz_hinweis").toHaveClass("text-muted");
    expect(".o_wb_voip_notiz").toHaveAttribute("aria-describedby", "wb_voip_notiz_hinweis");
    dienst.stop();
});

test("Notiz landet beim Auflegen im RPC", async () => {
    const { dienst, uhr, fabrik, orm } = await echterDienst();
    await softphoneMit(dienst);
    await dienst.waehlen("0170 1234567");
    fabrik.letzterAgent.invites[0].gegenstelleNimmtAn();
    await uhr.vorspulen(0);
    await animationFrame();
    expect(".o_wb_voip_notiz").toHaveAttribute("maxlength", String(MAX_NOTIZ));
    // ausgehend: kein Hinweis auf Kollegen
    expect(".o_wb_voip_notiz_hinweis").toHaveCount(0);
    await click(".o_wb_voip_notiz");
    await edit("Rückruf am Freitag");
    await animationFrame();
    await click(".o_wb_voip_auflegen");
    await uhr.vorspulen(0);
    const beendet = orm.letzter("wb_voip_beendet");
    expect(beendet.args[1]).toBe("Rückruf am Freitag");
    await animationFrame();
    expect(".o_wb_voip_anrufansicht").toHaveCount(0);
    dienst.stop();
});

test("Anrufansicht: Stumm, Halten, Tastatur, Weiterleiten", async () => {
    const { dienst, uhr, fabrik } = await echterDienst();
    await softphoneMit(dienst);
    await dienst.waehlen("0170 1234567");
    const sitzung = fabrik.letzterAgent.invites[0];
    sitzung.gegenstelleNimmtAn();
    await uhr.vorspulen(0);
    await animationFrame();
    await click(".o_wb_voip_stumm");
    await animationFrame();
    expect(sitzung.hat("mute")).toEqual([["mute", true]]);
    expect(".o_wb_voip_stumm").toHaveAttribute("aria-pressed", "true");
    await click(".o_wb_voip_halten");
    await uhr.vorspulen(0);
    expect(sitzung.hat("hold").length).toBe(1);
    await click(".o_wb_voip_tastatur_umschalten");
    await animationFrame();
    await click(".o_wb_voip_anrufansicht .o_wb_voip_taste[data-taste='5']");
    expect(sitzung.hat("dtmf")).toEqual([["dtmf", "5"]]);
    await click(".o_wb_voip_weiterleiten_umschalten");
    await animationFrame();
    await click(".o_wb_voip_weiterleiten_ziel");
    await edit("+49 30 123");
    await click(".o_wb_voip_weiterleiten");
    await uhr.vorspulen(0);
    expect(sitzung.hat("refer")).toEqual([["refer", "004930123"]]);
    dienst.stop();
});

test("Fenster öffnet sich bei einem Anruf von selbst", async () => {
    patchWithCleanup(session, { wb_voip_aktiv: true });
    const { dienst, uhr, fabrik } = await echterDienst();
    mockService("wb_voip_telefon", () => dienst);
    await mountWithCleanup(SoftphoneFenster, { noMainContainer: true });
    expect(".o_wb_voip_softphone").toHaveCount(0);
    fabrik.letzterAgent.eingehend("0170 5555");
    await uhr.vorspulen(0);
    await animationFrame();
    expect(".o_wb_voip_softphone").toHaveCount(1);
    expect(".o_wb_voip_fenster").toHaveClass("position-fixed");
    dienst.stop();
});

// --- Reiter ---------------------------------------------------------------------------------

test("Reiter-Registry: Standardreiter und Erweiterung", async () => {
    const reiter = registry.category("wb_voip_reiter");
    expect(reiter.getAll().length).toBeGreaterThan(3);
    for (const schluessel of ["waehlen", "anrufe", "kontakte", "aufgaben"]) {
        expect(reiter.contains(schluessel)).toBe(true);
    }
    class Extra extends Component {
        static template = xml`<div class="o_test_extra">Extra-Inhalt</div>`;
        static props = ["*"];
    }
    reiter.add("test_extra", { titel: "Extra", Component: Extra, sequence: 99 });
    try {
        await softphoneMit(attrappe("verbunden"));
        expect(".o_wb_voip_reiter button").toHaveCount(reiter.getAll().length);
        await click(".o_wb_voip_reiter button:contains(Extra)");
        await animationFrame();
        expect(".o_test_extra").toHaveCount(1);
    } finally {
        reiter.remove("test_extra");
    }
});

test("Reiter Anrufe zeigt eigene Anrufe und wählt zurück", async () => {
    onRpc("wb.voip.anruf", "wb_voip_liste", ({ kwargs }) => {
        expect.step(`liste ${kwargs.limit}`);
        return [
            {
                id: 3,
                richtung: "eingehend",
                nummer: "0170 999",
                zustand: "beendet",
                partner: [1, "Erika Muster"],
                res_model: false,
                res_id: false,
                beginn: "2026-11-24 10:00:00",
                dauer_sek: 65,
                protokoll_id: 4,
            },
        ];
    });
    const dienst = attrappe("verbunden");
    await softphoneMit(dienst);
    await click(".o_wb_voip_reiter button:contains(Calls)");
    await animationFrame();
    expect.verifySteps(["liste 50"]);
    expect(".o_wb_voip_anrufliste .o_wb_voip_eintrag").toHaveCount(1);
    expect(".o_wb_voip_anrufliste .o_wb_voip_eintrag").toHaveText(/Erika Muster/);
    expect(".o_wb_voip_anrufliste .o_wb_voip_eintrag").toHaveText(/1:05/);
    await click(".o_wb_voip_anrufliste .o_wb_voip_eintrag_waehlen");
    expect(dienst.aufrufe).toEqual([
        ["waehlen", "0170 999", { resModel: "res.partner", resId: 1 }],
    ]);
    // nach Anrufende neu laden
    dienst.bus.trigger("anruf-beendet", { anrufId: 3 });
    await animationFrame();
    expect.verifySteps(["liste 50"]);
});

test("Reiter Kontakte sucht und wählt", async () => {
    onRpc("res.partner", "wb_voip_kontakte_suchen", ({ args, kwargs }) => {
        expect.step(`suche ${args[0]}`);
        return [{ id: 1, name: "Erika Muster", nummer: "+49 170 1234567", firma: false }];
    });
    const dienst = attrappe("verbunden");
    await softphoneMit(dienst);
    await click(".o_wb_voip_reiter button:contains(Contacts)");
    await animationFrame();
    expect.verifySteps(["suche "]);
    await click(".o_wb_voip_kontaktsuche");
    await edit("Eri");
    await press("Enter");
    await animationFrame();
    expect.verifySteps(["suche Eri"]);
    await click(".o_wb_voip_kontaktliste .o_wb_voip_eintrag_waehlen");
    expect(dienst.aufrufe).toEqual([
        ["waehlen", "+49 170 1234567", { resModel: "res.partner", resId: 1 }],
    ]);
});

test("Reiter Aufgaben wählt mit Datensatz der Aufgabe", async () => {
    onRpc("mail.activity", "wb_voip_aufgaben", () => [
        {
            id: 8,
            titel: "Angebot besprechen",
            faellig: "2026-11-25",
            res_model: "res.partner",
            res_id: 1,
            res_name: "Erika Muster",
            partner: [1, "Erika Muster"],
            nummer: "+49 170 1234567",
        },
    ]);
    const dienst = attrappe("verbunden");
    await softphoneMit(dienst);
    await click(".o_wb_voip_reiter button:contains(Tasks)");
    await animationFrame();
    expect(".o_wb_voip_aufgabenliste .o_wb_voip_eintrag").toHaveText(/Angebot besprechen/);
    await click(".o_wb_voip_aufgabenliste .o_wb_voip_eintrag_waehlen");
    expect(dienst.aufrufe).toEqual([
        ["waehlen", "+49 170 1234567", { resModel: "res.partner", resId: 1 }],
    ]);
});

// --- Telefonfeld ----------------------------------------------------------------------------

test("Telefonsymbol im Kontaktformular wählt mit Datensatz", async () => {
    patchWithCleanup(session, { wb_voip_aktiv: true });
    const dienst = attrappe("verbunden");
    mockService("wb_voip_telefon", () => dienst);
    const partnerId = await kontaktAnlegen();
    await mountView({
        type: "form",
        resModel: "res.partner",
        resId: partnerId,
        arch: `<form><sheet><group><field name="phone" widget="phone"/></group></sheet></form>`,
    });
    expect(".o_field_phone .o_wb_voip_waehlen").toHaveCount(1);
    expect(".o_field_phone .o_wb_voip_waehlen").toHaveAttribute("aria-label");
    await click(".o_field_phone .o_wb_voip_waehlen");
    expect(dienst.aufrufe).toEqual([
        ["waehlen", "+49 170 1234567", { resModel: "res.partner", resId: partnerId }],
    ]);
});

test("Telefonsymbol nur bei aktivem Softphone", async () => {
    patchWithCleanup(session, { wb_voip_aktiv: false });
    const partnerId = await kontaktAnlegen();
    await mountView({
        type: "form",
        resModel: "res.partner",
        resId: partnerId,
        arch: `<form><sheet><group><field name="phone" widget="phone"/></group></sheet></form>`,
    });
    expect(".o_field_phone").toHaveCount(1);
    expect(".o_wb_voip_waehlen").toHaveCount(0);
});

// --- Desktop-Benachrichtigung ---------------------------------------------------------------

function fakeNotificationApi(permission) {
    const api = function (titel, optionen) {
        api.gezeigt.push([titel, optionen]);
        this.close = () => api.geschlossen++;
    };
    api.permission = permission;
    api.gezeigt = [];
    api.geschlossen = 0;
    api.anfragen = 0;
    api.requestPermission = async () => {
        api.anfragen++;
        api.permission = api.antwort || "denied";
        return api.permission;
    };
    return api;
}

test("Benachrichtigung: Erlaubnis nur einmal anfragen, dann zeigen", async () => {
    const api = fakeNotificationApi("default");
    api.antwort = "granted";
    const b = new Benachrichtiger({ api, hatFokus: () => false });
    await b.eingehend({ nummer: "0170 1", partner: false });
    expect(api.anfragen).toBe(1);
    expect(api.gezeigt.length).toBe(1);
    expect(api.gezeigt[0][1].body).toBe("0170 1");
    b.schliessen();
    expect(api.geschlossen).toBe(1);
    await b.eingehend({ nummer: "0170 2", partner: [1, "Erika Muster"] });
    expect(api.anfragen).toBe(1);
    expect(api.gezeigt[1][1].body).toBe("Erika Muster (0170 2)");
});

test("Benachrichtigung: abgelehnt blockiert nie", async () => {
    const api = fakeNotificationApi("default");
    api.antwort = "denied";
    const b = new Benachrichtiger({ api, hatFokus: () => false });
    await b.eingehend({ nummer: "0170 1", partner: false });
    await b.eingehend({ nummer: "0170 1", partner: false });
    expect(api.anfragen).toBe(1);
    expect(api.gezeigt.length).toBe(0);
    // ohne API (alter Browser) kein Fehler
    const ohne = new Benachrichtiger({ api: null, hatFokus: () => false });
    await ohne.eingehend({ nummer: "1", partner: false });
    expect(ohne.erlaubnis).toBe("unsupported");
});

test("Benachrichtigung: nicht bei sichtbarem Fenster mit Fokus", async () => {
    const api = fakeNotificationApi("granted");
    const b = new Benachrichtiger({ api, hatFokus: () => true });
    await b.eingehend({ nummer: "0170 1", partner: false });
    expect(api.gezeigt.length).toBe(0);
});

test("Softphone: Knopf fragt Desktop-Benachrichtigung an", async () => {
    const api = fakeNotificationApi("default");
    api.antwort = "granted";
    mockService("wb_voip_benachrichtigung", () => new Benachrichtiger({ api, hatFokus: () => true }));
    await softphoneMit(attrappe("verbunden"));
    await click(".o_wb_voip_benachrichtigung_erlauben");
    await animationFrame();
    expect(api.anfragen).toBe(1);
    expect(".o_wb_voip_benachrichtigung_erlauben").toHaveCount(0);
});

test("Fehler des Dienstes wird im Softphone angezeigt", async () => {
    const dienst = attrappe("verbunden", { fehler: "A call is already in progress." });
    await softphoneMit(dienst);
    expect(".o_wb_voip_fehler").toHaveText("A call is already in progress.");
    expect(queryAll(".o_wb_voip_softphone button[aria-label]").length).toBeGreaterThan(5);
    expect(queryOne(".o_wb_voip_softphone")).not.toBe(null);
});

test("Benutzer ohne Nebenstelle: „nicht eingerichtet“ mit Hinweis für Administratoren", async () => {
    patchWithCleanup(session, { wb_voip_manager: true });
    mockService("wb_voip_telefon", () =>
        attrappe("aus", { fehler: "The phone is not set up for this user." })
    );
    await mountWithCleanup(Softphone);
    expect(".o_wb_voip_status").toHaveText(/Telephony is not set up yet\./);
    expect(".o_wb_voip_hinweis_manager").toHaveText(/SIP user/);
    expect(".o_wb_voip_fehler").toHaveCount(0);
});
