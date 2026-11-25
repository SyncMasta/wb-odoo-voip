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

* Am Benutzer (Reiter Telefonie): Ansitel-Nebenstelle (VoIP-Administratoren, je Anlage eindeutig),
  persönlicher Token und „AB über zentralen Token schalten“ (nur Systemverwaltung, Token nur als Chiffrat).
  Der Benutzer selbst sieht davon nichts. Wechselt jemand ohne Systemrecht die Anlage, wird der persönliche
  Token gelöscht; ebenso alle persönlichen Token einer Anlage, deren URL ohne Systemrecht geändert wird.
* „Mein Handy“ (Mein Profil › Telefonie) liest und schreibt nur der Benutzer selbst (und die Systemverwaltung);
  gültig sind Rufnummern mit mindestens drei Ziffern. Gewählt wird nur mit Ziffern, „+“ wird zu „00“.
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
