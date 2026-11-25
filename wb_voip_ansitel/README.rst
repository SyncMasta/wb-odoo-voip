WB VoIP Ansitel
===============

Anbindung einer Ansitel-Telefonanlage an WB VoIP über deren REST-Schnittstelle.

* Felder an der Anlage: Schnittstelle aktiv, REST-URL (nur https), zentraler Token, Zeitzone der Anrufliste.
* Der Token wird nur als Fernet-Chiffrat gespeichert (Schlüssel ``WB_VOIP_FERNET_KEY``), ist nur für
  Systemadministratoren sichtbar und wird nie gelesen, geloggt oder im Cache abgelegt.
* ``wb.voip.ansitel._aufruf`` ist die einzige Netzstelle: https, Zeitlimit 10 s, keine Weiterleitungen,
  Fehler als ``AnsitelFehler`` ohne Token und URL.
* Knopf „Verbindung testen“ (VoIP-Administratoren) liest die Geräteliste.
* URL, Schalter und Token der Ansitel-Schnittstelle sind nur für Systemadministratoren les- und schreibbar;
  ändert jemand ohne Systemrecht die URL (auch über sudo), wird der gespeicherte Token gelöscht.
* HTTP über eine eigene Sitzung ohne Umgebungs-Proxys, mit Zertifikatsprüfung.

Einrichtung
-----------

1. Modul ``wb_voip_ansitel`` installieren (setzt ``wb_voip`` voraus, installiert sich nicht von selbst).
2. Systemverwaltung: an der Telefonanlage (Einstellungen › Technisch › WB VoIP › Telefonanlagen) „Ansitel-Schnittstelle
   aktiv“ setzen, die REST-URL (nur ``https``) und den zentralen Token eintragen, die Zeitzone der Anrufliste prüfen.
   Danach „Verbindung testen“.
3. Systemverwaltung: am Benutzer (Reiter Telefonie) die Ansitel-Nebenstelle setzen. Wer den Anrufbeantworter über
   einen eigenen Zugang schalten soll, bekommt zusätzlich einen persönlichen Token.
4. Systemverwaltung: an der Anlage „Geräte aus Anlage laden“, Zuordnung der Geräte zu Benutzern prüfen
   (Amtsleitung nie als Gerät führen).
5. Tasten unter Einstellungen › Technisch › WB VoIP › Softphone-Tasten prüfen (feste Ziele setzt nur die
   Systemverwaltung). Jeder Benutzer trägt unter „Mein Profil“ › Telefonie „Mein Handy“ ein.
6. Anzeige der Tastenleiste je Benutzer (Einstellung „Schnelltasten“: Softphone, Rand oder beide); die Änderung
   wirkt nach dem Neuladen der Seite. Die Rand-Leiste ist 2,75 rem breit; die Seite rückt dafür nach links.

Rechte
------

.. list-table::
   :header-rows: 1

   * - Aktion
     - VoIP-Administrator
     - Systemverwaltung
   * - Anlage anlegen, löschen
     - nein
     - ja
   * - API-URL, Schalter, zentraler Token
     - nein
     - ja
   * - Nebenstelle, persönlicher Token, AB-Freigabe
     - nein (lesen: Nebenstelle)
     - ja
   * - Geräte ändern, „Geräte aus Anlage laden“
     - nein (lesen ja)
     - ja
   * - Feste Tastenziele setzen
     - nein
     - ja
   * - Tasten (Beschriftung, Art, Reihenfolge) pflegen
     - ja
     - ja
   * - Verbindung testen
     - ja
     - ja
   * - Eigene Tasten schalten, „Mein Handy“ setzen
     - jeder Benutzer mit Lizenz bzw. für sich
     - ebenso

Hinweis: Wer die URL oder die Anlage eines Benutzers ohne Systemrecht ändert, löscht damit Token und Nebenstelle; die
Kette ist so ausgelegt, dass kein Token an einen fremden Host geschickt wird.

Datenschutz
-----------

* Der Abgleich importiert die Anrufliste der Anlage in Odoo: Zeitpunkt, Dauer, Gerät und Gegennummer. Nummern, die
  keinem Kontakt gehören, werden ebenfalls gespeichert (ohne Zuordnung).
* Es gilt die Löschfrist von WB VoIP (Einstellungen › WB VoIP, Standard 12 Monate); importierte Anrufe werden wie
  Softphone-Anrufe gelöscht, Protokolle bleiben erhalten.
* Die „Bemerkung“ zu einem anderswo angenommenen Anruf sieht der Besitzer; der Kollege, an den der Anruf ging, sieht
  Name, Gerät und Dauer, die Bemerkung nur als Notiz seines eigenen Anrufs.
* Der Abgleich zeigt jedem Benutzer nur eigene Anrufe (und beim Kollegen-Hinweis die genannten Angaben); der
  Anlagenbetreiber sollte Beschäftigte über die Auswertung der Anrufliste informieren (§ 26 BDSG, ggf. Betriebsrat).
* Zugangsdaten stehen nie in Logs, Bus oder Browser; Cache-Schlüssel enthalten nur einen Hash.

Schnelltasten
-------------

* Am Benutzer (Reiter Telefonie): Ansitel-Nebenstelle (VoIP-Administratoren lesen sie, setzen darf sie nur die
  Systemverwaltung; je Anlage eindeutig; Änderungen stehen mit Benutzerkennungen im Log; wechselt jemand ohne
  Systemrecht die Anlage des Benutzers, werden Nebenstelle und persönlicher Token gelöscht),
  persönlicher Token und „AB über zentralen Token schalten“ (nur Systemverwaltung, Token nur als Chiffrat).
  Der Benutzer selbst sieht davon nichts. Wechselt jemand ohne Systemrecht die Anlage, wird der persönliche
  Token gelöscht; ebenso alle persönlichen Token einer Anlage, deren URL ohne Systemrecht geändert wird.
* „Mein Handy“ (Mein Profil › Telefonie) liest und schreibt nur der Benutzer selbst (und die Systemverwaltung).
  Gewählt wird nur mit Ziffern, „+“ wird zu „00“.
* Tasten unter Einstellungen › Technisch › WB VoIP › Softphone-Tasten: Arten Weiterleitung (feste Nummer oder
  eigenes Handy), Anrufschutz und Anrufbeantworter (sofort oder nach Rufzeit). Standardtasten „AB“, „→ Handy“,
  „Nicht stören“ (noupdate).
* ``res.users.wb_voip_tasten_zustand()`` liest frisch; ohne gültige Lizenz wird die Anlage nicht gefragt und
  alle Tasten bleiben grau. ``wb_voip_taste_schalten(taste_id, an=None)`` und ``wb_voip_weiterleitung_aus()``
  verlangen die Lizenz. ``an`` True/False stellt genau diesen Zustand her (zweiter Tab schaltet nicht zurück),
  None schaltet um. AB, Weiterleitung und Anrufschutz schließen sich aus.
* AB-Token: persönlicher Token, sonst zentraler Token mit Freigabe (Nebenstelle muss zum zentralen Benutzer
  gehören), sonst zentraler Token, wenn dieser genau eine Nebenstelle besitzt und es die des Benutzers ist;
  sonst ist die AB-Taste grau mit Hinweis.
* Fehler der Anlage machen die betroffenen Tasten grau (``aktiv`` None) mit Hinweis; ist die Anlage nicht
  erreichbar, entfallen weitere Aufrufe. Jeder Zustand geht per Bus ``wb_voip/tasten`` an die Tabs des
  Benutzers, ohne Token, URL oder Nebenstelle.

Weiterleitungsziele und Gebührenbetrug
--------------------------------------

Eine Weiterleitung lässt die Anlage auf Kosten des Anschlusses nach außen wählen. Deshalb gilt für „Mein Handy“
und für feste Tastenziele dieselbe Prüfung (``wb.voip.taste._ziel_pruefen``), beim Speichern und erneut bei jedem
Schalten (ein früher gespeichertes Ziel wird nicht gewählt, die Taste bleibt grau mit Hinweis):

* nur die Ziffern 0–9 (keine anderen Unicode-Ziffern);
* alle Regeln gelten für die Normalform (Ländervorwahl + nationale Rufnummer), ``+49 (0) 900…`` zählt also wie
  ``0900…``; gespeichert und gewählt wird nur die Normalform (``+49…`` bzw. ``0049…`` an die Anlage);
* mindestens 6 Ziffern, gültige Rufnummer;
* keine Notruf-, Kurzwahl- und Auskunftsnummern (110, 112, 115, 116xxx, 118xx);
* keine Sonder- und Mehrwertnummern: Deutschland 012x, 0137, 0138, 0180–0189, 0190–0194, 031, 0700, 0900;
  Österreich 0900, 0901, 0930, 0939; Schweiz 0900, 0901, 0906 (je nach Ländervorwahl der Nummer);
* Satelliten- und globale Dienste (+881, +882, +883) immer gesperrt;
* Ziele mit anderer Ländervorwahl als das Land der Anlage nur, wenn der Systemparameter
  ``wb_voip_ansitel.ausland_erlaubt`` den Wert ``1`` hat (Standard: gesperrt).

Hinweis: Mit ``wb_voip_ansitel.ausland_erlaubt`` = ``1`` sind Ziele im Ausland erlaubt; die Sperre der
Mehrwert- und Notrufnummern gilt weiter, fremde Mehrwertnummern sind damit aber nicht ausgeschlossen. Eine interne
Nebenstelle der Anlage als Ziel ist nicht vorgesehen.

Feste Ziele der Tasten setzt nur die Systemverwaltung; VoIP-Administratoren pflegen Beschriftung, Art und
Reihenfolge.

Abgleich mit der Anrufliste
---------------------------

Der Cron „WB VoIP Ansitel: Abgleich mit der Anrufliste“ (alle 5 Minuten) liest je aktiver Anlage mit Token die
Anrufliste (``getcdr``, zentraler Token, je Abruf höchstens 1000 Zeilen, geblättert über ``linepos``) und gleicht sie
mit den Anrufen in Odoo ab. So erscheinen auch Anrufe, die Odoo nicht gesehen hat (Odoo geschlossen), und Anrufe, die
an einem Tischtelefon, einem anderen Gerät oder über eine Weiterleitung angenommen wurden.

* Fenster: von = Stand − 4 Stunden (nie vor den Beginn des ersten Laufs), bis = jetzt − 60 Sekunden, höchstens
  6 Stunden je Lauf. Der erste Lauf liest die letzten 30 Minuten und merkt sich diesen Beginn erst nach erfolgreichem
  Lesen. Liefert die Anlage mehr als 50 Seiten, entfällt zuerst die Überlappung, danach wird das Fenster ab dem Stand
  halbiert (bis 10 Minuten); reicht auch das nicht, wird dieses Stück mit einem Fehler im Log übersprungen (diese
  Anrufe fehlen dann). Der Stand geht nie zurück. Stand und Beginn sind nur für die Systemverwaltung sichtbar.
* Die Zeiten der Anrufliste gelten in der Zeitzone der Anlage (Feld „Zeitzone der Anrufliste“, Standard
  Europe/Berlin). In der doppelten Stunde im Herbst werden beim Zuordnen zu Softphone-Anrufen beide Zeiten probiert.
* Geräte sind nur ``PJSIP/…``- und ``SIP/…``-Kanäle; andere Techniken (z. B. IAX2, DAHDI) nennen kein Gerät.
* Verarbeitet wird je ``linkedid`` eine Gruppe mit mindestens einem eingehenden Zweig, und erst, wenn ihr letzter
  Zweig vor „bis“ endete. Doppelt gelesene Gruppen ändern nichts (je Benutzer und ``linkedid`` ein Anruf).
* Angenommen ist ein Zweig mit ``ANSWERED`` und ``Dial`` auf einem Gerät, das nicht die Amtsleitung ist;
  Weiterleitung: ein ``Local/…``-Zweig (oder ein eingehender Zweig auf der Amtsleitung) und ein ausgehender Zweig auf
  der Amtsleitung mit ``ANSWERED`` und Gesprächsdauer > 0. Anrufbeantworter, Warteschlange und ``Local`` ohne
  angenommenen Amtszweig gelten als nicht angenommen. Amtsleitung ist das Gerät eines ausgehenden Zweigs, das weder
  in der Geräte-Tabelle noch als SIP-Benutzer der Anlage vorkommt; die Amtsleitung also nie als Gerät eintragen.
* Beteiligt sind Benutzer der Anlage, deren Nebenstelle angerufen wurde, deren SIP-Benutzer ein Gerät der Gruppe ist
  oder denen ein Gerät der Gruppe gehört. Fehlt ihr Anruf, wird er angelegt (Herkunft „Anrufliste der Anlage“).
* Nirgends angenommen: verpasst, mit Protokoll und Rückruf-Aktivität wie im Softphone. Mit dem eigenen
  Softphone-Konto angenommen: ein klingelnder oder verpasster Anruf wird beendet (Annahme = Beginn + Klingelzeit,
  Dauer laut Anlage, Protokoll „erreicht“), ein angenommener bleibt. Sonst:
  „anderswo angenommen“ mit Gerät und Dauer (beim Kollegen mit Verweis auf dessen Anruf); Rückruf-Aktivität und
  unberührtes Protokoll eines vorher verpassten Anrufs entfallen. Eine Annahme erledigt offene Verpasst-Einträge der
  Nummer bis zum Ende der Gruppe („Angenommen von <Name>“, ohne Besitzer „Angenommen auf <Gerät>“). Ein per Rückruf
  erledigter Eintrag, den der Abgleich später „anderswo“ setzt, öffnet sich bei geändertem Rückruf-Ergebnis wieder
  (ohne neue Rückruf-Aktivität). Laufende Anrufe werden nie geändert.
* In der Verpasst-Liste steht „↪ angenommen auf <Gerät> (Dauer)“ bzw. beim Kollegen „↪ angenommen von <Name> · <Gerät>
  (Dauer)“ (Gerät nur, wenn der Kollege nicht im Softphone angenommen hat). Mit
  „Bemerkung“ legt der Besitzer einmal ein Protokoll „erreicht“ an („Angenommen auf <Gerät>.“ und der Text), danach
  wird nur die Beschreibung nachgeführt; eine von Hand geänderte Beschreibung (erkannt über einen gespeicherten Hash
  der zuletzt erzeugten) bleibt erhalten und bekommt den Text angehängt.
* Der Abgleich läuft auch ohne gültige Lizenz: er liest nur und schreibt Anrufe, er schaltet nichts an der Anlage.
  Fehler einer Anlage oder einer Gruppe halten die übrigen nicht auf; das Log nennt nur Kennungen und Fehlertypen.

Geräte (Einstellungen › Technisch › WB VoIP › Geräte): Bezeichnung, Peer-Name, Nebenstelle, Anlage, Benutzer.
VoIP-Administratoren lesen sie, ändern darf sie nur die Systemverwaltung (sie ordnen Geräte der Anlage Benutzern zu).
Der Knopf „Geräte aus Anlage laden“ an der Anlage (nur Systemverwaltung) legt fehlende Geräte aus ``getpeer`` an,
Besitzer über den SIP-Benutzer, sonst über die Nebenstelle (nur bei genau einem Treffer).

Bekannte Grenzen des Abgleichs:

* Hat der Benutzer das Protokoll „nicht erreicht“ eines verpassten Anrufs bearbeitet und zeigt die Anrufliste später,
  dass er mit seinem Softphone-Konto angenommen hat, bleibt dieses Protokoll erhalten (es wird nicht durch „erreicht“
  ersetzt).
* Bei wiederholtem Überlauf (mehr als 50 Seiten je Abruf) werden Fenster halbiert und, wenn auch das nicht reicht,
  übersprungen; die Ruhe-Regel (eine Gruppe wird erst verarbeitet, wenn ihr Ende vor „bis“ liegt) verschiebt sehr
  lange Gespräche in den nächsten Lauf. Ein Hinweis an Administratoren über ein übersprungenes Fenster fehlt; das
  Log nennt es.
* Die doppelte Stunde im Herbst wird nur beim Zuordnen zu Softphone-Anrufen berücksichtigt; Beginn, Ende und Ruhe einer
  Gruppe rechnen mit dem ersten Vorkommen.
* Die Amtsleitung nie in die Geräte-Tabelle eintragen; eine Handy-Mailbox hinter einer Weiterleitung zählt als
  Weiterleitung.
