import { Component, onMounted, onWillUnmount, useEffect, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { useBus, useService } from "@web/core/utils/hooks";

import { AktionsLeiste, ORT_SOFTPHONE, useAktionAusfuehren } from "../aktionen/aktions_leiste";

import { MAX_NOTIZ } from "../dienste/telefon_dienst";
import { formatDauer } from "./waehlen_hilfe";
import { Tastenfeld } from "./waehltastatur";

/** Laufender oder klingelnder Anruf: Gegenüber, Dauer, Bedienung und Notiz. */
export class Anrufansicht extends Component {
    static template = "wb_voip.Anrufansicht";
    static components = { Tastenfeld, AktionsLeiste };
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

    get anruf() {
        return this.state.anruf;
    }

    async aktionenLaden(id) {
        const nr = ++this._aktionenNr;
        if (!Number.isInteger(id)) {
            this.lokal.aktionen = [];
            return;
        }
        let daten;
        try {
            daten = await this.orm.call("wb.voip.anruf", "wb_voip_kontext", [[id]]);
        } catch {
            // ohne Buttons bleibt die Anrufansicht voll benutzbar
            daten = null;
        }
        if (nr === this._aktionenNr && id === this.state.anruf?.id) {
            this.lokal.aktionen = daten?.aktionen || [];
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

    get name() {
        return this.anruf.partner ? this.anruf.partner[1] : this.anruf.nummer;
    }

    get eingehendKlingelt() {
        return this.anruf.richtung === "eingehend" && !this.anruf.annahme;
    }

    get laeuft() {
        return this.anruf.zustand === "laeuft";
    }

    get zustandText() {
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
    }

    weiterleitenUmschalten() {
        this.lokal.weiterleiten = !this.lokal.weiterleiten;
        this.lokal.tastatur = false;
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
            this.notification.add(
                this.state.fehler || _t("Weiterleiten nicht möglich."),
                { type: "danger" }
            );
        }
    }
}
