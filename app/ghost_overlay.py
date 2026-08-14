"""
La "interfaz fantasma" es una ventana GTK aparte, sin decoración,
siempre encima, que no roba el foco del teclado. Aparece cuando se
minimiza la ventana principal y desaparece cuando se vuelve a abrir.

Mover la ventana es a mano: no usamos begin_move_drag() del window
manager porque en ventanas sin decorar algunos gestores de Marco/Mate
lo ignoran. Guardamos el offset del click y vamos moviendo la ventana
nosotros mismos mientras el botón sigue apretado y Ctrl está activo.
"""
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo

MARGIN = 18


class MarqueeLabel(Gtk.DrawingArea):
    """Si el texto entra, se muestra quieto. Si no entra, se desplaza en loop."""

    def __init__(self, width=190):
        super().__init__()
        self.set_size_request(width, 18)
        self._text = ""
        self._offset = 0
        self._layout = None
        self.connect("draw", self._on_draw)
        GLib.timeout_add(80, self._tick)

    def set_text(self, text):
        self._text = text or ""
        self._offset = 0
        self.queue_draw()

    def _tick(self):
        w = self.get_allocated_width()
        if self._layout is not None:
            text_w, _ = self._layout.get_pixel_size()
            if text_w > w:
                self._offset = (self._offset + 1) % (text_w + 30)
                self.queue_draw()
        return True

    def _on_draw(self, widget, ctx):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        layout = widget.create_pango_layout(self._text)
        layout.set_font_description(Pango.FontDescription("Share Tech Mono 9"))
        self._layout = layout
        text_w, text_h = layout.get_pixel_size()

        ctx.set_source_rgb(0.91, 0.93, 0.89)
        if text_w <= w:
            ctx.move_to(0, (h - text_h) / 2)
            PangoCairo.show_layout(ctx, layout)
        else:
            # dos copias seguidas para que el loop no se note en el corte
            y = (h - text_h) / 2
            ctx.move_to(-self._offset, y)
            PangoCairo.show_layout(ctx, layout)
            ctx.move_to(-self._offset + text_w + 30, y)
            PangoCairo.show_layout(ctx, layout)
        return False


class GhostOverlay(Gtk.Window):
    def __init__(self, on_prev, on_play_pause, on_next, on_position_changed):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.on_position_changed = on_position_changed
        self._dragging = False
        self._drag_offset = (0, 0)

        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_resizable(False)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)
        self.set_app_paintable(True)

        self.get_style_context().add_class("ghost-overlay")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(10)
        box.set_margin_end(10)
        self.add(box)

        self.btn_prev = self._transport_button("⏮", on_prev)
        self.btn_play = self._transport_button("⏯", on_play_pause)
        self.btn_next = self._transport_button("⏭", on_next)
        box.pack_start(self.btn_prev, False, False, 0)
        box.pack_start(self.btn_play, False, False, 0)
        box.pack_start(self.btn_next, False, False, 0)

        self.marquee = MarqueeLabel()
        self.marquee.get_style_context().add_class("ghost-label")
        box.pack_start(self.marquee, True, True, 4)

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                         | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("motion-notify-event", self._on_motion)

    def _transport_button(self, glyph, callback):
        btn = Gtk.Button(label=glyph)
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.connect("clicked", lambda *_: callback())
        return btn

    # --------------------------------------------------------- posición
    def place_default(self):
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        area = monitor.get_workarea()
        self.move(area.x + MARGIN, area.y + area.height - 70)

    def place_at(self, x, y):
        self.move(x, y)

    # --------------------------------------------------------- arrastre
    def _on_button_press(self, widget, event):
        if event.button == 1 and (event.state & Gdk.ModifierType.CONTROL_MASK):
            self._dragging = True
            win_x, win_y = self.get_position()
            self._drag_offset = (event.x_root - win_x, event.y_root - win_y)
            return True
        return False

    def _on_motion(self, widget, event):
        if self._dragging:
            new_x = event.x_root - self._drag_offset[0]
            new_y = event.y_root - self._drag_offset[1]
            self.move(int(new_x), int(new_y))
        return False

    def _on_button_release(self, widget, event):
        if self._dragging:
            self._dragging = False
            x, y = self.get_position()
            self.on_position_changed(x, y)
        return False

    # --------------------------------------------------------- contenido
    def set_track_label(self, text):
        self.marquee.set_text(text)

    def set_play_glyph(self, playing: bool):
        self.btn_play.set_label("⏸" if playing else "▶")
