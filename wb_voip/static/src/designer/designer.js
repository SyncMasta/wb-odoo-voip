import { Component, onPatched, onWillStart, useEffect, useRef, useState } from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { MultiRecordSelector } from "@web/core/record_selectors/multi_record_selector";
import { RecordSelector } from "@web/core/record_selectors/record_selector";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useSortable } from "@web/core/utils/sortable_owl";
import { useSetupAction } from "@web/search/action_hook";

import { ORT_FENSTER, ORT_POPUP, fehlertext } from "../aktionen/aktions_leiste";
import { BREITEN, LayoutBloecke, artLabel } from "../layout/bloecke";

/** Grenzen wie auf dem Server (``wb_voip_layout.py``). */
export const MAX_BLOECKE = 30;
export const MAX_FELDER = 30;
const TEXT_MAX = 2000;
const TITEL_MAX = 200;
const ANZAHL_MIN = 1;
const ANZAHL_MAX = 20;
const GEZAEHLT = ["protokolle", "aufgaben"];

let naechsteUid = 0;

/** Block aus den Serverdaten in die Form des Entwurfs (Texte nie ``false`` in Eingabefeldern). */
function blockAusServer(b) {
    naechsteUid += 1;
    return {
        uid: naechsteUid,
        art: b.art,
        titel: typeof b.titel === "string" ? b.titel : "",
        spalte: b.spalte === "2" ? "2" : "1",
        anzahl: GEZAEHLT.includes(b.art) && Number.isInteger(b.anzahl) ? b.anzahl : 5,
        text: typeof b.text === "string" ? b.text : "",
        feld_ids: Array.isArray(b.feld_ids) ? b.feld_ids.filter(Number.isInteger) : [],
    };
}

/** Block so, wie ``wb_voip_designer_speichern`` ihn erwartet. */
function blockDaten(b) {
    return {
        art: b.art,
        titel: b.titel ? b.titel : false,
        spalte: b.spalte === "2" ? "2" : "1",
        anzahl: GEZAEHLT.includes(b.art) ? b.anzahl : false,
        text: b.art === "text" && b.text ? b.text : false,
        feld_ids: b.art === "felder" ? [...b.feld_ids] : [],
    };
}

/** Platzhalter der Vorschau, solange kein Beispielkontakt Daten liefert. */
function beispiele() {
    return {
        kontakt: { id: false, name: _t("Beispielkontakt"), firma: false, telefon: "+49 …", email: false },
        protokolle: [
            {
                id: 0,
                datum: false,
                ergebnis_label: _t("Beispiel"),
                dauer_sek: 0,
                beschreibung_text: _t("Letztes Gespräch"),
            },
        ],
        aufgaben: [{ id: 0, titel: _t("Beispielaufgabe"), faellig: false }],
        aktionen: [
            {
                id: 0,
                name: _t("Beispiel-Button"),
                icon: false,
                farbe: "secondary",
                art: "url",
                orte: [ORT_FENSTER],
            },
        ],
    };
}

/**
 * Designer für Fenster-Layouts (Client-Aktion ``wb_voip.layout_designer``, nur für
 * Administratoren von WB VoIP; der Server prüft jede Anfrage selbst). Links die Blockarten
 * des Fensters, in der Mitte die Spalten mit Ziehen und Ablegen (oder Hoch/Runter per
 * Tastatur), rechts die Eigenschaften des gewählten Blocks, darunter die Live-Vorschau mit
 * denselben Komponenten wie im Anrufer-Fenster und im Popup.
 */
export class LayoutDesigner extends Component {
    static template = "wb_voip.LayoutDesigner";
    static components = { LayoutBloecke, MultiRecordSelector, RecordSelector };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.wurzel = useRef("wurzel");
        this.spaltenRef = useRef("spalten");
        this.state = useState({
            daten: null,
            entwurf: null,
            gewaehlt: false,
            feldsuche: "",
            partnerId: false,
            vorschau: null,
            speichert: false,
            fehler: null,
            // Live-Bereich für Screenreader (Verschieben)
            meldung: "",
            vorschauFehler: "",
        });
        this.beispielAnruf = useState({ id: 0, notiz: "" });
        this.original = "";
        this._vorschauNr = 0;
        this._fokus = null;

        useSortable({
            ref: this.spaltenRef,
            elements: ".o_wb_voip_ds_block",
            groups: ".o_wb_voip_ds_liste",
            connectGroups: true,
            handle: ".o_wb_voip_ds_griff",
            placeholderClasses: ["o_wb_voip_ds_platzhalter"],
            onDrop: (params) => this.fallenLassen(params),
        });

        onWillStart(() => this.laden(this.props.action?.params?.layout_id || false));

        // Feldwerte nur neu laden, wenn Kontakt oder Feldauswahl sich ändern
        useEffect(
            (partnerId) => {
                if (this.state.entwurf) {
                    this.vorschauLaden(partnerId);
                }
            },
            () => [this.state.partnerId, this.vorschauSignatur]
        );

        onPatched(() => {
            this._fokusSetzen();
            this._ariaSetzen();
        });
        useEffect(() => this._ariaSetzen());

        // ungespeicherte Änderungen nicht stillschweigend verlieren
        useSetupAction({
            beforeLeave: () => (this.geaendert ? this.verwerfenBestaetigen() : true),
            beforeUnload: (ev) => {
                if (this.geaendert) {
                    ev.preventDefault();
                    ev.returnValue = "";
                }
            },
        });
    }

    /** Fragt, ob ungespeicherte Änderungen verworfen werden dürfen (Promise auf true/false). */
    verwerfenBestaetigen() {
        return new Promise((resolve) => {
            this.dialog.add(
                ConfirmationDialog,
                {
                    title: _t("Ungespeicherte Änderungen"),
                    body: _t("Die Änderungen am Layout sind nicht gespeichert. Verwerfen?"),
                    confirmLabel: _t("Verwerfen"),
                    confirm: () => resolve(true),
                    cancel: () => resolve(false),
                },
                { onClose: () => resolve(false) }
            );
        });
    }

    /** Zugänglicher Name für die Eingabefelder der Odoo-Auswahlkomponenten. */
    _ariaSetzen() {
        const wurzel = this.wurzel.el;
        for (const [klasse, name] of [
            [".o_wb_voip_ds_gruppen", _t("Gruppen")],
            [".o_wb_voip_ds_beispiel", _t("Beispielkontakt")],
        ]) {
            const eingabe = wurzel?.querySelector(`${klasse} input`);
            if (eingabe && eingabe.getAttribute("aria-label") !== name) {
                eingabe.setAttribute("aria-label", name);
            }
        }
    }

    // --- Laden, Speichern, Verwerfen -------------------------------------------------

    async laden(layoutId) {
        let daten;
        try {
            daten = await this.orm.call("wb.voip.layout", "wb_voip_designer_laden", [layoutId]);
        } catch (fehler) {
            this.state.fehler = fehlertext(fehler);
            return;
        }
        this.uebernehmen(daten);
    }

    uebernehmen(daten) {
        this.state.daten = daten;
        const layout = daten.layout;
        this.state.entwurf = {
            id: layout.id,
            name: layout.name || "",
            ort: layout.ort,
            breite: BREITEN.includes(layout.breite) ? layout.breite : "normal",
            ist_standard: Boolean(layout.ist_standard),
            group_ids: [...(layout.group_ids || [])],
            bloecke: (layout.bloecke || []).map(blockAusServer),
        };
        this.original = JSON.stringify(this.speicherDaten());
        this.state.gewaehlt = false;
        this.state.fehler = null;
        if (!this.state.partnerId && Number.isInteger(daten.beispielkontakt_id)) {
            this.state.partnerId = daten.beispielkontakt_id;
        }
    }

    speicherDaten() {
        const e = this.state.entwurf;
        return {
            name: e.name,
            ort: e.ort,
            breite: e.breite,
            group_ids: [...e.group_ids],
            bloecke: e.bloecke.map(blockDaten),
        };
    }

    get geaendert() {
        return Boolean(this.state.entwurf) && JSON.stringify(this.speicherDaten()) !== this.original;
    }

    async speichern() {
        if (!this.gueltig || this.state.speichert) {
            return;
        }
        this.state.speichert = true;
        let daten;
        try {
            daten = await this.orm.call("wb.voip.layout", "wb_voip_designer_speichern", [
                this.state.entwurf.id,
                this.speicherDaten(),
            ]);
        } catch (fehler) {
            this.notification.add(fehlertext(fehler), { type: "danger" });
            return;
        } finally {
            this.state.speichert = false;
        }
        this.uebernehmen(daten);
        this.notification.add(_t("Layout gespeichert."), { type: "success" });
    }

    verwerfen() {
        if (this.state.daten) {
            this.uebernehmen(this.state.daten);
        }
    }

    zuruecksetzen() {
        const id = this.state.entwurf?.id;
        this.dialog.add(ConfirmationDialog, {
            title: _t("Auf Standard zurücksetzen"),
            body: _t("Alle Blöcke werden auf den Auslieferungszustand zurückgesetzt. Fortfahren?"),
            confirm: async () => {
                try {
                    await this.orm.call("wb.voip.layout", "action_auf_standard_zuruecksetzen", [[id]]);
                } catch (fehler) {
                    this.notification.add(fehlertext(fehler), { type: "danger" });
                    return;
                }
                await this.laden(id);
            },
            cancel: () => {},
        });
    }

    async layoutWechseln(ev) {
        const auswahl = ev.target;
        const id = parseInt(auswahl.value, 10);
        if (!Number.isInteger(id)) {
            return;
        }
        if (this.geaendert && !(await this.verwerfenBestaetigen())) {
            // Auswahl zurück auf das bearbeitete Layout
            auswahl.value = String(this.entwurf.id);
            return;
        }
        this.laden(id);
    }

    // --- Abgeleitete Werte -----------------------------------------------------------

    get entwurf() {
        return this.state.entwurf;
    }

    get arten() {
        return this.state.daten?.arten?.[this.entwurf?.ort] || [];
    }

    get orte() {
        return this.state.daten?.orte || [];
    }

    get breiten() {
        return this.state.daten?.breiten || [];
    }

    get layouts() {
        return this.state.daten?.layouts || [];
    }

    get katalog() {
        const katalog = {};
        for (const feld of this.state.daten?.felder || []) {
            katalog[feld.id] = feld;
        }
        return katalog;
    }

    get hatNotiz() {
        return this.entwurf.bloecke.some((b) => b.art === "notiz");
    }

    neuMoeglich(art) {
        return this.entwurf.bloecke.length < MAX_BLOECKE && !(art === "notiz" && this.hatNotiz);
    }

    /** Zwei Spalten nur im Anrufer-Fenster ab „breit“ (das Popup ist immer einspaltig). */
    get zweispaltig() {
        return (
            this.entwurf.ort === ORT_FENSTER && ["breit", "sehr_breit"].includes(this.entwurf.breite)
        );
    }

    get spalten() {
        const bloecke = this.entwurf.bloecke;
        if (!this.zweispaltig) {
            return [{ spalte: "alle", titel: _t("Blöcke"), bloecke }];
        }
        return [
            { spalte: "1", titel: _t("Spalte 1"), bloecke: bloecke.filter((b) => b.spalte !== "2") },
            { spalte: "2", titel: _t("Spalte 2"), bloecke: bloecke.filter((b) => b.spalte === "2") },
        ];
    }

    artName(art) {
        return this.arten.find((a) => a.art === art)?.label || artLabel(art) || art;
    }

    erlaubt(block) {
        return this.arten.some((a) => a.art === block.art);
    }

    /** Fehlertext eines Blocks (leer, wenn er gespeichert werden kann). */
    blockFehler(block) {
        if (!this.erlaubt(block)) {
            return _t("In diesem Fenster nicht erlaubt.");
        }
        if (block.titel.length > TITEL_MAX) {
            return _t("Titel zu lang.");
        }
        if (block.art === "felder" && !block.feld_ids.length) {
            return _t("Bitte mindestens ein Feld wählen.");
        }
        if (block.art === "felder" && block.feld_ids.length > MAX_FELDER) {
            return _t("Zu viele Felder.");
        }
        if (
            GEZAEHLT.includes(block.art) &&
            !(
                Number.isInteger(block.anzahl) &&
                block.anzahl >= ANZAHL_MIN &&
                block.anzahl <= ANZAHL_MAX
            )
        ) {
            return _t("Anzahl 1 bis 20.");
        }
        if (block.art === "text" && block.text.length > TEXT_MAX) {
            return _t("Text zu lang.");
        }
        return "";
    }

    get gueltig() {
        const e = this.entwurf;
        return Boolean(
            e &&
                e.name.trim() &&
                e.bloecke.length <= MAX_BLOECKE &&
                e.bloecke.filter((b) => b.art === "notiz").length <= 1 &&
                e.bloecke.every((b) => !this.blockFehler(b))
        );
    }

    get gewaehlterBlock() {
        return this.entwurf?.bloecke.find((b) => b.uid === this.state.gewaehlt) || null;
    }

    get gefilterteFelder() {
        const block = this.gewaehlterBlock;
        const suche = this.state.feldsuche.trim().toLowerCase();
        const felder = this.state.daten?.felder || [];
        const passend = suche
            ? felder.filter(
                  (f) =>
                      f.label.toLowerCase().includes(suche) || f.name.toLowerCase().includes(suche)
              )
            : felder;
        // gewählte zuerst, in ihrer Reihenfolge
        const gewaehlt = block ? block.feld_ids : [];
        return [
            ...gewaehlt.map((id) => passend.find((f) => f.id === id)).filter(Boolean),
            ...passend.filter((f) => !gewaehlt.includes(f.id)),
        ];
    }

    // --- Bearbeiten ------------------------------------------------------------------

    hinzufuegen(art) {
        if (!this.neuMoeglich(art) || !this.arten.some((a) => a.art === art)) {
            return;
        }
        const block = blockAusServer({ art, spalte: "1", anzahl: 5, feld_ids: [] });
        this.entwurf.bloecke.push(block);
        this.waehlen(block.uid);
    }

    waehlen(uid) {
        this.state.gewaehlt = uid;
        this.state.feldsuche = "";
    }

    entfernen(uid) {
        const index = this.entwurf.bloecke.findIndex((b) => b.uid === uid);
        if (index >= 0) {
            this.entwurf.bloecke.splice(index, 1);
        }
        if (this.state.gewaehlt === uid) {
            this.state.gewaehlt = false;
        }
    }

    /** Blöcke derselben angezeigten Spalte wie ``block`` (in Block-Reihenfolge). */
    nachbarn(block) {
        if (!this.zweispaltig) {
            return this.entwurf.bloecke;
        }
        const zwei = block.spalte === "2";
        return this.entwurf.bloecke.filter((b) => (b.spalte === "2") === zwei);
    }

    kannVerschieben(block, richtung) {
        const liste = this.nachbarn(block);
        const index = liste.findIndex((b) => b.uid === block.uid);
        return Boolean(liste[index + richtung]);
    }

    /** Hoch (-1) oder runter (+1) innerhalb der Spalte; der Fokus bleibt am Block. */
    verschieben(uid, richtung) {
        const bloecke = this.entwurf.bloecke;
        const block = bloecke.find((b) => b.uid === uid);
        if (!block) {
            return;
        }
        const liste = this.nachbarn(block);
        const ziel = liste[liste.findIndex((b) => b.uid === uid) + richtung];
        if (!ziel) {
            return;
        }
        const a = bloecke.findIndex((b) => b.uid === uid);
        const z = bloecke.findIndex((b) => b.uid === ziel.uid);
        const [erster, zweiter] = [bloecke[a], bloecke[z]];
        bloecke[a] = zweiter;
        bloecke[z] = erster;
        this._fokus = { uid, knopf: richtung < 0 ? "hoch" : "runter" };
        this._positionMelden(uid);
    }

    /** Meldet die neue Position eines Blocks in seiner Spalte (Live-Bereich). */
    _positionMelden(uid) {
        const block = this.entwurf.bloecke.find((b) => b.uid === uid);
        if (!block) {
            return;
        }
        const pos = this.nachbarn(block).findIndex((b) => b.uid === uid) + 1;
        const art = this.artName(block.art);
        this.state.meldung = this.zweispaltig
            ? _t("Block %(art)s an Position %(pos)s in Spalte %(spalte)s", {
                  art,
                  pos,
                  spalte: block.spalte,
              })
            : _t("Block %(art)s an Position %(pos)s", { art, pos });
    }

    /** Erster Fehler eines Blocks für den Live-Bereich (leer, wenn alles gültig ist). */
    get fehlerMeldung() {
        const block = this.entwurf?.bloecke.find((b) => this.blockFehler(b));
        return block ? _t("%(art)s: %(fehler)s", { art: this.artName(block.art), fehler: this.blockFehler(block) }) : "";
    }

    /** Ablegen per Maus: neue Position aus der Reihenfolge in der Zielspalte. */
    fallenLassen({ element, parent }) {
        // ``parent``: Zielspalte (``group`` wäre die Ausgangsspalte)
        const group = parent;
        const uid = parseInt(element?.dataset.uid, 10);
        const bloecke = this.entwurf.bloecke;
        const block = bloecke.find((b) => b.uid === uid);
        if (!block || !group) {
            return;
        }
        const reihe = [...group.children]
            .filter(
                (el) => el.matches(".o_wb_voip_ds_block") && !el.classList.contains("o_dragged")
            )
            .map((el) =>
                el.classList.contains("o_wb_voip_ds_platzhalter") ? uid : parseInt(el.dataset.uid, 10)
            );
        const pos = reihe.indexOf(uid);
        if (pos < 0) {
            return;
        }
        const davor = reihe
            .slice(0, pos)
            .filter((u) => u !== uid)
            .at(-1);
        const danach = reihe.slice(pos + 1).find((u) => u !== uid);
        const kopie = { ...block, feld_ids: [...block.feld_ids] };
        if (this.zweispaltig && ["1", "2"].includes(group.dataset.spalte)) {
            kopie.spalte = group.dataset.spalte;
        }
        bloecke.splice(
            bloecke.findIndex((b) => b.uid === uid),
            1
        );
        let index = bloecke.length;
        if (danach !== undefined) {
            index = bloecke.findIndex((b) => b.uid === danach);
        } else if (davor !== undefined) {
            index = bloecke.findIndex((b) => b.uid === davor) + 1;
        }
        bloecke.splice(index, 0, kopie);
        this._positionMelden(uid);
    }

    feldUmschalten(feldId, an) {
        const block = this.gewaehlterBlock;
        if (!block || !this.katalog[feldId]) {
            return;
        }
        const index = block.feld_ids.indexOf(feldId);
        if (an && index < 0 && block.feld_ids.length < MAX_FELDER) {
            block.feld_ids.push(feldId);
        } else if (!an && index >= 0) {
            block.feld_ids.splice(index, 1);
        }
    }

    gruppenSetzen(ids) {
        this.entwurf.group_ids = ids.filter(Number.isInteger);
    }

    beispielSetzen(id) {
        this.state.partnerId = Number.isInteger(id) ? id : false;
    }

    _fokusSetzen() {
        if (!this._fokus) {
            return;
        }
        const { uid, knopf } = this._fokus;
        this._fokus = null;
        const eintrag = this.wurzel.el?.querySelector(`.o_wb_voip_ds_block[data-uid="${uid}"]`);
        let knopfEl = eintrag?.querySelector(`.o_wb_voip_ds_${knopf}`);
        if (knopfEl?.disabled) {
            knopfEl = eintrag.querySelector(`.o_wb_voip_ds_${knopf === "hoch" ? "runter" : "hoch"}`);
        }
        knopfEl?.focus();
    }

    // --- Vorschau --------------------------------------------------------------------

    /** Feldblöcke mit Feldern: nur ihre Änderung lädt die Werte neu. */
    get vorschauSignatur() {
        if (!this.entwurf) {
            return "";
        }
        return JSON.stringify(
            this.entwurf.bloecke
                .filter((b) => b.art === "felder" && b.feld_ids.length)
                .map((b) => b.feld_ids)
        );
    }

    /** Entwurf für die Vorschau: nur Blöcke, die der Server so annimmt. */
    vorschauEntwurf() {
        const daten = this.speicherDaten();
        let notiz = false;
        daten.name = daten.name.trim() || "-";
        daten.bloecke = this.entwurf.bloecke
            .filter((b) => !this.blockFehler(b))
            .filter((b) => {
                if (b.art !== "notiz") {
                    return true;
                }
                const erste = !notiz;
                notiz = true;
                return erste;
            })
            .slice(0, MAX_BLOECKE)
            .map(blockDaten);
        return daten;
    }

    async vorschauLaden(partnerId) {
        const nr = ++this._vorschauNr;
        if (!Number.isInteger(partnerId)) {
            this.state.vorschau = null;
            this.state.vorschauFehler = "";
            return;
        }
        let daten;
        let fehler = "";
        try {
            daten = await this.orm.call("wb.voip.layout", "wb_voip_designer_vorschau", [
                this.entwurf.id,
                partnerId,
                this.vorschauEntwurf(),
            ]);
        } catch (e) {
            daten = null;
            fehler = fehlertext(e);
        }
        if (nr === this._vorschauNr) {
            this.state.vorschau = daten;
            this.state.vorschauFehler = fehler;
        }
    }

    /** Feldwerte des Beispielkontakts je Feldname (aus der letzten Vorschau). */
    get feldwerte() {
        const werte = {};
        for (const block of this.state.vorschau?.layout?.bloecke || []) {
            for (const feld of block.felder || []) {
                if (feld && typeof feld.name === "string") {
                    werte[feld.name] = feld;
                }
            }
        }
        return werte;
    }

    /** Layout der Vorschau, sofort aus dem Entwurf (Feldwerte aus der letzten Serverantwort). */
    get vorschauLayout() {
        const katalog = this.katalog;
        const werte = this.feldwerte;
        const felderVon = (b) =>
            b.feld_ids
                .filter((id) => katalog[id])
                .map((id) => {
                    const feld = katalog[id];
                    const wert = werte[feld.name];
                    return { name: feld.name, label: wert?.label || feld.label, wert: wert?.wert || "—" };
                });
        return {
            id: this.entwurf.id,
            breite: this.entwurf.breite,
            bloecke: this.entwurf.bloecke
                .filter((b) => this.erlaubt(b))
                .map((b) => ({
                    ...blockDaten(b),
                    text: b.art === "text" ? b.text : false,
                    felder: b.art === "felder" ? felderVon(b) : [],
                })),
        };
    }

    get vorschauDaten() {
        const v = this.state.vorschau;
        const b = beispiele();
        return {
            kontakt: v?.kontakt || b.kontakt,
            protokolle: v?.protokolle?.length ? v.protokolle : b.protokolle,
            aufgaben: v?.aufgaben?.length ? v.aufgaben : b.aufgaben,
            aktionen: b.aktionen,
        };
    }

    /** Breite der Vorschau als Klasse (nur bekannte Werte). */
    get vorschauKlasse() {
        const breite = BREITEN.includes(this.entwurf.breite) ? this.entwurf.breite : "normal";
        return `o_wb_voip_ds_vorschau_${breite}`;
    }

    knopfLabel(knopf, block) {
        const name = this.artName(block.art);
        if (knopf === "hoch") {
            return _t("Nach oben: %s", name);
        }
        if (knopf === "runter") {
            return _t("Nach unten: %s", name);
        }
        return _t("Entfernen: %s", name);
    }

    get istPopup() {
        return this.entwurf.ort === ORT_POPUP;
    }

    /** Buttons der Vorschau tun nichts. */
    vorschauAktion() {}
}

registry.category("actions").add("wb_voip.layout_designer", LayoutDesigner);
