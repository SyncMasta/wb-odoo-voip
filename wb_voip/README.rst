=======
WB VoIP
=======

Softphone im Browser, Anrufprotokoll und Anbindung an SIP-Telefonanlagen für Odoo 19.

Stand Phase 1: Softphone, Anrufprotokoll mit Popup nach dem Anruf, Löschfrist und Lizenzanbindung
(Produktcode ``VOIP``).

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
