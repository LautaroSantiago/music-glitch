"""
Acá vive la ventana principal y toda la orquestación: qué widget
dispara qué acción sobre la base y el motor de audio. Cada pestaña
tiene su método _build_<nombre>_tab() que arma y devuelve el widget
raíz de esa pestaña; los datos se cargan con _refresh_<nombre>()
aparte, para poder refrescar una pestaña sin reconstruirla entera.
"""
import json
import os
import random
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

from app.config import load_config, save_config, DB_FILE, APP_DISPLAY_NAME
from app.database import Database
from app.player_engine import PlayerEngine
from app.ghost_overlay import GhostOverlay
from app.image_utils import get_cover_pixbuf, get_cover_source_pixbuf, pixelate_pixbuf, save_custom_cover
from app.glitch_widgets import PixelWaveform, GlitchBarChart, StatCard, GlitchFlash, TVStaticBackground, ColorSwatch
from app.palette import ACC_LILA, ACC_AMATISTA, ACC_CIRUELA, gba_color_for_index
from app.metadata import _creation_date_str
from app import scanner
from app import chiptune

ICON_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icons", "puma-256.png")

FLEUR = "⚜"


def format_duration(seconds):
    seconds = int(seconds or 0)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="reproductor")
        self.set_default_size(980, 640)
        if os.path.exists(ICON_PATH):
            self.set_icon_from_file(ICON_PATH)

        self.cfg = load_config()
        self.db = Database(DB_FILE)

        self.current_track = None      # fila de sqlite del track cargado en el reproductor
        self.queue = []                # lista de track_id, orden de reproducción actual
        self.queue_pos = -1
        self._seek_lock = False        # evita que el tick de posición pelee con un seek manual
        self.loop_enabled = self.cfg.get("loop_enabled", False)

        # estado de la previsualización "convertir a GBA" (nada de esto se guarda hasta confirmarlo)
        self._gba_preview_path = None
        self._gba_preview_suffix = "gba"
        self._gba_source_track = None
        self._gba_preview_engine = None
        self._gba_preview_playing = False
        self._gba_preview_loaded = False
        self._gba_convert_running = False
        self._gba_button_css_provider = None
        self._gba_preview_color = None
        self._gba_cancel_event = None

        self.player = PlayerEngine(on_eos=self._on_track_end,
                                    on_error=self._on_player_error,
                                    on_level=self._on_level)
        self.player.set_volume(self.cfg.get("volume", 0.8))

        self.ghost = GhostOverlay(on_prev=self.prev_track,
                                   on_play_pause=self.toggle_play_pause,
                                   on_next=self.next_track,
                                   on_position_changed=self._on_ghost_moved)
        if self.cfg.get("ghost_pos_x") is not None:
            self.ghost.place_at(self.cfg["ghost_pos_x"], self.cfg["ghost_pos_y"])
        else:
            self.ghost.place_default()
        self.ghost.set_opacity(self.cfg.get("ghost_opacity", 0.85))

        self._build_headerbar()
        self._build_body()
        self._restore_last_track()

        self.connect("window-state-event", self._on_window_state)
        self.connect("delete-event", self._on_delete_event)
        self.connect("destroy", self._on_destroy)
        GLib.timeout_add(500, self._tick)
        chiptune.warmup()  # precalienta librosa/numba en segundo plano, para que la primera conversión GBA no tarde de más
        GLib.timeout_add(1000, self._update_clock)
        self._update_clock()

    # ============================================================ layout
    def _build_headerbar(self):
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(f"◤ {APP_DISPLAY_NAME} ◢")
        self.header = header
        self.set_titlebar(header)

    def _update_clock(self):
        if not getattr(self, "_alive", True):
            return False
        now = time.localtime()
        fecha = time.strftime("%d/%m/%y", now)
        hora12 = now.tm_hour % 12
        hora12 = 12 if hora12 == 0 else hora12
        ampm = "AM" if now.tm_hour < 12 else "PM"
        self.header.set_subtitle(f"{fecha} · {hora12:02d}:{now.tm_min:02d} {ampm}")
        return True

    def _build_body(self):
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher(stack=self.stack)
        switcher.set_halign(Gtk.Align.CENTER)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.pack_start(switcher, False, False, 8)

        stack_overlay = Gtk.Overlay()
        stack_overlay.add(self.stack)
        self.glitch_flash = GlitchFlash()
        self.glitch_flash.set_hexpand(True)
        self.glitch_flash.set_vexpand(True)
        stack_overlay.add_overlay(self.glitch_flash)
        stack_overlay.set_overlay_pass_through(self.glitch_flash, True)

        outer.pack_start(stack_overlay, True, True, 0)

        # capa de "estática de TV" detrás de todo — se ve en los márgenes/huecos de cada pestaña
        root_overlay = Gtk.Overlay()
        self.tv_static = TVStaticBackground()
        root_overlay.add(self.tv_static)
        root_overlay.add_overlay(outer)
        self.add(root_overlay)

        self.stack.add_titled(self._build_player_tab(), "reproductor", "Reproductor")
        self.stack.add_titled(self._build_library_tab(), "biblioteca", "Biblioteca")
        self.stack.add_titled(self._build_playlists_tab(), "listas", "Listas")
        self.stack.add_titled(self._build_history_tab(), "historial", "Historial")
        self.stack.add_titled(self._build_stats_tab(), "estadisticas", "Estadísticas")
        self.stack.add_titled(self._build_config_tab(), "configuracion", "Configuración")

        self.stack.connect("notify::visible-child-name", self._on_tab_changed)
        self._refresh_library()
        self._refresh_playlists()

    # ---------------------------------------------------------- reproductor
    def _build_player_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(24)
        box.set_margin_bottom(16)
        box.set_margin_start(30)
        box.set_margin_end(30)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        box.pack_start(top, False, False, 0)

        cover_overlay = Gtk.Overlay()
        self.cover_image = Gtk.Image()
        self.cover_image.set_size_request(220, 220)
        cover_overlay.add(self.cover_image)
        change_cover_btn = Gtk.Button(label="cambiar portada")
        change_cover_btn.set_halign(Gtk.Align.END)
        change_cover_btn.set_valign(Gtk.Align.END)
        change_cover_btn.connect("clicked", self._on_change_cover_clicked)
        cover_overlay.add_overlay(change_cover_btn)
        top.pack_start(cover_overlay, False, False, 0)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top.pack_start(info_box, True, True, 0)

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_title = Gtk.Label(label="Ningún tema cargado", xalign=0)
        self.lbl_title.get_style_context().add_class("track-title")
        self.btn_favorite = Gtk.ToggleButton(label=FLEUR)
        self.btn_favorite.get_style_context().add_class("favorite-off")
        self.btn_favorite.connect("toggled", self._on_favorite_toggled)
        title_row.pack_start(self.lbl_title, True, True, 0)
        title_row.pack_start(self.btn_favorite, False, False, 0)
        info_box.pack_start(title_row, False, False, 0)

        self.lbl_artist = Gtk.Label(label="", xalign=0)
        self.lbl_artist.get_style_context().add_class("track-artist")
        info_box.pack_start(self.lbl_artist, False, False, 0)

        self.expander_info = Gtk.Expander(label="(+) Información")
        self.lbl_extra = Gtk.Label(xalign=0)
        self.lbl_extra.get_style_context().add_class("extra-info")
        self.lbl_extra.set_line_wrap(True)
        self.expander_info.add(self.lbl_extra)
        info_box.pack_start(self.expander_info, False, False, 6)

        # barra de progreso + tiempos
        seek_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.lbl_pos = Gtk.Label(label="0:00")
        self.seek_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.seek_scale.set_draw_value(False)
        self.seek_scale.set_hexpand(True)
        self.seek_scale.connect("change-value", self._on_seek)
        self.lbl_dur = Gtk.Label(label="0:00")
        seek_row.pack_start(self.lbl_pos, False, False, 0)
        seek_row.pack_start(self.seek_scale, True, True, 0)
        seek_row.pack_start(self.lbl_dur, False, False, 0)
        box.pack_start(seek_row, False, False, 0)

        # transporte
        transport = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        transport.set_halign(Gtk.Align.CENTER)
        btn_prev = Gtk.Button(label="⏮")
        self.btn_play = Gtk.Button(label="▶")
        self.btn_play.get_style_context().add_class("glitch-primary")
        btn_next = Gtk.Button(label="⏭")
        self.btn_loop = Gtk.ToggleButton(label="🔁")
        self.btn_loop.set_active(self.loop_enabled)
        self.btn_loop.set_tooltip_text("repetir este tema en bucle")
        if self.loop_enabled:
            self.btn_loop.get_style_context().add_class("glitch-primary")
        self.btn_loop.connect("toggled", self._on_loop_toggled)
        btn_prev.connect("clicked", lambda *_: self.prev_track())
        self.btn_play.connect("clicked", lambda *_: self.toggle_play_pause())
        btn_next.connect("clicked", lambda *_: self.next_track())
        transport.pack_start(btn_prev, False, False, 0)
        transport.pack_start(self.btn_play, False, False, 0)
        transport.pack_start(btn_next, False, False, 0)
        transport.pack_start(self.btn_loop, False, False, 0)

        vol_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        vol_box.pack_start(Gtk.Label(label="vol"), False, False, 0)
        self.vol_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.05)
        self.vol_scale.set_value(self.cfg.get("volume", 0.8))
        self.vol_scale.set_size_request(110, -1)
        self.vol_scale.set_draw_value(False)
        self.vol_scale.connect("value-changed", self._on_volume_changed)
        vol_box.pack_start(self.vol_scale, False, False, 0)

        transport_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        transport_row.set_center_widget(transport)
        transport_row.pack_end(vol_box, False, False, 0)
        box.pack_start(transport_row, False, False, 4)

        gba_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        gba_box.set_halign(Gtk.Align.CENTER)

        gba_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.btn_gba = Gtk.Button(label="🎼 GBA fiel a la melodía")
        self.btn_gba.set_tooltip_text(
            "Convierte el tema a chiptune 8-bit priorizando fidelidad: onsets reales y dinámica original"
        )
        self.btn_gba.connect("clicked", lambda *_: self._on_convert_gba_clicked(chiptune.render_gba_preview, "gba"))
        self.btn_gba_arcade = Gtk.Button(label="🕹️ GBA estilo videojuego")
        self.btn_gba_arcade.set_tooltip_text(
            "Convierte el tema con timbres variados, bajo y percusión propios — más groove, menos fidelidad literal"
        )
        self.btn_gba_arcade.connect(
            "clicked", lambda *_: self._on_convert_gba_clicked(chiptune.render_gba_preview_arcade, "gba-arcade")
        )
        self.lbl_gba_status = Gtk.Label(label="")
        self.lbl_gba_status.get_style_context().add_class("extra-info")
        self.btn_gba_cancel = Gtk.Button(label="✖ cancelar")
        self.btn_gba_cancel.set_tooltip_text(
            "Pide cancelar la conversión — hace efecto en el próximo paso, no siempre al instante"
        )
        self.btn_gba_cancel.connect("clicked", self._on_gba_cancel_clicked)
        self.btn_gba_cancel.set_no_show_all(True)
        self.btn_gba_cancel.hide()
        gba_row.pack_start(self.btn_gba, False, False, 0)
        gba_row.pack_start(self.btn_gba_arcade, False, False, 0)
        gba_row.pack_start(self.btn_gba_cancel, False, False, 0)
        gba_row.pack_start(self.lbl_gba_status, False, False, 0)
        gba_box.pack_start(gba_row, False, False, 0)

        # fila de previsualización: oculta hasta que haya una conversión lista para escuchar
        self.gba_preview_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.gba_color_swatch = ColorSwatch()
        self.gba_color_swatch.set_tooltip_text("Color propio de esta conversión")
        self.btn_gba_play = Gtk.Button(label="▶ escuchar")
        self.btn_gba_play.connect("clicked", self._on_gba_preview_play)
        self.btn_gba_save = Gtk.Button(label="💾 guardar en biblioteca")
        self.btn_gba_save.get_style_context().add_class("glitch-primary")
        self.btn_gba_save.connect("clicked", self._on_gba_preview_save)
        self.btn_gba_discard = Gtk.Button(label="✖ descartar")
        self.btn_gba_discard.connect("clicked", self._on_gba_preview_discard)
        self.gba_preview_row.pack_start(self.gba_color_swatch, False, False, 0)
        self.gba_preview_row.pack_start(self.btn_gba_play, False, False, 0)
        self.gba_preview_row.pack_start(self.btn_gba_save, False, False, 0)
        self.gba_preview_row.pack_start(self.btn_gba_discard, False, False, 0)
        # ojo acá: show_all() primero para que los botones queden con visible=True adentro,
        # y recién después hide() del contenedor — así el .show() de más adelante alcanza
        # (un .show() simple NO revela hijos que nunca fueron mostrados al menos una vez)
        self.gba_preview_row.show_all()
        self.gba_preview_row.hide()
        self.gba_preview_row.set_no_show_all(True)  # que el show_all() inicial de toda la ventana no la reabra sola
        gba_box.pack_start(self.gba_preview_row, False, False, 0)

        box.pack_start(gba_box, False, False, 0)

        self.waveform = PixelWaveform()
        box.pack_start(self.waveform, True, True, 10)

        return box

    # ----------------------------------------------------------- biblioteca
    def _build_library_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(14)
        box.set_margin_start(14)
        box.set_margin_end(14)
        box.set_margin_bottom(14)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        scan_btn = Gtk.Button(label="🔍 detectar música")
        scan_btn.get_style_context().add_class("glitch-primary")
        scan_btn.connect("clicked", self._on_scan_clicked)
        self.lbl_scan_status = Gtk.Label(label=f"{self.db.track_count()} temas en biblioteca")
        top_row.pack_start(scan_btn, False, False, 0)
        top_row.pack_start(self.lbl_scan_status, False, False, 8)
        box.pack_start(top_row, False, False, 0)

        self.library_store, tv = self._make_tracks_view(self._on_library_row_activated)
        self.library_tv = tv
        scroller = Gtk.ScrolledWindow()
        scroller.add(tv)
        box.pack_start(scroller, True, True, 0)
        return box

    def _make_tracks_view(self, on_activated):
        """Treeview reusado en Biblioteca y en el panel derecho de Listas."""
        store = Gtk.ListStore(int, str, str, str, str, str, str)  # id, fav, título, artista, álbum, duración, formato
        tv = Gtk.TreeView(model=store)
        tv.connect("row-activated", on_activated)

        col_fav = Gtk.TreeViewColumn(FLEUR, Gtk.CellRendererText(), text=1)
        tv.append_column(col_fav)
        for i, title in enumerate(["Título", "Artista", "Álbum", "Duración", "Formato"], start=2):
            renderer = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, renderer, text=i)
            col.set_resizable(True)
            col.set_sort_column_id(i)
            tv.append_column(col)

        tv.connect("button-press-event", self._on_track_view_click)
        return store, tv

    def _fill_tracks_store(self, store, tracks):
        store.clear()
        for t in tracks:
            fav = FLEUR if self.db.is_favorite(t["id"]) else ""
            store.append([t["id"], fav, t["title"], t["artist"], t["album"],
                          format_duration(t["duration"]), t["format"]])

    def _refresh_library(self):
        tracks = self.db.list_tracks()
        self._fill_tracks_store(self.library_store, tracks)
        self.lbl_scan_status.set_text(f"{self.db.track_count()} temas en biblioteca")

    def _on_library_row_activated(self, tv, path, column):
        model = tv.get_model()
        track_id = model[path][0]
        tracks = self.db.list_tracks()
        self.play_from_queue(tracks, track_id)

    def _on_track_view_click(self, tv, event):
        if event.button != 3:
            return False
        path_info = tv.get_path_at_pos(int(event.x), int(event.y))
        if path_info is None:
            return False
        path, col, cx, cy = path_info
        tv.set_cursor(path)
        track_id = tv.get_model()[path][0]
        self._show_track_context_menu(track_id, event)
        return True

    def _show_track_context_menu(self, track_id, event):
        menu = Gtk.Menu()
        fav_item = Gtk.MenuItem(label="Marcar / desmarcar favorito")
        fav_item.connect("activate", lambda *_: self._toggle_favorite_by_id(track_id))
        menu.append(fav_item)

        add_item = Gtk.MenuItem(label="Agregar a lista de reproducción…")
        add_item.connect("activate", lambda *_: self._prompt_add_to_playlist(track_id))
        menu.append(add_item)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _toggle_favorite_by_id(self, track_id):
        self.db.toggle_favorite(track_id)
        self._refresh_library()
        self._refresh_playlists()
        if self.current_track and self.current_track["id"] == track_id:
            self._sync_favorite_button()

    def _prompt_add_to_playlist(self, track_id):
        dialog = Gtk.Dialog(title="Agregar a lista", transient_for=self, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Agregar", Gtk.ResponseType.OK)
        combo = Gtk.ComboBoxText()
        playlists = [p for p in self.db.list_playlists() if p["kind"] in ("manual", "favoritos")]
        for p in playlists:
            combo.append(str(p["id"]), p["name"])
        combo.set_active(0)
        box = dialog.get_content_area()
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.add(combo)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK and combo.get_active_id() is not None:
            self.db.add_track_to_playlist(int(combo.get_active_id()), track_id)
            self._refresh_playlists()
        dialog.destroy()

    def _on_scan_clicked(self, btn):
        btn.set_sensitive(False)
        self.lbl_scan_status.set_text("escaneando…")
        scanner.run_scan(self._on_scan_progress, lambda added: self._on_scan_done(added, btn))

    def _on_scan_progress(self, found, added, current_path):
        self.lbl_scan_status.set_text(f"revisados {found} · nuevos {added}")
        return False

    def _on_scan_done(self, added, btn):
        self.cfg["last_scan_epoch"] = time.time()
        save_config(self.cfg)
        self._refresh_library()
        self._refresh_playlists()
        btn.set_sensitive(True)
        self.lbl_scan_status.set_text(f"{self.db.track_count()} temas en biblioteca · {added} nuevos recién")
        return False

    # -------------------------------------------------------------- listas
    def _build_playlists_tab(self):
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left.set_margin_top(10)
        left.set_margin_start(10)
        left.set_margin_end(6)

        gen_grid = Gtk.Grid(column_spacing=4, row_spacing=4)
        btn_new = Gtk.Button(label="+ nueva")
        btn_random = Gtk.Button(label="🎲 aleatoria")
        btn_most = Gtk.Button(label="🔥 más escuchados")
        btn_least = Gtk.Button(label="🌙 menos escuchados")
        btn_related = Gtk.Button(label="🔗 mismo autor/álbum")
        btn_new.connect("clicked", self._on_new_playlist_clicked)
        btn_random.connect("clicked", self._on_generate_random)
        btn_most.connect("clicked", self._on_generate_most_played)
        btn_least.connect("clicked", self._on_generate_least_played)
        btn_related.connect("clicked", self._on_generate_related)
        for i, b in enumerate([btn_new, btn_random, btn_most, btn_least, btn_related]):
            gen_grid.attach(b, i % 2, i // 2, 1, 1)
        left.pack_start(gen_grid, False, False, 0)

        self.playlists_store = Gtk.ListStore(int, str, str, int)  # id, nombre, kind, n_tracks
        self.playlists_tv = Gtk.TreeView(model=self.playlists_store)
        col = Gtk.TreeViewColumn("Lista", Gtk.CellRendererText(), text=1)
        self.playlists_tv.append_column(col)
        col2 = Gtk.TreeViewColumn("Temas", Gtk.CellRendererText(), text=3)
        self.playlists_tv.append_column(col2)
        self.playlists_tv.get_selection().connect("changed", self._on_playlist_selected)
        self.playlists_tv.connect("button-press-event", self._on_playlist_view_click)
        pl_scroller = Gtk.ScrolledWindow()
        pl_scroller.add(self.playlists_tv)
        left.pack_start(pl_scroller, True, True, 0)
        paned.pack1(left, resize=False, shrink=False)
        left.set_size_request(260, -1)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.set_margin_top(10)
        right.set_margin_end(10)
        right.set_margin_start(6)
        self.lbl_playlist_name = Gtk.Label(label="Elegí una lista a la izquierda", xalign=0)
        self.lbl_playlist_name.get_style_context().add_class("track-title")
        right.pack_start(self.lbl_playlist_name, False, False, 4)
        self.playlist_tracks_store, self.playlist_tracks_tv = self._make_tracks_view(self._on_playlist_row_activated)
        pt_scroller = Gtk.ScrolledWindow()
        pt_scroller.add(self.playlist_tracks_tv)
        right.pack_start(pt_scroller, True, True, 0)
        paned.pack2(right, resize=True, shrink=False)

        self._selected_playlist_id = None
        return paned

    def _refresh_playlists(self):
        self.playlists_store.clear()
        for p in self.db.list_playlists():
            name = f"{FLEUR} {p['name']}" if p["kind"] == "favoritos" else p["name"]
            self.playlists_store.append([p["id"], name, p["kind"], p["n_tracks"]])
        if self._selected_playlist_id is not None:
            self._load_playlist_tracks(self._selected_playlist_id)

    def _on_playlist_selected(self, selection):
        model, treeiter = selection.get_selected()
        if treeiter is None:
            return
        playlist_id = model[treeiter][0]
        self._selected_playlist_id = playlist_id
        self.lbl_playlist_name.set_text(model[treeiter][1])
        self._load_playlist_tracks(playlist_id)

    def _load_playlist_tracks(self, playlist_id):
        tracks = self.db.playlist_tracks(playlist_id)
        self._fill_tracks_store(self.playlist_tracks_store, tracks)

    def _on_playlist_row_activated(self, tv, path, column):
        track_id = tv.get_model()[path][0]
        tracks = self.db.playlist_tracks(self._selected_playlist_id)
        self.play_from_queue(tracks, track_id)

    def _on_playlist_view_click(self, tv, event):
        if event.button != 3:
            return False
        path_info = tv.get_path_at_pos(int(event.x), int(event.y))
        if path_info is None:
            return False
        path = path_info[0]
        playlist_id = tv.get_model()[path][0]
        kind = tv.get_model()[path][2]
        menu = Gtk.Menu()
        if kind not in ("favoritos",):
            del_item = Gtk.MenuItem(label="Eliminar lista")
            del_item.connect("activate", lambda *_: self._delete_playlist(playlist_id))
            menu.append(del_item)
            menu.show_all()
            menu.popup_at_pointer(event)
        return True

    def _delete_playlist(self, playlist_id):
        self.db.delete_playlist(playlist_id)
        self._selected_playlist_id = None
        self._refresh_playlists()

    def _on_new_playlist_clicked(self, btn):
        dialog = Gtk.Dialog(title="Nueva lista", transient_for=self, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, "Crear", Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_placeholder_text("nombre de la lista")
        box = dialog.get_content_area()
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.add(entry)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK and entry.get_text().strip():
            self.db.create_playlist(entry.get_text().strip(), kind="manual")
            self._refresh_playlists()
        dialog.destroy()

    def _on_generate_random(self, btn):
        all_tracks = self.db.list_tracks()
        if not all_tracks:
            return
        self.glitch_flash.flash(frames=6)
        ids = [t["id"] for t in all_tracks]
        random.shuffle(ids)
        pid = self.db.get_or_create_playlist("Aleatoria", kind="generada")
        self.db.replace_playlist_tracks(pid, ids[:60])
        self._refresh_playlists()

    def _on_generate_most_played(self, btn):
        self.glitch_flash.flash(frames=4)
        tracks = self.db.tracks_most_played(60)
        pid = self.db.get_or_create_playlist("Más escuchados", kind="generada")
        self.db.replace_playlist_tracks(pid, [t["id"] for t in tracks])
        self._refresh_playlists()

    def _on_generate_least_played(self, btn):
        self.glitch_flash.flash(frames=4)
        tracks = self.db.tracks_least_played(60)
        pid = self.db.get_or_create_playlist("Menos escuchados", kind="generada")
        self.db.replace_playlist_tracks(pid, [t["id"] for t in tracks])
        self._refresh_playlists()

    def _on_generate_related(self, btn):
        if self.current_track is None:
            self._info_dialog("Reproducí o seleccioná primero un tema para generar la lista relacionada.")
            return
        related = self.db.tracks_same_artist_or_album(self.current_track["id"])
        if not related:
            self._info_dialog("No hay otros temas del mismo autor o álbum en la biblioteca.")
            return
        self.glitch_flash.flash(frames=4)
        name = f"Como: {self.current_track['title'][:30]}"
        pid = self.db.get_or_create_playlist(name, kind="generada")
        self.db.replace_playlist_tracks(pid, [t["id"] for t in related])
        self._refresh_playlists()

    def _info_dialog(self, text):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
                                    buttons=Gtk.ButtonsType.OK, text=text)
        dialog.run()
        dialog.destroy()

    # ------------------------------------------------------------ historial
    def _build_history_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(14)
        box.set_margin_start(14)
        box.set_margin_end(14)
        box.set_margin_bottom(14)

        top_row = Gtk.Box(spacing=8)
        clear_btn = Gtk.Button(label="limpiar historial")
        clear_btn.connect("clicked", self._on_clear_history)
        top_row.pack_start(clear_btn, False, False, 0)
        box.pack_start(top_row, False, False, 0)

        self.history_store = Gtk.ListStore(int, str, str, str, str)  # id, cuándo, título, artista, álbum
        tv = Gtk.TreeView(model=self.history_store)
        for i, title in enumerate(["Reproducido", "Título", "Artista", "Álbum"], start=1):
            col = Gtk.TreeViewColumn(title, Gtk.CellRendererText(), text=i)
            col.set_resizable(True)
            tv.append_column(col)
        tv.connect("row-activated", self._on_history_row_activated)
        self.history_tv = tv
        scroller = Gtk.ScrolledWindow()
        scroller.add(tv)
        box.pack_start(scroller, True, True, 0)
        return box

    def _refresh_history(self):
        self.history_store.clear()
        for row in self.db.recent_history():
            when = time.strftime("%d/%m %H:%M", time.localtime(row["played_at"]))
            self.history_store.append([row["id"], when, row["title"], row["artist"], row["album"]])

    def _on_history_row_activated(self, tv, path, column):
        track_id = tv.get_model()[path][0]
        track = self.db.get_track(track_id)
        if track:
            self.play_from_queue([track], track_id)

    def _on_clear_history(self, btn):
        self.db.clear_history()
        self._refresh_history()

    # --------------------------------------------------------- estadísticas
    def _build_stats_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(14)
        box.set_margin_start(14)
        box.set_margin_end(14)
        box.set_margin_bottom(14)

        cards_row = Gtk.Box(spacing=10)
        self.card_tracks = StatCard("temas en biblioteca", "0", ACC_LILA)
        self.card_plays = StatCard("reproducciones totales", "0", ACC_AMATISTA)
        self.card_time = StatCard("tiempo escuchado", "0", ACC_CIRUELA)
        self.card_playlists = StatCard("listas creadas", "0", ACC_LILA)
        for c in (self.card_tracks, self.card_plays, self.card_time, self.card_playlists):
            cards_row.pack_start(c, True, True, 0)
        box.pack_start(cards_row, False, False, 0)

        charts_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.chart_plays = GlitchBarChart("temas más reproducidos")
        self.chart_time = GlitchBarChart("temas con más tiempo escuchado")
        self.chart_artists = GlitchBarChart("artistas más escuchados")
        for c in (self.chart_plays, self.chart_time, self.chart_artists):
            charts_row.pack_start(c, True, True, 0)
        box.pack_start(charts_row, True, True, 0)

        refresh_btn = Gtk.Button(label="↻ actualizar estadísticas")
        refresh_btn.connect("clicked", lambda *_: self._refresh_stats())
        box.pack_start(refresh_btn, False, False, 0)
        return box

    def _refresh_stats(self):
        summary = self.db.stats_summary()
        self.card_tracks.set_value(str(summary["n_tracks"]))
        self.card_plays.set_value(str(summary["total_plays"]))
        self.card_time.set_value(format_duration(summary["total_seconds"]))
        self.card_playlists.set_value(str(summary["n_playlists"]))

        self.chart_plays.set_data([(f"{r['title']} — {r['artist']}", r["play_count"])
                                    for r in self.db.top_tracks_by_plays()])
        self.chart_time.set_data([(f"{r['title']} — {r['artist']}", r["total_seconds_played"])
                                   for r in self.db.top_tracks_by_time()])
        self.chart_artists.set_data([(r["artist"], r["plays"]) for r in self.db.top_artists()])

    # -------------------------------------------------------- configuración
    def _build_config_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(20)
        box.set_margin_start(20)
        box.set_margin_end(20)

        grid = Gtk.Grid(column_spacing=12, row_spacing=14)
        box.pack_start(grid, False, False, 0)

        grid.attach(Gtk.Label(label="Interfaz fantasma (al minimizar)", xalign=0), 0, 0, 1, 1)
        self.switch_ghost = Gtk.Switch()
        self.switch_ghost.set_active(self.cfg.get("ghost_enabled", True))
        self.switch_ghost.connect("notify::active", self._on_ghost_switch)
        self.switch_ghost.set_halign(Gtk.Align.START)
        grid.attach(self.switch_ghost, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Transparencia del overlay", xalign=0), 0, 1, 1, 1)
        self.opacity_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.25, 1.0, 0.05)
        self.opacity_scale.set_value(self.cfg.get("ghost_opacity", 0.85))
        self.opacity_scale.set_size_request(200, -1)
        self.opacity_scale.connect("value-changed", self._on_opacity_changed)
        grid.attach(self.opacity_scale, 1, 1, 1, 1)

        reset_pos_btn = Gtk.Button(label="restablecer posición del overlay")
        reset_pos_btn.connect("clicked", self._on_reset_ghost_position)
        grid.attach(reset_pos_btn, 0, 2, 2, 1)

        self.lbl_config_info = Gtk.Label(xalign=0)
        self.lbl_config_info.get_style_context().add_class("extra-info")
        box.pack_start(self.lbl_config_info, False, False, 10)
        self._update_config_info_label()

        return box

    def _update_config_info_label(self):
        last_scan = self.cfg.get("last_scan_epoch")
        last_scan_str = time.strftime("%d/%m/%Y %H:%M", time.localtime(last_scan)) if last_scan else "nunca"
        self.lbl_config_info.set_text(
            f"último escaneo: {last_scan_str}   ·   {self.db.track_count()} temas   ·   "
            f"{len(self.db.list_playlists())} listas"
        )

    def _on_ghost_switch(self, switch, _pspec):
        self.cfg["ghost_enabled"] = switch.get_active()
        save_config(self.cfg)
        if not switch.get_active():
            self.ghost.hide()

    def _on_opacity_changed(self, scale):
        value = scale.get_value()
        self.cfg["ghost_opacity"] = value
        save_config(self.cfg)
        self.ghost.set_opacity(value)

    def _on_reset_ghost_position(self, btn):
        self.cfg["ghost_pos_x"] = None
        self.cfg["ghost_pos_y"] = None
        save_config(self.cfg)
        self.ghost.place_default()

    def _on_ghost_moved(self, x, y):
        self.cfg["ghost_pos_x"] = x
        self.cfg["ghost_pos_y"] = y
        save_config(self.cfg)

    # ============================================================ tabs sync
    def _on_tab_changed(self, stack, _pspec):
        self.glitch_flash.flash()
        name = stack.get_visible_child_name()
        if name == "historial":
            self._refresh_history()
        elif name == "estadisticas":
            self._refresh_stats()
        elif name == "configuracion":
            self._update_config_info_label()

    # ============================================================ playback
    def play_from_queue(self, tracks, track_id):
        self.queue = [t["id"] for t in tracks]
        self.queue_pos = self.queue.index(track_id)
        self._load_and_play(track_id)

    def _load_and_play(self, track_id):
        track = self.db.get_track(track_id)
        if track is None:
            return
        self._stop_gba_preview_playback()  # que no queden dos audios sonando a la vez
        self.glitch_flash.flash(frames=5)
        self.current_track = track
        self.player.load(track["path"])
        self.player.play()
        self.db.register_play_start(track_id)
        self._render_player_info(track)
        self.btn_play.set_label("⏸")
        self.ghost.set_play_glyph(True)
        self.ghost.set_track_label(f"{track['title']} — {track['artist']}")
        self._sync_favorite_button()

    def toggle_play_pause(self):
        if self.current_track is None:
            if self.queue:
                self._load_and_play(self.queue[max(self.queue_pos, 0)])
            return
        if self.player.is_playing():
            self.player.pause()
            self.btn_play.set_label("▶")
            self.ghost.set_play_glyph(False)
        else:
            self.player.play()
            self.btn_play.set_label("⏸")
            self.ghost.set_play_glyph(True)

    def next_track(self):
        if not self.queue:
            return
        self.queue_pos = (self.queue_pos + 1) % len(self.queue)
        self._load_and_play(self.queue[self.queue_pos])

    def prev_track(self):
        if not self.queue:
            return
        self.queue_pos = (self.queue_pos - 1) % len(self.queue)
        self._load_and_play(self.queue[self.queue_pos])

    def _on_track_end(self):
        if self.loop_enabled and self.current_track is not None:
            GLib.idle_add(self._replay_current)
        else:
            GLib.idle_add(self.next_track)

    def _replay_current(self):
        self._load_and_play(self.current_track["id"])
        return False

    def _on_loop_toggled(self, btn):
        self.loop_enabled = btn.get_active()
        self.cfg["loop_enabled"] = self.loop_enabled
        save_config(self.cfg)
        ctx = btn.get_style_context()
        if self.loop_enabled:
            ctx.add_class("glitch-primary")
        else:
            ctx.remove_class("glitch-primary")

    def _on_player_error(self, err, debug):
        GLib.idle_add(self._info_dialog, f"No se pudo reproducir el archivo: {err}")

    def _on_level(self, value):
        GLib.idle_add(self.waveform.push_level, value)

    def _render_player_info(self, track):
        self.lbl_title.set_text(track["title"])
        self.lbl_artist.set_text(track["artist"] + (f" · {track['album']}" if track["album"] else ""))
        self.lbl_dur.set_text(format_duration(track["duration"]))
        self.seek_scale.set_range(0, max(1, track["duration"]))

        extra = json.loads(track["extra_json"] or "{}")
        extra_lines = [f"{k}: {v}" for k, v in extra.items()]
        extra_lines.append(f"Formato: {track['format']}")
        self.lbl_extra.set_text("\n".join(extra_lines))

        pixbuf_source = get_cover_source_pixbuf(track, 220)
        self._animate_cover_reveal(pixbuf_source)

    def _animate_cover_reveal(self, source_pixbuf):
        """Transición glitch al cambiar de tema: arranca bien bloqueado y va afinando hasta el pixelado final."""
        final_block = max(2, self.cfg.get("pixelate_block", 10))
        steps = [final_block * 5, final_block * 3, final_block * 2, final_block]
        self._cover_anim_token = getattr(self, "_cover_anim_token", 0) + 1
        token = self._cover_anim_token

        def _step(i=0):
            if not getattr(self, "_alive", True) or token != self._cover_anim_token:
                return False  # ya se cargó otro tema encima, o se cerró la ventana: se aborta la animación
            block = steps[min(i, len(steps) - 1)]
            self.cover_image.set_from_pixbuf(pixelate_pixbuf(source_pixbuf, block))
            if i < len(steps) - 1:
                GLib.timeout_add(55, lambda: _step(i + 1))
            return False

        _step()

    def _sync_favorite_button(self):
        is_fav = self.db.is_favorite(self.current_track["id"])
        self.btn_favorite.handler_block_by_func(self._on_favorite_toggled)
        self.btn_favorite.set_active(is_fav)
        self.btn_favorite.handler_unblock_by_func(self._on_favorite_toggled)
        ctx = self.btn_favorite.get_style_context()
        ctx.remove_class("favorite-on" if not is_fav else "favorite-off")
        ctx.add_class("favorite-on" if is_fav else "favorite-off")

    def _on_favorite_toggled(self, btn):
        if self.current_track is None:
            return
        self.glitch_flash.flash(frames=3)
        self.db.toggle_favorite(self.current_track["id"])
        self._sync_favorite_button()
        self._refresh_library()
        self._refresh_playlists()

    def _on_change_cover_clicked(self, btn):
        if self.current_track is None:
            self._info_dialog("Primero elegí un tema para cambiarle la portada.")
            return
        dialog = Gtk.FileChooserDialog(title="Elegir imagen", transient_for=self,
                                        action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        img_filter = Gtk.FileFilter()
        img_filter.set_name("Imágenes")
        img_filter.add_mime_type("image/png")
        img_filter.add_mime_type("image/jpeg")
        img_filter.add_mime_type("image/webp")
        dialog.add_filter(img_filter)
        if dialog.run() == Gtk.ResponseType.OK:
            path = save_custom_cover(self.current_track["id"], dialog.get_filename())
            self.db.set_custom_cover(self.current_track["id"], path)
            self.current_track = self.db.get_track(self.current_track["id"])
            self._render_player_info(self.current_track)
        dialog.destroy()

    def _stop_gba_preview_playback(self):
        if self._gba_preview_engine is not None and self._gba_preview_playing:
            self._gba_preview_engine.pause()
        self._gba_preview_playing = False
        self.btn_gba_play.set_label("▶ escuchar")

    def _tint_gba_buttons(self, hex_color):
        """Repinta los botones de la previsualización con el color propio de esta conversión."""
        css = (
            f"button.gba-tinted {{ border-color: {hex_color}; box-shadow: 0 0 0 1px {hex_color}; }}\n"
            f"button.gba-tinted:hover {{ background-color: {hex_color}; color: #0c0f0c; }}"
        ).encode("utf-8")
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        for btn in (self.btn_gba_play, self.btn_gba_save, self.btn_gba_discard):
            ctx = btn.get_style_context()
            if self._gba_button_css_provider is not None:
                ctx.remove_provider(self._gba_button_css_provider)
            ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            ctx.add_class("gba-tinted")
        self._gba_button_css_provider = provider

    def _on_convert_gba_clicked(self, render_fn, suffix):
        if self.current_track is None:
            self._info_dialog("Primero elegí o reproducí un tema para convertirlo.")
            return
        # si había una previsualización sin guardar de otro tema, se detiene y se descarta
        self._stop_gba_preview_playback()
        self._discard_gba_preview_file()
        self.btn_gba.set_sensitive(False)
        self.btn_gba_arcade.set_sensitive(False)
        self.gba_preview_row.hide()
        self.btn_gba_cancel.show()
        track = self.current_track

        # cada conversión tiene su propio color, recorriendo la paleta del proyecto en
        # degradé — se genera acá (antes de saber siquiera si esto se va a guardar) para
        # que la previsualización ya lo muestre
        self.cfg["gba_color_index"] = self.cfg.get("gba_color_index", 0) + 1
        save_config(self.cfg)
        self._gba_preview_color = gba_color_for_index(self.cfg["gba_color_index"])
        self.gba_color_swatch.set_color_hex(self._gba_preview_color)
        self._tint_gba_buttons(self._gba_preview_color)

        # ticker de "sigo vivo": una conversión de calidad puede tardar varios minutos en
        # un tema largo, y sin esto la interfaz parece colgada aunque esté trabajando bien
        self._gba_convert_start = time.time()
        self._gba_convert_running = True

        def _tick_status():
            if not self._gba_convert_running or not getattr(self, "_alive", True):
                return False
            elapsed = int(time.time() - self._gba_convert_start)
            mins, secs = divmod(elapsed, 60)
            tiempo = f"{mins}:{secs:02d}"
            self.lbl_gba_status.set_text(f"analizando y generando el chip… ({tiempo}, puede tardar varios minutos)")
            return True

        _tick_status()
        GLib.timeout_add(1000, _tick_status)

        def _done(preview_path):
            self._gba_convert_running = False
            self.btn_gba_cancel.hide()
            if not getattr(self, "_alive", True):
                return False
            self.btn_gba.set_sensitive(True)
            self.btn_gba_arcade.set_sensitive(True)
            self.glitch_flash.flash(frames=6)
            self._gba_preview_path = preview_path
            self._gba_preview_suffix = suffix
            self._gba_source_track = track
            self.lbl_gba_status.set_text("previsualización lista — escuchala antes de guardarla")
            self.gba_preview_row.show()
            return False

        def _error(message):
            self._gba_convert_running = False
            self.btn_gba_cancel.hide()
            if not getattr(self, "_alive", True):
                return False
            self.btn_gba.set_sensitive(True)
            self.btn_gba_arcade.set_sensitive(True)
            if message == "cancelado":
                self.lbl_gba_status.set_text("conversión cancelada")
            else:
                self.lbl_gba_status.set_text("no se pudo convertir")
                self._info_dialog(f"No se pudo convertir el tema a GBA: {message}")
            return False

        self._gba_cancel_event = render_fn(track["path"], _done, _error)

    def _on_gba_cancel_clicked(self, btn):
        if getattr(self, "_gba_cancel_event", None) is not None:
            self._gba_cancel_event.set()
        self.btn_gba_cancel.set_sensitive(False)
        self.lbl_gba_status.set_text("cancelando… (puede tardar un momento en frenar)")
        GLib.timeout_add(300, lambda: (self.btn_gba_cancel.set_sensitive(True), False)[1])


    def _get_gba_preview_engine(self):
        if self._gba_preview_engine is None:
            self._gba_preview_engine = PlayerEngine(
                on_eos=self._on_gba_preview_eos, on_error=lambda e, d: None, on_level=lambda v: None
            )
        return self._gba_preview_engine

    def _on_gba_preview_play(self, btn):
        if not self._gba_preview_path:
            return
        engine = self._get_gba_preview_engine()
        if self._gba_preview_playing:
            engine.pause()
            self._gba_preview_playing = False
            self.btn_gba_play.set_label("▶ escuchar")
        else:
            if self.player.is_playing():  # que no suenen el tema principal y la preview juntos
                self.player.pause()
                self.btn_play.set_label("▶")
                self.ghost.set_play_glyph(False)
            if not self._gba_preview_loaded:
                engine.load(self._gba_preview_path)
                self._gba_preview_loaded = True
            engine.play()
            self._gba_preview_playing = True
            self.btn_gba_play.set_label("⏸ pausar")

    def _on_gba_preview_eos(self):
        def _reset():
            self._gba_preview_playing = False
            self._gba_preview_loaded = False
            self.btn_gba_play.set_label("▶ escuchar")
            return False
        GLib.idle_add(_reset)

    def _on_gba_preview_save(self, btn):
        if not self._gba_preview_path:
            return
        self._stop_gba_preview_playback()
        if self._gba_preview_engine is not None:
            self._gba_preview_engine.stop()
        self._gba_preview_loaded = False

        dest_path = chiptune.keep_gba_file(
            self._gba_preview_path, self._gba_source_track["title"], suffix=self._gba_preview_suffix
        )
        self._register_gba_track(self._gba_source_track, dest_path)
        self.glitch_flash.flash(frames=6)
        self.lbl_gba_status.set_text(f"guardado en la biblioteca: {os.path.basename(dest_path)}")
        self._gba_preview_path = None
        self._gba_source_track = None
        self.gba_preview_row.hide()

    def _on_gba_preview_discard(self, btn):
        self._stop_gba_preview_playback()
        if self._gba_preview_engine is not None:
            self._gba_preview_engine.stop()
        self._gba_preview_loaded = False
        self._discard_gba_preview_file()
        self.lbl_gba_status.set_text("previsualización descartada")
        self.gba_preview_row.hide()

    def _discard_gba_preview_file(self):
        if self._gba_preview_path:
            chiptune.discard_gba_file(self._gba_preview_path)
        self._gba_preview_path = None
        self._gba_source_track = None

    def _register_gba_track(self, original_track, dest_path):
        """Da de alta el WAV convertido como un track más, y lo agrupa en una lista aparte."""
        etiqueta = "GBA arcade" if self._gba_preview_suffix == "gba-arcade" else "GBA"
        meta_title = f"{original_track['title']} ({etiqueta})"
        extra = {
            "Origen": original_track["title"],
            "Modo": "estilo videojuego (timbres + bajo + percusión)" if etiqueta == "GBA arcade" else "fiel a la melodía",
            "Creado": _creation_date_str(dest_path),
        }
        track_id, _ = self.db.upsert_track(
            path=dest_path, title=meta_title, artist=original_track["artist"],
            album=original_track["album"], duration=original_track["duration"],
            fmt="WAV", extra_json=json.dumps(extra, ensure_ascii=False),
            has_embedded_cover=False, assigned_emoji="🕹️", accent_color=self._gba_preview_color,
        )
        playlist_id = self.db.get_or_create_playlist("Game Boy Advance", kind="generada")
        self.db.add_track_to_playlist(playlist_id, track_id)
        self._refresh_library()
        self._refresh_playlists()

    def _on_seek(self, scale, scroll, value):
        self.player.seek(value)
        return False

    def _on_volume_changed(self, scale):
        value = scale.get_value()
        self.player.set_volume(value)
        self.cfg["volume"] = value
        save_config(self.cfg)

    def _tick(self):
        if not getattr(self, "_alive", True):
            return False
        if self.current_track and self.player.is_playing():
            pos = self.player.get_position_seconds()
            self.lbl_pos.set_text(format_duration(pos))
            self.seek_scale.handler_block_by_func(self._on_seek)
            self.seek_scale.set_value(pos)
            self.seek_scale.handler_unblock_by_func(self._on_seek)
            self.db.add_listened_seconds(self.current_track["id"], 0.5)
        return True

    # ============================================================ ventana
    def _on_window_state(self, widget, event):
        iconified = bool(event.new_window_state & Gdk.WindowState.ICONIFIED)
        if iconified and self.cfg.get("ghost_enabled", True):
            self.ghost.show_all()
        else:
            self.ghost.hide()

    def _on_delete_event(self, *_args):
        """Se dispara al cerrar la ventana (antes del destroy): acá es donde persiste qué estaba sonando."""
        if self.current_track is not None:
            self.cfg["last_track_id"] = self.current_track["id"]
            self.cfg["last_position_seconds"] = self.player.get_position_seconds()
            save_config(self.cfg)
        return False  # deja que el cierre siga su curso normal (dispara destroy)

    def _on_destroy(self, *_args):
        self._alive = False
        self._discard_gba_preview_file()
        if self._gba_preview_engine is not None:
            self._gba_preview_engine.stop()
        self.player.stop()
        self.db.close()

    # ------------------------------------------------------ retomar sesión
    def _restore_last_track(self):
        """Deja cargado (en pausa) el tema que estaba sonando la última vez que se cerró el programa."""
        last_id = self.cfg.get("last_track_id")
        if last_id is None:
            return
        track = self.db.get_track(last_id)
        if track is None:
            return
        self.current_track = track
        self.queue = [track["id"]]
        self.queue_pos = 0
        self.player.load(track["path"])
        self.player.pause()  # precarga el pipeline sin arrancar a sonar solo
        position = self.cfg.get("last_position_seconds", 0.0) or 0.0
        GLib.timeout_add(200, lambda: (self.player.seek(position), False)[1])
        self._render_player_info(track)
        self.lbl_pos.set_text(format_duration(position))
        self.seek_scale.set_value(position)
        self.btn_play.set_label("▶")
        self.ghost.set_play_glyph(False)
        self.ghost.set_track_label(f"{track['title']} — {track['artist']}")
        self._sync_favorite_button()
