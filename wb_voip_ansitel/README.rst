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

Feste Ziele der Tasten setzt nur die Systemverwaltung; VoIP-Administratoren pflegen Beschriftung, Art und
Reihenfolge.
