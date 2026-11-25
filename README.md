# WB VoIP – Telefonie direkt in Odoo 19

**Softphone im Browser, Anrufprotokoll und Anbindung an SIP-Telefonanlagen für Odoo 19.**
Ein Produkt von [WISSEN BERATUNG](https://www.wissen-beratung.de).

Wer in Odoo arbeitet und daneben ein Tischtelefon oder eine zweite Telefon-App bedient, tippt Nummern ab, vergisst
Notizen und findet später nicht mehr, wer wann mit wem gesprochen hat. WB VoIP holt das Telefon in Odoo: anrufen per
Klick aus jedem Telefonfeld, eingehende Anrufe mit Namen des Kontakts, und nach jedem Gespräch ein Protokolleintrag
am richtigen Datensatz.

> **Stand:** Phase 6 (`wb_voip` 19.0.4.4.0, `wb_voip_crm` 19.0.1.0.0, `wb_voip_ansitel` 19.0.1.0.0), Ansitel-Anbindung
> fertig, Live-Test gegen eine echte Anlage steht aus. Phase 5 (CRM-Anbindung) und Phase 4b
> abgeschlossen. Team-Übersicht, zweite Leitung, Übergeben mit
> Rückfrage, Konferenz im Browser, verpasste Anrufe mit Rückruf-Liste, Softphone, Anrufprotokoll, Anrufer-Fenster
> mit Layout-Designer, eigene Anruf-Buttons mit Assistent, Rufnummer zuordnen und Lizenzanbindung sind fertig und mit
> automatischen Tests abgesichert. Gegen eine echte Telefonanlage getestet ist die Signalisierung aus Phase 1 und 2
> (Anmelden, Anrufe, Annehmen, Auflegen); Übergeben, Heranholen und Konferenz noch nicht. Bis dahin gilt das Modul
> als Vorabversion.

---

## Funktionen

**Softphone in der Odoo-Kopfzeile**
- Telefonsymbol mit Statuspunkt: verbunden, aus, in anderem Tab, getrennt, ohne Lizenz, nicht eingerichtet
- Reiter „Wählen“, „Anrufe“, „Verpasst“, „Team“, „Kontakte“ und „Aufgaben“; weitere Reiter können Zusatzmodule einhängen
- Im Gespräch: Stumm, Halten, Tastatur (DTMF), Weiterleiten, Rückfrage, Konferenz, Notiz, Auflegen
- Eingehend: Annehmen oder Ablehnen, auf Wunsch mit Desktop-Benachrichtigung
- Telefonsymbol neben jedem Odoo-Telefonfeld; der Anruf wird mit dem Datensatz verknüpft
- Mehrere Odoo-Tabs: Nur ein Tab meldet sich an der Anlage an (eine Registrierung je Nebenstelle), die anderen
  reichen Wählen an ihn weiter. Schließt der Tab, übernimmt der nächste.

**Team, zweite Leitung, Übergeben, Konferenz**
- Reiter „Team“: Kollegen mit Status frei, telefoniert (optional mit Dauer), klingelt oder abgemeldet. Angezeigt
  werden aktive interne Benutzer mit VoIP-Recht, SIP-Benutzer, derselben Firma und derselben Anlage, nicht man selbst
- Standardmäßig ausgeschaltet; je Firma unter Einstellungen › WB VoIP „Team-Übersicht anzeigen“ und „Gesprächsdauer
  im Team anzeigen“; maßgeblich ist die Firma, in der die Person gerade arbeitet (aktive Firma). Vor dem Einschalten Beschäftigte informieren (Art. 13 DSGVO) und die Mitbestimmung beachten (§ 87 Abs. 1 Nr. 6 BetrVG)
- Datenschutz: nur Name, Nebenstelle, Status und Dauer; nie Gegennummer, Kontakt oder Notiz. Als Anwesenheit
  steht nur der Zeitpunkt des letzten Lebenszeichens des Telefon-Tabs in Odoo (nach 150 s ohne Meldung
  „abgemeldet“, beim Abmelden gelöscht)
- Heranholen eines klingelnden Kollegen per Wahlpräfix der Anlage (Beispiel `**` bei Anlagen auf Asterisk-Basis);
  leeres Präfix blendet den Knopf aus
- Zweite Leitung: wartender Anruf mit Annehmen/Ablehnen und kurzem Anklopfton; gehaltene Leitungen senden kein
  Mikrofon; ein dritter Anruf wird mit 480 abgewiesen und zählt als verpasst (bisher 486)
- Übergeben direkt (blind) oder mit Rückfrage (attended, REFER mit Replaces); bestätigt die Anlage nicht binnen
  15 s, bleibt der Kunde gehalten und ein Hinweis erscheint (Verbinden erneut oder Zurück)
- Konferenz im Browser mit höchstens drei Teilnehmern; braucht eine stabile Verbindung und ausreichend Bandbreite
  am Gastgeber, Stumm gilt in beide Richtungen
- Je Kundengespräch ein Protokoll-Popup, bei mehreren nacheinander

**Anrufprotokoll**
- Nach jedem Anruf genau ein Protokolleintrag (erreicht, nicht erreicht, Mailbox, besetzt) mit Dauer und Notiz
- Breites Popup direkt nach dem Auflegen mit Kontakt, den letzten Anrufen und „Folgeaktivität planen“
- Hinweis im Chatter des verknüpften Datensatzes bzw. des Kontakts
- Kontakterkennung über die Rufnummer, unabhängig von der Schreibweise (`0170 1234567` = `+49 170 1234567`)

**Verpasste Anrufe**
- Zustände „verpasst“ (Anrufer legt vor der Annahme auf) und „anderswo angenommen“ (ein Kollege war schneller)
- Reiter „Verpasst“ im Softphone: offene Einträge je Rufnummer, „Jetzt anrufen“, ✓ mit „Wie erledigt?“, Zähler am
  Telefonsymbol (nur ungesehene verpasste, nie „anderswo“)
- Rückruf-Aktivität „Rückruf: verpasster Anruf“ am Kontakt (nur bei Leserecht)
- Ein angenommener Anruf oder ein erreichter Rückruf derselben Nummer schließt die Einträge; ändert man das
  Ergebnis des Rückruf-Protokolls, öffnen sich genau diese Einträge wieder. Die Rückruf-Aktivität im Chatter zu
  erledigen schließt den Eintrag ebenfalls.
- Hinweis „angenommen von <Name>“ mit Dauer und Notiz des Kollegen, nur bei gleichzeitigem Anruf (Beginn ± 10 s,
  gleiche E.164-Nummer) und gemeinsamer Firma
- Datenschutz: Kollegen sehen nur Name, Dauer und Notiz des Annehmenden (Hinweis am Notizfeld eingehender Anrufe).
  Die Annahme meldet der Browser, der Server kann sie nicht prüfen; eine falsche Meldung kann offene Einträge von
  Kollegen derselben Nummer im 10-Sekunden-Fenster schließen. Ein „anderswo angenommen“ kann der Browser nur in
  den ersten 120 Sekunden nach dem Beginn noch als selbst angenommen melden, später bleibt es unverändert.
  Ausgehende Anrufe, die der Aufräum-Lauf beendet, schließen keine Einträge.
- Wird der Tab geschlossen, während ein Anruf klingelt, beendet ihn der Aufräum-Lauf nach 4 Stunden als
  „fehlgeschlagen“, nicht als „verpasst“: ohne Rückruf-Aktivität und ohne Eintrag in der Liste. Ist gar kein
  Odoo-Tab mit dem Telefon offen, erfährt Odoo vom Anruf nichts.

**Anrufer-Fenster**
- Seitenpanel bei jedem Anruf, nur im Tab, der telefoniert: Kontakt mit Bild, letzte Gespräche, offene Aufgaben,
  Notiz zum laufenden Gespräch
- Gesprächsknöpfe im Kopf: Annehmen/Ablehnen beim Klingeln, Stumm/Auflegen im Gespräch
- Schließt sich zehn Sekunden nach dem Auflegen, solange nicht Maus oder Tastaturfokus im Panel sind;
  nimmt keinen Fokus aus einem Eingabefeld
- Unbekannte Nummer: „Zuordnen“ und „Neuer Kontakt“

**Layout-Designer**
- Aufbau von Anrufer-Fenster und Protokoll-Popup aus Blöcken (Kontaktkopf, Kontaktfelder, letzte Protokolle,
  offene Aufgaben, Notiz, Buttons, Text), grafisch mit Vorschau; Breite, ein oder zwei Spalten
- Layouts je Benutzergruppe mit Reihenfolge; Standard-Layout je Fenster mit „Auf Standard zurücksetzen“
- Felder erscheinen nur, wenn der Benutzer sie auch sonst lesen darf; Zugangsdaten sind ausgeschlossen

**Eigene Anruf-Buttons**
- Selbst konfigurierbar unter Einstellungen › Technisch › WB VoIP › Anruf-Buttons: Fenster öffnen,
  Serveraktion, Webadresse (nur `https://`) oder neuer Kontakt
- Anzeige im Anrufer-Fenster, im Protokoll-Popup und im Softphone, einstellbar nach Richtung, Anrufzustand,
  Benutzergruppen und einer Bedingung am Kontakt
- Läuft mit den Rechten des Benutzers; als Serveraktion nur Python-Code und „Feld schreiben“ am Datensatz selbst

**Button-Assistent**
- Button und Serveraktion in einem Schritt anlegen: Feld setzen, Vorlage (Kategorie hinzufügen, Sprache setzen)
  oder Python-Code (nur Systemadministratoren)
- Erzeugte Serveraktionen laufen nur aus WB VoIP heraus; Bearbeiten über „Mit Assistent bearbeiten“

**CRM-Anbindung (Modul `wb_voip_crm`, installiert sich mit CRM selbst)**
- Anrufe werden dem offenen Vorgang des Kontakts oder seiner Firma zugeordnet (aktiv, Wahrscheinlichkeit unter
  100 %); bei mehreren zählt der zuletzt geänderte eigene, sonst keiner. Manuell zuordnen und lösen im Block „Vorgänge“
- Smart-Button „Anrufe“ und Reiter „Anrufprotokolle“ am Vorgang, nur mit den Rechten des Benutzers
- Rückruf-Aktivität bei verpassten Anrufen am offenen Vorgang, sonst am Kontakt
- Anruf-Button „Vorgang anlegen“ mit Titel-Vorlage (`{kontakt}`, `{nummer}`, `{datum}`) und Verkaufsteam;
  angelegt mit den Rechten des Benutzers
- Vorgangsnamen nur bei Leserecht am Vorgang; das technische Feld Anruf → Vorgang nur für Systemadministratoren

**Ansitel-Anbindung (Modul `wb_voip_ansitel`, optional)**
- Schnelltasten für Weiterleitung („→ Handy“ oder feste Nummer), „Nicht stören“ und Anrufbeantworter, im Softphone und/oder
  als schmale Leiste am rechten Rand; Zustand wird frisch gelesen und per Bus an alle Tabs gemeldet
- Weiterleitungsziele werden gegen Mehrwert-, Notruf- und Auslandsnummern geprüft (Schutz vor Gebührenbetrug); Ziele im
  Ausland nur nach Freischaltung durch die Systemverwaltung
- Abgleich mit der Anrufliste der Anlage alle 5 Minuten: Anrufe, die Odoo nicht gesehen hat, und Anrufe, die am Tischtelefon
  oder über eine Weiterleitung angenommen wurden, erscheinen als „anderswo angenommen“ mit Gerät und Dauer
- Zugangsdaten nur als Fernet-Chiffrat, nur für die Systemverwaltung; Einrichtung, Rechte und Datenschutz in
  [`wb_voip_ansitel/README.rst`](wb_voip_ansitel/README.rst)
- Nicht enthalten: Team-Status von der Anlage (Tischtelefone im Team bleiben unsichtbar)

**Rufnummer zuordnen**
- Unbekannte Nummer einem Kontakt zuordnen und auf Wunsch in dessen Telefonfeld speichern; danach erkennt
  WB VoIP die Nummer selbst

**Sicherheit und Datenschutz**
- SIP-Passwort nur verschlüsselt gespeichert (Fernet); Benutzer und Administratoren können es setzen, nie lesen
- Jeder Benutzer sieht nur seine eigenen Anrufe und Protokolle; Administratoren sehen alle
- Keine Daten an Dritte: standardmäßig kein fremder STUN-Server, optional eigene STUN-Server je Anlage
- Einstellbare Löschfrist für Anrufdaten (Standard 12 Monate); Protokolle bleiben erhalten
- Alle Serverfunktionen prüfen Gruppe, Eigentümer und Eingaben; geprüft gegen OWASP ASVS und die
  Odoo-Sicherheitsrichtlinien

## Voraussetzungen

| | |
|---|---|
| Odoo | 19.0 (Community oder Enterprise ohne das Enterprise-Modul `voip`) |
| Odoo-Module | `mail`, `phone_validation`, `bus`, `web` (Standard) und `wb_license_client` (Lizenz-Client von WISSEN BERATUNG); für die CRM-Anbindung zusätzlich `crm` |
| Python | `cryptography` (im offiziellen Odoo-Image enthalten) |
| Telefonanlage | SIP über WebSocket mit TLS (`wss://`, RFC 7118), eine Nebenstelle je Benutzer |
| Browser | aktueller Chrome, Edge oder Firefox; Odoo über HTTPS |
| Lizenz | gültiger Lizenzschlüssel für den Produktcode `VOIP` |

## Installation

1. `wb_voip` und `wb_license_client` in den Addons-Pfad legen; für die CRM-Anbindung auch `wb_voip_crm`
   (installiert sich selbst, sobald `crm` und `wb_voip` installiert sind).
2. Schlüssel für die Passwort-Verschlüsselung erzeugen und als Umgebungsvariable des Odoo-Prozesses setzen:

   ```bash
   python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
   # Ergebnis als WB_VOIP_FERNET_KEY setzen, Odoo neu starten
   ```

   Der Schlüssel gehört nie in die Datenbank oder in ein Repository. Geht er verloren, müssen alle SIP-Passwörter
   neu eingegeben werden.
3. Modul „WB VoIP“ installieren und den Lizenzschlüssel unter Einstellungen aktivieren.

## Einrichtung

1. **Telefonanlage** (Einstellungen › Technisch › WB VoIP › Telefonanlagen): WebSocket-URL (`wss://…`), SIP-Domain,
   Land. Modus „test“ simuliert Anrufe ohne Anlage, „prod“ meldet sich an der Anlage an.
2. **Nebenstellen** (Benutzerformular, Reiter „Telefonie“): Anlage und SIP-Benutzer je Benutzer; jede Nebenstelle
   gehört genau einem Benutzer. Das SIP-Passwort setzt der Benutzer unter „Mein Profil“ oder der Administrator.
3. **Rechte**: „WB VoIP: Benutzer“ für alle, die telefonieren; „WB VoIP: Administrator“ für die Pflege von Anlagen
   und Nebenstellen. Anlagen anlegen, löschen und ihre Verbindungsdaten ändern (WebSocket-URL, SIP-Domain,
   ICE-Server und Land) darf nur die Systemverwaltung: Der Browser meldet sich an der URL an, und das Land der
   Anlage gilt bei der Zielprüfung als Inland. Name, Modus und Heranholen-Präfix pflegt auch der Administrator.
4. **Löschfrist** (Einstellungen › WB VoIP): Monate bis zur Löschung beendeter Anrufe, `0` = nie.
5. **Anruf-Buttons** (Einstellungen › Technisch › WB VoIP › Anruf-Buttons, optional): eigene Schaltflächen für
   Anrufer-Fenster, Protokoll-Popup und Softphone anlegen. Buttons vom Typ „Webadresse“ geben Rufnummer und
   Kontakt-ID an den Betreiber der Adresse weiter: Bei externen Diensten einen Vertrag zur Auftragsverarbeitung
   (Art. 28 DSGVO) schließen und bei Anbietern außerhalb der EU/des EWR die Drittlandübermittlung prüfen.

Das Menü Einstellungen › Technisch erscheint nur im Entwicklermodus (Einstellungen › „Entwicklermodus
aktivieren“).

Details stehen in [`wb_voip/README.rst`](wb_voip/README.rst) und [`wb_voip_crm/README.rst`](wb_voip_crm/README.rst).

## Module

| Modul | Inhalt |
|---|---|
| `wb_voip` | Softphone, Anrufprotokoll, Anrufer-Fenster, Layout-Designer, Anruf-Buttons, Lizenzanbindung |
| `wb_voip_ansitel` | Anbindung einer Ansitel-Anlage: Schnelltasten, Geräte, Abgleich mit der Anrufliste |
| `wb_voip_crm` | CRM-Anbindung: Anrufe und Protokolle am Vorgang, Rückruf-Aufgabe, Button „Vorgang anlegen“, Block „Vorgänge“ |

## Geplant

- Live-Test von Übergeben, Heranholen und Konferenz gegen verschiedene Telefonanlagen
- Live-Test der Ansitel-Anbindung gegen eine echte Anlage (Phase 6 ist umgesetzt, bisher nur mit simulierter Anlage getestet);
  Team-Status von der Anlage; Adapter für weitere Telefonanlagen
- Englische Oberfläche

## Lizenz

Odoo Proprietary License v1.0 (OPL-1), siehe [LICENSE](LICENSE). Der Quelltext ist zur Einsicht veröffentlicht; die
Nutzung setzt eine Lizenz von WISSEN BERATUNG voraus. Enthalten ist [SIP.js](https://sipjs.com) 0.21.2 unter der
MIT-Lizenz (`wb_voip/static/lib/SIP.js-LICENSE`).

## Kontakt

WISSEN BERATUNG · Inh. Tobias Wissen · Lerchenstr. 16 · 49451 Holdorf
Telefon +49 5494 2419770 · info@wissen-beratung.de · [www.wissen-beratung.de](https://www.wissen-beratung.de)
