WB VoIP Ansitel
===============

Anbindung einer Ansitel-Telefonanlage an WB VoIP über deren REST-Schnittstelle.

* Felder an der Anlage: Schnittstelle aktiv, REST-URL (nur https), zentraler Token, Zeitzone der Anrufliste.
* Der Token wird nur als Fernet-Chiffrat gespeichert (Schlüssel ``WB_VOIP_FERNET_KEY``), ist nur für
  Systemadministratoren sichtbar und wird nie gelesen, geloggt oder im Cache abgelegt.
* ``wb.voip.ansitel._aufruf`` ist die einzige Netzstelle: https, Zeitlimit 10 s, keine Weiterleitungen,
  Fehler als ``AnsitelFehler`` ohne Token und URL.
* Knopf „Verbindung testen“ (VoIP-Administratoren) liest die Geräteliste.
