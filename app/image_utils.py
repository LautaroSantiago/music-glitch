"""
Todo lo relacionado a la imagen de portada de un tema:

1. si hay arte embebido en el archivo, se relee con mutagen y se usa
2. si no, se dibuja una portada con el emoji asignado sobre un
   degradé con la paleta del proyecto
3. lo que sea que haya salido de 1 o 2 pasa siempre por el filtro de
   pixelado antes de llegar a pantalla

El emoji en sí se sortea una sola vez por track (en el scanner) y
queda guardado en la DB; acá sólo lo dibujamos.
"""
import io
import os
import random
import shutil
import uuid

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GdkPixbuf, Gdk, Pango, PangoCairo

from app.config import COVERS_DIR
from app.palette import MINT_GREEN, accent_for_id, hex_to_rgb

EMOJI_POOL = [
    # música
    "🎵", "🎶", "🎧", "🎤", "🎸", "🥁", "🎹", "🎷", "🪗", "🎻", "📻", "💿",
    # espacio
    "🚀", "🛸", "🌌", "🌠", "🪐", "🌙", "⭐", "☄️", "👽", "🛰️",
    # internet / tecnología
    "💻", "🖥️", "📡", "🔌", "🔋", "🧠", "🤖", "⚙️", "🔗", "📶", "🕹️",
]


def random_emoji() -> str:
    return random.choice(EMOJI_POOL)


def _surface_to_pixbuf(surface: "cairo.ImageSurface") -> GdkPixbuf.Pixbuf:
    buf = io.BytesIO()
    surface.write_to_png(buf)
    buf.seek(0)
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(buf.read())
    loader.close()
    return loader.get_pixbuf()


def render_emoji_cover(emoji: str, seed_id: int, size: int = 320, accent_hex: str = None) -> GdkPixbuf.Pixbuf:
    """Portada generada: degradé verde -> acento (elegido según el id del track, o uno propio
    si el track ya trae su color asignado, como las conversiones GBA) con el emoji al centro."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)

    accent_rgb = hex_to_rgb(accent_hex or accent_for_id(seed_id))
    green_rgb = hex_to_rgb(MINT_GREEN)

    grad = cairo.LinearGradient(0, 0, size, size)
    grad.add_color_stop_rgb(0, *green_rgb)
    grad.add_color_stop_rgb(0.55, 0.09, 0.11, 0.09)
    grad.add_color_stop_rgb(1, *accent_rgb)
    ctx.set_source(grad)
    ctx.paint()

    # un par de franjas de "ruido" para que no quede una portada demasiado prolija
    ctx.set_source_rgba(*accent_rgb, 0.25)
    for i in range(3):
        y = size * (0.15 + 0.3 * i) + random.uniform(-6, 6)
        ctx.rectangle(0, y, size, random.uniform(2, 6))
        ctx.fill()

    layout = PangoCairo.create_layout(ctx)
    layout.set_text(emoji, -1)
    layout.set_font_description(Pango.FontDescription(f"Noto Color Emoji {int(size * 0.4)}"))
    ink, logical = layout.get_pixel_extents()
    ctx.translate(size / 2 - logical.width / 2 - logical.x, size / 2 - logical.height / 2 - logical.y)
    PangoCairo.show_layout(ctx, layout)

    return _surface_to_pixbuf(surface)


def pixelate_pixbuf(pixbuf: GdkPixbuf.Pixbuf, block_size: int = 10) -> GdkPixbuf.Pixbuf:
    """Achica y vuelve a agrandar con interpolación NEAREST: el efecto 'roto en bloques' pedido."""
    w, h = pixbuf.get_width(), pixbuf.get_height()
    small_w = max(1, w // max(2, block_size))
    small_h = max(1, h // max(2, block_size))
    small = pixbuf.scale_simple(small_w, small_h, GdkPixbuf.InterpType.BILINEAR)
    return small.scale_simple(w, h, GdkPixbuf.InterpType.NEAREST)


def get_cover_source_pixbuf(track_row, size: int) -> GdkPixbuf.Pixbuf:
    """Igual que get_cover_pixbuf pero sin pixelar todavía: la usa la animación de transición al cambiar de tema."""
    if track_row["cover_path"] and os.path.exists(track_row["cover_path"]):
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(track_row["cover_path"], size, size, False)

    if track_row["has_embedded_cover"]:
        from app.metadata import _extract_cover, MutagenFile
        audio = MutagenFile(track_row["path"])
        cover_bytes = _extract_cover(track_row["path"], audio) if audio else None
        if cover_bytes:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(cover_bytes)
            loader.close()
            raw = loader.get_pixbuf()
            return raw.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)

    emoji = track_row["assigned_emoji"] or random_emoji()
    accent_hex = track_row["accent_color"] if "accent_color" in track_row.keys() else None
    return render_emoji_cover(emoji, track_row["id"], size, accent_hex=accent_hex)


def get_cover_pixbuf(track_row, size: int, pixelate_block: int) -> GdkPixbuf.Pixbuf:
    return pixelate_pixbuf(get_cover_source_pixbuf(track_row, size), pixelate_block)


def save_custom_cover(track_id: int, source_path: str) -> str:
    """Copia la imagen elegida por el usuario a la carpeta de datos de la app y devuelve el path guardado."""
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(source_path)[1] or ".png"
    dest = COVERS_DIR / f"track-{track_id}-{uuid.uuid4().hex[:8]}{ext}"
    shutil.copyfile(source_path, dest)
    return str(dest)
