=======
WB VoIP
=======

Softphone im Browser, Anrufprotokoll und Anbindung an SIP-Telefonanlagen für Odoo 19.

Stand Phase 2: Softphone, Anrufprotokoll mit Popup nach dem Anruf, Anrufer-Fenster, eigene Anruf-Buttons,
Rufnummer zuordnen, Löschfrist und Lizenzanbindung (Produktcode ``VOIP``).

Einrichtung
-----------

1. **Anlage**: Einstellungen › Technisch › WB VoIP › Telefonanlagen (Administrator). Eintragen: WebSocket-URL
   (muss mit ``wss://`` beginnen, z. B. ``wss://anlage.example/ws``), SIP-Domain und Land. Modus „test“
   simuliert die Gegenstelle ohne echte Anlage, „prod“ meldet sich an der Anlage an. „ICE-Server“ ist
   optional (eine URL je Zeile, nur ``stun:``, TURN-Server werden noch nicht unterstützt); leer fragt der Browser keinen
   fremden STUN-Server (keine Daten an Dritte).
2. **Nebenstelle**: Im Benutzerformular (Reiter „Telefonie“; Anlage und Nebenstelle nur durch Administratoren) Anlage und Nebenstelle
   (SIP-Benutzer) wählen; jede Nebenstelle gehört genau einem Benutzer. Das SIP-Passwort setzt der
   Benutzer selbst (Mein Profil) oder der Administrator; es ist nur schreibbar, nie lesbar.
3. **Rechte**: Gruppe „WB VoIP: Benutzer“ erlaubt Softphone und eigene Protokolle (jeder sieht nur seine
   eigenen); „WB VoIP: Administrator“ sieht alle Anrufe und Protokolle und pflegt Anlagen und Nebenstellen.
4. **Schlüssel**: ``WB_VOIP_FERNET_KEY`` setzen (siehe nächster Abschnitt).
5. **Lizenz**: Das Modul braucht eine gültige Lizenz für den Produktcode ``VOIP`` (Modul
   ``wb_license_client``). Ohne Lizenz zeigt das Softphone „Lizenz fehlt oder abgelaufen“; Anrufen und
   Anmelden sind gesperrt, Protokolle bleiben les- und bearbeitbar.
6. **Löschfrist**: Einstellungen › „WB VoIP“ › Löschfrist für Anrufdaten (Administrator), Systemparameter
   ``wb_voip.aufbewahrung_monate``, Standard 12, ``0`` = nie löschen. Ein täglicher Lauf entfernt beendete
   Anrufe, die älter sind; die Protokolle bleiben erhalten.

Protokoll-Popup
---------------

Nach dem Auflegen öffnet sich im Tab, der den Anruf geführt hat, ein breites Popup: links Ergebnis
(vorbelegt) und Notiz, rechts Kontakt mit Telefon und E-Mail sowie die letzten fünf Anrufe des Kontakts
(nur eigene). „Folgeaktivität planen“ öffnet den Odoo-Dialog am verknüpften Datensatz (wenn der Benutzer
dort schreiben darf), sonst am Protokoll. Verwerfen lässt das vorbelegte Ergebnis stehen.

Anrufer-Fenster
---------------

Bei einem eingehenden oder ausgehenden Anruf öffnet sich im Telefon-Tab ein Seitenpanel rechts. Es zeigt
Kontakt (Bild, Name, Firma, Telefon, E-Mail, „Kontakt öffnen“), die letzten Gespräche mit dem Kontakt
(nur eigene), offene Aufgaben am Kontakt, die Notiz zum laufenden Gespräch und die Anruf-Buttons.
Im Kopf stehen die Gesprächsknöpfe (beim Klingeln „Annehmen“ und „Ablehnen“, sonst „Stumm“ und
„Auflegen“), damit sie auch dann erreichbar sind, wenn das Panel auf schmaleren Bildschirmen das Softphone
verdeckt. Bei der Annahme und nach dem Auflegen lädt das Panel die Anruf-Buttons neu (Zustand „Während des
Gesprächs“ bzw. „Nach dem Anruf“).
Ist die Nummer keinem Kontakt zugeordnet, steht dort „Unbekannte Nummer“ mit „Zuordnen“. Das Panel schließt
sich mit der Escape-Taste, über das Kreuz oder zehn Sekunden nach dem Auflegen; solange der Mauszeiger darüber
steht oder der Fokus im Panel liegt, bleibt es offen. Es nimmt keinen Fokus aus einem Eingabefeld und
öffnet sich nur in dem Tab, der den Anruf führt. Gezeigt werden nur Daten, die der angemeldete Benutzer
auch sonst lesen darf.

Anruf-Buttons
-------------

Einstellungen › Technisch › WB VoIP › Anruf-Buttons (Administrator). Jeder Button erscheint an den gewählten
Orten: Anrufer-Fenster, Protokoll-Popup nach dem Auflegen und Softphone-Anrufansicht. Im Softphone sind
höchstens vier Buttons sichtbar, der Rest steht unter „Mehr“.

Je Button einstellbar:

* **Art**:

  * *Fenster öffnen*: öffnet eine Odoo-Fensteraktion (z. B. ein Formular oder eine Liste).
  * *Serveraktion*: führt eine Serveraktion aus. Erlaubt sind nur Python-Code (``code``) und „Feld schreiben“
    am Datensatz selbst (ohne Punktnotation im Feldpfad); alle anderen Arten (Datensatz anlegen/kopieren,
    Mehrfachaktion, Webhook, Mail-Arten) lehnt das Modul ab, weil Odoo sie mit erhöhten Rechten ausführt.
  * *Webadresse öffnen*: öffnet eine Adresse in einem neuen Tab. Nur ``https://``, höchstens 500 Zeichen,
    Platzhalter ``{nummer}``, ``{nummer_e164}`` und ``{partner_id}`` (werden URL-kodiert eingesetzt). Keine
    Zugangsdaten, Tokens oder Schlüssel in die Adresse schreiben: Sie ist für jeden Benutzer der Gruppe
    im Browser sichtbar. Datenschutz: Rufnummer und Kontakt-ID gehen an den Betreiber der Webadresse. Ist
    das ein externer Dienst, braucht es in der Regel einen Vertrag zur Auftragsverarbeitung (Art. 28
    DSGVO); bei Anbietern außerhalb der EU/des EWR zusätzlich die Regeln für Drittlandübermittlungen
    prüfen.
  * *Neuer Kontakt*: öffnet ein Kontaktformular mit der Rufnummer; erscheint nur, solange der Anruf noch
    keinem Kontakt zugeordnet ist.

* **Orte**: Anrufer-Fenster, Protokoll-Popup, Softphone (mindestens einer).
* **Richtung**: beide, nur eingehend oder nur ausgehend.
* **Zustand**: immer, während es klingelt, während des Gesprächs oder nach dem Anruf.
* **Gruppen**: Leer = alle WB-VoIP-Benutzer, sonst nur Mitglieder der gewählten Gruppen.
* **Bedingung für Kontakte**: optionale Domain auf Kontakte, z. B. ``[('is_company', '=', True)]``. Sie wird nie
  als Code ausgeführt, nur bekannte Felder und Standard-Operatoren sind erlaubt, und der Autor darf nur Felder
  verwenden, die er selbst lesen darf. Ohne Kontakt am Anruf passt eine Bedingung nie.
* **Symbol** (Font-Awesome-Klasse wie ``fa-user``) und **Farbe** aus einer festen Liste.

Alles läuft mit den Rechten des Benutzers, der den Button drückt: Serveraktionen und Fensteraktionen sehen und
ändern nur, was dieser Benutzer auch sonst darf. Der Server prüft bei jedem Klick erneut, ob der Button zu
Anruf, Richtung, Zustand, Gruppe und Kontakt passt; ein ausgeblendeter Button lässt sich nicht per Aufruf
umgehen. Ausnahme: Code oder Formeln, die der Administrator in einer Serveraktion selbst mit ``.sudo()``
schreibt, laufen mit erhöhten Rechten; dafür ist der Administrator verantwortlich. Der mitgelieferte
Beispiel-Button „Neuer Kontakt“ lässt sich ändern oder archivieren; der damit angelegte Kontakt wird dem
Anruf zugeordnet.

Das Menü Einstellungen › Technisch erscheint nur im Entwicklermodus (Einstellungen › „Entwicklermodus
aktivieren“).

Rufnummer zuordnen
------------------

„Zuordnen“ im Anrufer-Fenster öffnet einen Assistenten für Anrufe mit unbekannter Nummer: Kontakt wählen und
bestimmen, in welchem Telefonfeld des Kontakts die Nummer gespeichert wird (Telefon, Mobil usw. oder „Nicht
speichern“). Ist das Feld schon belegt, speichert der Assistent erst nach „Ersetzen“. Darf der Benutzer den
Kontakt nicht ändern, wird der Anruf trotzdem zugeordnet und die Nummer nicht gespeichert (mit Hinweis). Danach
gehören Anruf und Protokoll zum Kontakt, und spätere Anrufe dieser Nummer erkennt WB VoIP selbst. Zuordnen
kann nur, wer den Anruf selbst geführt hat.

SIP-Passwort und Schlüssel
--------------------------

Das SIP-Passwort wird nur verschlüsselt gespeichert (Fernet). Benutzer und Administratoren können es
setzen, aber nicht lesen; entschlüsselt wird es nur bei der Softphone-Anmeldung des Benutzers selbst.

Der Schlüssel kommt ausschließlich aus der Umgebungsvariable ``WB_VOIP_FERNET_KEY`` des Odoo-Prozesses
(nie in Datenbank, Systemparametern, Logs oder Repo):

1. Schlüssel erzeugen::

     python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"

2. Als Umgebungsvariable ``WB_VOIP_FERNET_KEY`` des Odoo-Containers setzen (z. B. in Coolify) und Odoo
   neu starten.

Fehlt der Schlüssel, lässt sich kein Passwort speichern und das Softphone meldet „nicht konfiguriert“.
Geht der Schlüssel verloren oder wird er gewechselt, müssen alle SIP-Passwörter neu eingegeben werden.

Softphone
---------

Telefonsymbol in der oberen Leiste mit Statuspunkt (grün verbunden, grau aus, gelb in anderem Tab, rot
getrennt, ohne Lizenz oder nicht eingerichtet). Ein Klick öffnet das Softphone unten rechts mit den Reitern
„Wählen“, „Anrufe“ (letzte 50 eigene), „Kontakte“ und „Aufgaben“; weitere Reiter tragen Module in die
Registry ``wb_voip_reiter`` ein (``{titel, Component, sequence}``). Während eines Anrufs: Name/Nummer,
Dauer, Stumm, Halten, Tastatur, Weiterleiten, Notiz und Auflegen; eingehend Annehmen/Ablehnen und eine
Desktop-Benachrichtigung (Erlaubnis wird einmal angefragt). Neben jedem Odoo-Telefonfeld wählt ein
Telefonsymbol über das Softphone und verknüpft den Anruf mit dem Datensatz. Aus einem zweiten Tab wird
der Anruf an den Telefon-Tab übergeben.

Lizenz: OPL-1. Autor: WISSEN BERATUNG (Tobias Wissen), https://www.wissen-beratung.de
