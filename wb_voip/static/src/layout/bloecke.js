import { Component, onWillUpdateProps, reactive } from "@odoo/owl";
import {
    deserializeDate,
    deserializeDateTime,
    formatDate,
    formatDateTime,
} from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { SIZES } from "@web/core/ui/ui_service";
import { useService } from "@web/core/utils/hooks";

import { AktionsLeiste } from "../aktionen/aktions_leiste";
import { formatDauer } from "../softphone/waehlen_hilfe";

/** Block-Komponenten je Art (``wb.voip.layout.block.art``); unbekannte Arten fallen weg. */
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

/** Gemeinsame Props aller Block-Komponenten. */
const BLOCK_PROPS = { block: { type: Object }, daten: { type: Object } };

class BlockBasis extends Component {
    static props = BLOCK_PROPS;

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
 * beobachtet den Anruf selbst: eine Änderung der Notiz an anderer Stelle ändert die
 * Props nicht und würde sonst nicht neu gezeichnet.
 */
export class NotizBlock extends BlockBasis {
    static template = "wb_voip.LayoutBlockNotiz";

    setup() {
        this._quelle = undefined;
        this._beobachten(this.props.daten.anruf);
        onWillUpdateProps((naechste) => this._beobachten(naechste.daten.anruf));
    }

    _beobachten(anruf) {
        if (anruf !== this._quelle) {
            this._quelle = anruf;
            this.anruf = anruf ? reactive(anruf, () => this.render()) : null;
        }
    }
}

/** Anruf-Buttons des Ortes über die gemeinsame Leiste, ohne Kontakt auch „Zuordnen“. */
export class ButtonsBlock extends BlockBasis {
    static template = "wb_voip.LayoutBlockButtons";
    static components = { AktionsLeiste };

    get aktionen() {
        return this.props.daten.aktionen || [];
    }

    get zeigen() {
        const d = this.props.daten;
        return Boolean((!this.kontakt && d.onZuordnen) || (d.onAktion && this.aktionen.length));
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
 * der zugehörige Knopf.
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
        onZuordnen: { type: Function, optional: true },
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
