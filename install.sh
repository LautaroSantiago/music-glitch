#!/bin/bash
# Instala el lanzador de music-glitch en el menú de aplicaciones,
# resolviendo automáticamente la ruta absoluta donde está este script
# (no hace falta editar nada a mano).
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="$HOME/.local/share/applications"
DEST_FILE="$DEST_DIR/music-glitch.desktop"

mkdir -p "$DEST_DIR"

cat > "$DEST_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=music-glitch
Comment=Reproductor de música con estética glitch
Exec=python3 "$PROJECT_DIR/main.py"
Icon=$PROJECT_DIR/assets/icons/puma-256.png
Path=$PROJECT_DIR
Terminal=false
Categories=AudioVideo;Audio;Player;
EOF

chmod +x "$DEST_FILE"

# también lo dejamos en el escritorio si existe la carpeta, para tenerlo a mano
if [ -d "$HOME/Escritorio" ]; then
    cp "$DEST_FILE" "$HOME/Escritorio/"
    chmod +x "$HOME/Escritorio/music-glitch.desktop"
elif [ -d "$HOME/Desktop" ]; then
    cp "$DEST_FILE" "$HOME/Desktop/"
    chmod +x "$HOME/Desktop/music-glitch.desktop"
fi

echo "Listo. music-glitch ya debería aparecer en el menú de aplicaciones."
echo "Si no aparece al toque, cerrá sesión y volvé a entrar (o corré: xdg-desktop-menu forceupdate)."
