# WB VoIP einrichten

Diese Anleitung führt durch die Einrichtung von `wb_voip`, `wb_voip_crm` und `wb_voip_ansitel` in Odoo 19. Sie richtet sich
an die Person, die Odoo betreibt (Zugriff auf Server und Systemverwaltung). Die englische Fassung steht in
[setup.md](setup.md). Funktionsumfang und Stand: [README](../README.md).

Oberfläche: Englisch und Deutsch (die Bezeichnungen in dieser Anleitung sind die deutschen).

Beispieladressen in dieser Anleitung (`pbx.example.com`, Nummern im Bereich `030 23125 xxx`) sind erfunden.

## 1. Voraussetzungen

| | |
|---|---|
| Odoo | 19.0, Community oder Enterprise. In Enterprise darf das Odoo-Modul `voip` nicht installiert sein (`wb_voip` schließt es aus). |
| Odoo-Module | `mail`, `phone_validation`, `bus`, `web` (Standard), `wb_license_client` (Lizenz-Client); für `wb_voip_crm` zusätzlich `crm` |
| Python | `cryptography` (im offiziellen Odoo-Image enthalten); `phonenumbers` (**Pflicht**: Rufnummernprüfung und Gebührensperre; ohne das Paket werden alle Zielnummern abgelehnt, die Manifeste fordern es) |
| Odoo-Adresse | **HTTPS mit gültigem Zertifikat.** Der Browser gibt Mikrofon und WebRTC nur in einem sicheren Kontext frei. |
| Telefonanlage | SIP über WebSocket mit TLS (`wss://`, RFC 7118) und WebRTC-fähigen Nebenstellen, eine Nebenstelle je Benutzer |
| Netzwerk | Der Browser jedes Benutzers erreicht die `wss://`-Adresse der Anlage und deren Medienports. |
| ICE | Optional. Unterstützt werden nur `stun:`-URLs; TURN wird nicht unterstützt. Ohne Eintrag fragt der Browser keinen fremden STUN-Server. |
| Browser | aktueller Chrome, Edge oder Firefox |
| Lizenz | gültiger Lizenzschlüssel für den Produktcode `VOIP` |

## 2. Installation

1. `wb_voip` und `wb_license_client` in den Addons-Pfad legen. Für die CRM-Anbindung auch `wb_voip_crm`, für eine
   Ansitel-Anlage auch `wb_voip_ansitel`.
2. Apps-Liste aktualisieren (Entwicklermodus) und **WB VoIP** installieren. `wb_license_client` wird als Abhängigkeit
   mit installiert.
3. `wb_voip_crm` installiert sich selbst, sobald `crm` und `wb_voip` installiert sind (`auto_install`). Es hat keine
   eigene Einrichtung.
4. `wb_voip_ansitel` installiert sich nicht selbst. Es wird nur gebraucht, wenn die Anlage von Ansitel stammt
   (Abschnitt 10).

Die Menüs unter Einstellungen › Technisch erscheinen nur im Entwicklermodus (Einstellungen › „Entwicklermodus aktivieren“).

Beim Installieren von `wb_voip` werden die Benutzer `base.user_root` und `base.user_admin` einmalig in die Gruppe
„WB VoIP: Administrator“ aufgenommen. Weitere Benutzer ordnet man selbst zu (Abschnitt 6).

## 3. Lizenz aktivieren

`wb_voip` prüft über `wb_license_client` die Lizenz für den Produktcode `VOIP`. Aktivieren (nur Systemverwaltung):

1. Einstellungen › **WB Lizenzen** › **Lizenz aktivieren**.
2. Produktcode `VOIP`, Lizenzschlüssel (Format `WB-XXXX-…`) und Aktivierungs-Code (25 Zeichen in 5er-Gruppen, aus dem
   Portal-Download) eintragen. Das API-Secret (`WBS-…`) ist optional und kommt mit dem Schlüssel, falls eines
   ausgegeben wurde. Die Pflichtbestätigungen (EULA, AGB, Datenschutzhinweis) setzen und bestätigen.
3. Unter **Lizenz-Status** prüfen, dass die Lizenz „aktiv“ ist. Ein täglicher Lauf gleicht sie mit dem Lizenzserver
   ab; ist der Server kurz nicht erreichbar, gilt die Lizenz zunächst weiter.

Testsysteme (eine Datenbank, die nicht die lizenzierte Produktivinstanz ist) laufen zeitlich begrenzt zur Probe; danach
braucht die Datenbank eine DEV-Lizenz, und eine kopierte Datenbank sollte neutralisiert werden. Nach einem Umzug auf eine
andere Domain oder Datenbank beantragt man unter **Migration beantragen** die Umstellung. Fragen zur Lizenz:
support@wissen-beratung.de.

Ohne gültige Lizenz zeigt das Softphone „Lizenz fehlt oder abgelaufen“. Anrufen und Anmelden an der Anlage sind dann
gesperrt; Listen, Formulare und bereits erfasste Protokolle bleiben les- und bearbeitbar.

## 4. Server-Schlüssel `WB_VOIP_FERNET_KEY`

SIP-Passwörter (und bei Ansitel die API-Token) speichert WB VoIP nur als Fernet-Chiffrat. Der Schlüssel liegt **nicht**
in Odoo, sondern nur in der Umgebung des Odoo-Prozesses.

1. Schlüssel erzeugen:

   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Ergebnis als Umgebungsvariable `WB_VOIP_FERNET_KEY` für den Odoo-Prozess setzen (Systemd-Unit, Container-Umgebung
   oder Ihre Verwaltung der Umgebungsvariablen) und Odoo neu starten. Bei mehreren Odoo-Workern oder -Servern auf derselben
   Datenbank muss überall derselbe Schlüssel stehen.
3. Schlüssel geheim halten: nie in die Datenbank, in Systemparameter, Logs, Tickets oder ein Repository. Eine Kopie
   gehört in den Passwortspeicher des Betreibers.

Fehlt der Schlüssel, lässt sich kein Passwort speichern und das Softphone meldet, dass es auf dem Server nicht konfiguriert ist.

**Schlüsselwechsel und Verlust:** Mit einem neuen Schlüssel lassen sich die gespeicherten Chiffrate nicht mehr lesen.
Alle SIP-Passwörter müssen dann neu eingegeben werden (jeder Benutzer unter Meine Präferenzen › Telefonie oder ein
VoIP-Administrator am Benutzer); bei Ansitel auch der zentrale und alle persönlichen Token. Einen Schlüssel also nur
bewusst wechseln.

## 5. Anlage anlegen

Einstellungen › Technisch › WB VoIP › **Telefonanlagen**. Anlegen und die Verbindungsdaten ändern darf nur die
Systemverwaltung, weil sich der Browser an der eingetragenen Adresse anmeldet und eine falsche Adresse die Zugangsdaten
aller Benutzer abgreifen würde.

| Feld | Bedeutung |
|---|---|
| Name | frei wählbar |
| WebSocket-URL | muss mit `wss://` beginnen, z. B. `wss://pbx.example.com/ws` |
| SIP-Domain | z. B. `pbx.example.com` |
| Land | gilt als Inland bei der Prüfung von Weiterleitungszielen (Ansitel) und der Rufnummernnormalisierung |
| ICE-Server (STUN) | optional, eine URL je Zeile, nur `stun:`, z. B. `stun:stun.example.org:3478`, höchstens 10 Zeilen. Leer reicht, wenn die Anlage die Medien selbst vermittelt. |
| Modus | **Test** simuliert die Gegenstelle ohne echte Anlage (zum Ausprobieren); **Produktiv** meldet sich an der Anlage an |
| Heranholen-Präfix | optional, Wahlpräfix der Anlage zum Heranholen eines klingelnden Kollegen (z. B. `**` bei Asterisk-Anlagen), höchstens 8 Zeichen aus `0-9`, `*`, `#`; leer = Heranholen aus |

Name, Modus und Heranholen-Präfix darf auch ein VoIP-Administrator ändern. Neue Anlagen stehen zunächst auf „Test“;
erst nach der Einrichtung der Benutzer auf „Produktiv“ stellen.

## 6. Benutzer einrichten

1. **Gruppe zuweisen:** Benutzerformular › Zugriffsrechte: „WB VoIP: Benutzer“ für alle, die telefonieren, „WB VoIP:
   Administrator“ für die Pflege von Anlagen, Nebenstellen und Einstellungen. Der Administrator schließt die
   Benutzergruppe ein. **Systemadministratoren (Gruppe „Administration: Einstellungen“) bekommen die VoIP-Gruppen nicht
   automatisch:** Für die VoIP-Menüs und -Einstellungen brauchen auch sie die Gruppe „WB VoIP: Administrator“.
2. **Nebenstelle:** Benutzerformular, Reiter „Telefonie“ (ändern dürfen nur VoIP-Administratoren, alle anderen sehen die Felder nur schreibgeschützt): Telefonanlage und SIP-Benutzer (die Nebenstelle auf
   der Anlage) wählen. Jede Nebenstelle gehört genau einem Benutzer und einer Anlage.
3. **SIP-Passwort:** setzt der Benutzer selbst unter Meine Präferenzen › **Telefonie** (Feld „SIP-Passwort“) oder ein
   VoIP-Administrator im Benutzerformular. Das Feld ist nur schreibbar, nie lesbar; die Anzeige „Gespeichert – zum Ändern
   neu eingeben“ zeigt, dass eines vorliegt. Entschlüsselt wird es nur bei der Anmeldung des Benutzers selbst.
4. Der Benutzer lädt Odoo neu. Das Telefonsymbol in der Kopfzeile zeigt den Zustand: grün verbunden, grau aus, gelb in
   einem anderen Tab, rot getrennt, ohne Lizenz oder nicht eingerichtet. Beim ersten eingehenden Anruf fragt der Browser
   nach der Erlaubnis für Benachrichtigungen und Mikrofon.

## 7. Rechte

Geprüft gegen Zugriffsrechte (`ir.model.access.csv`), Datensatzregeln und die Prüfungen im Code. „VoIP-Administrator“
ist die Gruppe „WB VoIP: Administrator“. **Die Systemverwaltung (Gruppe „Administration: Einstellungen“) schließt die
VoIP-Gruppen nicht ein.** Wer beides braucht, gehört beiden Gruppen an; nur `admin` und der Systembenutzer werden bei der
Installation in die VoIP-Gruppe aufgenommen.

| Aktion | Benutzer | VoIP-Administrator | Systemverwaltung |
|---|---|---|---|
| Softphone nutzen, anrufen, Anrufe annehmen | ja (mit Lizenz) | ja | nur mit VoIP-Gruppe |
| Eigene Anrufe und Protokolle sehen, anlegen, ändern | ja | ja | nur mit VoIP-Gruppe |
| Fremde Anrufe und Protokolle sehen | nein | ja, alle | nur mit VoIP-Gruppe |
| Anrufe und Protokolle löschen | nein | ja | nur mit VoIP-Gruppe |
| Eigenes SIP-Passwort setzen | ja | ja | nur mit VoIP-Gruppe |
| SIP-Passwort anderer Benutzer setzen | nein | ja | nur mit VoIP-Gruppe |
| SIP-Passwort lesen | nie | nie | nie (nur Chiffrat in der Datenbank) |
| Telefonanlage und Nebenstelle am Benutzer setzen | nein (nur lesen) | ja | nur mit VoIP-Gruppe |
| Anlage lesen | ja | ja | ja |
| Anlage: Name, Modus, Heranholen-Präfix ändern | nein | ja | ja |
| Anlage anlegen, löschen; WebSocket-URL, SIP-Domain, ICE-Server, Land ändern | nein | nein (ohne Systemrecht) | ja |
| Anruf-Buttons und Assistent-Buttons ansehen | ja (nur Lesen) | ja, anlegen, ändern, löschen | nur mit VoIP-Gruppe |
| Buttons mit Python-Code anlegen oder ändern (Assistent) | nein | nein (ohne Systemrecht) | ja (mit VoIP-Administrator) |
| Fenster-Layouts und Layout-Designer | nein | ja (Menü nur mit Zugang zu den Einstellungen) | nur mit VoIP-Gruppe |
| Löschfrist, Team-Übersicht, Gesprächsdauer einstellen | nein | ja (Einstellungsseite braucht Zugang zu den Einstellungen) | nur mit VoIP-Gruppe |
| Lizenz aktivieren, Migration beantragen | nein | nein | ja |
| Ansitel: Schnittstelle, URL, zentraler Token, Zeitzone | nein | nein (ohne Systemrecht) | ja |
| Ansitel: Nebenstelle am Benutzer lesen (Block im Benutzerformular) | nein | ja | ja (mit VoIP-Administrator) |
| Ansitel: Nebenstelle, persönlicher Token, AB-Freigabe setzen | nein | nein (ohne Systemrecht) | ja (mit VoIP-Administrator) |
| Ansitel: Geräte lesen (Menü) | nein | ja | ja (Menü nur mit VoIP-Administrator) |
| Ansitel: Geräte ändern, „Geräte aus Anlage laden“, weitere interne Ziele | nein | nein (ohne Systemrecht) | ja |
| Ansitel: Tasten ansehen | ja (nur Lesen) | ja, Beschriftung, Art, Reihenfolge pflegen | nur mit VoIP-Gruppe |
| Ansitel: feste und interne Tastenziele setzen | nein | nein (ohne Systemrecht) | ja (mit VoIP-Administrator) |
| Ansitel: „Verbindung testen“ | nein | ja | ja (mit VoIP-Administrator) |
| Ansitel: Tasten schalten | ja, nur für sich | ja | nur mit VoIP-Gruppe |
| Ansitel: „Mein Handy“ setzen | ja, nur für sich | ja | ja |
| CRM: Anrufe und Protokolle am Vorgang sehen | eigene | alle | nur mit VoIP-Gruppe |
| CRM: technisches Feld Anruf → Vorgang lesen | nein | nein | ja (mit VoIP-Gruppe, sonst kein Zugriff auf Anrufe) |

Hinweis zu den Menüs: Das Menü „WB VoIP“ unter Einstellungen › Technisch ist für VoIP-Administratoren sichtbar, aber nur
im Entwicklermodus.

## 8. Team-Übersicht, Datenschutz und Betriebsrat

Der Reiter „Team“ im Softphone zeigt Kollegen mit Status frei, telefoniert, klingelt oder abgemeldet. Das ist eine
Verarbeitung personenbezogener Daten von Beschäftigten (Anwesenheit und Telefonstatus).

- **Standard: eingeschaltet.** Ab Version 19.0.4.5.0 ist die Übersicht für alle Firmen an; das Update schaltet sie für
  vorhandene Firmen ein. Die Gesprächsdauer ist standardmäßig **aus**.
- **Verantwortung beim Betreiber.** Die datenschutzfreundliche Voreinstellung (Art. 25 DSGVO) liegt beim Betreiber:
  Schalten Sie die Übersicht **aus, bis die Beschäftigten informiert sind** (Informationspflicht Art. 13 DSGVO), und prüfen
  Sie, ob eine Datenschutz-Folgenabschätzung (Art. 35 DSGVO) nötig ist.
- **Betriebsrat.** Ist ein Betriebsrat vorhanden, ist die Einführung einer technischen Einrichtung, die geeignet ist,
  Verhalten oder Leistung zu überwachen, mitbestimmungspflichtig (§ 87 Abs. 1 Nr. 6 BetrVG). Nutzen Sie die Übersicht erst
  nach dessen Zustimmung (in der Regel Betriebsvereinbarung); bis dahin ausschalten. Das gilt ebenso für den Abgleich mit
  der Anrufliste von Ansitel (Abschnitt 10).
- **Schalter je Firma:** Einstellungen › **WB VoIP** › „Team-Übersicht anzeigen“ und „Gesprächsdauer im Team anzeigen“.
  Maßgeblich ist die Firma, in der die Person gerade arbeitet (aktive Firma). Beim Ausschalten löscht Odoo die gespeicherten
  Lebenszeichen aller Benutzer dieser Firma.
- **Was Kollegen sehen:** nur Name, Nebenstelle, Status und bei „telefoniert“ (wenn eingeschaltet) die Dauer; nie
  Gegennummer, Kontakt oder Notiz. Als Anwesenheit speichert Odoo nur den Zeitpunkt des letzten Lebenszeichens des
  Telefon-Tabs; nach 150 Sekunden ohne Meldung gilt der Kollege als „abgemeldet“, beim Abmelden wird es gelöscht.
- **Verpasste Anrufe:** Kollegen derselben Firma sehen bei einem von ihnen angenommenen Anruf Name, Dauer und Notiz des
  Annehmenden. Das Notizfeld eingehender Anrufe weist darauf hin.

**Löschfrist.** Einstellungen › **WB VoIP** › „Löschfrist für Anrufdaten“ (Systemparameter `wb_voip.aufbewahrung_monate`):
Standard **12 Monate**, `0` = nie löschen. Ein täglicher Lauf entfernt beendete Anrufe, die älter sind; die
Anrufprotokolle bleiben erhalten. Legen Sie die Frist passend zu Ihrem Löschkonzept fest und dokumentieren Sie sie im
Verzeichnis von Verarbeitungstätigkeiten (Art. 30 DSGVO).

Buttons vom Typ „Webadresse“ geben Rufnummer und Kontakt-ID an den Betreiber der Adresse weiter: Bei externen Diensten
einen Vertrag zur Auftragsverarbeitung (Art. 28 DSGVO) schließen und bei Anbietern außerhalb der EU/des EWR die
Drittlandübermittlung prüfen.

## 9. CRM-Anbindung (`wb_voip_crm`)

Ohne eigene Einrichtung; Rechte und Anlage richten sich nach `wb_voip`.

- Anrufe werden beim Beginn dem **offenen Vorgang** zugeordnet: aktiv, Wahrscheinlichkeit unter 100 %, zur aktuellen
  Firma oder ohne Firma, am Kontakt oder an dessen Firma. Gesucht wird mit den Rechten des Benutzers.
- **Zuordnungsregel:** Ein Vorgang, von dem aus angerufen wurde, hat Vorrang. Sonst gilt bei mehreren der zuletzt
  geänderte unter den dem Anrufer zugewiesenen Vorgängen; gibt es keinen eigenen, wird keiner zugeordnet (lieber keine
  als eine falsche). Manuell zuordnen und lösen geht im Block „Vorgänge“ des Anrufer-Fensters.
- Am Vorgang: Smart-Button „Anrufe“ und Reiter „Anrufprotokolle“, jeweils nur mit den Rechten des Benutzers.
- **Rückruf-Aktivität** bei verpassten Anrufen am offenen Vorgang (sonst am Kontakt).
- **Anruf-Button „Vorgang anlegen“** (Einstellungen › Technisch › WB VoIP › Anruf-Buttons) mit Titel-Vorlage
  (`{kontakt}`, `{nummer}`, `{datum}`) und optional Verkaufsteam; angelegt mit den Rechten des Benutzers.
- Bekannte Grenze: Vorgänge an Mitarbeitern eines Firmenkontakts findet das Modul nicht, wenn die Firma selbst anruft.

## 10. Ansitel-Anbindung (`wb_voip_ansitel`)

Für Anlagen von Ansitel über deren REST-Schnittstelle: Schnelltasten (Weiterleitung, Nicht stören, Anrufbeantworter)
und Abgleich mit der Anrufliste. Alle Schritte außer dem Test macht die Systemverwaltung.

1. **Schnittstelle:** An der Anlage „Ansitel-Schnittstelle aktiv“ setzen, REST-URL eintragen (nur `https`, z. B.
   `https://pbx.example.com/awi/api/rest`) und den **zentralen Token** (er wird nur als Chiffrat gespeichert, nie
   angezeigt). Zeitzone der Anrufliste prüfen (Standard Europe/Berlin). Dann „Verbindung testen“ (liest die Geräteliste).
2. **Nebenstellen:** Am Benutzer (Reiter „Telefonie“) die **Ansitel-Nebenstelle** setzen; je Anlage eindeutig. Wer den
   Anrufbeantworter über einen eigenen Zugang schalten soll, bekommt einen **persönlichen Token**. Die Anlage des
   Benutzers wechseln löscht Nebenstelle und persönlichen Token, ändert jemand ohne Systemrecht die URL einer Anlage, werden
   deren Token gelöscht.
3. **Anrufbeantworter-Token:** Reihenfolge: persönlicher Token; sonst zentraler Token, wenn die Systemverwaltung „AB über
   zentralen Token schalten“ am Benutzer gesetzt hat und die Nebenstelle zum zentralen Token gehört; sonst zentraler
   Token, wenn dieser genau eine Nebenstelle hat und es die des Benutzers ist. Sonst bleibt die AB-Taste grau mit Hinweis.
4. **Geräte:** An der Anlage „Geräte aus Anlage laden“ (legt fehlende Geräte aus der Geräteliste an) und die Zuordnung zu
   den Benutzern prüfen (Einstellungen › Technisch › WB VoIP › Geräte). **Die Amtsleitung nie als Gerät eintragen.**
5. **Tasten:** Einstellungen › Technisch › WB VoIP › Softphone-Tasten. Mitgeliefert sind „→ Handy“, „Nicht stören“ und
   „AB“. Jeder Benutzer trägt unter Meine Präferenzen › Telefonie „Mein Handy“ ein. Anzeige je Benutzer (Einstellung
   „Schnelltasten“): im Softphone, als schmale Leiste am Rand oder beides; die Änderung wirkt nach dem Neuladen. AB,
   Weiterleitung und Nicht stören schließen sich aus.

### Weiterleitungsziele (Schutz vor Gebührenbetrug)

Eine Weiterleitung lässt die Anlage auf Kosten des Anschlusses nach außen wählen. Deshalb prüft WB VoIP jedes Ziel beim
Speichern und bei jedem Schalten:

- nur Ziffern 0–9, mindestens 6 Ziffern, gültige Rufnummer; gespeichert wird die Normalform (`+49…`);
- keine Notruf-, Kurzwahl- und Auskunftsnummern (110, 112, 115, 116xxx, 118xx);
- keine Sonder- und Mehrwertnummern (Deutschland u. a. 0137, 0138, 0180–0194, 0700, 0900; Österreich 0900, 0901, 0930,
  0939; Schweiz 0900, 0901, 0906), Satellitendienste (+881, +882, +883) immer gesperrt;
- Ziele mit anderer Ländervorwahl als das Land der Anlage nur, wenn der Systemparameter
  **`wb_voip_ansitel.ausland_erlaubt` den Wert `1`** hat (Standard: gesperrt). Mehrwert- und Notrufsperre gelten weiter,
  fremde Mehrwertnummern sind damit aber nicht ausgeschlossen.

**Interne Nebenstelle oder Rufgruppe** (Zielart „Interne Nebenstelle“): 2 bis 6 Ziffern, keine führende 0, keine
Notruf-/Kurzwahlnummern. Gültig nur, wenn das Ziel unter „Weitere interne Ziele“ an der Anlage steht (Rufgruppen,
Warteschlangen; eine Nummer je Zeile) oder die Anlage einen Teilnehmer mit genau dieser internen Nummer kennt. Die
Geräteliste wird bis zu 10 Minuten zwischengespeichert. Die eigene Nebenstelle ist als Ziel gesperrt. Eine Rufgruppe, in
der der Benutzer selbst Mitglied ist, erkennt das Modul nicht als Schleife.

Feste und interne Tastenziele setzt nur die Systemverwaltung; VoIP-Administratoren pflegen Beschriftung, Art und
Reihenfolge. Gewählt wird nur mit Ziffern; ein führendes „+“ wird zu „00“.

### Abgleich mit der Anrufliste

Der Cron „WB VoIP Ansitel: Abgleich mit der Anrufliste“ läuft alle 5 Minuten je aktiver Anlage mit Token und liest die
Anrufliste (`getcdr`, höchstens 1000 Zeilen je Abruf). So erscheinen auch Anrufe, die Odoo nicht gesehen hat (Odoo war
geschlossen), und Anrufe, die an einem Tischtelefon oder per Weiterleitung angenommen wurden („anderswo angenommen“ mit
Gerät und Dauer).

- Fenster: von = Stand − 4 Stunden, bis = jetzt − 60 Sekunden, höchstens 6 Stunden je Lauf; der erste Lauf liest die
  letzten 30 Minuten.
- Der Abgleich läuft auch ohne gültige Lizenz (er liest nur und schaltet nichts an der Anlage).
- Grenzen: Liefert die Anlage mehr als 50 Seiten je Abruf, wird das Fenster halbiert; reicht das nicht, wird dieses
  Stück übersprungen (Fehler im Log, Anrufe fehlen dann). Die doppelte Stunde im Herbst berücksichtigt nur die Zuordnung zu
  Softphone-Anrufen. Eine Handy-Mailbox hinter einer Weiterleitung zählt als Weiterleitung. Der Team-Status der Tischtelefone
  kommt nicht von der Anlage und fehlt daher.
- Datenschutz: importiert werden Zeitpunkt, Dauer, Gerät und Gegennummer (auch unbekannter Nummern, ohne Zuordnung). Es
  gilt die Löschfrist aus Abschnitt 8. Beschäftigte sind zu informieren (Art. 13 DSGVO), die Mitbestimmung ist zu beachten
  (§ 87 Abs. 1 Nr. 6 BetrVG).
- Neue Anlagen: Nach dem Einrichten die ersten Läufe beobachten; Fehler stehen im Odoo-Log (nur Kennungen und
  Fehlertypen, nie Token oder URL).

## 11. Fehlersuche

| Symptom | Ursache und Abhilfe |
|---|---|
| Symbol gelb, „Telefon läuft in einem anderen Tab“ | Die Anlage nimmt je Nebenstelle nur eine Registrierung an; nur ein Odoo-Tab hält das Telefon. Die anderen Tabs geben Wählen an ihn weiter. Schließt der Tab, übernimmt der nächste. Der Browser muss `navigator.locks` und `BroadcastChannel` unterstützen. |
| Symbol rot, „Nicht verbunden“, Registrierung abgelehnt | Falsches SIP-Passwort oder falsche Nebenstelle, Nebenstelle ist schon an anderer Stelle angemeldet, oder die Anlage lehnt die Registrierung ab. Passwort neu eingeben und Anlage-Log prüfen. WB VoIP versucht es nach 3, 10, 30 Sekunden und danach jede Minute erneut; „Neu verbinden“ im Softphone löst sofort einen Versuch aus. |
| Sofort „Nicht verbunden“ ohne Meldung der Anlage | `wss://`-Adresse falsch oder nicht erreichbar, Zertifikat der Anlage ungültig (siehe unten), Firewall oder Proxy sperrt WebSocket. In der Browser-Konsole (Netzwerk) die Fehlermeldung des WebSocket ansehen. |
| „Telefonie ist noch nicht eingerichtet.“ oder „Softphone ist auf dem Server nicht konfiguriert.“ | Zweite Meldung: `WB_VOIP_FERNET_KEY` fehlt am Odoo-Prozess (Abschnitt 4). Erste Meldung: Telefonie ist für den Benutzer noch nicht fertig eingerichtet (Abschnitt 6). |
| „Telefon ist für diesen Benutzer nicht eingerichtet.“ | Anlage und SIP-Benutzer am Benutzer eintragen; Gruppe „WB VoIP: Benutzer“ prüfen. |
| Kein Ton oder einseitiger Ton | Medien kommen nicht durch (ICE). Prüfen: Mikrofon im Browser erlaubt; die Anlage vermittelt Medien selbst oder ein erreichbarer `stun:`-Server ist an der Anlage eingetragen; Firewall lässt die Medienports (UDP, Bereich der Anlage) zu. TURN wird nicht unterstützt: Hinter symmetrischem NAT ohne Medienvermittlung durch die Anlage funktioniert WebRTC dann nicht. |
| Zertifikatsfehler bei `wss://` | Der Browser prüft das TLS-Zertifikat der Anlage wie bei jeder Webseite: gültig, nicht abgelaufen, Name passt zum Host der URL, vollständige Kette. Selbstsignierte Zertifikate lehnt der Browser ab. Zertifikat auf der Anlage erneuern; zum Prüfen die `https://`-Adresse desselben Hosts im Browser öffnen. |
| Mikrofon wird nicht gefragt oder „Browser unterstützt das Softphone nicht“ | Odoo läuft nicht über HTTPS (kein sicherer Kontext) oder der Browser ist zu alt. |
| „Lizenz fehlt oder abgelaufen“ | Lizenz nicht aktiviert, abgelaufen oder die Datenbank passt nicht zur lizenzierten Instanz (Abschnitt 3). Anrufen und Anmelden sind gesperrt, Protokolle bleiben les- und bearbeitbar. Schalter der Ansitel-Tasten bleiben grau. Der Lizenz-Status unter Einstellungen › WB Lizenzen zeigt die Meldung des Lizenzservers. |
| Ansitel: Tasten grau, Hinweis, dass die Anlage nicht erreichbar ist | URL und Token an der Anlage prüfen, „Verbindung testen“. Nur `https` mit gültigem Zertifikat; Zeitlimit 10 s, keine Weiterleitungen, keine Proxys. Bei Anlagenfehlern entfallen weitere Aufrufe, bis die Anlage antwortet. |
| Ansitel: Taste grau mit Hinweis „Bitte unter Meine Präferenzen › Telefonie Ihr Handy eintragen.“ | „Mein Handy“ fehlt oder besteht die Zielprüfung nicht (Mehrwert-, Notruf-, Auslandsnummer, Abschnitt 10). |
| Ansitel: AB-Taste grau | Kein persönlicher Token und keine Freigabe für den zentralen Token (Abschnitt 10, Punkt 3). |
| Ansitel: Anrufe fehlen im Abgleich | Cron aktiv? Nebenstelle und Gerätezuordnung am Benutzer prüfen; Zeitzone der Anrufliste; Log nach übersprungenen Fenstern durchsuchen. |
| Team zeigt „Die Team-Übersicht ist ausgeschaltet.“ | Schalter je Firma unter Einstellungen › WB VoIP (aktive Firma beachten). |
| Anruf klingelte, aber nichts in „Verpasst“ | Ist gar kein Odoo-Tab mit dem Telefon offen, erfährt Odoo vom Anruf nichts (außer bei Ansitel über den Abgleich). Wird der Tab während des Klingelns geschlossen, beendet der Aufräum-Lauf den Anruf nach 4 Stunden als „fehlgeschlagen“. |

Bei Problemen mit der Lizenz oder dem Produkt: support@wissen-beratung.de. Das Odoo-Log enthält nie Zugangsdaten; für eine
Fehlermeldung genügen Zeitpunkt, Benutzer und die Meldung im Softphone.
