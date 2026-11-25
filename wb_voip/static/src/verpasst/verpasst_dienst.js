import { EventBus, reactive } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { session } from "@web/session";

/** Bus-Kanal des Servers; die Nutzlast ist leer, Zähler und Liste kommen per RPC. */
export const BUS_VERPASST = "wb_voip/verpasst";

/**
 * Zähler der ungesehenen verpassten Anrufe (für Systray und Reiter). Lädt beim Start und
 * bei jeder Bus-Meldung neu; die Liste selbst gehört dem Reiter, der auf ``aktualisieren``
 * hört.
 */
export class VerpasstDienst {
    constructor({ orm, busService, aktiv }) {
        this.orm = orm;
        this.bus = new EventBus();
        this.state = reactive({ zaehler: 0 });
        if (aktiv) {
            busService.subscribe(BUS_VERPASST, () => this._beiBus());
            this.zaehlerLaden();
        }
    }

    async _beiBus() {
        await this.zaehlerLaden();
        // der offene Reiter lädt die Liste und markiert sie als gesehen
        this.bus.trigger("aktualisieren");
    }

    async zaehlerLaden() {
        try {
            this._setzen(await this.orm.call("wb.voip.anruf", "wb_voip_verpasst_zaehler", []));
        } catch {
            // Plakette bleibt, wie sie ist; der nächste Bus-Impuls versucht es erneut
        }
    }

    /** Markiert alle offenen Einträge als gesehen und übernimmt den neuen Zähler. */
    async gesehen() {
        try {
            this._setzen(await this.orm.call("wb.voip.anruf", "wb_voip_verpasst_gesehen", []));
        } catch {
            // nicht kritisch: der Zähler bleibt bis zum nächsten Öffnen stehen
        }
    }

    _setzen(wert) {
        this.state.zaehler = Number.isInteger(wert) && wert > 0 ? wert : 0;
    }
}

export const verpasstDienst = {
    dependencies: ["orm", "bus_service"],
    start(env, { orm, bus_service }) {
        return new VerpasstDienst({
            orm,
            busService: bus_service,
            aktiv: Boolean(session.wb_voip_aktiv),
        });
    },
};

registry.category("services").add("wb_voip_verpasst", verpasstDienst);
