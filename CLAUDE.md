# wb-odoo-voip (öffentlich)

WB VoIP für Odoo 19 (Modul `wb_voip`) von WISSEN BERATUNG, Lizenz OPL-1, Lizenzprodukt `VOIP`.

**Dieses Repo ist öffentlich.** Hier nur Quellcode, README und LICENSE. Nie ablegen: Server-, Coolify- oder
Kundendaten, Pläne, Pfade zu Zugangsdaten, interne Arbeitsregeln. Vor jedem Push den Diff auf
solche Angaben prüfen.

## Regeln

- Kein Code aus AGPL-Modulen (z. B. OCA `voip_oca`, `crm_phonecall`) kopieren oder abwandeln.
- Abhängigkeiten nur Odoo-Core und `wb_license_client`.
- Namen mit Präfix `wb_voip` / `wb.voip.`; Gruppen `group_wb_voip_user` / `group_wb_voip_manager`.
- Jede öffentliche Methode: `@nur_voip_benutzer`, Eingaben prüfen, `sudo()` nur nach Zugriffsprüfung.
- Zugangsdaten nie in Logs, Bus, Browser-Speicher oder Repo; SIP-Passwort nur als Fernet-Chiffrat.
- Oberfläche, Kommentare zur Fachlogik und Commits auf Deutsch, Docstrings Englisch; Odoo-19-Syntax.
- Neue oder geänderte Felder: Version in `__manifest__.py` erhöhen.
