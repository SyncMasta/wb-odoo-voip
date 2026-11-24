import { Component, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import {
    deserializeDate,
    deserializeDateTime,
    formatDate,
    formatDateTime,
} from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

import { AktionsLeiste, ORT_FENSTER, fehlertext, useAktionAusfuehren } from "../aktionen/aktions_leiste";
import { MAX_NOTIZ } from "../dienste/telefon_dienst";
import { formatDauer } from "../softphone/waehlen_hilfe";

/** Nach dem Anrufende bleibt das Fenster so lange offen (Einstellung je Benutzer später). */
export const SCHLIESSEN_NACH_MS = 10_000;
export const BUS_KONTEXT = "wb_voip/kontext";
/** Menü des „Mehr“-Auswahlfelds; Odoo hängt es außerhalb des Panels ein. */
const MENUE = ".o-dropdown--menu";

/** True, wenn ``el`` ein Eingabefeld oder editierbarer Bereich ist (dort nie Fokus wegnehmen). */
export function tipptGerade(el) {
    if (!el || el === document.body) {
        return false;
    }
    return (
        ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) ||
        el.isContentEditable === true ||
        el.getAttribute?.("contenteditable") === "true"
    );
}

/**
 * Seitenpanel rechts mit Kontakt, letzten Protokollen, offenen Aufgaben, Notiz und den
 * Anruf-Buttons. Öffnet sich nur im Telefon-Tab (Ereignis ``anruf-aktiv`` des Dienstes)
 * und schließt sich manuell oder ``SCHLIESSEN_NACH_MS`` nach dem Anrufende.
 */
export class AnruferFenster extends Component {
    static template = "wb_voip.AnruferFenster";
    static components = { AktionsLeiste };
    static props = {};

    setup() {
        this.telefon = useService("wb_voip_telefon");
        this.orm = useService("orm");
        this.action = useService("action");
        this._ausfuehren = useAktionAusfuehren();
        this.ort = ORT_FENSTER;
        this.busService = useService("bus_service");
        this.state = useState(this.telefon.state);
        this.fenster = useState({
            offen: false,
            anrufId: false,
            daten: null,
            fehler: null,
            beendet: false,
        });
        this.maxNotiz = MAX_NOTIZ;
        this.panel = useRef("panel");
        this._ladeNr = 0;
        this._timer = null;
        this._vorher = null;
        // Zeiger über dem Panel oder Fokus auf einem Element darin: nicht automatisch schließen
        this._zeiger = false;
        this._fokusInnen = false;

        useBus(this.telefon.bus, "anruf-aktiv", ({ detail }) => this._aktiv(detail));
        useBus(this.telefon.bus, "anruf-beendet", ({ detail }) => this._beendet(detail));
        // Buttons mit Zustand „Während des Gesprächs“ gibt der Server erst nach der Annahme
        useBus(this.telefon.bus, "anruf-angenommen", ({ detail }) => {
            if (this.fenster.offen && detail?.anrufId === this.fenster.anrufId) {
                this.laden();
            }
        });

        this._beiKontext = (payload) => {
            const id = payload?.anruf_id;
            if (this.fenster.offen && Number.isInteger(id) && id === this.fenster.anrufId) {
                this.laden();
            }
        };
        if (session.wb_voip_aktiv) {
            this.busService.subscribe(BUS_KONTEXT, this._beiKontext);
        }

        useEffect(
            (offen) => {
                if (!offen) {
                    return;
                }
                // Fokus nur holen, wenn niemand gerade tippt
                if (!tipptGerade(document.activeElement)) {
                    this.panel.el?.focus();
                }
                document.body.classList.add("o_wb_voip_af_offen");
                // Fokus im „Mehr“-Menü (liegt außerhalb des Panels) zählt als Fokus im Panel
                const beiFokus = (ev) => {
                    if (this._innen(ev.target)) {
                        this._fokusInnen = true;
                        this._timerStoppen();
                    } else if (this._fokusInnen) {
                        this._fokusInnen = false;
                        this._schliessenPlanen();
                    }
                };
                document.addEventListener("focusin", beiFokus);
                return () => {
                    document.removeEventListener("focusin", beiFokus);
                    document.body.classList.remove("o_wb_voip_af_offen");
                };
            },
            () => [this.fenster.offen]
        );

        onWillUnmount(() => {
            this._timerStoppen();
            if (session.wb_voip_aktiv) {
                this.busService.unsubscribe(BUS_KONTEXT, this._beiKontext);
            }
        });
    }

    // --- Öffnen und Schließen ---------------------------------------------------------

    _aktiv(detail) {
        const id = detail?.anrufId;
        if (!detail?.eigenerTab || !Number.isInteger(id)) {
            return;
        }
        this._timerStoppen();
        // der Merker aus dem vorigen Anruf kann veraltet sein (Element zwischenzeitlich entfernt)
        this._fokusInnen = this._innen(document.activeElement);
        if (!this.fenster.offen) {
            const aktiv = document.activeElement;
            this._vorher = aktiv && aktiv !== document.body ? aktiv : null;
        }
        Object.assign(this.fenster, {
            offen: true,
            anrufId: id,
            daten: null,
            fehler: null,
            beendet: false,
        });
        this.laden();
    }

    _beendet(detail) {
        if (!this.fenster.offen || detail?.anrufId !== this.fenster.anrufId) {
            return;
        }
        this.fenster.beendet = true;
        // einmal neu laden: Buttons „Nach dem Anruf“ für die Zeit bis zum Schließen
        this.laden();
        this._schliessenPlanen();
    }

    /** Startet die Frist bis zum automatischen Schließen, sofern niemand das Panel benutzt. */
    _schliessenPlanen() {
        this._timerStoppen();
        if (this._fokusInnen && !this._innen(document.activeElement)) {
            // Merker veraltet: das Element mit Fokus ist weg (kein focusout bei Entfernung)
            this._fokusInnen = false;
        }
        if (!this.fenster.offen || !this.fenster.beendet || this._zeiger || this._fokusInnen) {
            return;
        }
        this._timer = this.telefon.uhr.setTimeout(() => {
            this._timer = null;
            this.schliessen();
        }, SCHLIESSEN_NACH_MS);
    }

    onZeigerEin() {
        this._zeiger = true;
        this._timerStoppen();
    }

    onZeigerAus() {
        this._zeiger = false;
        this._schliessenPlanen();
    }

    /** Fokus auf einem Element im Panel (nicht das Panel selbst, das ist nur der Anker). */
    onFokusEin(ev) {
        if (ev.target === this.panel.el) {
            return;
        }
        this._fokusInnen = true;
        this._timerStoppen();
    }

    /** True, wenn ``el`` im Panel (nicht das Panel selbst) oder im „Mehr“-Menü liegt. */
    _innen(el) {
        const panel = this.panel.el;
        if (!el || !panel || el === panel) {
            return false;
        }
        return panel.contains(el) || Boolean(el.closest?.(MENUE));
    }

    onFokusAus(ev) {
        if (this._innen(ev.relatedTarget)) {
            return;
        }
        this._fokusInnen = false;
        this._schliessenPlanen();
    }

    _timerStoppen() {
        if (this._timer !== null) {
            this.telefon.uhr.clearTimeout(this._timer);
            this._timer = null;
        }
    }

    schliessen() {
        this._timerStoppen();
        this._zeiger = false;
        this._fokusInnen = false;
        this._ladeNr++;
        Object.assign(this.fenster, { offen: false, anrufId: false, daten: null, fehler: null });
        const vorher = this._vorher;
        this._vorher = null;
        if (vorher?.isConnected) {
            vorher.focus();
        }
    }

    onKeydown(ev) {
        if (ev.key === "Escape") {
            ev.preventDefault();
            ev.stopPropagation();
            this.schliessen();
        }
    }

    // --- Daten ------------------------------------------------------------------------

    async laden() {
        const id = this.fenster.anrufId;
        const nr = ++this._ladeNr;
        let daten;
        try {
            daten = await this.orm.call("wb.voip.anruf", "wb_voip_kontext", [[id]]);
        } catch (fehler) {
            if (nr === this._ladeNr) {
                this.fenster.fehler = fehlertext(fehler);
            }
            return;
        }
        // zwischenzeitlich ein anderer Anruf oder geschlossen: Antwort verwerfen
        if (nr !== this._ladeNr || id !== this.fenster.anrufId) {
            return;
        }
        this.fenster.daten = daten || null;
        this.fenster.fehler = null;
    }

    get daten() {
        return this.fenster.daten;
    }

    get kontakt() {
        return this.daten?.kontakt || false;
    }

    get nummer() {
        return this.daten?.anruf?.nummer || "";
    }

    get bildUrl() {
        const id = this.kontakt?.id;
        return Number.isInteger(id) ? `/web/image/res.partner/${id}/avatar_128` : false;
    }

    /** Notiz nur, solange der gezeigte Anruf der laufende ist (ein Zustand mit der Anrufansicht). */
    get laufenderAnruf() {
        const anruf = this.state.anruf;
        return anruf && anruf.id && anruf.id === this.fenster.anrufId ? anruf : null;
    }

    get protokolle() {
        return (this.daten?.protokolle || []).map((p) => ({
            id: p.id,
            datum: p.datum ? formatDateTime(deserializeDateTime(p.datum)) : "",
            ergebnis: p.ergebnis_label || "",
            dauer: p.dauer_sek ? formatDauer(p.dauer_sek) : "",
            text: p.beschreibung_text || "",
        }));
    }

    get aufgaben() {
        return (this.daten?.aufgaben || []).map((a) => ({
            ...a,
            faellig: a.faellig ? formatDate(deserializeDate(a.faellig)) : "",
        }));
    }

    /** Buttons für das Fenster (der Server wählt aus, z. B. „Neuer Kontakt“ nur ohne Kontakt). */
    get aktionen() {
        return this.daten?.aktionen || [];
    }

    // --- Gesprächsknöpfe (dieselben Dienst-Methoden wie die Anrufansicht) ---------------

    /** Eingehender Anruf, der noch klingelt: Annehmen/Ablehnen statt Stumm/Auflegen. */
    get klingeltEingehend() {
        const anruf = this.laufenderAnruf;
        return Boolean(anruf && anruf.richtung === "eingehend" && !anruf.annahme);
    }

    get imGespraech() {
        return this.laufenderAnruf?.zustand === "laeuft";
    }

    annehmen() {
        return this.telefon.annehmen();
    }

    ablehnen() {
        return this.telefon.ablehnen();
    }

    auflegen() {
        return this.telefon.auflegen();
    }

    stumm() {
        const anruf = this.laufenderAnruf;
        if (anruf) {
            this.telefon.stumm(!anruf.stumm);
        }
    }

    // --- Bedienung --------------------------------------------------------------------

    aktionAusfuehren(aktionId) {
        const id = this.fenster.anrufId;
        return this._ausfuehren(() =>
            this.orm.call("wb.voip.anruf", "wb_voip_aktion_ausfuehren", [[id], aktionId])
        );
    }

    zuordnen() {
        const id = this.fenster.anrufId;
        return this._ausfuehren(() =>
            this.orm.call("wb.voip.anruf", "action_wb_voip_zuordnen", [[id]])
        );
    }

    datensatzOeffnen(resModel, resId) {
        if (typeof resModel !== "string" || !Number.isInteger(resId)) {
            return;
        }
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: resModel,
            res_id: resId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    kontaktOeffnen() {
        return this.datensatzOeffnen("res.partner", this.kontakt?.id);
    }

    aufgabeOeffnen(aufgabe) {
        return this.datensatzOeffnen(aufgabe.res_model, aufgabe.res_id);
    }
}

registry.category("main_components").add("wb_voip.AnruferFenster", {
    Component: AnruferFenster,
});
