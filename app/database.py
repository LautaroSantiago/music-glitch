"""
Todo lo que toca SQLite pasa por acá. Un track vive en `tracks`, sus
apariciones en listas viven en `playlist_tracks`, y las estadísticas
de reproducción se llevan aparte en `play_stats` + `play_history` para
no mezclar "datos del archivo" con "datos de uso".

La lista "Favoritos" y las listas automáticas por carpeta son filas
normales de `playlists` con `kind` distinto a 'manual'; no hay tablas
especiales para eso.
"""
import sqlite3
import time
from pathlib import Path

from app.config import DB_FILE, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT UNIQUE NOT NULL,
    title         TEXT NOT NULL,
    artist        TEXT NOT NULL DEFAULT 'Desconocido',
    album         TEXT NOT NULL DEFAULT '',
    duration      REAL NOT NULL DEFAULT 0,
    format        TEXT NOT NULL DEFAULT '',
    extra_json    TEXT NOT NULL DEFAULT '{}',   -- metadata secundaria: bitrate, año, género, etc
    cover_path    TEXT,                          -- portada elegida a mano por el usuario
    has_embedded_cover INTEGER NOT NULL DEFAULT 0,
    assigned_emoji TEXT,                         -- emoji fijo asignado si no hay portada
    accent_color  TEXT,                          -- color propio (conversiones GBA); si es NULL se usa accent_for_id
    added_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS playlists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'manual',   -- manual | favoritos | carpeta | generada
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, track_id)
);

CREATE TABLE IF NOT EXISTS favorites (
    track_id  INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    marked_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS play_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id  INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    played_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS play_stats (
    track_id            INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    play_count          INTEGER NOT NULL DEFAULT 0,
    total_seconds_played REAL NOT NULL DEFAULT 0,
    last_played         REAL
);
"""


class Database:
    """
    Wrapper fino sobre sqlite3. Cada hilo que necesite escribir (el
    escaneo corre en un hilo aparte) debe pedir su propia instancia
    vía `Database()` en vez de compartir la conexión de la UI -
    sqlite no banca conexiones compartidas entre hilos sin dolores de cabeza.
    """

    def __init__(self, path: Path = DB_FILE):
        ensure_dirs()
        self.path = path
        self.conn = sqlite3.connect(str(path), timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")  # permite lecturas de la UI mientras el escaneo escribe
        self.conn.executescript(SCHEMA)
        self._migrate_schema()
        self._ensure_favoritos_playlist()

    def _migrate_schema(self):
        """Agrega columnas nuevas a bases de datos que ya existían de antes, sin tocar los datos."""
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(tracks)")}
        if "accent_color" not in existing:
            self.conn.execute("ALTER TABLE tracks ADD COLUMN accent_color TEXT")
            self.conn.commit()

    # ---------------------------------------------------------- utilidades
    def _ensure_favoritos_playlist(self):
        cur = self.conn.execute("SELECT id FROM playlists WHERE kind = 'favoritos'")
        if cur.fetchone() is None:
            self.conn.execute(
                "INSERT INTO playlists (name, kind, created_at) VALUES ('Favoritos', 'favoritos', ?)",
                (time.time(),),
            )
            self.conn.commit()

    def close(self):
        self.conn.close()

    # -------------------------------------------------------------- tracks
    def upsert_track(self, path, title, artist, album, duration, fmt, extra_json,
                      has_embedded_cover, assigned_emoji=None, accent_color=None):
        """Inserta el track si es nuevo; si ya existía por `path`, sólo refresca metadata."""
        existing = self.conn.execute(
            "SELECT id, assigned_emoji, accent_color FROM tracks WHERE path = ?", (path,)
        ).fetchone()
        if existing:
            emoji = existing["assigned_emoji"] or assigned_emoji
            color = existing["accent_color"] or accent_color
            self.conn.execute(
                """UPDATE tracks SET title=?, artist=?, album=?, duration=?, format=?,
                   extra_json=?, has_embedded_cover=?, assigned_emoji=?, accent_color=? WHERE id=?""",
                (title, artist, album, duration, fmt, extra_json, int(has_embedded_cover),
                 emoji, color, existing["id"]),
            )
            self.conn.commit()
            return existing["id"], False
        cur = self.conn.execute(
            """INSERT INTO tracks (path, title, artist, album, duration, format, extra_json,
               has_embedded_cover, assigned_emoji, accent_color, added_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (path, title, artist, album, duration, fmt, extra_json,
             int(has_embedded_cover), assigned_emoji, accent_color, time.time()),
        )
        track_id = cur.lastrowid
        self.conn.execute("INSERT INTO play_stats (track_id, play_count, total_seconds_played) VALUES (?, 0, 0)",
                           (track_id,))
        self.conn.commit()
        return track_id, True

    def track_exists(self, path) -> bool:
        return self.conn.execute("SELECT 1 FROM tracks WHERE path = ?", (path,)).fetchone() is not None

    def get_track(self, track_id):
        return self.conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

    def list_tracks(self, order_by="title"):
        order_by = order_by if order_by in ("title", "artist", "album", "duration", "format", "added_at") else "title"
        return self.conn.execute(f"SELECT * FROM tracks ORDER BY {order_by} COLLATE NOCASE").fetchall()

    def set_custom_cover(self, track_id, cover_path):
        self.conn.execute("UPDATE tracks SET cover_path = ? WHERE id = ?", (str(cover_path), track_id))
        self.conn.commit()

    def track_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]

    # ----------------------------------------------------------- playlists
    def get_or_create_playlist(self, name, kind="manual"):
        row = self.conn.execute("SELECT * FROM playlists WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO playlists (name, kind, created_at) VALUES (?, ?, ?)", (name, kind, time.time())
        )
        self.conn.commit()
        return cur.lastrowid

    def create_playlist(self, name, kind="manual"):
        return self.get_or_create_playlist(name, kind)

    def delete_playlist(self, playlist_id):
        # la lista de favoritos no se borra por accidente desde acá
        row = self.conn.execute("SELECT kind FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        if row and row["kind"] == "favoritos":
            return
        self.conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        self.conn.commit()

    def list_playlists(self):
        return self.conn.execute(
            """SELECT p.*, COUNT(pt.track_id) as n_tracks
               FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
               GROUP BY p.id ORDER BY (p.kind='favoritos') DESC, p.name COLLATE NOCASE"""
        ).fetchall()

    def add_track_to_playlist(self, playlist_id, track_id):
        pos_row = self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        self.conn.execute(
            "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
            (playlist_id, track_id, pos_row["next_pos"]),
        )
        self.conn.commit()

    def remove_track_from_playlist(self, playlist_id, track_id):
        self.conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?", (playlist_id, track_id)
        )
        self.conn.commit()

    def playlist_tracks(self, playlist_id):
        return self.conn.execute(
            """SELECT t.* FROM tracks t
               JOIN playlist_tracks pt ON pt.track_id = t.id
               WHERE pt.playlist_id = ? ORDER BY pt.position""",
            (playlist_id,),
        ).fetchall()

    def replace_playlist_tracks(self, playlist_id, track_ids):
        """Usado por los generadores (random / más escuchados / etc): pisa el contenido entero."""
        self.conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        self.conn.executemany(
            "INSERT INTO playlist_tracks (playlist_id, track_id, position) VALUES (?, ?, ?)",
            [(playlist_id, tid, i) for i, tid in enumerate(track_ids)],
        )
        self.conn.commit()

    # ----------------------------------------------------------- favoritos
    def is_favorite(self, track_id) -> bool:
        return self.conn.execute("SELECT 1 FROM favorites WHERE track_id = ?", (track_id,)).fetchone() is not None

    def toggle_favorite(self, track_id) -> bool:
        fav_playlist = self.conn.execute("SELECT id FROM playlists WHERE kind='favoritos'").fetchone()["id"]
        if self.is_favorite(track_id):
            self.conn.execute("DELETE FROM favorites WHERE track_id = ?", (track_id,))
            self.remove_track_from_playlist(fav_playlist, track_id)
            self.conn.commit()
            return False
        self.conn.execute("INSERT INTO favorites (track_id, marked_at) VALUES (?, ?)", (track_id, time.time()))
        self.conn.commit()
        self.add_track_to_playlist(fav_playlist, track_id)
        return True

    # ------------------------------------------------------- reproducción
    def register_play_start(self, track_id):
        """Se llama cada vez que un tema arranca a sonar: cuenta como una reproducción más."""
        self.conn.execute("INSERT INTO play_history (track_id, played_at) VALUES (?, ?)", (track_id, time.time()))
        self.conn.execute(
            """UPDATE play_stats SET play_count = play_count + 1, last_played = ?
               WHERE track_id = ?""",
            (time.time(), track_id),
        )
        self.conn.commit()

    def add_listened_seconds(self, track_id, seconds):
        if seconds <= 0:
            return
        self.conn.execute(
            "UPDATE play_stats SET total_seconds_played = total_seconds_played + ? WHERE track_id = ?",
            (seconds, track_id),
        )
        self.conn.commit()

    def recent_history(self, limit=200):
        return self.conn.execute(
            """SELECT h.played_at, t.* FROM play_history h
               JOIN tracks t ON t.id = h.track_id
               ORDER BY h.played_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    def clear_history(self):
        self.conn.execute("DELETE FROM play_history")
        self.conn.commit()

    # -------------------------------------------------------- generadores
    def tracks_most_played(self, limit=30):
        return self.conn.execute(
            """SELECT t.*, s.play_count FROM tracks t JOIN play_stats s ON s.track_id = t.id
               WHERE s.play_count > 0 ORDER BY s.play_count DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    def tracks_least_played(self, limit=30):
        return self.conn.execute(
            """SELECT t.*, s.play_count FROM tracks t JOIN play_stats s ON s.track_id = t.id
               ORDER BY s.play_count ASC, t.title COLLATE NOCASE LIMIT ?""",
            (limit,),
        ).fetchall()

    def tracks_same_artist_or_album(self, track_id, limit=50):
        t = self.get_track(track_id)
        if t is None:
            return []
        return self.conn.execute(
            """SELECT * FROM tracks WHERE id != ? AND (artist = ? OR (album = ? AND album != ''))
               ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE""",
            (track_id, t["artist"], t["album"]),
        ).fetchall()[:limit]

    # ------------------------------------------------------- estadísticas
    def stats_summary(self):
        row = self.conn.execute(
            """SELECT (SELECT COUNT(*) FROM tracks) as n_tracks,
                      (SELECT COUNT(*) FROM playlists) as n_playlists,
                      (SELECT COALESCE(SUM(total_seconds_played),0) FROM play_stats) as total_seconds,
                      (SELECT COALESCE(SUM(play_count),0) FROM play_stats) as total_plays"""
        ).fetchone()
        return dict(row)

    def top_tracks_by_plays(self, limit=8):
        return self.conn.execute(
            """SELECT t.title, t.artist, s.play_count FROM tracks t JOIN play_stats s ON s.track_id=t.id
               WHERE s.play_count > 0 ORDER BY s.play_count DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    def top_tracks_by_time(self, limit=8):
        return self.conn.execute(
            """SELECT t.title, t.artist, s.total_seconds_played FROM tracks t JOIN play_stats s ON s.track_id=t.id
               WHERE s.total_seconds_played > 0 ORDER BY s.total_seconds_played DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    def top_artists(self, limit=8):
        return self.conn.execute(
            """SELECT t.artist as artist, SUM(s.play_count) as plays, SUM(s.total_seconds_played) as seconds
               FROM tracks t JOIN play_stats s ON s.track_id = t.id
               WHERE t.artist != '' GROUP BY t.artist HAVING plays > 0
               ORDER BY plays DESC LIMIT ?""",
            (limit,),
        ).fetchall()
