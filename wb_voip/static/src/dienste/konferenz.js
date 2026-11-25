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

/** Audiospur eines MediaStreamDestination-Knotens. */
function zielSpur(ziel) {
    const spuren = ziel.stream?.getAudioTracks?.() || [];
    if (!spuren.length) {
        throw new Error("Konferenz: keine Audiospur für die Mischung.");
    }
    return spuren[0];
}

/**
 * Baut die Mischung auf und ersetzt die gesendete Spur beider Leitungen.
 *
 * @param {object} p
 * @param {AudioContext} p.ctx
 * @param {MediaStream} p.mikrofon  eigenes Mikrofon
 * @param {{empfang: MediaStream, sender: RTCRtpSender}} p.a  Leitung A
 * @param {{empfang: MediaStream, sender: RTCRtpSender}} p.b  Leitung B
 * @param {boolean} [p.stumm]  Mikrofon von Anfang an stumm
 * @returns {Promise<object>} handle for ``konferenzAbbauen`` with ``stumm(an)``
 */
export async function konferenzAufbauen({ ctx, mikrofon, a, b, stumm = false }) {
    if (!ctx || !mikrofon || !a?.empfang || !a?.sender || !b?.empfang || !b?.sender) {
        throw new Error("Konferenz: Ton einer Leitung fehlt.");
    }
    const handle = {
        ctx,
        knoten: [],
        ziele: [],
        seiten: [],
        mikVerstaerkung: null,
        stumm(an) {
            if (handle.mikVerstaerkung) {
                handle.mikVerstaerkung.gain.value = an ? 0 : 1;
            }
        },
    };
    try {
        const mik = ctx.createMediaStreamSource(mikrofon);
        const verstaerkung = ctx.createGain();
        verstaerkung.gain.value = stumm ? 0 : 1;
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
        // Ein neu angelegter Kontext kann angehalten sein (Autoplay-Regeln)
        await Promise.resolve(ctx.resume?.()).catch(() => {});
        for (const [seite, ziel] of [
            [a, zuA],
            [b, zuB],
        ]) {
            const original = seite.sender.track;
            handle.seiten.push({ sender: seite.sender, original, ersetzt: false });
            await seite.sender.replaceTrack(zielSpur(ziel));
            handle.seiten.at(-1).ersetzt = true;
        }
        // Stumm regelt ab jetzt die Verstärkung; eine vorher abgeschaltete Mikrofon-Spur
        // (Stumm vor der Konferenz) muss für die Mischung wieder Ton liefern
        for (const { original } of handle.seiten) {
            if (original) {
                original.enabled = true;
            }
        }
    } catch (fehler) {
        await konferenzAbbauen(handle);
        throw fehler;
    }
    return handle;
}

/**
 * Löst die Mischung auf: jede Leitung sendet wieder ihre eigene Mikrofon-Spur
 * (``replaceTrack``), alle Knoten werden getrennt, der Kontext geschlossen. Fehler einer
 * schon beendeten Leitung werden ignoriert.
 *
 * @param {object} handle Ergebnis von ``konferenzAufbauen``
 * @param {{stumm?: boolean}} [optionen] stumm: Mikrofon-Spur bleibt danach stumm
 */
export async function konferenzAbbauen(handle, { stumm = false } = {}) {
    if (!handle || handle.abgebaut) {
        return;
    }
    handle.abgebaut = true;
    await Promise.all(
        handle.seiten.map(async ({ sender, original, ersetzt }) => {
            if (original) {
                original.enabled = !stumm;
            }
            if (!ersetzt) {
                return;
            }
            try {
                await sender.replaceTrack(original || null);
            } catch {
                // Leitung ist schon beendet
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
    try {
        await handle.ctx.close?.();
    } catch {
        // schon geschlossen
    }
}
