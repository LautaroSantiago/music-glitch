"""
Barrido del $HOME buscando archivos de audio. Corre en un hilo aparte
(la llamada la dispara main_window con threading.Thread) para no
congelar la interfaz durante un escaneo que puede tardar. Los avisos
de progreso vuelven al hilo principal con GLib.idle_add, que es la
única forma segura de tocar widgets GTK desde otro hilo.

Cada tema encontrado termina en una lista automática (kind='carpeta')
que lleva el nombre de la carpeta que lo contiene directamente. Si dos
carpetas en rutas distintas se llaman igual, terminan compartiendo la
misma lista automática a propósito: es más útil que tener veinte
listas "Covers" separadas.
"""
import os
import threading
import random

from gi.repository import GLib

from app.config import DB_FILE
from app.database import Database
from app.metadata import read_track, AUDIO_EXTENSIONS
from app.image_utils import random_emoji

# directorios que no aportan nada y sólo hacen lento el escaneo
SKIP_DIR_NAMES = {
    ".cache", ".git", ".local", "node_modules", ".mozilla", ".config",
    ".thumbnails", ".Trash", "venv", ".venv", "__pycache__", ".npm",
}


def _iter_audio_files(root):
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in AUDIO_EXTENSIONS:
                yield os.path.join(dirpath, name)


def run_scan(progress_cb, done_cb):
    """
    progress_cb(encontrados, nuevos, ruta_actual) y done_cb(total_nuevos)
    se llaman siempre en el hilo principal de GTK vía GLib.idle_add,
    aunque esta función corra en background.
    """
    def _worker():
        db = Database(DB_FILE)
        home = os.path.expanduser("~")
        found = 0
        added = 0
        for path in _iter_audio_files(home):
            found += 1
            if not db.track_exists(path):
                meta = read_track(path)
                if meta is not None:
                    emoji = None if meta.cover_bytes else random_emoji()
                    track_id, is_new = db.upsert_track(
                        path=path, title=meta.title, artist=meta.artist, album=meta.album,
                        duration=meta.duration, fmt=meta.fmt,
                        extra_json=_extra_to_json(meta.extra),
                        has_embedded_cover=bool(meta.cover_bytes),
                        assigned_emoji=emoji,
                    )
                    # la portada embebida no se persiste en la DB como blob: se vuelve a
                    # leer del archivo de audio con mutagen cuando la UI la necesita mostrar
                    folder_name = os.path.basename(os.path.dirname(path)) or "Raíz"
                    playlist_id = db.get_or_create_playlist(folder_name, kind="carpeta")
                    db.add_track_to_playlist(playlist_id, track_id)
                    added += 1
            if found % 15 == 0:
                GLib.idle_add(progress_cb, found, added, path)
        GLib.idle_add(progress_cb, found, added, "")
        db.close()
        GLib.idle_add(done_cb, added)

    threading.Thread(target=_worker, daemon=True).start()


def _extra_to_json(extra: dict) -> str:
    import json
    return json.dumps(extra, ensure_ascii=False)
