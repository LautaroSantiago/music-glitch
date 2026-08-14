"""
"Convertir a GBA": lo que pidió el usuario es que suene a música de
videojuego de verdad (varios instrumentos sintetizados sonando juntos,
no una melodía sola), no una versión con menos calidad del audio
original. Así que esto no es un filtro sobre la señal: es un mini
"audio a MIDI a chip", con tres canales por separado (melodía, bajo y
percusión) que se mezclan al final dejándole margen a cada uno — así
un golpe de batería no queda tapado por el pulso de la melodía.

Pipeline (los dos modos, "fiel" y "estilo videojuego", comparten esta base):
1. GStreamer decodifica el archivo original (cualquier formato) a un
   WAV estéreo de espectro completo — el registro de melodía y el de
   bajo se separan después, en Python, porque cada uno necesita una
   parte distinta de la señal.
2. Para la MELODÍA: pasa-altos (~180 Hz) para sacarse de encima el
   bajo/bombo, extracción del canal central (la voz suele ir centrada
   en la mezcla; los demás instrumentos suelen tener paneo), separación
   armónica/percusiva, y detección de tono con el YIN de librosa (la
   implementación de referencia, madura y probada) — con corrección de
   saltos de octava, anclaje de cada nota al ataque real (onset
   detection) y la dinámica real del original en vez de un volumen fijo.
3. Para el BAJO: pasa-bajos + YIN en rango de bajo eléctrico,
   resintetizado una octava abajo. Para la PERCUSIÓN: onsets sobre la
   parte percusiva, clasificados en graves ("bombo") o agudos
   ("redoblante/hi-hat") según su centroide espectral.
4. Cada canal se sintetiza por separado (ondas de pulso/triangular) y
   se mezclan con normalización de pico, no recorte duro, para que no
   se tapen entre sí — y recién ahí se cuantiza a 8 bits sin signo.

Nada de esto se guarda solo: primero se arma un preview en un archivo
temporal (ver render_gba_preview). Recién si el usuario lo confirma
desde la interfaz, keep_gba_file lo pasa a la carpeta definitiva.
"""
import gc
import math
import os
import tempfile
import threading
import wave

import numpy as np
import librosa
import scipy.signal
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

from app.config import DATA_DIR

Gst.init(None)

GBA_DIR = DATA_DIR / "gba"
WORK_RATE = 22050        # sample rate de análisis. Se probó a 44100 para más precisión, pero
                         # en máquinas con menos RAM/CPU la conversión se colgaba directamente
                         # (pyin sobre un tema entero a esa resolución es pesado de verdad) —
                         # así que se volvió a esto: sigue siendo preciso, y no se cuelga.
OUT_RATE = 11025         # sample rate del WAV final (chip de verdad, no el original — esto es estético)
FRAME_SIZE = 2048        # ventana de análisis (~93ms a 22050 Hz)
HOP_SIZE = 256            # ventanas bien solapadas: mejor resolución temporal para no perderse notas rápidas
MELODY_MIN_FREQ = 140.0   # ~C3 — por encima del rango típico de bajo/kick, así no engancha graves
MELODY_MAX_FREQ = 1100.0  # ~C6
BASS_MIN_FREQ = 41.0      # ~E1
BASS_MAX_FREQ = 220.0     # ~A3
HIGHPASS_CUTOFF = 180.0   # separa "registro de melodía" de "registro de bajo" en la fuente
MIN_NOTE_SECONDS = 0.05   # notas más cortas que esto se funden con el silencio vecino
PULSE_DUTY = 0.28         # ancho de pulso por defecto, como los canales de un chip de sonido viejo
BIT_LEVELS = 48           # cuantización final del volumen, para que no quede un tono perfecto de sintetizador
PYIN_VOICED_THRESHOLD = 0.45  # confianza mínima de pyin para aceptar una ventana como "hay tono acá"

# niveles de cada canal ANTES de mezclar — dejar margen (headroom) es lo que evita que un
# golpe de percusión se recorte/tape contra el pulso de la melodía cuando coinciden
GAIN_MELODY = 0.60
GAIN_BASS = 0.38
GAIN_PERCUSSION = 0.42
MIX_TARGET_PEAK = 0.92    # a esto se normaliza el pico de la mezcla final, no a un recorte duro


def _safe_filename(title: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()
    return keep[:60] or "tema"


def build_output_path(title: str, suffix: str = "gba") -> str:
    GBA_DIR.mkdir(parents=True, exist_ok=True)
    base = _safe_filename(title)
    dest = GBA_DIR / f"{base}-{suffix}.wav"
    n = 2
    while dest.exists():
        dest = GBA_DIR / f"{base}-{suffix}-{n}.wav"
        n += 1
    return str(dest)


# --------------------------------------------------------- paso 1: decode
def _decode_to_pcm(source_path: str, pcm_path: str):
    """Decodifica a estéreo, espectro completo — sin recortar nada todavía. El registro de
    melodía y el de bajo se separan más adelante, en Python, porque a los dos hace falta
    la señal completa (uno la parte de arriba, el otro la de abajo)."""
    pipeline_desc = (
        f'filesrc location="{source_path}" ! decodebin ! audioconvert ! audioresample ! '
        f'audio/x-raw,rate={WORK_RATE},channels=2 ! '
        f'audioconvert ! audio/x-raw,format=S16LE ! '
        f'wavenc ! filesink location="{pcm_path}"'
    )
    pipeline = Gst.parse_launch(pipeline_desc)
    bus = pipeline.get_bus()
    pipeline.set_state(Gst.State.PLAYING)
    msg = bus.timed_pop_filtered(Gst.CLOCK_TIME_NONE, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pipeline.set_state(Gst.State.NULL)
    if msg is None or msg.type == Gst.MessageType.ERROR:
        detail = msg.parse_error()[0] if msg else "sin respuesta del pipeline"
        raise RuntimeError(str(detail))


# --------------------------------------------------------- paso 2: análisis
def _read_stereo_float(pcm_path: str):
    with wave.open(pcm_path, "rb") as w:
        raw = w.readframes(w.getnframes())
    interleaved = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    left = interleaved[0::2]
    right = interleaved[1::2]
    return left, right


def _butter_filter(samples: np.ndarray, rate: int, cutoff: float, btype: str, order: int = 4) -> np.ndarray:
    sos = scipy.signal.butter(order, cutoff, btype=btype, fs=rate, output="sos")
    return scipy.signal.sosfiltfilt(sos, samples).astype(np.float32)


def _frame_rms(signal: np.ndarray, frame_size: int, hop_size: int, n_frames: int) -> np.ndarray:
    """
    RMS por ventana, calculado con convolución en vez de armar una matriz densa
    (n_frames x frame_size) — para un tema de varios minutos esa matriz pesa
    cientos de MB o más, y llegamos a quedarnos sin memoria por eso.
    """
    power = scipy.signal.fftconvolve(signal.astype(np.float64) ** 2, np.ones(frame_size) / frame_size, mode="full")
    power = power[frame_size - 1: frame_size - 1 + n_frames * hop_size: hop_size][:n_frames]
    power = np.clip(power, 0, None)
    return np.sqrt(power + 1e-12).astype(np.float32)


def _extract_center_channel(left: np.ndarray, right: np.ndarray, n_fft=2048, hop_length=512) -> np.ndarray:
    """
    La voz principal casi siempre se mezcla justo al centro (mismo
    nivel en los dos canales); los demás instrumentos suelen tener
    algo de paneo o de ancho estéreo. Esto separa, banda por banda de
    frecuencia, lo que está centrado (mid = (L+R)/2) de lo que está
    paneado (side = (L-R)/2), y arma una máscara que se queda con lo
    centrado y atenúa lo paneado — así la detección de tono no compite
    contra un instrumento fuerte metido a un costado de la mezcla.
    En un archivo mono no cambia nada (side siempre da ~0, la máscara
    queda en 1 en todos lados).
    """
    mid_stft = librosa.stft((left + right) * 0.5, n_fft=n_fft, hop_length=hop_length)
    side_stft = librosa.stft((left - right) * 0.5, n_fft=n_fft, hop_length=hop_length)
    mid_mag, side_mag = np.abs(mid_stft), np.abs(side_stft)
    # ojo con la fórmula: un instrumento paneado 100% a un lado da |mid|==|side| (no side>>mid),
    # así que un cociente simple mid/(mid+side) sólo lo atenúa a la mitad — no alcanza. Esta
    # versión mide cuánto SUPERA mid a side como fracción de mid: da 0 en cuanto se emparejan
    # (bien paneado, se suprime del todo) y 1 sólo cuando mid domina claramente (bien centrado).
    mask = np.clip((mid_mag - side_mag) / (mid_mag + 1e-9), 0.0, 1.0)
    center = librosa.istft(mid_stft * mask, hop_length=hop_length, length=len(left))
    return center.astype(np.float32)


def _quantize_to_note(freq: float) -> int:
    """Devuelve el número de nota MIDI más cercano (entero), no la frecuencia — agrupar por
    entero es mucho más estable que comparar floats cuando la detección tiembla un poco entre frames."""
    midi = 69 + 12 * math.log2(freq / 440.0)
    return round(midi)


def _midi_to_freq(midi_note: int) -> float:
    return 440.0 * (2 ** ((midi_note - 69) / 12))


def _smooth_notes(per_frame_midi, window=7):
    """Filtro de moda en ventana chica: saca los saltos de una o dos frames que rompen una nota
    sostenida en pedazos demasiado cortos como para sobrevivir el filtro de duración mínima."""
    from collections import Counter
    n = len(per_frame_midi)
    half = window // 2
    smoothed = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        window_vals = per_frame_midi[lo:hi]
        counts = Counter(window_vals)
        best, best_count = per_frame_midi[i], -1
        for val, count in counts.items():
            if count > best_count:
                best, best_count = val, count
        smoothed.append(best)
    return smoothed


def _detect_onsets(samples: np.ndarray, rate: int) -> np.ndarray:
    """Momentos exactos donde arranca algo (ataques reales del audio), vía librosa.
    Sirve para no depender solo de cuándo cambia el tono detectado — una nota puede
    empezar a sonar un poco antes de que el pitch "asiente" en su valor final."""
    return librosa.onset.onset_detect(y=samples, sr=rate, hop_length=HOP_SIZE, units="time")


def _snap_to_onsets(notes, onset_times, tolerance=0.08):
    """Corre el inicio de cada nota al ataque real más cercano (si hay uno cerca).
    Si la nota anterior terminaba justo ahí (sin silencio de por medio), la estira/acorta
    para que las dos sigan encajando, sin dejar huecos ni superposiciones nuevas."""
    if len(onset_times) == 0 or not notes:
        return notes
    snapped = []
    for t0, t1, freq in notes:
        idx = np.searchsorted(onset_times, t0)
        candidates = onset_times[max(0, idx - 1): idx + 1]
        if len(candidates):
            nearest = candidates[np.argmin(np.abs(candidates - t0))]
            new_t0 = nearest if abs(nearest - t0) <= tolerance else t0
        else:
            new_t0 = t0
        if snapped and abs(snapped[-1][1] - t0) < 1e-6:
            prev_t0, _, prev_freq = snapped[-1]
            snapped[-1] = (prev_t0, new_t0, prev_freq)
        snapped.append((new_t0, t1, freq))
    return snapped


def _fix_octave_jumps(per_frame_midi):
    """
    Corrige el error clásico de YIN: un frame aislado (o un par) que detecta
    la octava de arriba o de abajo en medio de una nota sostenida. Si un
    frame difiere de sus vecinos por exactamente una octava (±12 semitonos)
    y esos vecinos coinciden entre sí, se lo pisa con el valor de los vecinos.
    """
    n = len(per_frame_midi)
    fixed = list(per_frame_midi)
    for i in range(1, n - 1):
        cur, prev, nxt = fixed[i], fixed[i - 1], fixed[i + 1]
        if cur is None or prev is None or nxt is None:
            continue
        if prev == nxt and cur != prev and abs(cur - prev) in (12, 24):
            fixed[i] = prev
    return fixed


def _group_notes(per_frame_midi, rate: int, min_note_seconds: float = MIN_NOTE_SECONDS):
    """Agrupa ventanas consecutivas con la misma nota MIDI en una lista de
    (tiempo_inicio, tiempo_fin, frecuencia). Lo usan tanto la melodía como el bajo."""
    notes = []
    if not per_frame_midi:
        return notes
    cur_note = per_frame_midi[0]
    cur_start = 0
    for i in range(1, len(per_frame_midi) + 1):
        note = per_frame_midi[i] if i < len(per_frame_midi) else object()  # cierra la última nota
        if note != cur_note:
            t0 = cur_start * HOP_SIZE / rate
            t1 = i * HOP_SIZE / rate
            if cur_note is not None and (t1 - t0) >= min_note_seconds:
                notes.append((t0, t1, _midi_to_freq(cur_note)))
            cur_note = note
            cur_start = i
    return notes


def _extract_notes(left: np.ndarray, right: np.ndarray, rate: int):
    """Devuelve (notas, curva_de_volumen). Cada nota es (tiempo_inicio, tiempo_fin, frecuencia);
    la curva de volumen es (tiempos, niveles 0-1) del original, para que la síntesis seguir esa
    dinámica de forma continua en vez de un volumen fijo por nota."""
    if len(left) < FRAME_SIZE:
        return [], (np.array([0.0]), np.array([0.0]))

    mix = (left + right) * 0.5  # la mezcla completa: se usa para dinámica, silencio y onsets

    # de acá para abajo trabajamos solo con el registro de melodía (por encima de ~180 Hz):
    # separado en Python, no en la decodificación, porque el modo "estilo videojuego" necesita
    # también el registro de bajo que quedaría cortado si filtráramos ya en el decode.
    left_hp = _butter_filter(left, rate, HIGHPASS_CUTOFF, "highpass")
    right_hp = _butter_filter(right, rate, HIGHPASS_CUTOFF, "highpass")

    # separa lo que está centrado en la mezcla (típicamente la voz principal) de lo que
    # está paneado a un costado (el resto de los instrumentos) — sin esto, un instrumento
    # fuerte metido a un lado le puede tapar la voz a la detección de tono.
    centered = _extract_center_channel(left_hp, right_hp)

    # separa la parte armónica (melodía/acordes) de la percusiva (bombo, redoblante, hi-hat)
    # antes de analizar tono — sin esto, un golpe de batería puede meter una lectura de pitch
    # espuria en medio de una nota sostenida y "romperla" en pedazos que no son.
    harmonic = librosa.effects.harmonic(centered, margin=2.0, n_fft=1024)

    # el motor de tono es el de librosa: pyin (versión probabilística de YIN, con
    # seguimiento tipo Viterbi) en vez del YIN simple — da una curva de tono mucho más
    # estable entre frames y una decisión de "hay voz/nota acá" propia, no un truco
    # aparte. Es más lento, pero acá la prioridad es afinación, no velocidad.
    f0, pyin_voiced, pyin_prob = librosa.pyin(
        harmonic, fmin=MELODY_MIN_FREQ, fmax=MELODY_MAX_FREQ, sr=rate,
        frame_length=FRAME_SIZE, hop_length=HOP_SIZE, center=False, fill_na=0.0
    )

    n_frames = len(f0)
    # el volumen para la DINÁMICA (qué tan fuerte suena la nota) se mide sobre la mezcla
    # completa — eso incluye el aporte real de la batería a lo fuerte que suena el tema.
    rms = _frame_rms(mix, FRAME_SIZE, HOP_SIZE, n_frames)
    max_rms = float(np.max(rms)) if len(rms) else 1.0

    # pero si HAY tono o no ("¿esto es una nota real o es batería/ruido sonando fuerte?")
    # se decide sobre la parte armónica separada, no sobre la mezcla — un platillo o un
    # golpe de bombo pueden sonar fuerte en la mezcla sin tener nada de tono real adentro,
    # y eso era justo lo que generaba "notas que no son nada" tapando la melodía real.
    harmonic_rms = _frame_rms(harmonic, FRAME_SIZE, HOP_SIZE, n_frames)
    noise_floor = min(0.02, max(float(np.max(harmonic_rms)), 1e-6) * 0.08)

    # además: cuánto de "tono puro" hay contra cuánto de "ruido esparcido" (chatura espectral)
    # — un platillo puede dejar algo de energía armónica residual después del HPSS, pero
    # sigue siendo espectralmente plano (ruidoso), no concentrado en una frecuencia
    flatness = librosa.feature.spectral_flatness(
        y=harmonic, n_fft=FRAME_SIZE, hop_length=HOP_SIZE, center=False
    )[0][:n_frames]

    voiced_mask = (harmonic_rms >= noise_floor) & (flatness < 0.35) & pyin_voiced & (pyin_prob >= PYIN_VOICED_THRESHOLD)

    # curva de volumen del original (0.35-1.0, nunca muda del todo): se interpola en la síntesis
    loudness_times = np.arange(n_frames) * HOP_SIZE / rate
    loudness_values = 0.35 + 0.65 * np.clip(rms / max(max_rms, 1e-6), 0, 1)

    midi = np.round(69 + 12 * np.log2(np.clip(f0, 1e-6, None) / 440.0)).astype(int)
    per_frame_midi = [int(m) if v else None for m, v in zip(midi, voiced_mask)]
    per_frame_midi = _fix_octave_jumps(per_frame_midi)
    per_frame_midi = _smooth_notes(per_frame_midi)

    notes = _group_notes(per_frame_midi, rate)
    onset_times = _detect_onsets(mix, rate)
    notes = _snap_to_onsets(notes, onset_times)
    return notes, (loudness_times, loudness_values)


# ---------------------------------------------- extras del modo "estilo videojuego"
def _extract_bass_notes(mix: np.ndarray, rate: int):
    """
    El registro de bajo, que en el modo 'fiel a la melodía' se descarta a propósito
    (para que no confunda la detección de la melodía), acá se aprovecha como canal
    aparte: se filtra a pasa-bajos, se le busca tono en el rango de un bajo eléctrico
    y se agrupa en notas con la misma lógica que la melodía.

    Acá se usa el YIN simple, no pyin: el bajo suele ser una línea más lenta y limpia
    (menos ambigüedad de octava que una voz o un lead), y correr pyin dos veces por
    conversión (melodía + bajo) era buena parte de por qué esto se ponía tan pesado.
    """
    low = _butter_filter(mix, rate, BASS_MAX_FREQ + 40, "lowpass")
    f0 = librosa.yin(low, fmin=BASS_MIN_FREQ, fmax=BASS_MAX_FREQ, sr=rate,
                      frame_length=FRAME_SIZE, hop_length=HOP_SIZE, center=False)
    n_frames = len(f0)
    rms = _frame_rms(low, FRAME_SIZE, HOP_SIZE, n_frames)
    noise_floor = min(0.015, max(float(np.max(rms)), 1e-6) * 0.10)
    voiced_mask = rms >= noise_floor

    midi = np.round(69 + 12 * np.log2(np.clip(f0, 1e-6, None) / 440.0)).astype(int)
    per_frame_midi = [int(m) if v else None for m, v in zip(midi, voiced_mask)]
    per_frame_midi = _fix_octave_jumps(per_frame_midi)
    per_frame_midi = _smooth_notes(per_frame_midi, window=9)  # el bajo se mueve más lento: ventana más ancha

    # una octava abajo de lo detectado — el bajo de un chip de consola vieja suele ir
    # bien al fondo, más grave que la nota "real" tocada en la grabación
    notes = _group_notes(per_frame_midi, rate, min_note_seconds=0.10)
    return [(t0, t1, freq / 2) for t0, t1, freq in notes]


def _extract_percussion_hits(mix: np.ndarray, rate: int):
    """
    Detecta golpes de batería sobre la parte PERCUSIVA (lo opuesto de lo que usa la
    melodía) y clasifica cada uno como grave ("bombo") o agudo ("redoblante/hi-hat")
    según el centroide espectral de ese instante — así se les puede dar un timbre de
    ruido distinto a cada uno en vez de un solo sonido de percusión genérico.
    """
    percussive = librosa.effects.percussive(mix, margin=2.0, n_fft=1024)
    onset_times = librosa.onset.onset_detect(y=percussive, sr=rate, hop_length=HOP_SIZE, units="time")
    if len(onset_times) == 0:
        return []

    hits = []
    half_win = int(0.02 * rate)
    for t in onset_times:
        center_idx = int(t * rate)
        lo, hi = max(0, center_idx - half_win), min(len(percussive), center_idx + half_win)
        segment = percussive[lo:hi]
        if len(segment) < 8:
            continue
        energy = float(np.sqrt(np.mean(segment ** 2)))
        if energy < 0.01:
            continue  # golpe demasiado débil, seguramente falso positivo
        centroid = float(librosa.feature.spectral_centroid(y=segment, sr=rate, n_fft=min(512, len(segment)))[0, 0])
        kind = "kick" if centroid < 900 else "snare"
        hits.append((float(t), kind, min(1.0, energy * 6)))
    return hits


# --------------------------------------------------------- paso 3: síntesis
def _synthesize(notes, loudness_curve, total_seconds: float, out_rate: int) -> np.ndarray:
    total_samples = max(1, int(total_seconds * out_rate))
    out = np.zeros(total_samples, dtype=np.float32)
    envelope_len = max(1, int(0.006 * out_rate))  # ~6ms de ataque/caída, evita clicks
    loud_times, loud_values = loudness_curve

    for t0, t1, freq in notes:
        i0 = int(t0 * out_rate)
        i1 = min(total_samples, int(t1 * out_rate))
        if i1 <= i0:
            continue
        t = np.arange(i0, i1) / out_rate
        phase = (t * freq) % 1.0
        # pulso sin sesgo de DC: alto = (1-duty), bajo = -duty, así el promedio da 0
        wave_seg = np.where(phase < PULSE_DUTY, 1.0 - PULSE_DUTY, -PULSE_DUTY).astype(np.float32)

        seg_len = i1 - i0
        env = np.ones(seg_len, dtype=np.float32)
        ramp = min(envelope_len, seg_len // 2)
        if ramp > 0:
            env[:ramp] = np.linspace(0, 1, ramp)
            env[-ramp:] = np.linspace(1, 0, ramp)

        # sigue la dinámica real del original en vez de un volumen fijo — así una nota
        # sostenida que baja de volumen en el tema original también baja acá
        loudness = np.interp(t, loud_times, loud_values)

        out[i0:i1] += wave_seg * env * loudness

    # el recorte/cuantización final se hace UNA sola vez, después de mezclar los tres
    # canales (ver _finalize_mix) — hacerlo acá antes de sumar bajo/percusión es lo que
    # tapaba todo lo demás contra el pulso de la melodía
    return out * GAIN_MELODY


# ------------------------------------------- síntesis del modo "estilo videojuego"
VIBRATO_DELAY = 0.16     # una nota tiene que estar sostenida este tiempo antes de que arranque el vibrato
VIBRATO_RATE = 6.0       # Hz — velocidad del vibrato, como un temblor de dedo en la cuerda
VIBRATO_DEPTH = 0.010    # profundidad, como fracción de la frecuencia de la nota


def _timbre_for_note(freq: float, index: int):
    """
    Elige el timbre según el registro de la nota, como hacían las
    consolas viejas: las notas graves de la melodía van en onda
    triangular (más redonda, sin armónicos ásperos), y las medias/agudas
    turnan entre un par de anchos de pulso para que no suene todo el
    tema con el mismo timbre parejo. Devuelve ("triangle", None) o
    ("pulse", ancho_de_pulso).
    """
    if freq < 220:
        return "triangle", None
    duties = (0.125, 0.25, 0.5)
    return "pulse", duties[index % len(duties)]


def _wave_cycle(phase: np.ndarray, kind: str, duty) -> np.ndarray:
    if kind == "triangle":
        return (2.0 * np.abs(2.0 * (phase - np.floor(phase + 0.5))) - 1.0).astype(np.float32)
    # pulso sin sesgo de DC: alto = (1-duty), bajo = -duty, así el promedio da 0
    frac = phase % 1.0
    return np.where(frac < duty, 1.0 - duty, -duty).astype(np.float32)


def _synthesize_melody_arcade(notes, loudness_curve, total_samples: int, out_rate: int) -> np.ndarray:
    out = np.zeros(total_samples, dtype=np.float32)
    envelope_len = max(1, int(0.006 * out_rate))
    loud_times, loud_values = loudness_curve

    for idx, (t0, t1, freq) in enumerate(notes):
        i0 = int(t0 * out_rate)
        i1 = min(total_samples, int(t1 * out_rate))
        if i1 <= i0:
            continue
        t = np.arange(i0, i1) / out_rate
        kind, duty = _timbre_for_note(freq, idx)

        # vibrato: recién arranca pasado un ratito de nota sostenida, para no hacer
        # temblar notas cortas/staccato — así suena a técnica real, no a un tic constante
        held = t - t0
        vibrato_amount = np.clip((held - VIBRATO_DELAY) / 0.05, 0.0, 1.0)
        freq_mod = freq * (1.0 + VIBRATO_DEPTH * vibrato_amount * np.sin(2 * np.pi * VIBRATO_RATE * held))
        phase = np.cumsum(freq_mod) / out_rate

        wave_seg = _wave_cycle(phase, kind, duty if duty is not None else PULSE_DUTY)

        seg_len = i1 - i0
        env = np.ones(seg_len, dtype=np.float32)
        ramp = min(envelope_len, seg_len // 2)
        if ramp > 0:
            env[:ramp] = np.linspace(0, 1, ramp)
            env[-ramp:] = np.linspace(1, 0, ramp)

        loudness = np.interp(t, loud_times, loud_values)
        out[i0:i1] += wave_seg * env * loudness
    return out * GAIN_MELODY


def _synthesize_bass(bass_notes, total_samples: int, out_rate: int) -> np.ndarray:
    out = np.zeros(total_samples, dtype=np.float32)
    envelope_len = max(1, int(0.008 * out_rate))
    for t0, t1, freq in bass_notes:
        i0 = int(t0 * out_rate)
        i1 = min(total_samples, int(t1 * out_rate))
        if i1 <= i0 or freq <= 0:
            continue
        t = np.arange(i0, i1) / out_rate
        phase = t * freq
        wave_seg = _wave_cycle(phase, "triangle", None)  # el bajo va en triangular, como el canal 2 de una NES
        seg_len = i1 - i0
        env = np.ones(seg_len, dtype=np.float32)
        ramp = min(envelope_len, seg_len // 2)
        if ramp > 0:
            env[:ramp] = np.linspace(0, 1, ramp)
            env[-ramp:] = np.linspace(1, 0, ramp)
        out[i0:i1] += wave_seg * env
    return out * GAIN_BASS


def _synthesize_percussion(hits, total_samples: int, out_rate: int) -> np.ndarray:
    out = np.zeros(total_samples, dtype=np.float32)
    rng = np.random.default_rng(7)
    for t, kind, energy in hits:
        i0 = int(t * out_rate)
        dur = 0.10 if kind == "kick" else 0.06
        n = min(total_samples - i0, int(dur * out_rate))
        if n <= 0 or i0 >= total_samples:
            continue
        decay = np.exp(-np.arange(n) / out_rate * (22 if kind == "kick" else 45))
        if kind == "kick":
            # ruido con un empujón grave abajo, como el "thump" de un bombo digital
            tone = np.sin(2 * np.pi * 65 * np.arange(n) / out_rate)
            burst = 0.6 * tone + 0.4 * rng.uniform(-1, 1, n)
        else:
            burst = rng.uniform(-1, 1, n)  # ruido blanco parejo, tipo redoblante/hi-hat
        out[i0:i0 + n] += (burst * decay * energy).astype(np.float32)
    return out * GAIN_PERCUSSION


def _finalize_mix(*layers, total_samples: int) -> np.ndarray:
    out = np.zeros(total_samples, dtype=np.float32)
    for layer in layers:
        out[:len(layer)] += layer[:total_samples]

    # normalizar el PICO en vez de recortarlo: si algo se pasa, se le baja el volumen a
    # toda la mezcla por igual — eso conserva el balance entre canales. Recortar cada
    # muestra por separado (clip duro) es lo que aplastaba la percusión contra el pulso
    # de la melodía cada vez que coincidían.
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > MIX_TARGET_PEAK:
        out *= MIX_TARGET_PEAK / peak
    np.clip(out, -1.0, 1.0, out=out)  # red de seguridad, no debería hacer nada casi nunca

    # cuantización final: menos niveles que un 8-bit "limpio", para que no suene a sintetizador prolijo
    out = np.round(out * (BIT_LEVELS / 2)) / (BIT_LEVELS / 2)
    return out


def _write_wav_u8(samples: np.ndarray, rate: int, path: str):
    u8 = ((samples * 0.5 + 0.5) * 255).astype(np.uint8)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(u8.tobytes())


# --------------------------------------------------------------- API pública
class ConversionCanceled(Exception):
    pass


def render_gba_preview(source_path: str, on_done, on_error):
    """
    Genera la versión chip en un archivo TEMPORAL — no toca la
    biblioteca. on_done(temp_path) / on_error(mensaje) se llaman en el
    hilo principal de GTK vía GLib.idle_add. Devuelve un threading.Event:
    llamar a su .set() pide cancelar (efectivo en el próximo punto de
    control entre pasos, no en el instante — un solo paso pesado, como
    pyin, no se puede interrumpir a mitad de camino).
    """
    cancel_event = threading.Event()

    def _worker():
        pcm_path = None
        try:
            def check_cancel():
                if cancel_event.is_set():
                    raise ConversionCanceled()

            pcm_fd, pcm_path = tempfile.mkstemp(suffix=".wav", prefix="music-glitch-pcm-")
            os.close(pcm_fd)
            _decode_to_pcm(source_path, pcm_path)
            check_cancel()

            left, right = _read_stereo_float(pcm_path)
            if len(left) == 0:
                raise RuntimeError("el archivo decodificado quedó vacío")
            total_seconds = len(left) / WORK_RATE
            total_samples = max(1, int(total_seconds * OUT_RATE))
            mix = (left + right) * 0.5

            notes, loudness_curve = _extract_notes(left, right, WORK_RATE)
            check_cancel()
            gc.collect()  # cada paso de análisis usa arreglos grandes; los libera antes de seguir
            bass_notes = _extract_bass_notes(mix, WORK_RATE)
            check_cancel()
            gc.collect()
            perc_hits = _extract_percussion_hits(mix, WORK_RATE)
            check_cancel()
            gc.collect()

            melody_layer = _synthesize(notes, loudness_curve, total_seconds, OUT_RATE)
            bass_layer = _synthesize_bass(bass_notes, total_samples, OUT_RATE)
            perc_layer = _synthesize_percussion(perc_hits, total_samples, OUT_RATE)
            synth = _finalize_mix(melody_layer, bass_layer, perc_layer, total_samples=total_samples)

            out_fd, out_path = tempfile.mkstemp(suffix=".wav", prefix="music-glitch-gba-preview-")
            os.close(out_fd)
            _write_wav_u8(synth, OUT_RATE, out_path)

            GLib.idle_add(on_done, out_path)
        except ConversionCanceled:
            GLib.idle_add(on_error, "cancelado")
        except Exception as e:
            GLib.idle_add(on_error, str(e))
        finally:
            if pcm_path and os.path.exists(pcm_path):
                os.remove(pcm_path)

    threading.Thread(target=_worker, daemon=True).start()
    return cancel_event


def render_gba_preview_arcade(source_path: str, on_done, on_error):
    """
    La versión 'estilo videojuego': tres canales por separado (melodía con
    timbres variados + vibrato, bajo resintetizado una octava abajo, y
    percusión con ráfagas de ruido) mezclados en un WAV nuevo. También
    genera un archivo TEMPORAL, igual que render_gba_preview. Devuelve un
    threading.Event para poder pedir cancelación (ver render_gba_preview).
    """
    cancel_event = threading.Event()

    def _worker():
        pcm_path = None
        try:
            def check_cancel():
                if cancel_event.is_set():
                    raise ConversionCanceled()

            pcm_fd, pcm_path = tempfile.mkstemp(suffix=".wav", prefix="music-glitch-pcm-")
            os.close(pcm_fd)
            _decode_to_pcm(source_path, pcm_path)
            check_cancel()

            left, right = _read_stereo_float(pcm_path)
            if len(left) == 0:
                raise RuntimeError("el archivo decodificado quedó vacío")
            total_seconds = len(left) / WORK_RATE
            total_samples = max(1, int(total_seconds * OUT_RATE))
            mix = (left + right) * 0.5

            melody_notes, loudness_curve = _extract_notes(left, right, WORK_RATE)
            check_cancel()
            gc.collect()
            bass_notes = _extract_bass_notes(mix, WORK_RATE)
            check_cancel()
            gc.collect()
            perc_hits = _extract_percussion_hits(mix, WORK_RATE)
            check_cancel()
            gc.collect()

            melody_layer = _synthesize_melody_arcade(melody_notes, loudness_curve, total_samples, OUT_RATE)
            bass_layer = _synthesize_bass(bass_notes, total_samples, OUT_RATE)
            perc_layer = _synthesize_percussion(perc_hits, total_samples, OUT_RATE)
            synth = _finalize_mix(melody_layer, bass_layer, perc_layer, total_samples=total_samples)

            out_fd, out_path = tempfile.mkstemp(suffix=".wav", prefix="music-glitch-gba-arcade-preview-")
            os.close(out_fd)
            _write_wav_u8(synth, OUT_RATE, out_path)

            GLib.idle_add(on_done, out_path)
        except ConversionCanceled:
            GLib.idle_add(on_error, "cancelado")
        except Exception as e:
            GLib.idle_add(on_error, str(e))
        finally:
            if pcm_path and os.path.exists(pcm_path):
                os.remove(pcm_path)

    threading.Thread(target=_worker, daemon=True).start()
    return cancel_event


def keep_gba_file(temp_path: str, title: str, suffix: str = "gba") -> str:
    """Confirma el preview: lo pasa de /tmp a la carpeta definitiva de conversiones."""
    dest = build_output_path(title, suffix=suffix)
    os.replace(temp_path, dest)
    return dest


def discard_gba_file(temp_path: str):
    if temp_path and os.path.exists(temp_path):
        os.remove(temp_path)


def warmup():
    """
    La primera llamada a librosa.pyin/yin en todo el proceso paga la
    compilación JIT de numba (puede ser bastante) — sin esto, esa
    demora se la comería la primera conversión que pida el usuario.
    Se llama una vez al arrancar la app, en un hilo de fondo, con un
    audio de mentira de medio segundo.
    """
    def _worker():
        try:
            dummy = np.random.randn(WORK_RATE // 2).astype(np.float32) * 0.1
            librosa.pyin(dummy, fmin=MELODY_MIN_FREQ, fmax=MELODY_MAX_FREQ, sr=WORK_RATE,
                         frame_length=FRAME_SIZE, hop_length=HOP_SIZE, center=False, fill_na=0.0)
            librosa.yin(dummy, fmin=BASS_MIN_FREQ, fmax=BASS_MAX_FREQ, sr=WORK_RATE,
                        frame_length=FRAME_SIZE, hop_length=HOP_SIZE, center=False)
            librosa.effects.harmonic(dummy, margin=2.0, n_fft=1024)
            librosa.effects.percussive(dummy, margin=2.0, n_fft=1024)
            librosa.onset.onset_detect(y=dummy, sr=WORK_RATE, hop_length=HOP_SIZE, units="time")
            librosa.feature.spectral_centroid(y=dummy, sr=WORK_RATE, n_fft=512)
            _butter_filter(dummy, WORK_RATE, HIGHPASS_CUTOFF, "highpass")
        except Exception:
            pass  # si falla el precalentamiento no pasa nada, la primera conversión real lo hace igual

    threading.Thread(target=_worker, daemon=True).start()
