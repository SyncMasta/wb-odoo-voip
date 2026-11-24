// Töne des Softphones, mit WebAudio erzeugt (keine Audiodateien).
//   Freizeichen (ausgehend, erst nach 180 ohne SDP): 425 Hz, 1 s an, 4 s aus
//   Klingeln (eingehend): 2 × 0,4 s Zweiklang, alle 3 s

const FREIZEICHEN = { frequenzen: [425], muster: [[0, 1]], periode: 5 };
const KLINGELN = { frequenzen: [440, 480], muster: [[0, 0.4], [0.6, 1.0]], periode: 3 };
const LAUTSTAERKE = 0.15;
const VORLAUF_S = 15;

export class Klaenge {
    /**
     * @param {object} [optionen]
     * @param {() => AudioContext} [optionen.kontextFabrik]
     */
    constructor({ kontextFabrik } = {}) {
        this._kontextFabrik =
            kontextFabrik ||
            (() => {
                const Kontext = window.AudioContext || window.webkitAudioContext;
                return Kontext ? new Kontext() : null;
            });
        this._kontext = null;
        this._laufend = { freizeichen: null, klingeln: null };
    }

    freizeichen(an) {
        this._schalten("freizeichen", FREIZEICHEN, an);
    }

    klingeln(an) {
        this._schalten("klingeln", KLINGELN, an);
    }

    aus() {
        this.freizeichen(false);
        this.klingeln(false);
    }

    _schalten(name, ton, an) {
        if (!an) {
            this._stoppen(name);
            return;
        }
        if (this._laufend[name]) {
            return;
        }
        try {
            this._laufend[name] = this._starten(ton);
        } catch {
            // Kein Ton ist besser als ein abgebrochener Anruf
            this._laufend[name] = null;
        }
    }

    _starten(ton) {
        if (!this._kontext) {
            this._kontext = this._kontextFabrik();
        }
        const kontext = this._kontext;
        if (!kontext) {
            return null;
        }
        if (kontext.state === "suspended") {
            kontext.resume().catch(() => {});
        }
        const pegel = kontext.createGain();
        pegel.gain.value = 0;
        pegel.connect(kontext.destination);
        const oszillatoren = ton.frequenzen.map((frequenz) => {
            const osz = kontext.createOscillator();
            osz.type = "sine";
            osz.frequency.value = frequenz;
            osz.connect(pegel);
            osz.start();
            return osz;
        });
        const lauf = { pegel, oszillatoren, timer: null, bis: kontext.currentTime };
        // Hüllkurve blockweise im Voraus planen
        const planen = () => {
            const jetzt = kontext.currentTime;
            let start = Math.max(lauf.bis, jetzt);
            while (start < jetzt + VORLAUF_S) {
                for (const [von, bis] of ton.muster) {
                    pegel.gain.setValueAtTime(LAUTSTAERKE, start + von);
                    pegel.gain.setValueAtTime(0, start + bis);
                }
                start += ton.periode;
            }
            lauf.bis = start;
        };
        planen();
        lauf.timer = setInterval(planen, (VORLAUF_S / 2) * 1000);
        return lauf;
    }

    _stoppen(name) {
        const lauf = this._laufend[name];
        this._laufend[name] = null;
        if (!lauf) {
            return;
        }
        clearInterval(lauf.timer);
        try {
            lauf.pegel.gain.cancelScheduledValues(0);
            lauf.pegel.gain.value = 0;
            for (const osz of lauf.oszillatoren) {
                osz.stop();
                osz.disconnect();
            }
            lauf.pegel.disconnect();
        } catch {
            // bereits gestoppt
        }
    }
}
