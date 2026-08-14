#!/usr/bin/env python3
"""
Reproductor de música para Linux Mint, estética glitch/roto digital.
Uso: python3 main.py   (ver README.md para dependencias del sistema)
"""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from app.theme import apply_theme
from app.main_window import MainWindow


class MusicPlayerApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="ar.lautaro.musicglitch")

    def do_activate(self):
        apply_theme()
        win = MainWindow(self)
        win.show_all()


if __name__ == "__main__":
    MusicPlayerApp().run()
