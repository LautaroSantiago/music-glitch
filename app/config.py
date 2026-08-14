"""
Rutas de la aplicación y persistencia de configuración de usuario.

Todo lo que el usuario puede tocar desde la pestaña "Configuración"
(interfaz fantasma, opacidad, posición del overlay, volumen recordado)
vive acá adentro como un diccionario plano que se vuelca a JSON.
No hay nada raro: se lee entero al arrancar, se pisa entero al guardar.
"""
import json
import os
from pathlib import Path

APP_SLUG = "music-glitch"
APP_DISPLAY_NAME = "MUSIC-GLITCH"

# Respetamos XDG por si el usuario tiene las variables seteadas distinto
# del default de Mint (~/.config y ~/.local/share).
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_SLUG
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_SLUG

CONFIG_FILE = CONFIG_DIR / "config.json"
DB_FILE = DATA_DIR / "biblioteca.db"
COVERS_DIR = DATA_DIR / "portadas"  # portadas custom que el usuario elige a mano

DEFAULTS = {
    "ghost_enabled": True,      # interfaz fantasma activa por defecto
    "ghost_opacity": 0.85,      # 0.0 - 1.0
    "ghost_pos_x": None,        # None = todavía no se movió, usar esquina inferior izquierda
    "ghost_pos_y": None,
    "volume": 0.8,
    "pixelate_block": 10,       # tamaño de bloque del filtro de pixelado (px), "leve" por defecto
    "last_scan_epoch": None,    # último escaneo de biblioteca, para mostrar info en Config
    "loop_enabled": False,      # repetir en bucle el tema actual
    "last_track_id": None,      # para retomar lo que sonaba cuando se cerró el programa
    "last_position_seconds": 0.0,
    "gba_color_index": 0,        # avanza en cada conversión GBA, para irle dando un color distinto a cada una
}


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COVERS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_dirs()
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            cfg.update(stored)
        except (json.JSONDecodeError, OSError):
            # config corrupta o ilegible: seguimos con defaults en vez de romper el arranque
            pass
    return cfg


def save_config(cfg: dict) -> None:
    ensure_dirs()
    tmp = CONFIG_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
    tmp.replace(CONFIG_FILE)  # escritura atómica, evita dejar el json a medio escribir
