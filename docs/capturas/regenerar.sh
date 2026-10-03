#!/usr/bin/env bash
# Vuelve a tomar las capturas del README cuando cambia la interfaz.
#
# Usa el mismo microscopio simulado que el instructivo
# (docs/instructivo/fuente/demo): no hace falta la Pi ni el hardware.
# Requiere Python 3 con Pillow, Node con Playwright (Chromium) y
# pdftoppm (poppler-utils).
#
#   docs/capturas/regenerar.sh
set -euo pipefail

aqui="$(cd "$(dirname "$0")" && pwd)"
raiz="$aqui/../.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# 1. Página /ui con el microscopio simulado
python3 "$raiz/docs/instructivo/fuente/demo/construir.py" \
    "$raiz/codigo/MicroscopeOS/server/static/index_uiux.html" "$tmp/demo.html"

# 2. Capturas de la página
node "$aqui/capturas.mjs" "$tmp/demo.html" "$aqui"

# 3. Mosaico con cuatro páginas del instructivo (portada, luz, foco, tarjeta)
pdftoppm -r 80 -png "$raiz/docs/instructivo/Instructivo_MicroscopeOS.pdf" "$tmp/pag"
python3 - "$tmp" "$aqui/instructivo_paginas.png" <<'EOF'
import sys
from PIL import Image, ImageDraw
tmp, destino = sys.argv[1], sys.argv[2]
pags = [Image.open(f"{tmp}/pag-{n:02d}.png").convert("RGB") for n in (1, 4, 5, 12)]
w, h = pags[0].size
g = 28
out = Image.new("RGB", (len(pags) * w + (len(pags) + 1) * g, h + 2 * g), (226, 230, 236))
d = ImageDraw.Draw(out)
for i, im in enumerate(pags):
    x = g + i * (w + g)
    d.rectangle([x + 4, g + 4, x + w + 4, g + h + 4], fill=(200, 205, 212))
    out.paste(im, (x, g))
    d.rectangle([x - 1, g - 1, x + w, g + h], outline=(190, 195, 202))
out.save(destino, optimize=True)
EOF
echo "Capturas en $aqui"
