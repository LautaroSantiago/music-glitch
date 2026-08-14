import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

STYLE_PATH = os.path.join(os.path.dirname(__file__), "style.css")


def apply_theme():
    provider = Gtk.CssProvider()
    provider.load_from_path(STYLE_PATH)
    screen = Gdk.Screen.get_default()
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
