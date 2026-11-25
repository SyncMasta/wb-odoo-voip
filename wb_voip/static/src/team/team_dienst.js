import { EventBus } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { session } from "@web/session";

/** Bus-Kanal des Servers; die Nutzlast ist leer, die Liste kommt per RPC. */
export const BUS_TEAM = "wb_voip/team";

/**
 * Leitet die Bus-Meldung „Team-Status geändert“ an den offenen Team-Reiter weiter. Der Dienst
 * lädt selbst nichts: nur ein eingehängter Reiter hört auf ``aktualisieren``.
 */
export class TeamDienst {
    constructor({ busService, aktiv }) {
        this.bus = new EventBus();
        if (aktiv) {
            busService.subscribe(BUS_TEAM, () => this.bus.trigger("aktualisieren"));
        }
    }
}

export const teamDienst = {
    dependencies: ["bus_service"],
    start(env, { bus_service }) {
        return new TeamDienst({ busService: bus_service, aktiv: Boolean(session.wb_voip_aktiv) });
    },
};

registry.category("services").add("wb_voip_team", teamDienst);
