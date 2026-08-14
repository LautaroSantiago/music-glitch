"""
Widgets dibujados a mano con Cairo, sin librerías de gráficos:

- PixelWaveform: barras tipo pixel-art que reaccionan al nivel de
  audio en vivo (lo alimenta player_engine a través de main_window).
- GlitchBarChart: gráfico de barras para la pestaña de Estadísticas,
  con el mismo lenguaje visual (bloques, jitter, paleta del proyecto).
- GlitchFlash: capa transparente que se dibuja arriba de todo por unos
  frames para dar sensación de "interferencia" en transiciones (cambio
  de pestaña, cambio de tema, confirmaciones de acciones).

Ninguno necesita librerías de plotting: es Cairo puro sobre un
Gtk.DrawingArea, redibujado con queue_draw() cuando hace falta.
"""
import random
from collections import deque

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from app.palette import MINT_GREEN, ACCENTS, hex_to_rgb, FG_MUTED, STATIC_VIOLETS, ACC_VIOLETA_PROFUNDO

_GREEN_RGB = hex_to_rgb(MINT_GREEN)
_ACCENT_RGB = [hex_to_rgb(c) for c in ACCENTS]
_STATIC_RGB = [hex_to_rgb(c) for c in STATIC_VIOLETS]


class PixelWaveform(Gtk.DrawingArea):
    """Barras verticales en bloques que suben con el nivel de audio y se apagan en silencio."""

    N_BARS = 28

    def __init__(self):
        super().__init__()
        self.set_size_request(-1, 90)
        self._levels = deque([0.0] * self.N_BARS, maxlen=self.N_BARS)
        self.connect("draw", self._on_draw)

    def push_level(self, value: float):
        self._levels.append(max(0.0, min(1.0, value)))
        self.queue_draw()

    def _on_draw(self, widget, ctx):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        ctx.set_source_rgba(0, 0, 0, 0)
        ctx.paint()

        bar_w = w / self.N_BARS
        block_h = max(3, h // 14)

        for i, level in enumerate(self._levels):
            n_blocks = max(1, int(level * (h // block_h)))
            x = i * bar_w + 1
            for b in range(n_blocks):
                y = h - (b + 1) * block_h
                # glitch: de vez en cuando un bloque se corre unos px al costado o cambia de color
                jitter_x = random.choice([0, 0, 0, 2, -2]) if level > 0.6 else 0
                color = _ACCENT_RGB[b % len(_ACCENT_RGB)] if (level > 0.75 and random.random() < 0.15) else _GREEN_RGB
                ctx.set_source_rgba(*color, 0.9)
                ctx.rectangle(x + jitter_x, y, max(1, bar_w - 2), block_h - 1)
                ctx.fill()
        return False


class GlitchBarChart(Gtk.DrawingArea):
    """Gráfico de barras horizontal simple: recibe [(etiqueta, valor), ...] y se redibuja solo."""

    def __init__(self, title: str = ""):
        super().__init__()
        self.title = title
        self.data = []
        self.set_size_request(-1, 220)
        self.connect("draw", self._on_draw)

    def set_data(self, pairs):
        self.data = list(pairs)
        self.queue_draw()

    def _on_draw(self, widget, ctx):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        ctx.set_source_rgba(0, 0, 0, 0)
        ctx.paint()

        if self.title:
            ctx.set_source_rgb(*hex_to_rgb(MINT_GREEN))
            ctx.select_font_face("monospace")
            ctx.set_font_size(13)
            ctx.move_to(6, 16)
            ctx.show_text(self.title.upper())

        if not self.data:
            ctx.set_source_rgb(*hex_to_rgb(FG_MUTED))
            ctx.set_font_size(11)
            ctx.move_to(6, h / 2)
            ctx.show_text("sin datos todavía")
            return False

        top = 28
        bottom = h - 10
        available_h = bottom - top
        row_h = available_h / len(self.data)
        max_val = max(v for _, v in self.data) or 1
        label_w = min(140, w * 0.32)
        bar_area_w = w - label_w - 50

        for i, (label, value) in enumerate(self.data):
            y = top + i * row_h + row_h * 0.2
            bar_h = row_h * 0.55
            bar_w = max(2, (value / max_val) * bar_area_w)
            accent = _ACCENT_RGB[i % len(_ACCENT_RGB)]

            ctx.set_source_rgb(*hex_to_rgb(FG_MUTED))
            ctx.set_font_size(10.5)
            ctx.move_to(4, y + bar_h * 0.8)
            trimmed = label if len(label) <= 20 else label[:18] + "…"
            ctx.show_text(trimmed)

            # "ghosting": una copia corrida y semitransparente atrás de la barra real, efecto glitch
            ctx.set_source_rgba(*accent, 0.25)
            ctx.rectangle(label_w + 3, y, bar_w, bar_h)
            ctx.fill()
            ctx.set_source_rgba(*accent, 0.9)
            ctx.rectangle(label_w, y, bar_w, bar_h)
            ctx.fill()

            ctx.set_source_rgb(*hex_to_rgb(MINT_GREEN))
            ctx.set_font_size(10.5)
            ctx.move_to(label_w + bar_w + 6, y + bar_h * 0.8)
            ctx.show_text(_format_value(value))
        return False


def _format_value(value):
    if value >= 3600:
        return f"{value / 3600:.1f} h"
    if value >= 60:
        return f"{value / 60:.0f} min"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.0f}"
    return str(int(value))


class StatCard(Gtk.DrawingArea):
    """Numerito grande + etiqueta, con un borde tironeado a propósito (glitch)."""

    def __init__(self, label: str, value: str, accent_hex: str):
        super().__init__()
        self.label = label
        self.value = value
        self.accent_rgb = hex_to_rgb(accent_hex)
        self.set_size_request(150, 90)
        self.connect("draw", self._on_draw)

    def set_value(self, value: str):
        self.value = value
        self.queue_draw()

    def _on_draw(self, widget, ctx):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        ctx.set_source_rgba(*self.accent_rgb, 0.08)
        ctx.rectangle(0, 0, w, h)
        ctx.fill()

        # borde con un segmento desplazado, para no dibujar un rectángulo perfecto
        ctx.set_source_rgba(*self.accent_rgb, 0.8)
        ctx.set_line_width(1.5)
        ctx.rectangle(1, 1, w - 2, h - 2)
        ctx.stroke()
        ctx.move_to(w * 0.15, 1)
        ctx.line_to(w * 0.4, 1)
        ctx.set_source_rgba(*hex_to_rgb(MINT_GREEN), 0.9)
        ctx.set_line_width(2.5)
        ctx.stroke()

        ctx.set_source_rgb(*hex_to_rgb("#E8EDE4"))
        ctx.select_font_face("monospace", 0, 1)
        ctx.set_font_size(22)
        ctx.move_to(12, h * 0.55)
        ctx.show_text(self.value)

        ctx.set_source_rgb(*hex_to_rgb(FG_MUTED))
        ctx.set_font_size(10.5)
        ctx.move_to(12, h - 12)
        ctx.show_text(self.label)
        return False


class GlitchFlash(Gtk.DrawingArea):
    """
    Capa invisible por defecto que se pone arriba de todo (dentro de un
    Gtk.Overlay) y parpadea con franjas de color por 3-4 frames cuando
    se la dispara con .flash(). No bloquea clicks: se marca como
    "pass-through" en el Gtk.Overlay que la contiene.
    """

    def __init__(self):
        super().__init__()
        self.set_no_show_all(True)
        self.hide()
        self._frames_left = 0
        self._gone = False
        self.connect("draw", self._on_draw)
        self.connect("destroy", lambda *_: setattr(self, "_gone", True))

    def flash(self, frames=4, interval_ms=45):
        if self._gone:
            return
        self._frames_left = frames
        self.show()
        self._tick(interval_ms)

    def _tick(self, interval_ms):
        if self._gone:
            return
        self._frames_left -= 1
        self.queue_draw()
        if self._frames_left <= 0:
            GLib.timeout_add(interval_ms, self._finish)
        else:
            GLib.timeout_add(interval_ms, lambda: (self._tick(interval_ms), False)[1])

    def _finish(self):
        if not self._gone:
            self.hide()
        return False

    def _on_draw(self, widget, ctx):
        if self._frames_left <= 0:
            return False
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        if w <= 1 or h <= 1:
            return False
        colors = _ACCENT_RGB + [_GREEN_RGB]
        for _ in range(random.randint(3, 6)):
            y = random.uniform(0, h)
            bar_h = random.uniform(3, 16)
            x_off = random.uniform(-24, 24)
            color = random.choice(colors)
            ctx.set_source_rgba(*color, 0.30)
            ctx.rectangle(x_off, y, w, bar_h)
            ctx.fill()
        return False


class TVStaticBackground(Gtk.DrawingArea):
    """
    Va como capa de fondo, detrás de todo el contenido: interferencia
    tipo "sin señal" continua, en tonos violeta (misma gama que los
    acentos del proyecto + un par de variantes más oscuras/claras para
    que la nieve no se vea plana). Se anima sola con un timeout propio;
    no hace falta que nadie la dispare.
    """

    def __init__(self, interval_ms=110):
        super().__init__()
        self._gone = False
        self.connect("draw", self._on_draw)
        self.connect("destroy", lambda *_: setattr(self, "_gone", True))
        GLib.timeout_add(interval_ms, self._tick)

    def _tick(self):
        if self._gone:
            return False
        if self.get_mapped():
            self.queue_draw()
        return True

    def _on_draw(self, widget, ctx):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        if w <= 1 or h <= 1:
            return False

        # fondo base: violeta casi negro, el "canal muerto"
        ctx.set_source_rgb(*hex_to_rgb("#120D17"))
        ctx.rectangle(0, 0, w, h)
        ctx.fill()

        # nieve: motas chicas sueltas por toda la superficie
        speckle_count = max(150, min(700, (w * h) // 1300))
        for _ in range(speckle_count):
            x = random.uniform(0, w)
            y = random.uniform(0, h)
            size = random.uniform(1, 3)
            color = random.choice(_STATIC_RGB)
            ctx.set_source_rgba(*color, random.uniform(0.10, 0.34))
            ctx.rectangle(x, y, size, size)
            ctx.fill()

        # textura de líneas de barrido tipo CRT, bien tenue y parpadeando
        line_step = 3
        yy = 0
        while yy < h:
            if random.random() < 0.5:
                ctx.set_source_rgba(*hex_to_rgb(ACC_VIOLETA_PROFUNDO), random.uniform(0.03, 0.08))
                ctx.rectangle(0, yy, w, 1)
                ctx.fill()
            yy += line_step

        # de tanto en tanto, una o dos franjas de "corte" de señal, más marcadas
        if random.random() < 0.65:
            for _ in range(random.randint(1, 2)):
                y = random.uniform(0, h)
                band_h = random.uniform(2, 10)
                x_off = random.uniform(-40, 40)
                color = random.choice(_STATIC_RGB)
                ctx.set_source_rgba(*color, random.uniform(0.14, 0.34))
                ctx.rectangle(x_off, y, w, band_h)
                ctx.fill()
        return False


class ColorSwatch(Gtk.DrawingArea):
    """
    Un cuadradito de color, sin más. Lo usa la conversión a GBA para mostrar
    de qué color le tocó a esta conversión en particular — cada una tiene
    el suyo (ver palette.gba_color_for_index).
    """

    def __init__(self, size=16):
        super().__init__()
        self.set_size_request(size, size)
        self._rgb = _GREEN_RGB
        self.connect("draw", self._on_draw)

    def set_color_hex(self, hex_color: str):
        self._rgb = hex_to_rgb(hex_color)
        self.queue_draw()

    def _on_draw(self, widget, ctx):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        ctx.set_source_rgb(*self._rgb)
        ctx.rectangle(1, 1, w - 2, h - 2)
        ctx.fill()
        ctx.set_source_rgba(0, 0, 0, 0.4)
        ctx.set_line_width(1)
        ctx.rectangle(0.5, 0.5, w - 1, h - 1)
        ctx.stroke()
        return False
