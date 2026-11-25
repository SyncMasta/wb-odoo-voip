===========
WB VoIP CRM
===========

Brücke zwischen WB VoIP und dem Odoo-CRM: Anrufe werden dem offenen Vorgang des Kontakts zugeordnet, am Vorgang
erscheinen Anrufe und Anrufprotokolle, Rückruf-Aufgaben landen am Vorgang, und aus dem Anruf lässt sich ein neuer
Vorgang anlegen.

Stand Phase 5 (Version 19.0.1.0.0, braucht ``wb_voip`` ab 19.0.4.2.0).

Installation
------------

Das Modul hängt an ``wb_voip`` und ``crm`` und installiert sich selbst, sobald beide vorhanden sind
(``auto_install``). Es gibt keine eigene Einrichtung; Rechte und Anlage richten sich nach ``wb_voip``.
``wb_voip`` bleibt auch ohne CRM nutzbar.

Zuordnung zum Vorgang
---------------------

Beginnt oder endet ein Anruf (auch ein abgewiesener), sucht WB VoIP den passenden Vorgang:

* Gesucht wird mit den Rechten des angemeldeten Benutzers (Record Rules des CRM gelten).
* **Offen** heißt: aktiv und Wahrscheinlichkeit unter 100 %. Gewonnene, verlorene und archivierte Vorgänge
  werden nicht zugeordnet.
* Der Vorgang gehört zur aktuellen Firma oder zu keiner Firma und hängt am Kontakt oder an dessen Firma.
* Gibt es mehrere, gilt der zuletzt geänderte unter den Vorgängen, die dem Anrufer zugewiesen sind; sonst
  wird keiner zugeordnet (lieber keine Zuordnung als eine falsche).
* Ein Vorgang, von dem aus der Anruf gestartet wurde (Klick am Telefonfeld des Vorgangs), hat Vorrang. Ein
  Klick am Kontakt darf den Vorgang noch zuordnen.
* Manuell: Im Block „Vorgänge“ (siehe unten) lässt sich ein Vorgang zuordnen oder wieder lösen. Beim Lösen
  fällt die Zuordnung auf den Kontakt zurück.

Anrufe und Protokolle am Vorgang
--------------------------------

Am Vorgang steht für VoIP-Benutzer der Smart-Button „Anrufe“ (Zähler und Liste) und der Reiter
„Anrufprotokolle“. Sichtbar sind die Anrufe und Protokolle, die der Benutzer auch sonst sehen darf:
Benutzer ihre eigenen, „WB VoIP: Administrator“ alle. Der Hinweis im Chatter eines Anrufprotokolls erscheint
am zugeordneten Vorgang.

Rückruf-Aufgabe
---------------

Bei einem verpassten Anruf legt WB VoIP die Aktivität „Rückruf: verpasster Anruf“ am Vorgang an, wenn

* der Vorgang zum Zeitpunkt der Anlage noch offen ist und
* der Besitzer des Anrufs ihn lesen darf.

Sonst entsteht die Aktivität am Kontakt (wie ohne CRM). Wird der Vorgang später gewonnen, verloren oder
archiviert, bleibt die Aktivität bestehen und lässt sich wie gewohnt erledigen (✓ in der Liste „Verpasst“ oder
im Chatter). Ein neuer verpasster Anruf danach landet am Kontakt.

Button „Vorgang anlegen“
------------------------

Unter Einstellungen › Technisch › WB VoIP › Anruf-Buttons gibt es die zusätzliche Art „Vorgang anlegen“.
Ein Klick legt eine Verkaufschance an und ordnet den Anruf ihr zu; danach öffnet sich der Vorgang.

* **Titel-Vorlage** mit den Platzhaltern ``{kontakt}``, ``{nummer}`` und ``{datum}`` (reiner Text, höchstens
  200 Zeichen, Standard „Anruf von {kontakt}“). Andere Platzhalter bleiben unverändert stehen; es wird nichts
  ausgewertet.
* **Verkaufsteam** (optional). Das Team muss zur eigenen Firma gehören oder firmenlos sein und für den
  Benutzer lesbar sein.
* Der Vorgang wird mit den Rechten des Benutzers angelegt, ohne CRM-Recht gibt es eine Fehlermeldung. Der
  Benutzer ist Verantwortlicher; ohne Kontakt wird die Nummer als Telefon eingetragen.
* Die Button-Einstellungen (Richtung, Anrufzustand, Gruppen, Bedingung) gelten wie bei allen Buttons. Ein
  inaktiver Beispiel-Button wird mitgeliefert.

Block „Vorgänge“
----------------

Im Layout-Designer (nur Anrufer-Fenster) steht der Block „Vorgänge“. Er zeigt die offenen Vorgänge des
Kontakts und seiner Firma (zuletzt geändert zuerst, Anzahl 1 bis 20, Standard 5) mit Stufe und erwartetem
Umsatz. „Zuordnen“ ordnet den Vorgang dem laufenden Anruf zu, „Öffnen“ öffnet das Formular. Der erwartete
Umsatz erscheint nur, wenn der Benutzer das Feld lesen darf. Im Protokoll-Popup gibt es den Block nicht.

Datenschutz und Rechte
----------------------

* Vorgangsnamen erscheinen nur für Benutzer mit Leserecht am Vorgang; ohne Recht bleiben Name und Verknüpfung
  leer.
* Das technische Feld Anruf → Vorgang (``lead_id``) ist nur für Systemadministratoren lesbar. Im Formular
  zeigt ein eigenes, rechtegeprüftes Feld den Namen; Zähler, Liste und Smart-Button laufen über Modell und
  ID des Datensatzes.
* Zuordnung und Vorgang-Anlage laufen als angemeldeter Benutzer, ohne ``sudo()``. Das Feld wird nur vom
  Server beschrieben.

Grenzen
-------

* Vorgänge an Mitarbeitern eines Firmenkontakts werden nicht gefunden, wenn die Firma selbst anruft: Es
  wird am Kontakt und an dessen Firma gesucht, nicht an den untergeordneten Kontakten.
* Chatter-Einträge ziehen nicht um, wenn der Anruf später einem anderen Vorgang zugeordnet wird; sie bleiben
  am ursprünglichen Datensatz.
* Beim Deinstallieren des Moduls entfallen die Buttons der Art „Vorgang anlegen“; die übrigen Buttons
  bleiben.

Lizenz: OPL-1. Autor: WISSEN BERATUNG (Tobias Wissen), https://www.wissen-beratung.de
