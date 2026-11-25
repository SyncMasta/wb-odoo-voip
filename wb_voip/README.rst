=======
WB VoIP
=======

Softphone im Browser, Anrufprotokoll und Anbindung an SIP-Telefonanlagen für Odoo 19.

Stand Phase 4 (Version 19.0.4.1.0): Team-Übersicht, zweite Leitung, Übergeben, Konferenz, Softphone, Anrufprotokoll mit Popup nach dem Anruf, Anrufer-Fenster, eigene Anruf-Buttons,
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

Fenster-Layouts und Designer
----------------------------

Was im Anrufer-Fenster und im Protokoll-Popup steht, bestimmt ein Layout aus Blöcken. Administratoren
(„WB VoIP: Administrator“) pflegen es unter Einstellungen › Technisch › WB VoIP › „Layout-Designer“ (grafisch,
mit Vorschau an einem Beispielkontakt) oder „Fenster-Layouts“ (Liste und Formular); „Designer öffnen“ im
Formularkopf eines Layouts öffnet dieses Layout im Designer. Das Menü erscheint nur im Entwicklermodus und
nur mit Zugang zu den Einstellungen (Recht „Administration: Einstellungen“). Kontaktfelder eines Blocks wählt
man im Designer; im Formular ist die Feldspalte nur mit diesem Recht sichtbar.

* **Fenster**: Anrufer-Fenster oder Protokoll-Popup.
* **Blöcke je Fenster**: Anrufer-Fenster: Kontaktkopf, Kontaktfelder, Letzte Protokolle, Offene Aufgaben,
  Notiz, Buttons und Text. Protokoll-Popup: Kontaktkopf, Kontaktfelder, Letzte Protokolle und Text (dort
  gibt es keine Buttons, keine Notiz und keine Aufgaben). Blöcke lassen sich ordnen, mit Titel versehen und
  bei den Breiten ab „Breit“ auf zwei Spalten verteilen. Bei Protokollen und Aufgaben stellt man die Anzahl
  ein (1 bis 20).
* **Breite**: Normal, Breit oder Sehr breit. Im Anrufer-Fenster 720, 960 oder 1200 Pixel (höchstens
  Bildschirmbreite), im Popup große, extra große oder Vollbild-Dialoggröße.
* **Gruppen und Reihenfolge**: Ein Layout ohne Gruppen gilt für alle WB-VoIP-Benutzer, sonst nur für
  Mitglieder der gewählten Gruppen. Passen mehrere Layouts, gewinnt die kleinste Reihenfolge; passt keines,
  gilt das Standard-Layout. Das Standard-Layout selbst hat keine Gruppen.
* **Standard-Layout**: Je Fenster liefert das Modul genau eines mit. Es lässt sich bearbeiten, aber nicht
  löschen oder archivieren; „Auf Standard zurücksetzen“ stellt seine Blöcke wieder auf den ausgelieferten
  Stand. Fehlt es trotzdem (etwa nach einem Eingriff direkt in der Datenbank), zeigen die Fenster den
  ausgelieferten Aufbau, ohne einen Datensatz anzulegen.
* **Nur lesbare Felder**: Kontaktfelder erscheinen nur, wenn der angemeldete Benutzer sie auch sonst lesen
  darf; andere Felder bleiben für ihn weg. Zugangsdaten- und Systemfelder (Passwörter, Tokens, Schlüssel,
  Anmeldelinks) lassen sich nie einbinden, Verknüpfungen nur auf eine feste Liste unkritischer Modelle.

Die Schaltflächen „Zuordnen“ und „Neuer Kontakt“ bei unbekannter Nummer stehen immer im Kopf des Fensters,
unabhängig vom Layout, damit sich ein Anruf nie ohne Zuordnungsmöglichkeit verirrt.

Anruf-Buttons
-------------

Einstellungen › Technisch › WB VoIP › Anruf-Buttons (Administrator). Jeder Button erscheint an den gewählten
Orten: Anrufer-Fenster, Protokoll-Popup nach dem Auflegen und Softphone-Anrufansicht. Im Softphone sind
höchstens vier Buttons sichtbar, der Rest steht unter „Mehr“.

Am einfachsten legt man Buttons mit dem Button-Assistenten an (siehe unten): Er erzeugt Button und
Serveraktion zusammen und lässt nur erlaubte Formen zu. Von Hand angelegte Buttons mit Serveraktionen aus der
Odoo-Oberfläche funktionieren weiter und unterliegen unverändert den hier beschriebenen Regeln; der
Assistent ist die empfohlene Variante.

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
umgehen. Das gilt auch für Python-Code aus dem Button-Assistenten: Er läuft als der Benutzer, der klickt;
erhöhte Rechte bekommt er nur, wenn der Code selbst ausdrücklich ``.sudo()`` aufruft (dafür ist der
Systemadministrator verantwortlich, der ihn schreibt). Von Hand in der Odoo-Oberfläche angelegte
Serveraktionen an Buttons unterliegen weiter den oben beschriebenen Regeln (nur Python-Code und „Feld
schreiben“ am Datensatz selbst; ``.sudo()`` im Code wirkt genauso). Der mitgelieferte
Beispiel-Button „Neuer Kontakt“ lässt sich ändern oder archivieren; der damit angelegte Kontakt wird dem
Anruf zugeordnet, aber nur, wenn die Nummer des neuen Kontakts zur Anrufnummer passt (so bleibt etwa eine
nebenbei angelegte Firma unverknüpft).

Button-Assistent
~~~~~~~~~~~~~~~~

„Button mit Assistent anlegen“ (Knopf in der Button-Liste und Menü, nur WB-VoIP-Administratoren) legt den
Button zusammen mit seiner Serveraktion an, immer in einer erlaubten Form. Es gibt drei Wege; die ersten
beiden stehen allen WB-VoIP-Administratoren offen, der dritte nur Systemadministratoren:

* **Feld setzen**: ein Feld am Kontakt des Anrufs oder am Datensatz, aus dem heraus angerufen wurde, bekommt
  einen festen Wert. Wählbar sind nur Modelle, die der Administrator selbst ändern darf (keine technischen
  Modelle wie ``ir.*``, ``res.users``, ``res.groups``, ``mail.*``, ``bus.*``), und nur gespeicherte, nicht
  berechnete Felder der Typen Text, Auswahl, Ja/Nein, Zahl, Datum, Many2one und Many2many (Many2many:
  „hinzufügen“), die er selbst schreiben darf. Ein Datensatz als Wert muss beim Anlegen für den
  Administrator lesbar sein; beim Klick schreibt Odoo dessen ID als der Benutzer, der klickt.
* **Vorlage**: „Kategorie am Kontakt hinzufügen“ und „Sprache des Kontakts setzen“; intern ebenfalls
  „Feld setzen“.
* **Python-Code**: nur für Systemadministratoren (``base.group_system``), auch per Aufruf nicht umgehbar.

Die erzeugte Serveraktion bekommt die technische Gruppe „WB VoIP: Button-Serveraktionen (technisch)“, der
niemand angehört, und keinen Eintrag im Aktionsmenü. Außerhalb der Anruf-Buttons lässt Odoo sie deshalb von
niemandem ausführen, auch nicht, wenn jemand der Gruppe doch angehören sollte (Mitglieder lehnt das Modul
ab). Am Button läuft sie mit den Rechten des Benutzers, der klickt. Bei Buttons mit Python-Code dürfen nur
Systemadministratoren Gruppen, Bedingung, Orte, Richtung und Zustand ändern sowie den Button archivieren,
reaktivieren oder löschen. Ändern lässt sich die Aktion nur über „Mit Assistent bearbeiten“ am Button (Python-Code nur durch
Systemadministratoren); Bezeichnung, Orte und Gruppen auch direkt im Formular. Beim Löschen des Buttons wird
seine Serveraktion mitgelöscht, sofern kein anderer Button sie nutzt. Kopieren lässt sich ein solcher Button
nicht (bitte mit dem Assistenten neu anlegen). Beim Deinstallieren von WB VoIP löscht das Modul alle
Serveraktionen des Assistenten, damit sie ohne die technische Gruppe nicht frei ausführbar zurückbleiben.

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

Verpasste Anrufe
----------------

Zustände: Legt der Anrufer vor der Annahme auf (SIP CANCEL), ist der Anruf „verpasst“ (Protokoll „nicht
erreicht“). Nimmt ein Kollege den Anruf an, meldet die Anlage „Call completed elsewhere“; der eigene Anruf
wird dann „anderswo angenommen“. Selbst abgelehnte Anrufe bleiben „abgelehnt“. Ein verpasster Anruf legt eine
Aktivität „Rückruf: verpasster Anruf“ an (Aktivitätstyp „Rückruf“): am verknüpften Kontakt, nur wenn der
Benutzer ihn lesen darf, und je Benutzer und Kontakt nur eine offene.

Liste und Zähler: Im Softphone zeigt der Reiter „Verpasst“ die offenen Einträge je Rufnummer (mit Anzahl und
letzter Zeit), ein Zähler am Telefonsymbol und am Reiter die noch nicht gesehenen. Der Zähler zählt nur
ungesehene verpasste Anrufe, nie „anderswo angenommen“; als gesehen gilt, was die Liste in einem sichtbaren Tab
angezeigt hat. Liste und Zähler laden per RPC, der Bus meldet nur „neu laden“ (keine personenbezogenen Daten).

Erledigen: „Jetzt anrufen“ wählt die Nummer. Mit ✓ und der Eingabe „Wie erledigt?“ schließt man die Einträge der
Nummer; der Text wird die Rückmeldung der Aktivität. Ein angenommener eingehender Anruf derselben Nummer schließt
die offenen Einträge aller Benutzer sofort; ein ausgehender Rückruf schließt sie beim Auflegen, wenn sein
Protokoll „erreicht“ lautet. Wird die Rückruf-Aktivität im Chatter als erledigt markiert, ist auch der Eintrag
erledigt.

Wieder öffnen: Ändert man das Ergebnis eines Rückruf-Protokolls von „erreicht“ auf etwas anderes (z. B.
Mailbox), öffnen sich genau die Einträge wieder, die dieses Protokoll geschlossen hat, je Benutzer mit neuer
Rückruf-Aktivität und wieder als ungesehen (der Zähler steigt); zurück auf „erreicht“ schließt sie erneut. Einträge, die auf anderem Weg erledigt wurden,
bleiben erledigt. Archivierte Aktivitäten werden nie wiederverwendet.

Kollegen-Hinweis und gemeinsame Firma: Kam der Anruf bei mehreren Nebenstellen gleichzeitig an und nahm ihn ein
Kollege an, steht in der Liste „angenommen von <Name> (Dauer)“ und die Notiz des Kollegen. Als gleichzeitig gilt
derselbe eingehende Anruf (gleiche Rufnummer im E.164-Format, Beginn höchstens 10 Sekunden auseinander), aber nur
bei Benutzern mit gemeinsamer Firma; zwischen Firmen wird nichts verknüpft oder geschlossen. Anrufe ohne E.164-Nummer
werden nie verknüpft.

Datenschutz: Kollegen sehen von einem angenommenen Anruf nur Name, Dauer und Notiz des Annehmenden, und nur in
derselben Firma; das Notizfeld eingehender Anrufe weist darauf hin. Ob jemand angenommen hat, meldet der Browser;
der Server kann das nicht prüfen. Eine falsche Meldung kann daher offene Einträge von Kollegen mit derselben Nummer
im 10-Sekunden-Fenster schließen. Ein Anruf, der schon „anderswo angenommen“ ist, gilt nur dann noch als selbst
angenommen, wenn der Browser das in den ersten 120 Sekunden nach dem Beginn meldet; eine spätere Meldung ändert
nichts. Ausgehende Anrufe, die der Aufräum-Lauf beendet, schließen keine Einträge.

Geschlossener Tab: Wird der Tab mit dem Telefon geschlossen, während ein Anruf klingelt, erfährt Odoo das Ende
nie. Der Aufräum-Lauf beendet solche Anrufe nach 4 Stunden als „fehlgeschlagen“ (Protokoll „nicht erreicht“),
nicht als „verpasst“: ohne Rückruf-Aktivität und ohne Eintrag in der Liste. Ist gar kein Odoo-Tab mit dem Telefon
offen, erfährt Odoo vom Anruf nichts. Schlägt das Abschließen einer Aktivität fehl,
gilt der Eintrag trotzdem als erledigt (wird protokolliert). Bei der Deinstallation hängt das Modul vorhandene
Rückruf-Aktivitäten auf den Odoo-Typ „Anruf“ um, damit Aufgaben erhalten bleiben.

Team, zweite Leitung, Übergeben und Konferenz
---------------------------------------------

Team
~~~~

Der Reiter „Team“ im Softphone zeigt die Kollegen mit ihrem Status:

* **frei**: angemeldet, kein Gespräch.
* **telefoniert**: im Gespräch, mit der Dauer seit der Annahme.
* **klingelt**: ein Anruf klingelt gerade.
* **abgemeldet**: Telefon nicht angemeldet oder kein Lebenszeichen mehr.

Wer erscheint: aktive interne Benutzer mit Recht „WB VoIP: Benutzer“, mit Anlage und SIP-Benutzer, die mit
dem Betrachter dieselbe Firma und dieselbe Telefonanlage haben. Der Betrachter selbst erscheint nicht.

Datenschutz: Je Kollege sehen die anderen nur Name, Nebenstelle, Status und bei „telefoniert“ die Dauer. Nie
die Gegennummer, den Kontakt oder die Notiz eines Gesprächs. Als Anwesenheit speichert Odoo nur den Zeitpunkt
des letzten Lebenszeichens des Telefon-Tabs (alle 60 Sekunden gemeldet, nach 150 Sekunden ohne Meldung gilt
der Kollege als „abgemeldet“); beim Abmelden wird er gelöscht. Der Bus meldet nur „neu laden“.

Knöpfe je Kollege: Anrufen, Heranholen, im Gespräch Übergeben, „Mit Rückfrage“ und „Konferenz“.

Heranholen: Der Knopf erscheint nur bei einem klingelnden Kollegen und nur, wenn an der Anlage ein
Heranholen-Präfix eingetragen ist (Einstellungen › Technisch › WB VoIP › Telefonanlagen, höchstens 8 Zeichen
aus Ziffern, ``*`` und ``#``). Heranholen wählt Präfix plus Nebenstelle des Kollegen. Beispiel für Anlagen auf
Asterisk-Basis: ``**``. Leer heißt: Heranholen ist aus, der Knopf bleibt ausgeblendet. Das Präfix bestimmt
die Anlage; im Zweifel in deren Dokumentation nachsehen.

Zweite Leitung
~~~~~~~~~~~~~~

Pro Tab sind höchstens zwei gleichzeitige Anrufe möglich. Ruft jemand an, während ein Gespräch läuft,
erscheint ein wartender Anruf („Zweiter Anruf“) mit Annehmen und Ablehnen; Annehmen hält das erste Gespräch.
Ein dritter Anruf wird abgewiesen (SIP 480) und zählt als verpasst. Früher wurde 486 gesendet; wer die
Rückmeldecodes auswertet, beachtet die Änderung.

Übergeben
~~~~~~~~~

* **Direkt** (blind): Der Anrufer wird per REFER an die Zielnummer übergeben, ohne Rückfrage.
* **Mit Rückfrage** (attended): Das Gespräch wird gehalten, der Kollege angerufen; „Verbinden“ übergibt per
  REFER mit Replaces, „Zurück“ beendet die Rückfrage. Bestätigt die Anlage die Übergabe nicht innerhalb von
  15 Sekunden, bleiben beide Leitungen bestehen, der Kunde kommt zurück und es erscheint ein Hinweis. Nimmt der
  Kollege nicht ab oder legt er auf, kommt der Kunde ebenfalls automatisch zurück.

Jedes Kundengespräch bekommt sein eigenes Protokoll-Popup; laufen mehrere, öffnen sie sich nacheinander.

Konferenz im Browser
~~~~~~~~~~~~~~~~~~~~

Der Knopf „Konferenz“ (Leiste „Gehalten“) oder „Konferenz“ in der Team-Liste mischt beide Gespräche im
Browser: höchstens drei Teilnehmer (man selbst und zwei Gegenstellen). Der Ton läuft über den eigenen Rechner,
das braucht eine stabile Verbindung und ausreichend Bandbreite am Gastgeber (Upload und Download je zweimal
Sprache). Stumm gilt in beide Richtungen. Legt einer der beiden auf, läuft das Gespräch mit dem anderen
normal weiter; „Konferenz beenden“ legt beide auf. Tastatur (DTMF), Halten, Weiterleiten und Team sind in
der Konferenz gesperrt. Scheitert die automatische Konferenz mit einem Kollegen (der Browser hält den Ton
an), erscheint der Hinweis, im Gespräch „Konferenz“ zu drücken.

Grenzen: Übergeben, Heranholen und Konferenz sind mit automatischen Tests abgesichert, aber noch nicht gegen
jede Telefonanlage live geprüft. Eine Anlage muss REFER mit Replaces und Ruf-Heranholen per Wahlpräfix
unterstützen.

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
„Wählen“, „Anrufe“ (letzte 50 eigene), „Verpasst“, „Team“, „Kontakte“ und „Aufgaben“; weitere Reiter tragen Module in die
Registry ``wb_voip_reiter`` ein (``{titel, Component, sequence}``). Während eines Anrufs: Name/Nummer,
Dauer, Stumm, Halten, Tastatur, Weiterleiten, Rückfrage, Konferenz, Notiz und Auflegen; eingehend Annehmen/Ablehnen und eine
Desktop-Benachrichtigung (Erlaubnis wird einmal angefragt). Neben jedem Odoo-Telefonfeld wählt ein
Telefonsymbol über das Softphone und verknüpft den Anruf mit dem Datensatz. Aus einem zweiten Tab wird
der Anruf an den Telefon-Tab übergeben.

Lizenz: OPL-1. Autor: WISSEN BERATUNG (Tobias Wissen), https://www.wissen-beratung.de
