# Instructivo de uso

`Instructivo_MicroscopeOS.pdf` es el instructivo para imprimir (carta, 10 páginas), pensado para alguien que nunca usó el microscopio. Cubre la página `/ui`: encender la luz, ver la muestra, enfocar (con botones, con el teclado o de forma automática), tomar fotos y hacer un timelapse. Incluye una tabla de problemas y una tarjeta para recortar y tener junto al microscopio.

## Cómo regenerarlo si cambia la interfaz

Todo está en `fuente/`. Requiere Python 3 y Node con Playwright (Chromium).

1. Arma la demo con el microscopio simulado a partir de la página real:

   ```
   cd fuente
   python3 demo/construir.py ../../../codigo/MicroscopeOS/server/static/index_uiux.html demo/microscopeos_demo.html
   ```

2. Toma las capturas en tema claro. `capturas.mjs` abre `ui/microscopeos_demo.html` y guarda en `manual/img/`; ajusta esas dos rutas a `demo/` e `img/` antes de correrlo:

   ```
   node capturas.mjs
   ```

3. Genera el PDF desde `instructivo.html` (abre las imágenes de `img/` y las tipografías de `fonts/`):

   ```
   node pdf.mjs
   ```

Las ilustraciones (siluetas) son SVG escritos dentro de `instructivo.html`. Si cambian los nombres de botones o secciones de la página, actualiza también el texto del instructivo.
