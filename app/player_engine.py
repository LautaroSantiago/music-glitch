"""
Wrapper chico sobre un playbin de GStreamer. No sabe nada de playlists
ni de qué tema sigue: sólo reproduce una URI y avisa (por callbacks)
cuándo terminó un tema, cuándo hay un nivel de audio nuevo para el
visualizador de pixel art, o cuándo algo se rompió.

El elemento 'level' se engancha como audio-filter del playbin para
poder leer picos de señal en tiempo real sin tener que decodificar
el audio nosotros mismos.
"""
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

Gst.init(None)


class PlayerEngine:
    def __init__(self, on_eos=None, on_error=None, on_level=None):
        self.on_eos = on_eos
        self.on_error = on_error
        self.on_level = on_level

        self.playbin = Gst.ElementFactory.make("playbin", "reproductor")
        if self.playbin is None:
            raise RuntimeError("GStreamer no tiene disponible el elemento 'playbin'")

        level = Gst.ElementFactory.make("level", "nivel")
        convert = Gst.ElementFactory.make("audioconvert", "conv")
        sink = Gst.ElementFactory.make("autoaudiosink", "salida")
        audio_bin = Gst.Bin.new("audio-filter-bin")
        for el in (level, convert, sink):
            audio_bin.add(el)
        level.link(convert)
        convert.link(sink)
        audio_bin.add_pad(Gst.GhostPad.new("sink", level.get_static_pad("sink")))
        # el bin level -> audioconvert -> autoaudiosink reemplaza al audio-sink por defecto,
        # así los mensajes 'level' del bus quedan disponibles para el visualizador
        self.playbin.set_property("audio-sink", audio_bin)

        bus = self.playbin.get_bus()
        bus.add_signal_watch()
        bus.connect("message::eos", self._on_bus_eos)
        bus.connect("message::error", self._on_bus_error)
        bus.connect("message::element", self._on_bus_element)

        self._duration_ns = None

    # ------------------------------------------------------------- carga
    def load(self, path: str):
        self.stop()
        uri = Gst.filename_to_uri(path)
        self.playbin.set_property("uri", uri)
        self._duration_ns = None

    def play(self):
        self.playbin.set_state(Gst.State.PLAYING)

    def pause(self):
        self.playbin.set_state(Gst.State.PAUSED)

    def stop(self):
        self.playbin.set_state(Gst.State.NULL)

    def is_playing(self) -> bool:
        ok, state, _ = self.playbin.get_state(0)
        return state == Gst.State.PLAYING

    # --------------------------------------------------------- transporte
    def seek(self, seconds: float):
        self.playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                                  int(seconds * Gst.SECOND))

    def set_volume(self, value: float):
        self.playbin.set_property("volume", max(0.0, min(1.0, value)))

    def get_position_seconds(self) -> float:
        ok, pos = self.playbin.query_position(Gst.Format.TIME)
        return pos / Gst.SECOND if ok else 0.0

    def get_duration_seconds(self) -> float:
        ok, dur = self.playbin.query_duration(Gst.Format.TIME)
        return dur / Gst.SECOND if ok else 0.0

    # ---------------------------------------------------------- bus/señal
    def _on_bus_eos(self, _bus, _msg):
        if self.on_eos:
            self.on_eos()

    def _on_bus_error(self, _bus, msg):
        err, debug = msg.parse_error()
        if self.on_error:
            self.on_error(str(err), debug)

    def _on_bus_element(self, _bus, msg):
        structure = msg.get_structure()
        if structure is None or structure.get_name() != "level":
            return
        peaks = structure.get_value("peak")
        if not peaks:
            return
        # 'peak' viene en dB (negativo, 0 = pico máximo); lo normalizamos a 0..1 para dibujar
        peak_db = max(peaks)
        normalized = max(0.0, min(1.0, (peak_db + 60) / 60))
        if self.on_level:
            self.on_level(normalized)
