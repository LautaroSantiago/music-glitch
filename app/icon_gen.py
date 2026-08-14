"""
Ícono de la app: cara de puma de frente (simétrica), pixel-art. La
silueta se arma con un path vectorial (más fácil de ajustar que dibujar
píxel por píxel a mano) pero se renderiza a resolución baja con
antialiasing apagado -eso es lo que da el look de bloque- y después se
escala para arriba con NEAREST para cada tamaño de ícono final.

El pelaje usa un degradé verde -> amatista, el hocico/orejas internas
van en lavanda clarito y la nariz en violeta oscuro -toda la paleta
del proyecto- más unas franjas de ruido en ciruela adentro del
recorte de la silueta, mismo truco que las portadas con emoji.
"""
import random

import cairo
import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from app.palette import (MINT_GREEN, ACC_AMATISTA, ACC_CIRUELA, ACC_LAVANDA,
                          ACC_VIOLETA_PROFUNDO, ACC_VIOLETA_SOMBRA, hex_to_rgb)

PATH_CANVAS = 600  # sistema de coordenadas en el que están pensados los paths de abajo


def _head_outline(ctx):
    """Silueta simétrica de cara de puma mirando de frente, con las dos orejas."""
    ctx.move_to(170, 40)
    ctx.curve_to(200, 75, 230, 100, 250, 128)
    ctx.curve_to(270, 108, 285, 98, 300, 95)
    ctx.curve_to(315, 98, 330, 108, 350, 128)
    ctx.curve_to(370, 100, 400, 75, 430, 40)
    ctx.curve_to(465, 75, 500, 105, 490, 140)
    ctx.curve_to(510, 190, 520, 225, 515, 260)
    ctx.curve_to(510, 310, 490, 350, 460, 380)
    ctx.curve_to(410, 415, 355, 430, 300, 430)
    ctx.curve_to(245, 430, 190, 415, 140, 380)
    ctx.curve_to(110, 350, 90, 310, 85, 260)
    ctx.curve_to(80, 225, 90, 190, 110, 140)
    ctx.curve_to(100, 105, 135, 75, 170, 40)
    ctx.close_path()


def _eye_holes(ctx):
    """Sub-paths que se restan del pelaje con la regla even-odd: quedan como agujero (translúcido)."""
    ctx.new_sub_path()
    ctx.arc(228, 222, 15, 0, 6.28318)
    ctx.new_sub_path()
    ctx.arc(372, 222, 15, 0, 6.28318)


def _inner_ears_path(ctx):
    ctx.new_path()
    ctx.move_to(150, 95)
    ctx.line_to(190, 82)
    ctx.line_to(205, 132)
    ctx.close_path()
    ctx.move_to(450, 95)
    ctx.line_to(410, 82)
    ctx.line_to(395, 132)
    ctx.close_path()


def _muzzle_path(ctx):
    ctx.new_path()
    ctx.move_to(220, 275)
    ctx.curve_to(200, 300, 195, 340, 210, 375)
    ctx.curve_to(225, 405, 265, 425, 300, 425)
    ctx.curve_to(335, 425, 375, 405, 390, 375)
    ctx.curve_to(405, 340, 400, 300, 380, 275)
    ctx.curve_to(350, 300, 320, 305, 300, 305)
    ctx.curve_to(280, 305, 250, 300, 220, 275)
    ctx.close_path()


def _nose_path(ctx):
    ctx.new_path()
    ctx.move_to(300, 275)
    ctx.curve_to(285, 280, 275, 292, 278, 302)
    ctx.curve_to(280, 312, 290, 318, 300, 318)
    ctx.curve_to(310, 318, 320, 312, 322, 302)
    ctx.curve_to(325, 292, 315, 280, 300, 275)
    ctx.close_path()


def _render_at(px_size: int, glitch_noise: bool) -> cairo.ImageSurface:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, px_size, px_size)
    ctx = cairo.Context(surface)
    ctx.set_antialias(cairo.ANTIALIAS_NONE)  # clave del look pixel-art: sin suavizado
    scale = px_size / PATH_CANVAS
    ctx.scale(scale, scale)

    # 1) pelaje: degradé verde -> amatista, con los ojos ya restados (even-odd)
    ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
    _head_outline(ctx)
    _eye_holes(ctx)
    grad = cairo.LinearGradient(60, 40, 540, 430)
    grad.add_color_stop_rgb(0, *hex_to_rgb(MINT_GREEN))
    grad.add_color_stop_rgb(1, *hex_to_rgb(ACC_AMATISTA))
    ctx.set_source(grad)
    ctx.fill_preserve()

    # el recorte (pelaje + ojos restados) se guarda como clip para la nieve glitch de más abajo
    ctx.save()
    ctx.clip()
    if glitch_noise:
        rng = random.Random(7)  # semilla fija: el ícono no cambia entre corridas
        accent = hex_to_rgb(ACC_CIRUELA)
        for _ in range(8):
            y = rng.uniform(40, 420)
            h = rng.uniform(4, 11)
            ctx.set_source_rgba(*accent, 0.30)
            ctx.rectangle(0, y, PATH_CANVAS, h)
            ctx.fill()
    ctx.restore()

    # 2) orejas internas y hocico, en lavanda clarito -por encima del pelaje-
    ctx.set_fill_rule(cairo.FILL_RULE_WINDING)
    _inner_ears_path(ctx)
    ctx.set_source_rgb(*hex_to_rgb(ACC_LAVANDA))
    ctx.fill()
    _muzzle_path(ctx)
    ctx.set_source_rgb(*hex_to_rgb(ACC_LAVANDA))
    ctx.fill()

    # 3) nariz, violeta bien oscuro -por encima del hocico-
    _nose_path(ctx)
    ctx.set_source_rgb(*hex_to_rgb(ACC_VIOLETA_SOMBRA))
    ctx.fill()

    return surface


def _surface_to_pixbuf(surface) -> GdkPixbuf.Pixbuf:
    import io
    buf = io.BytesIO()
    surface.write_to_png(buf)
    buf.seek(0)
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(buf.read())
    loader.close()
    return loader.get_pixbuf()


def render_puma_icon(size: int = 256) -> GdkPixbuf.Pixbuf:
    """Íconos grandes (>=64) llevan ruido glitch; los chicos van limpios para no perder legibilidad."""
    block_res = max(28, min(72, size // 4))  # resolución "de bloque" antes de escalar
    small = _render_at(block_res, glitch_noise=(size >= 64))
    pixbuf_small = _surface_to_pixbuf(small)
    return pixbuf_small.scale_simple(size, size, GdkPixbuf.InterpType.NEAREST)
