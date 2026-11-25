import { Component, onWillUpdateProps, toRaw, useState } from "@odoo/owl";
import {
    deserializeDate,
    deserializeDateTime,
    formatDate,
    formatDateTime,
} from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { SIZES } from "@web/core/ui/ui_service";
import { useService } from "@web/core/utils/hooks";

import { AktionsLeiste, aktionenFuerOrt } from "../aktionen/aktions_leiste";
import { formatDauer } from "../softphone/waehlen_hilfe";

/**
 * Block-Komponenten je Art (``wb.voip.layout.block.art``); unbekannte Arten fallen weg.
 * Andere Module tragen eigene Arten ein: ``{ component, label? }``. Die Komponente bekommt
 * ``block`` (Serverdaten des Blocks, eigene Daten in ``block.daten`` aus
 * ``_wb_voip_block_daten_<art>``) und ``daten`` (Props von ``LayoutBloecke``: Kontakt,
 * Anruf, Rückrufe). Alle Serverwerte nur als Text ausgeben.
 */
export const blockRegistry = registry.category("wb_voip_layout_bloecke");

/** Breiten eines Layouts; alles andere gilt als „normal“. */
export const BREITEN = ["normal", "breit", "sehr_breit"];

/** Breite eines Layouts (nur Werte aus ``BREITEN``, nie ein Serverwert als Klasse). */
export function breiteVon(layout) {
    return BREITEN.includes(layout?.breite) ? layout.breite : "normal";
}

/** Ausgeliefertes Layout des Anrufer-Fensters (falls der Server keins mitschickt). */
export const STANDARD_LAYOUT = {
    id: false,
    breite: "normal",
    bloecke: [
        { art: "kontaktkopf", spalte: "1" },
        { art: "buttons", spalte: "1" },
        { art: "protokolle", spalte: "1", anzahl: 5 },
        { art: "aufgaben", spalte: "1", anzahl: 10 },
        { art: "notiz", spalte: "1" },
    ],
};

/** Name je Blockart für Screenreader, wenn der Block keinen Titel hat. */
export function artLabel(art) {
    return (
        {
            kontaktkopf: _t("Kontakt"),
            felder: _t("Kontaktfelder"),
            protokolle: _t("Letzte Gespräche"),
            aufgaben: _t("Offene Aufgaben"),
            notiz: _t("Notiz"),
            buttons: _t("Buttons"),
            text: _t("Hinweis"),
        }[art] ||
        // Arten anderer Module: Name aus ihrem Registry-Eintrag (Text oder übersetzbarer ``_t``-Text)
        (typeof art === "string" && blockRegistry.get(art, null)?.label
            ? String(blockRegistry.get(art).label)
            : "")
    );
}

/**
 * Button-Arten, die der Fensterkopf fest anbietet: im Buttons-Block wären sie doppelt
 * (der Server liefert „Neuer Kontakt“ nur ohne Kontakt, dann zeigt ihn der Kopf).
 */
export const KOPF_ARTEN = ["neuer_kontakt"];

let naechsteId = 0;
/** Eindeutige Element-ID je Komponente (mehrere Layouts können gleichzeitig offen sein). */
export function neueId(praefix = "wb_voip_lb_") {
    naechsteId += 1;
    return `${praefix}${naechsteId}`;
}

/** Gemeinsame Props aller Block-Komponenten. */
const BLOCK_PROPS = { block: { type: Object }, daten: { type: Object } };

class BlockBasis extends Component {
    static props = BLOCK_PROPS;

    setup() {
        this.titelId = neueId();
    }

    get artLabel() {
        return artLabel(this.props.block.art);
    }

    get titel() {
        return typeof this.props.block.titel === "string" ? this.props.block.titel : "";
    }

    get kontakt() {
        return this.props.daten.kontakt || false;
    }
}

/** Telefon, E-Mail und „Kontakt öffnen“ (im Popup auch der Name, dort fehlt die Kopfzeile). */
export class KontaktkopfBlock extends BlockBasis {
    static template = "wb_voip.LayoutBlockKontaktkopf";
}

/** Ausgewählte Kontaktfelder als Label und Wert (beides nur Text). */
export class FelderBlock extends BlockBasis {
    static template = "wb_voip.LayoutBlockFelder";

    get felder() {
        const felder = this.props.block.felder;
        return Array.isArray(felder)
            ? felder.filter((f) => f && typeof f.name === "string" && f.wert)
            : [];
    }
}

/** Letzte Gesprächsprotokolle des Kontakts. */
export class ProtokolleBlock extends BlockBasis {
    static template = "wb_voip.LayoutBlockProtokolle";

    get protokolle() {
        const anzahl = this.props.block.anzahl || Infinity;
        return (this.props.daten.protokolle || []).slice(0, anzahl).map((p) => ({
            id: p.id,
            datum: p.datum ? formatDateTime(deserializeDateTime(p.datum)) : "",
            ergebnis: p.ergebnis_label || "",
            dauer: p.dauer_sek ? formatDauer(p.dauer_sek) : "",
            text: p.beschreibung_text || "",
        }));
    }
}

/** Offene Aufgaben des Benutzers am Kontakt oder Datensatz. */
export class AufgabenBlock extends BlockBasis {
    static template = "wb_voip.LayoutBlockAufgaben";

    get aufgaben() {
        const anzahl = this.props.block.anzahl || Infinity;
        return (this.props.daten.aufgaben || []).slice(0, anzahl).map((a) => ({
            ...a,
            faellig: a.faellig ? formatDate(deserializeDate(a.faellig)) : "",
        }));
    }
}

/**
 * Notiz zum laufenden Gespräch (derselbe Zustand wie in der Anrufansicht). Der Block
 * beobachtet den Anruf über seinen eigenen ``useState``: eine Änderung der Notiz an anderer
 * Stelle ändert die Props nicht und würde sonst nicht neu gezeichnet; beim Abbau gibt Owl
 * die Beobachtung wieder frei.
 */
export class NotizBlock extends BlockBasis {
    static template = "wb_voip.LayoutBlockNotiz";

    setup() {
        super.setup();
        this.feldId = neueId("wb_voip_af_notiz_");
        this.beob = useState({ anruf: this.props.daten.anruf || null });
        onWillUpdateProps((naechste) => {
            const anruf = naechste.daten.anruf || null;
            if (toRaw(anruf) !== toRaw(this.beob.anruf)) {
                this.beob.anruf = anruf;
            }
        });
    }

    get anruf() {
        return this.beob.anruf;
    }
}

/** Anruf-Buttons des Ortes über die gemeinsame Leiste („Zuordnen“/„Neuer Kontakt“ stehen im Kopf). */
export class ButtonsBlock extends BlockBasis {
    static template = "wb_voip.LayoutBlockButtons";
    static components = { AktionsLeiste };

    get aktionen() {
        return (this.props.daten.aktionen || []).filter((a) => !KOPF_ARTEN.includes(a?.art));
    }

    get zeigen() {
        const d = this.props.daten;
        return Boolean(d.onAktion && aktionenFuerOrt(this.aktionen, d.ort).length);
    }
}

/** Freier Hinweistext, nur Klartext mit Zeilenumbrüchen. */
export class TextBlock extends BlockBasis {
    static template = "wb_voip.LayoutBlockText";

    get text() {
        return typeof this.props.block.text === "string" ? this.props.block.text : "";
    }
}

blockRegistry
    .add("kontaktkopf", { component: KontaktkopfBlock })
    .add("felder", { component: FelderBlock })
    .add("protokolle", { component: ProtokolleBlock })
    .add("aufgaben", { component: AufgabenBlock })
    .add("notiz", { component: NotizBlock })
    .add("buttons", { component: ButtonsBlock })
    .add("text", { component: TextBlock });

/**
 * Rendert die Blöcke eines Layouts (Anrufer-Fenster, Popup, Vorschau im Designer).
 * Zwei Spalten nur bei Breite „breit“/„sehr_breit“ und Bildschirm ab 992 px; sonst eine
 * Spalte in Block-Reihenfolge. Rückrufe (``onAktion`` usw.) sind optional: ohne sie fehlt
 * der zugehörige Knopf. „Zuordnen“ und „Neuer Kontakt“ gehören dem Fensterkopf, nicht dem Layout.
 */
export class LayoutBloecke extends Component {
    static template = "wb_voip.LayoutBloecke";
    static props = {
        layout: { type: Object },
        kontakt: { optional: true },
        protokolle: { type: Array, optional: true },
        aufgaben: { type: Array, optional: true },
        aktionen: { type: Array, optional: true },
        ort: { type: String },
        anruf: { optional: true },
        maxNotiz: { type: Number, optional: true },
        mitName: { type: Boolean, optional: true },
        einspaltig: { type: Boolean, optional: true },
        onAktion: { type: Function, optional: true },
        onKontaktOeffnen: { type: Function, optional: true },
        onAufgabeOeffnen: { type: Function, optional: true },
    };

    setup() {
        this.ui = useService("ui");
    }

    /** Blöcke mit bekannter Art, je mit ihrer Komponente. */
    get eintraege() {
        const bloecke = Array.isArray(this.props.layout?.bloecke) ? this.props.layout.bloecke : [];
        const ergebnis = [];
        bloecke.forEach((block, index) => {
            if (block && typeof block.art === "string" && blockRegistry.contains(block.art)) {
                ergebnis.push({
                    key: `${index}_${block.art}`,
                    block,
                    Component: blockRegistry.get(block.art).component,
                });
            }
        });
        return ergebnis;
    }

    get zweispaltig() {
        return (
            !this.props.einspaltig &&
            ["breit", "sehr_breit"].includes(this.props.layout?.breite) &&
            this.ui.size >= SIZES.LG
        );
    }

    /** Eine oder zwei Spalten mit ihren Blöcken. */
    get spalten() {
        const alle = this.eintraege;
        if (!this.zweispaltig) {
            return [alle];
        }
        return [
            alle.filter((e) => e.block.spalte !== "2"),
            alle.filter((e) => e.block.spalte === "2"),
        ];
    }
}
