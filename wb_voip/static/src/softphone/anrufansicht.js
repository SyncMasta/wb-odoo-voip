import { Component, onMounted, onWillUnmount, useEffect, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { useBus, useService } from "@web/core/utils/hooks";

import { AktionsLeiste, ORT_SOFTPHONE, useAktionAusfuehren } from "../aktionen/aktions_leiste";

import { MAX_NOTIZ } from "../dienste/telefon_dienst";
import { TeamListe } from "../team/team_liste";
import { formatDauer } from "./waehlen_hilfe";
import { Tastenfeld } from "./waehltastatur";

/**
 * Laufender oder klingelnder Anruf: Gegenüber, Dauer, Bedienung und Notiz. Mit zweiter
 * Leitung zusätzlich die Leiste „Gehalten“ (Zurück, Verbinden) bzw. der wartende Anruf.
 */
export class Anrufansicht extends Component {
    static template = "wb_voip.Anrufansicht";
    static components = { Tastenfeld, AktionsLeiste, TeamListe };
    static props = {};

    setup() {
        this.telefon = useService("wb_voip_telefon");
        this.notification = useService("notification");
        this.orm = useService("orm");
        this._aktionAusfuehren = useAktionAusfuehren();
        this.ort = ORT_SOFTPHONE;
        this.state = useState(this.telefon.state);
        this.lokal = useState({
            tastatur: false,
            weiterleiten: false,
            team: false,
            ziel: "",
            jetzt: this.telefon.uhr.jetzt(),
            aktionen: [],
        });
        this._aktionenNr = 0;
        // Buttons einmal je Anruf laden (kein Polling) und nach der Annahme noch einmal,
        // weil manche nur „während des Gesprächs“ gelten
        useEffect(
            (id) => {
                this.aktionenLaden(id);
            },
            () => [this.state.anruf?.id]
        );
        useBus(this.telefon.bus, "anruf-angenommen", ({ detail }) => {
            if (detail?.anrufId === this.state.anruf?.id) {
                this.aktionenLaden(detail.anrufId);
            }
        });
        this.maxNotiz = MAX_NOTIZ;
        onMounted(() => {
            this._takt = browser.setInterval(() => {
                this.lokal.jetzt = this.telefon.uhr.jetzt();
            }, 1000);
        });
        onWillUnmount(() => browser.clearInterval(this._takt));
    }

    /** Aktive Leitung; endet sie während „Verbinden“, bleibt das gehaltene Gespräch sichtbar. */
    get anruf() {
        return this.state.anruf || this.state.gehaltener;
    }

    get verbindet() {
        return Boolean(this.state.verbindet);
    }

    get konferenz() {
        return Boolean(this.state.konferenz);
    }

    /** Konferenz möglich: aktive und gehaltene Leitung, beide angenommen, kein „Verbinden“. */
    get konferenzMoeglich() {
        return Boolean(
            this.state.anruf &&
                this.gehaltener &&
                this.laeuft &&
                this.gehaltener.zustand === "laeuft" &&
                !this.verbindet &&
                !this.konferenz
        );
    }

    /** Halten, Tastatur und Weiterleiten: nur im eigenen Gespräch ohne Verbinden/Konferenz. */
    get bedienungGesperrt() {
        return !this.laeuft || this.verbindet || this.konferenz;
    }

    /** „Zurück“ legt die aktive Leitung auf: eine Rückfrage oder den angenommenen zweiten Anruf. */
    get zurueckText() {
        return this.state.anruf?.rueckfrage ? _t("Rückfrage beenden") : _t("Auflegen und zurück");
    }

    async aktionenLaden(id) {
        const nr = ++this._aktionenNr;
        if (!Number.isInteger(id)) {
            this.lokal.aktionen = [];
            return;
        }
        let aktionen;
        try {
            // nur die Buttons des Softphones, nicht der ganze Anrufer-Kontext
            aktionen = await this.orm.call("wb.voip.anruf", "wb_voip_aktionen", [
                [id],
                ORT_SOFTPHONE,
            ]);
        } catch {
            // ohne Buttons bleibt die Anrufansicht voll benutzbar
            aktionen = null;
        }
        if (nr === this._aktionenNr && id === this.state.anruf?.id) {
            this.lokal.aktionen = Array.isArray(aktionen) ? aktionen : [];
        }
    }

    aktionAusfuehren(aktionId) {
        const id = this.state.anruf?.id;
        if (!Number.isInteger(id)) {
            return;
        }
        return this._aktionAusfuehren(() =>
            this.orm.call("wb.voip.anruf", "wb_voip_aktion_ausfuehren", [[id], aktionId])
        );
    }

    /** Name des Kontakts, sonst die Nummer. */
    nameVon(anruf) {
        return anruf.partner ? anruf.partner[1] : anruf.nummer;
    }

    get name() {
        return this.nameVon(this.anruf);
    }

    get gehaltener() {
        return this.state.gehaltener;
    }

    get klopfend() {
        return this.state.klopfend;
    }

    /** Eine zweite Leitung ist frei: Rückfrage und Team-Auswahl möglich. */
    get zweiteFrei() {
        return this.laeuft && !this.gehaltener && !this.klopfend;
    }

    get eingehendKlingelt() {
        return this.anruf.richtung === "eingehend" && !this.anruf.annahme;
    }

    get laeuft() {
        return this.anruf.zustand === "laeuft";
    }

    get zustandText() {
        if (this.verbindet) {
            return _t("Verbinde …");
        }
        switch (this.anruf.zustand) {
            case "laeuft":
                return _t("Im Gespräch");
            case "klingelt":
                return this.anruf.richtung === "eingehend"
                    ? _t("Eingehender Anruf")
                    : _t("Klingelt …");
            default:
                return _t("Wählt …");
        }
    }

    get dauer() {
        if (!this.anruf.annahme) {
            return "";
        }
        return formatDauer((this.lokal.jetzt - this.anruf.annahme) / 1000);
    }

    annehmen() {
        this.telefon.annehmen();
    }

    ablehnen() {
        this.telefon.ablehnen();
    }

    auflegen() {
        this.telefon.auflegen();
    }

    stumm() {
        this.telefon.stumm(!this.anruf.stumm);
    }

    halten() {
        this.telefon.halten(!this.anruf.gehalten);
    }

    tastaturUmschalten() {
        this.lokal.tastatur = !this.lokal.tastatur;
        this.lokal.weiterleiten = false;
        this.lokal.team = false;
    }

    weiterleitenUmschalten() {
        this.lokal.weiterleiten = !this.lokal.weiterleiten;
        this.lokal.tastatur = false;
        this.lokal.team = false;
    }

    teamUmschalten() {
        this.lokal.team = !this.lokal.team;
        this.lokal.tastatur = false;
        this.lokal.weiterleiten = false;
    }

    teamFertig() {
        this.lokal.team = false;
    }

    klopfendAnnehmen() {
        return this.telefon.annehmen();
    }

    klopfendAblehnen() {
        return this.telefon.ablehnen();
    }

    zurueck() {
        return this.telefon.zurueck();
    }

    async verbinden() {
        if (!(await this.telefon.verbinden())) {
            this._fehler(_t("Verbinden nicht möglich."));
        }
    }

    async konferenzStarten() {
        if (!(await this.telefon.konferenz())) {
            this._fehler(_t("Konferenz nicht möglich."));
        }
    }

    konferenzBeenden() {
        return this.telefon.konferenzBeenden();
    }

    async rueckfrage() {
        const ziel = this.lokal.ziel.trim();
        if (!ziel) {
            return;
        }
        if (await this.telefon.rueckfrage(ziel)) {
            this.lokal.weiterleiten = false;
            this.lokal.ziel = "";
        } else {
            this._fehler(_t("Rückfrage nicht möglich."));
        }
    }

    _fehler(standard) {
        this.notification.add(this.state.fehler || standard, { type: "danger" });
    }

    dtmf(ziffer) {
        this.telefon.dtmf(ziffer);
    }

    onZielKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.weiterleiten();
        }
    }

    async weiterleiten() {
        const ziel = this.lokal.ziel.trim();
        if (!ziel) {
            return;
        }
        if (await this.telefon.weiterleiten(ziel)) {
            this.lokal.weiterleiten = false;
            this.lokal.ziel = "";
        } else {
            this._fehler(_t("Weiterleiten nicht möglich."));
        }
    }
}
