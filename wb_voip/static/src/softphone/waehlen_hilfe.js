import { _t } from "@web/core/l10n/translation";

/**
 * Wählt über den Telefon-Dienst und meldet das Ergebnis per Odoo-Benachrichtigung:
 * im Neben-Tab „übergeben“, bei Ablehnung den Fehler des Dienstes.
 *
 * @param {object} env  Odoo-Umgebung (Dienste wb_voip_telefon, notification)
 * @param {string} nummer
 * @param {{resModel?: string|false, resId?: number|false}} [ziel]
 * @returns {Promise<boolean>}
 */
export async function waehlenMitHinweis(env, nummer, { resModel = false, resId = false } = {}) {
    const telefon = env.services.wb_voip_telefon;
    const notification = env.services.notification;
    if (!telefon) {
        return false;
    }
    const nebenTab = telefon.state.status === "anderer_tab";
    const ok = await telefon.waehlen(String(nummer ?? ""), {
        resModel: resId ? resModel || false : false,
        resId: resId || false,
    });
    if (ok && nebenTab) {
        notification?.add(_t("Hand over to the phone in the other tab"), { type: "info" });
    } else if (!ok) {
        notification?.add(telefon.state.fehler || _t("The call could not be started."), {
            type: "danger",
        });
    }
    return ok;
}

/** Dauer in Sekunden als „m:ss“ bzw. „h:mm:ss“. */
export function formatDauer(sekunden) {
    const s = Math.max(0, Math.floor(Number(sekunden) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const rest = String(s % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${rest}` : `${m}:${rest}`;
}

/** Ziel-Datensatz für einen Rückruf aus einem Listeneintrag (Datensatz, sonst Kontakt). */
export function rueckrufZiel(eintrag) {
    if (eintrag.res_model && eintrag.res_id) {
        return { resModel: eintrag.res_model, resId: eintrag.res_id };
    }
    if (eintrag.partner) {
        return { resModel: "res.partner", resId: eintrag.partner[0] };
    }
    return {};
}
