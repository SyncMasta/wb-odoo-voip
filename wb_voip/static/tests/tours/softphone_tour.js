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
            trigger: ".o_wb_voip_anrufansicht .o_wb_voip_zustand:contains(In a call)",
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
            content: "Protokoll-Popup öffnet sich in der Größe des Standard-Layouts, rechts das Layout",
            trigger: ".modal-lg .o_form_view .o_wb_voip_popup_rechts .o_wb_voip_popup_layout",
        },
        {
            content: "Ergebnis-Auswahl öffnen",
            trigger: ".modal .o_field_widget[name=ergebnis] input",
            run: "click",
        },
        {
            content: "Ergebnis auf Mailbox ändern",
            trigger: ".o_select_menu_item:contains(Voicemail)",
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
            trigger: ".o_wb_voip_reiter button:contains(Calls)",
            run: "click",
        },
        {
            content: "Anruf in der Liste",
            trigger: ".o_wb_voip_anrufliste .o_wb_voip_eintrag:contains(Tour Kontakt)",
        },
        // Anrufer-Fenster: unbekannte Nummer einem Kontakt zuordnen
        {
            content: "Reiter Wählen öffnen",
            trigger: ".o_wb_voip_reiter button:contains(Dial)",
            run: "click",
        },
        {
            content: "Unbekannte Nummer eingeben",
            trigger: ".o_wb_voip_eingabe",
            run: "edit +49 30 9876543",
        },
        {
            content: "Anrufen",
            trigger: ".o_wb_voip_anrufen",
            run: "click",
        },
        {
            content: "Anrufer-Fenster zeigt die unbekannte Nummer",
            trigger: ".o_wb_voip_anrufer_fenster .o_wb_voip_af_name:contains(Unknown number)",
        },
        {
            content: "Zuordnen",
            trigger: ".o_wb_voip_anrufer_fenster .o_wb_voip_af_zuordnen",
            run: "click",
        },
        {
            content: "Kontakt wählen",
            trigger: ".modal .o_field_widget[name=partner_id] input",
            run: "edit Tour Kontakt",
        },
        {
            content: "Tour Kontakt übernehmen",
            trigger: ".o-autocomplete--dropdown-item:contains(Tour Kontakt)",
            run: "click",
        },
        {
            content: "Auswahl „Nummer speichern in“ öffnen",
            trigger: ".modal .o_field_widget[name=feld] input",
            run: "click",
        },
        {
            content: "Nummer nicht speichern",
            trigger: ".o_select_menu_item:contains(Do not save)",
            run: "click",
        },
        {
            content: "Zuordnen bestätigen",
            trigger: ".modal button[name=action_zuordnen]",
            run: "click",
        },
        {
            content: "Kopf zeigt den Namen",
            trigger: ".o_wb_voip_anrufer_fenster .o_wb_voip_af_name:contains(Tour Kontakt)",
        },
        {
            content: "Zweiter Anruf läuft",
            trigger: ".o_wb_voip_anrufansicht .o_wb_voip_zustand:contains(In a call)",
        },
        {
            content: "Auflegen",
            trigger: ".o_wb_voip_anrufansicht .o_wb_voip_auflegen",
            run: "click",
        },
        {
            content: "Protokoll-Popup speichern",
            trigger: ".modal .o_form_button_save",
            run: "click",
        },
        {
            content: "Popup geschlossen",
            trigger: "body:not(:has(.modal))",
        },
    ],
});
