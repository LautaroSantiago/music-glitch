"""
Extracción de tags con mutagen. Cubrimos los 7 formatos pedidos:
MP3 (ID3), OGG (Vorbis/Opus), FLAC, AAC/ALAC (contenedor MP4 .m4a/.aac),
WAV y AIFF. Cada contenedor guarda la portada distinto, así que
`_extract_cover` tiene un caso por familia de formato.

Todo lo que no sea título/autor/álbum/duración se guarda tal cual en
`extra` para mostrarlo bajo el desplegable "(+) Información" en la UI,
en vez de hardcodear qué campos importan.
"""
import os
import subprocess
import time
from dataclasses import dataclass, field

from mutagen import File as MutagenFile
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus
from mutagen.mp4 import MP4
from mutagen.wave import WAVE
from mutagen.aiff import AIFF

AUDIO_EXTENSIONS = {".mp3", ".aac", ".m4a", ".ogg", ".oga", ".opus",
                     ".flac", ".wav", ".wave", ".aif", ".aiff"}


@dataclass
class TrackMeta:
    title: str
    artist: str
    album: str
    duration: float
    fmt: str
    extra: dict = field(default_factory=dict)
    cover_bytes: bytes | None = None


def _clean(value, fallback=""):
    if value is None:
        return fallback
    if isinstance(value, list):
        value = value[0] if value else fallback
    return str(value).strip() or fallback


def _guess_format(path: str, audio) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    # dentro de un .m4a puede haber AAC o ALAC; mutagen expone el codec en 'codec' cuando puede
    if ext in ("m4a", "aac") and isinstance(audio, MP4):
        codec = (audio.info.codec_description or "").lower() if hasattr(audio.info, "codec_description") else ""
        if "alac" in codec:
            return "ALAC"
        return "AAC"
    return {"oga": "OGG", "opus": "OGG", "wave": "WAV", "aif": "AIFF"}.get(ext, ext.upper())


def _extract_cover(path: str, audio) -> bytes | None:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".mp3":
            id3 = MP3(path)
            for tag in id3.tags.values() if id3.tags else []:
                if tag.FrameID == "APIC":
                    return tag.data
        elif ext == ".flac":
            flac = FLAC(path)
            if flac.pictures:
                return flac.pictures[0].data
        elif ext in (".ogg", ".oga", ".opus"):
            pics = getattr(audio, "pictures", None)
            if pics:
                return pics[0].data
        elif ext in (".m4a", ".aac"):
            mp4 = MP4(path)
            covr = mp4.tags.get("covr") if mp4.tags else None
            if covr:
                return bytes(covr[0])
        # WAV/AIFF: mutagen no soporta portada embebida de forma confiable, se omite
    except Exception:
        return None
    return None


def _creation_date_str(path: str) -> str:
    """
    Linux no siempre guarda la fecha de creación real del archivo (depende
    del sistema de archivos). Se intenta con `stat -c %w` (birth time); si
    el filesystem no la soporta, se cae a la fecha de última modificación
    y se aclara que es aproximada.
    """
    try:
        out = subprocess.run(["stat", "-c", "%w", path], capture_output=True, text=True, timeout=2)
        birth = out.stdout.strip()
        if birth and birth != "-":
            # 'stat' devuelve algo como "2026-03-14 18:22:05.123456789 -0300"
            return birth.split(".")[0]
    except (subprocess.SubprocessError, OSError):
        pass
    try:
        mtime = os.path.getmtime(path)
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)) + " (aprox., última modificación)"
    except OSError:
        return "desconocida"


def _extra_fields(path: str, audio) -> dict:
    """Junta lo que sirve mostrar en "(+) Información": bitrate, samplerate, canales, año, género, tamaño."""
    info = audio.info
    extra = {}
    if hasattr(info, "bitrate") and info.bitrate:
        extra["Bitrate"] = f"{int(info.bitrate / 1000)} kbps"
    if hasattr(info, "sample_rate") and info.sample_rate:
        extra["Frecuencia de muestreo"] = f"{info.sample_rate} Hz"
    if hasattr(info, "channels") and info.channels:
        extra["Canales"] = info.channels
    if hasattr(info, "bits_per_sample") and info.bits_per_sample:
        extra["Profundidad"] = f"{info.bits_per_sample} bit"
    try:
        extra["Tamaño"] = f"{os.path.getsize(path) / (1024 * 1024):.1f} MB"
    except OSError:
        pass
    extra["Creado"] = _creation_date_str(path)

    tags = getattr(audio, "tags", None)
    easy = MutagenFile(path, easy=True)
    easy_tags = easy.tags if easy and easy.tags else {}
    field_map = {"genre": "Género", "date": "Año", "tracknumber": "N° de pista", "composer": "Compositor"}
    for key, label in field_map.items():
        if key in easy_tags:
            extra[label] = _clean(easy_tags[key])
    return extra


def read_track(path: str) -> TrackMeta | None:
    audio = MutagenFile(path)
    if audio is None or audio.info is None:
        return None

    easy = MutagenFile(path, easy=True)
    tags = easy.tags if easy and easy.tags else {}

    filename_title = os.path.splitext(os.path.basename(path))[0]
    title = _clean(tags.get("title"), filename_title)
    artist = _clean(tags.get("artist"), "Desconocido")
    album = _clean(tags.get("album"), "")
    duration = float(getattr(audio.info, "length", 0) or 0)
    fmt = _guess_format(path, audio)
    cover = _extract_cover(path, audio)
    extra = _extra_fields(path, audio)

    return TrackMeta(title=title, artist=artist, album=album, duration=duration,
                      fmt=fmt, extra=extra, cover_bytes=cover)
