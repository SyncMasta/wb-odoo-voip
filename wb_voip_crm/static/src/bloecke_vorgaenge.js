import { Component, onWillUpdateProps, toRaw, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

import { blockRegistry, neueId } from "@wb_voip/layout/bloecke";

/**
 * Block „Vorgänge“ (Anrufer-Fenster): offene Vorgänge des Kontakts aus ``block.daten``
 * (Server: ``_wb_voip_block_daten_vorgaenge``, nur Lesbares). Alle Serverwerte nur als Text.
 * „Zuordnen“ setzt den Vorgang am eigenen Anruf, „Lösen“ nimmt die Zuordnung wieder weg; den
 * Rest meldet der Server per Bus, das Fenster lädt neu. Ohne laufenden Anruf (Vorschau im Designer) gibt es keinen Zuordnen-Knopf.
 */
export class VorgaengeBlock extends Component {
    static template = "wb_voip_crm.LayoutBlockVorgaenge";
    static props = { block: { type: Object }, daten: { type: Object } };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.titelId = neueId();
        // Eigene Auswahl bis der Server neu geladen hat (null = Stand des Servers)
        this.state = useState({ gewaehlt: null, arbeitet: false, fehler: "" });
        // Der Kern zeichnet den Block mit stabilem Schlüssel neu: kommen neue Server-Daten
        // (Bus-Neuladen), gilt wieder der Stand des Servers, nicht meine frühere Auswahl.
        onWillUpdateProps((naechste) => {
            if (toRaw(naechste.block.daten) !== toRaw(this.props.block.daten)) {
                this.state.gewaehlt = null;
            }
        });
    }

    get titel() {
        return typeof this.props.block.titel === "string" ? this.props.block.titel : "";
    }

    get anrufId() {
        const id = this.props.daten.anruf?.id;
        return Number.isInteger(id) && id > 0 ? id : false;
    }

    get vorgaenge() {
        const daten = this.props.block.daten;
        const anzahl = this.props.block.anzahl || Infinity;
        return (Array.isArray(daten) ? daten : [])
            .filter((v) => v && Number.isInteger(v.id))
            .slice(0, anzahl)
            .map((v) => ({
                id: v.id,
                name: typeof v.name === "string" ? v.name : "",
                stufe: typeof v.stufe === "string" ? v.stufe : "",
                umsatz: typeof v.erwarteter_umsatz === "string" ? v.erwarteter_umsatz : "",
                zugeordnet: this.state.gewaehlt === null ? Boolean(v.zugeordnet) : this.state.gewaehlt === v.id,
            }));
    }

    zuordnenLabel(vorgang) {
        return _t("Vorgang %s dem Anruf zuordnen", vorgang.name);
    }

    loesenLabel(vorgang) {
        return _t("Zuordnung zu %s lösen", vorgang.name);
    }

    oeffnenLabel(vorgang) {
        return _t("Vorgang %s öffnen", vorgang.name);
    }

    /** Setzt (``vorgangId``) oder löst (``false``) die Zuordnung am eigenen Anruf. */
    async setzen(vorgangId, fehlertext) {
        if (!this.anrufId || this.state.arbeitet) {
            return;
        }
        this.state.arbeitet = true;
        this.state.fehler = "";
        try {
            await this.orm.call("wb.voip.anruf", "wb_voip_vorgang_setzen", [
                [this.anrufId],
                vorgangId,
            ]);
            // false = keiner zugeordnet (passt zu keiner Vorgangs-ID)
            this.state.gewaehlt = vorgangId;
        } catch (fehler) {
            this.state.fehler =
                typeof fehler?.data?.message === "string" ? fehler.data.message : fehlertext;
        } finally {
            this.state.arbeitet = false;
        }
    }

    zuordnen(vorgang) {
        return this.setzen(vorgang.id, _t("Der Vorgang konnte nicht zugeordnet werden."));
    }

    loesen() {
        return this.setzen(false, _t("Die Zuordnung konnte nicht gelöst werden."));
    }

    oeffnen(vorgang) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "crm.lead",
            res_id: vorgang.id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

blockRegistry.add("vorgaenge", { component: VorgaengeBlock, label: _t("Vorgänge") });
