import { patch } from "@web/core/utils/patch";
import { PhoneField } from "@web/views/fields/phone/phone_field";
import { session } from "@web/session";

import { waehlenMitHinweis } from "../softphone/waehlen_hilfe";

// Telefonsymbol neben jedem Odoo-Telefonfeld: wählt über das Softphone und merkt
// sich den Datensatz des Formulars (bzw. der Listenzeile).
patch(PhoneField.prototype, {
    get wbVoipZeigen() {
        return Boolean(session.wb_voip_aktiv) && Boolean(this.props.record.data[this.props.name]);
    },

    async wbVoipWaehlen(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const record = this.props.record;
        await waehlenMitHinweis(this.env, record.data[this.props.name], {
            resModel: record.resModel,
            resId: record.resId || false,
        });
    },
});
