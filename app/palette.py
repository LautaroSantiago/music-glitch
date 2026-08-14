"""
Paleta única de este proyecto. El verde es el que usamos siempre en los
programas de Mint; los tres acentos (lila, amatista, ciruela) son
exclusivos de este reproductor, no van a otros programas.
"""

MINT_GREEN = "#86BE43"
MINT_GREEN_DARK = "#5C8A2C"

ACC_LILA = "#9C8AA4"
ACC_AMATISTA = "#9966CC"
ACC_CIRUELA = "#8E4585"

ACCENTS = (ACC_LILA, ACC_AMATISTA, ACC_CIRUELA)

# variantes extra dentro de la misma gama violeta, para el ruido de fondo tipo estática de TV
ACC_VIOLETA_PROFUNDO = "#5B3A6E"
ACC_LAVANDA = "#C9B8DA"
ACC_VIOLETA_SOMBRA = "#241A2E"
STATIC_VIOLETS = (ACC_LILA, ACC_AMATISTA, ACC_CIRUELA, ACC_VIOLETA_PROFUNDO, ACC_LAVANDA, ACC_VIOLETA_SOMBRA)

BG_BASE = "#111411"
BG_PANEL = "#181c17"
FG_TEXT = "#E8EDE4"
FG_MUTED = "#8B968B"


def hex_to_rgb(hex_color: str):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def accent_for_id(numeric_id: int) -> str:
    """Elige un acento de forma estable para un track/playlist según su id, sin volver a tirar el dado cada vez."""
    return ACCENTS[numeric_id % len(ACCENTS)]


# ------------------------------------------------------- colores por conversión GBA
# recorrido de la paleta del proyecto en orden (verde -> lila -> amatista -> ciruela ->
# violeta profundo), para poder generar más colores "de paso" que los 4 nombrados sin
# salirse nunca de la misma gama — así ninguno desentona con el resto de la interfaz.
_GBA_COLOR_ANCHORS = (MINT_GREEN, ACC_LAVANDA, ACC_LILA, ACC_AMATISTA, ACC_CIRUELA, ACC_VIOLETA_PROFUNDO)
GBA_COLOR_STEPS = 18  # cuántos colores distintos hay para repartir entre conversiones


def _lerp_hex(color_a: str, color_b: str, t: float) -> str:
    ra, ga, ba = hex_to_rgb(color_a)
    rb, gb, bb = hex_to_rgb(color_b)
    r = ra + (rb - ra) * t
    g = ga + (gb - ga) * t
    b = ba + (bb - ba) * t
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))


def gba_color_for_index(index: int) -> str:
    """Un color distinto por cada conversión a GBA (índice creciente), recorriendo
    en degradé toda la paleta del proyecto en vez de repetir siempre los mismos 3-4 tonos."""
    t = (index % GBA_COLOR_STEPS) / GBA_COLOR_STEPS
    n = len(_GBA_COLOR_ANCHORS) - 1
    scaled = t * n
    i = min(int(scaled), n - 1)
    frac = scaled - i
    return _lerp_hex(_GBA_COLOR_ANCHORS[i], _GBA_COLOR_ANCHORS[i + 1], frac)
