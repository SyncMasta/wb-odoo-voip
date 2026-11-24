import { registry } from "@web/core/registry";

// Anlage im Modus „test“: der simulierte Agent nimmt nach einer Sekunde an.
registry.category("web_tour.tours").add("wb_voip_softphone_tour", {
    steps: () => [
        {
            content: "Telefon ist verbunden",
            trigger: ".o_wb_voip_systray .o_wb_voip_punkt.text-success",
        },
        {
            content: "Telefonsymbol am Telefonfeld des Kontakts",
            trigger: ".o_field_phone .o_wb_voip_waehlen",
            run: "click",
        },
        {
            content: "Anruf läuft",
            trigger: ".o_wb_voip_anrufansicht .o_wb_voip_zustand:contains(Im Gespräch)",
        },
        {
            content: "Notiz schreiben",
            trigger: ".o_wb_voip_notiz",
            run: "edit Tourtest",
        },
        {
            content: "Auflegen",
            trigger: ".o_wb_voip_auflegen",
            run: "click",
        },
        {
            content: "Anrufansicht geschlossen",
            trigger: ".o_wb_voip_softphone:not(:has(.o_wb_voip_anrufansicht))",
        },
        {
            content: "Protokoll-Popup öffnet sich breit",
            trigger: ".modal-xl .o_form_view",
        },
        {
            content: "Ergebnis-Auswahl öffnen",
            trigger: ".modal .o_field_widget[name=ergebnis] input",
            run: "click",
        },
        {
            content: "Ergebnis auf Mailbox ändern",
            trigger: ".o_select_menu_item:contains(Mailbox)",
            run: "click",
        },
        {
            content: "Popup speichern",
            trigger: ".modal .o_form_button_save",
            run: "click",
        },
        {
            content: "Popup geschlossen",
            trigger: "body:not(:has(.modal))",
        },
        {
            content: "Reiter Anrufe öffnen",
            trigger: ".o_wb_voip_reiter button:contains(Anrufe)",
            run: "click",
        },
        {
            content: "Anruf in der Liste",
            trigger: ".o_wb_voip_anrufliste .o_wb_voip_eintrag:contains(Tour Kontakt)",
        },
    ],
});
