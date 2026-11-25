// Dreier-Konferenz im Browser: Der Ton beider Leitungen wird per WebAudio gemischt.
//
//   an Leitung A geht: Mikrofon + Ton von B
//   an Leitung B geht: Mikrofon + Ton von A
//
// Lokal spielt weiterhin je Leitung das eigene Audio-Element den Ton der Gegenstelle ab
// (A + B). Die Mischung gibt deshalb nichts an ``ctx.destination`` aus, sonst wäre jede
// Gegenstelle doppelt zu hören. Stumm schaltet nur die Mikrofon-Verstärkung auf 0, so hören
// sich A und B weiterhin gegenseitig.
//
// Reine Funktionen ohne Zugriff auf Dienst oder SIP: Aufbau und Abbau bekommen alles
// übergeben und lassen sich mit einem Fake-AudioContext testen.

import { _t } from "@web/core/l10n/translation";

/** Kontext schließen, ohne Fehler weiterzugeben. */
async function kontextSchliessen(ctx) {
    try {
        await ctx?.close?.();
    } catch {
        // schon geschlossen
    }
}

/** Audiospur eines MediaStreamDestination-Knotens. */
function zielSpur(ziel) {
    const spuren = ziel.stream?.getAudioTracks?.() || [];
    if (!spuren.length) {
        throw new Error(_t("Konferenz: keine Audiospur für die Mischung."));
    }
    return spuren[0];
}

/**
 * Baut die Mischung auf und ersetzt die gesendete Spur beider Leitungen.
 *
 * @param {object} p
 * @param {AudioContext} p.ctx
 * @param {MediaStream} p.mikrofon  eigenes Mikrofon
 * @param {{empfang: MediaStream, sender: RTCRtpSender, zurueck?: () => void}} p.a  Leitung A;
 *        ``zurueck`` schaltet beim Abbau die Mikrofon-Spur der Leitung nach ihrem Zustand
 *        (Stumm, Halten) wieder ein, z. B. über ``sitzung.mute()``
 * @param {{empfang: MediaStream, sender: RTCRtpSender, zurueck?: () => void}} p.b  Leitung B
 * @param {boolean|(() => boolean)} [p.stumm]  Stumm-Zustand (Wert oder Abfrage); die
 *        Abfrage wird erst am Ende des Aufbaus gelesen, ein Umschalten währenddessen zählt
 * @returns {Promise<object>} handle for ``konferenzAbbauen`` with ``stumm(an)`` and
 *          ``istStumm()``
 */
export async function konferenzAufbauen({ ctx, mikrofon, a, b, stumm = false }) {
    if (!ctx || !mikrofon || !a?.empfang || !a?.sender || !b?.empfang || !b?.sender) {
        await kontextSchliessen(ctx);
        throw new Error(_t("Konferenz: Ton einer Leitung fehlt."));
    }
    const istStumm = typeof stumm === "function" ? stumm : () => Boolean(stumm);
    const handle = {
        ctx,
        knoten: [],
        ziele: [],
        seiten: [],
        mikVerstaerkung: null,
        _stumm: Boolean(istStumm()),
        /**
         * Mikrofon in beiden Richtungen stumm; die gemischten Spuren selbst bleiben an. Sie liegen
         * nur während der laufenden Konferenz an den Sendern, in der keine Leitung gehalten ist.
         */
        stumm(an) {
            handle._stumm = Boolean(an);
            if (handle.mikVerstaerkung) {
                handle.mikVerstaerkung.gain.value = an ? 0 : 1;
            }
            for (const ziel of handle.ziele) {
                for (const spur of ziel.stream?.getAudioTracks?.() || []) {
                    spur.enabled = true;
                }
            }
        },
        istStumm() {
            return handle._stumm;
        },
    };
    try {
        const mik = ctx.createMediaStreamSource(mikrofon);
        const verstaerkung = ctx.createGain();
        verstaerkung.gain.value = handle._stumm ? 0 : 1;
        mik.connect(verstaerkung);
        handle.mikVerstaerkung = verstaerkung;
        const vonA = ctx.createMediaStreamSource(a.empfang);
        const vonB = ctx.createMediaStreamSource(b.empfang);
        const zuA = ctx.createMediaStreamDestination();
        const zuB = ctx.createMediaStreamDestination();
        handle.knoten.push(mik, verstaerkung, vonA, vonB, zuA, zuB);
        handle.ziele.push(zuA, zuB);
        verstaerkung.connect(zuA);
        verstaerkung.connect(zuB);
        vonB.connect(zuA);
        vonA.connect(zuB);
        // Ein neu angelegter Kontext kann angehalten sein (Autoplay-Regeln); angehalten
        // verarbeitet er nichts, beide Seiten hörten dann nur Stille
        await Promise.resolve(ctx.resume?.()).catch(() => {});
        if (ctx.state !== "running") {
            throw new Error(_t("Ton für die Konferenz nicht verfügbar – bitte erneut klicken."));
        }
        for (const [seite, ziel] of [
            [a, zuA],
            [b, zuB],
        ]) {
            const original = seite.sender.track;
            handle.seiten.push({
                sender: seite.sender,
                original,
                ersetzt: false,
                zurueck: typeof seite.zurueck === "function" ? seite.zurueck : null,
            });
            await seite.sender.replaceTrack(zielSpur(ziel));
            handle.seiten.at(-1).ersetzt = true;
        }
        // Stumm regelt ab jetzt die Verstärkung (aktueller Stand, auch wenn während des Aufbaus
        // umgeschaltet); erst danach liefert eine vorher abgeschaltete Mikrofon-Spur wieder Ton.
        // Die Originale liegen jetzt an keinem Sender (ersetzt), sie speisen nur die Mischung.
        handle.stumm(istStumm());
        for (const { original } of handle.seiten) {
            if (original) {
                original.enabled = true;
            }
        }
    } catch (fehler) {
        // Stumm-Zustand aus der Abfrage: ein stummes Mikrofon bleibt aus
        await konferenzAbbauen(handle, { stumm: Boolean(istStumm()) });
        throw fehler;
    }
    return handle;
}

/**
 * Löst die Mischung auf: jede Leitung sendet wieder ihre eigene Mikrofon-Spur
 * (``replaceTrack``), alle Knoten werden getrennt, der Kontext geschlossen. Fehler einer
 * schon beendeten Leitung werden ignoriert.
 *
 * Die Mikrofon-Spur geht ausgeschaltet zurück an den Sender; eingeschaltet wird sie erst danach
 * über ``zurueck`` der Leitung (Mikrofon-Klasse: nie bei gehaltener Leitung). So bekommt eine
 * Leitung, die während des Abbaus gehalten wird, nie Mikrofon.
 *
 * @param {object} handle Ergebnis von ``konferenzAufbauen``
 * @param {{stumm?: boolean}} [optionen] stumm: nur für Leitungen ohne ``zurueck`` – Spur
 *        bleibt danach stumm (Standard: aktueller Zustand des Handles)
 */
export async function konferenzAbbauen(handle, { stumm } = {}) {
    if (!handle || handle.abgebaut) {
        return;
    }
    if (stumm === undefined) {
        stumm = Boolean(handle.istStumm?.());
    }
    handle.abgebaut = true;
    await Promise.all(
        handle.seiten.map(async ({ sender, original, ersetzt, zurueck }) => {
            if (original) {
                original.enabled = false;
            }
            if (ersetzt) {
                try {
                    await sender.replaceTrack(original || null);
                } catch {
                    // Leitung ist schon beendet
                }
            }
            if (zurueck) {
                try {
                    zurueck();
                } catch {
                    // Leitung ohne Ton
                }
            } else if (original) {
                original.enabled = !stumm;
            }
        })
    );
    for (const knoten of handle.knoten) {
        try {
            knoten.disconnect();
        } catch {
            // schon getrennt
        }
    }
    for (const ziel of handle.ziele) {
        for (const spur of ziel.stream?.getAudioTracks?.() || []) {
            spur.stop?.();
        }
    }
    await kontextSchliessen(handle.ctx);
}
