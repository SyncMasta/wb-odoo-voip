import { Component, useEffect, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";

import { Softphone } from "@wb_voip/softphone/softphone";
import { WbVoipSystray } from "@wb_voip/softphone/systray";
import { tastenSichtbar } from "./tasten_dienst";

/**
 * Leiste der Schnelltasten. Lage „softphone“: unter dem Statusbereich des Softphones;
 * Lage „rand“: schmal und senkrecht am rechten Bildschirmrand (nur Symbole).
 */
export class WbVoipTastenleiste extends Component {
    static template = "wb_voip_ansitel.Tastenleiste";
    static props = { lage: String };

    setup() {
        this.dienst = useService("wb_voip_ansitel_tasten");
        this.state = useState(this.dienst.state);
        // Rand-Leiste: Platz am rechten Rand schaffen, solange sie sichtbar ist
        useEffect(
            (sichtbar) => {
                if (this.props.lage !== "rand") {
                    return;
                }
                document.body.classList.toggle("o_wb_voip_tasten_rand_platz", sichtbar);
                return () => document.body.classList.remove("o_wb_voip_tasten_rand_platz");
            },
            () => [this.sichtbar]
        );
    }

    get sichtbar() {
        return tastenSichtbar(this.state, this.state.ort, this.props.lage);
    }

    /** Bei „beide“ gibt es zwei Gruppen mit gleichem Zweck: die am Rand bekommt einen eigenen Namen. */
    get gruppenName() {
        return this.props.lage === "rand" && this.state.ort === "beide"
            ? _t("Schnelltasten am Rand")
            : _t("Schnelltasten");
    }

    get fehlerId() {
        return `o_wb_voip_tasten_fehler_${this.props.lage}`;
    }

    symbol(taste) {
        return taste.icon ? `fa ${taste.icon}` : "fa fa-circle-o";
    }

    klasse(taste) {
        if (taste.aktiv === null) {
            return "o_wb_voip_taste_unbekannt";
        }
        return taste.aktiv ? "o_wb_voip_taste_aktiv" : "";
    }

    /** Graue Taste: Name samt Hinweis (der Hinweis steht auch sichtbar, sofern Platz ist). */
    titel(taste) {
        return taste.aktiv === null && this.state.fehler
            ? `${taste.name}: ${this.state.fehler}`
            : taste.name;
    }

    /** ``aria-disabled`` statt ``disabled``, damit der Fokus beim Schalten nicht verloren geht. */
    get beschaeftigt() {
        return this.state.schaltet ? "true" : undefined;
    }

    gedrueckt(taste) {
        return taste.aktiv === null ? "mixed" : taste.aktiv ? "true" : "false";
    }

    /** ``aria-describedby`` nur für graue Tasten und nur, wenn der Hinweis als Text sichtbar ist. */
    beschreibung(taste) {
        return taste.aktiv === null && this.state.fehler && this.props.lage === "softphone"
            ? this.fehlerId
            : undefined;
    }

    schalten(taste) {
        if (this.state.schaltet) {
            return; // aria-disabled lässt den Fokus stehen, der Klick wird hier ignoriert
        }
        return this.dienst.schalten(taste.id, this.dienst.wunschzustand(taste));
    }

    weiterleitungAus() {
        if (this.state.schaltet) {
            return;
        }
        return this.dienst.weiterleitungAus();
    }

    get fremdTitel() {
        return _t("Weiterleitung auf %s ausschalten", this.state.fremde_weiterleitung);
    }
}

// Leiste im Softphone (Vorlage tasten.xml hängt sie unter den Status)
patch(Softphone, {
    components: { ...Softphone.components, WbVoipTastenleiste },
});

// Rand-Leiste: eine eigene Hauptkomponente, nur sichtbar bei Einstellung „rand“ oder „beide“
registry.category("main_components").add("wb_voip_ansitel.TastenleisteRand", {
    Component: WbVoipTastenleiste,
    props: { lage: "rand" },
});

// Punkt am Telefonsymbol der Systray, solange Anrufe umgeleitet werden
patch(WbVoipSystray.prototype, {
    setup() {
        super.setup(...arguments);
        this.tasten = useState(useService("wb_voip_ansitel_tasten").state);
    },
    get titel() {
        const titel = super.titel;
        return this.tasten.umgeleitet ? _t("%s, Anrufe werden umgeleitet", titel) : titel;
    },
});
