<div align="center">

# 🐾 music-glitch

**Reproductor de música de escritorio para Linux Mint, con estética glitch / roto digital**

![Python](https://img.shields.io/badge/Python-3.12-86BE43?style=flat-square&logo=python&logoColor=white)
![GTK](https://img.shields.io/badge/GTK-3-9966CC?style=flat-square&logo=gtk&logoColor=white)
![GStreamer](https://img.shields.io/badge/GStreamer-1.0-8E4585?style=flat-square)
![SQLite](https://img.shields.io/badge/SQLite-3-9C8AA4?style=flat-square&logo=sqlite&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Linux%20Mint-9C8AA4?style=flat-square&logo=linuxmint&logoColor=white)
![License](https://img.shields.io/badge/Licencia-MIT-86BE43?style=flat-square)

</div>

---

## Índice

- [Qué es](#qué-es)
- [Captura](#captura)
- [Estética](#estética)
- [Funcionalidades](#funcionalidades)
- [Conversión a chiptune/GBA](#conversión-a-chiptunegba)
- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Uso](#uso)
- [Datos y configuración](#datos-y-configuración)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Autor](#autor)
- [Licencia](#licencia)

---

## Qué es

**music-glitch** es un reproductor de música local para Linux Mint (funciona
en cualquier entorno con GTK 3, en general). Reproduce los formatos de audio
más comunes, arma tu biblioteca escaneando tus propias carpetas, organiza
listas de reproducción, lleva estadísticas de qué escuchás más y le suma a
todo eso una identidad visual propia: interferencia tipo señal de TV,
pixel-art y transiciones glitch en vez de la interfaz plana de siempre.

No depende de servicios externos ni de internet: todo (biblioteca, listas,
historial, estadísticas) se guarda localmente en una base SQLite en tu propia
máquina.

## Captura

<!-- Reemplazá esta imagen por una captura real: sacá el pantallazo del
     programa corriendo, guardalo como docs/captura.png y dejá esta misma línea. -->
<p align="center">
  <img src="docs/captura.png" alt="Captura de music-glitch" width="720">
</p>

## Estética

La paleta combina el verde característico de Linux Mint con tres acentos
violetas propios de este proyecto:

| Color | Nombre | Hex |
|---|---|---|
| 🟩 | Verde Mint | `#86BE43` |
| 🟪 | Lila polvoriento | `#9C8AA4` |
| 🟣 | Amatista | `#9966CC` |
| 🟤 | Ciruela | `#8E4585` |

Sobre esa base:

- **Fondo con interferencia de señal**: una capa animada de "nieve" y
  líneas de barrido estilo TV sin señal, en tonos violeta, corre todo el
  tiempo detrás de la interfaz.
- **Flashes glitch en las transiciones**: cambiar de pestaña, cambiar de
  tema, marcar un favorito o generar una lista dispara un destello corto
  de franjas de color.
- **Portadas y visualizador en pixel-art**: las portadas se pixelan
  siempre (incluidas las generadas automáticamente con un emoji), y las
  ondas de audio en vivo se dibujan en bloques, no como una curva lisa.
- **Ícono propio**: cara de puma de frente, dibujada a mano en pixel-art
  con la paleta del proyecto.
- **Tipografía monoespaciada** en toda la interfaz, para reforzar el look
  "de terminal/consola retro".

## Funcionalidades

| | |
|---|---|
| 🎧 **Formatos** | MP3, AAC, OGG, FLAC, ALAC, WAV, AIFF (decodificados por GStreamer) |
| 📁 **Detección automática** | escanea tu carpeta personal y arma una lista por cada carpeta que contiene música |
| 🖼️ **Portadas** | arte embebido, o generada con emoji + degradé si el tema no tiene; se puede reemplazar por una imagen propia |
| ⚜️ **Favoritos** | marcado con flor de lis, con lista automática "Favoritos" |
| 🎲 **Generadores de listas** | aleatoria · más escuchados · menos escuchados · mismo autor/álbum |
| 🔁 **Bucle** | repetición del tema actual con un toque |
| ⏱️ **Retoma la sesión** | al reabrir, carga en pausa el tema (y el segundo exacto) donde quedó al cerrar |
| 📊 **Estadísticas** | reproducciones, tiempo escuchado y artistas top, con gráficos propios |
| 👻 **Interfaz fantasma** | overlay transparente con controles al minimizar; se mueve con Ctrl + click, opacidad ajustable |
| 🌈 **Visualizador** | ondas de audio en vivo en pixel-art |
| 🎼🕹️ **Conversión a chiptune/GBA** | dos modos — ver detalle abajo |

## Conversión a chiptune/GBA

Convierte cualquier tema en una versión resintetizada tipo consola portátil
vieja — no es un filtro de baja calidad sobre el audio original, es un mini
"audio a MIDI a chip": se analiza la melodía, el bajo y la percusión reales
por separado y se vuelven a sintetizar desde cero, cada uno como su propio
canal (con margen de volumen entre ellos, para que un golpe de batería no
quede tapado por el pulso de la melodía). Siempre genera antes una
previsualización temporal (escuchala, después decidís si la guardás o la
descartás — nada se suma a la biblioteca sin que lo confirmes).

Cada conversión recibe su propio color, recorriendo en degradé toda la
paleta del proyecto (nunca se repiten los mismos 3-4 tonos de siempre) —
se ve en un cuadradito junto a los botones de previsualización, y si la
guardás, ese mismo color pasa a ser el de la portada del tema en tu
biblioteca.

> El modo "fiel a la melodía" analiza con `pyin` (motor de tono probabilístico
> de librosa). Para un tema de 3-4 minutos suele tardar cerca de un minuto y
> usar poco más de 1GB de RAM. Mientras convierte, el estado va marcando el
> tiempo transcurrido para que quede claro que sigue trabajando (no que se
> colgó), y hay un botón "cancelar" si te querés bajar — pide la cancelación
> en el próximo paso del análisis, no siempre al instante.

**🎼 GBA fiel a la melodía**
- Separa la voz (lo que está centrado en la mezcla estéreo) de los
  instrumentos paneados a los costados
- Aísla la parte armónica de la percusiva, para que un golpe de batería no
  rompa una nota sostenida en pedazos
- Detecta el tono con pyin (versión probabilística de YIN, con seguimiento
  tipo Viterbi entre ventanas — más precisa que un YIN simple) y corrige
  saltos de octava sueltos
- Ancla el inicio de cada nota al ataque real del audio (onset detection)
- Sigue la dinámica (volumen) real del tema original en vez de dejar todas
  las notas parejas
- Suma bajo y percusión resintetizados como canales aparte (mismo detalle
  que el modo de abajo), con la melodía siempre en un único timbre de pulso

**🕹️ GBA estilo videojuego**
- Todo lo anterior, pero con más variedad y menos apego literal al original:
- Timbres alternados: pulso 12.5% / 25% / 50% + onda triangular en el
  registro grave, en vez de un único timbre fijo
- Bajo resintetizado como canal aparte (una octava abajo, onda triangular)
- Percusión propia: detecta golpes reales del tema y les mete ráfagas de
  ruido, distinguiendo graves ("bombo") de agudos ("redoblante/hi-hat")
- Vibrato en notas sostenidas, para que no suenen planas

## Requisitos

- Linux con GTK 3 (pensado y probado en Linux Mint)
- Python 3.10 o superior
- GStreamer 1.0 con los plugins base/good/bad/ugly + libav
- Python: [`mutagen`](https://mutagen.readthedocs.io/) (metadata),
  [`numpy`](https://numpy.org/) y [`scipy`](https://scipy.org/) (procesamiento
  de audio) y [`librosa`](https://librosa.org/) (análisis de melodía) para la
  conversión a chiptune/GBA

## Instalación

```bash
# dependencias del sistema
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
    gir1.2-gstreamer-1.0 gir1.2-gdkpixbuf-2.0 python3-cairo \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav fonts-noto-color-emoji

# dependencias de Python
pip install -r requirements.txt --break-system-packages
```

> `gstreamer1.0-libav` es el que habilita AAC y ALAC (van dentro de
> contenedor MP4/.m4a); sin él esos dos formatos no decodifican.
> `fonts-noto-color-emoji` es la fuente que usa el generador de
> portadas cuando un tema no tiene arte embebido. `librosa`/`scipy` sólo
> hacen falta para los botones de conversión a GBA — el resto del programa
> funciona igual sin ellos, salvo esos dos botones.

## Uso

```bash
git clone https://github.com/LautaroSantiago/music-glitch.git
cd music-glitch
python3 main.py
```

Para tener un lanzador de escritorio (y no depender de una terminal
abierta), corré `./install.sh`: arma el ícono en el menú de aplicaciones
resolviendo la ruta del proyecto automáticamente.

**Atajos y controles:**

- `Ctrl` + click sostenido sobre la interfaz fantasma → moverla de lugar
- Click en la flor de lis del reproductor → marcar/desmarcar favorito
- Botón 🔁 → repetir el tema actual en bucle

## Datos y configuración

Todo queda en tu propia máquina, nada se sube a ningún lado:

| Qué | Dónde |
|---|---|
| Biblioteca, listas, historial, estadísticas | `~/.local/share/music-glitch/biblioteca.db` (SQLite) |
| Configuración (interfaz fantasma, volumen, etc.) | `~/.config/music-glitch/config.json` |
| Portadas elegidas a mano | `~/.local/share/music-glitch/portadas/` |
| Conversiones a GBA guardadas | `~/.local/share/music-glitch/gba/` |

## Estructura del proyecto

```
music-glitch/
├── main.py                    # arranque de la app
├── install.sh                  # instala el lanzador de escritorio (ícono + menú de apps)
├── requirements.txt             # dependencias de Python
├── assets/icons/                 # ícono (puma pixel-art) en varios tamaños
└── app/
    ├── config.py                  # rutas de datos/config y persistencia del config.json
    ├── database.py                 # esquema SQLite y todas las queries (tracks, listas, historial, estadísticas)
    ├── metadata.py                  # lectura de tags y portada embebida con mutagen
    ├── scanner.py                    # escaneo del home en un hilo aparte + armado de listas por carpeta
    ├── player_engine.py               # wrapper sobre GStreamer (playbin): reproducción y nivel de audio en vivo
    ├── chiptune.py                     # conversión a chiptune/GBA: separación de voz/armónico/percusivo, YIN, síntesis
    ├── image_utils.py                  # portadas: arte embebido / generada con emoji / pixelado / guardado de portada propia
    ├── icon_gen.py                      # generación por código del ícono (cara de puma) en pixel-art
    ├── ghost_overlay.py                  # interfaz fantasma: overlay flotante con transporte
    ├── glitch_widgets.py                  # widgets Cairo a medida: visualizador, gráficos de estadísticas, flashes y fondo de estática
    ├── palette.py                          # paleta de colores del proyecto
    ├── theme.py / style.css                 # tema GTK de la aplicación
    └── main_window.py                        # ventana principal: arma y conecta todas las pestañas
```

## Autor

**Lautaro Subeldia**
Estudiante de la Tecnicatura Universitaria en Programación — UTN FRA

[![GitHub](https://img.shields.io/badge/GitHub-LautaroSantiago-181717?style=flat-square&logo=github)](https://github.com/LautaroSantiago)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-lautaro--subeldia-0A66C2?style=flat-square&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/lautaro-subeldia/)

## Licencia

Distribuido bajo licencia [MIT](LICENSE).
