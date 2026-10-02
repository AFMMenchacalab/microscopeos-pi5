"""Arma el artefacto de demo a partir de la pagina real (/ui).

La pagina no se modifica: se le quitan las etiquetas de documento (el
visor pone las suyas), se le agrega el aviso de demo con la guia rapida
y dos scripts que reemplazan al servidor por un microscopio simulado.
"""
import re
import sys
from pathlib import Path

aqui = Path(__file__).parent
src = Path(sys.argv[1]).read_text()

# Fuera las etiquetas de documento y metas: el visor pone su esqueleto.
for patron in (r"<!DOCTYPE html>\s*", r"<html[^>]*>\s*", r"</html>\s*",
               r"<head>\s*", r"</head>\s*", r"<body>\s*", r"</body>\s*",
               r"<meta [^>]*>\s*", r'<link rel="icon"[^>]*>\s*'):
    src = re.sub(patron, "", src)

css_demo = """
  :root{color-scheme:dark}
  .demo{max-width:1400px;margin:0 auto var(--sp-4);border:1px solid var(--green);border-radius:var(--radius);
    background:var(--green-soft);padding:var(--sp-3) var(--sp-4);display:grid;gap:10px;
    grid-template-columns:1fr auto;align-items:start}
  .demo h2{font-size:15px;color:var(--text);margin-bottom:2px}
  .demo p{color:var(--dim);font-size:13px;max-width:70ch}
  .demo ol{display:flex;flex-wrap:wrap;gap:8px 22px;list-style:none;margin-top:8px;font-size:13px;color:var(--text)}
  .demo ol li{display:flex;align-items:center;gap:6px}
  .demo ol b{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;
    background:var(--green);color:#04130a;font-size:11.5px}
  @media(max-width:640px){.demo{grid-template-columns:1fr}}
"""
src = src.replace("</style>", css_demo + "</style>", 1)

aviso = """<section class="demo" aria-label="Aviso de demo">
  <div>
    <h2>Demo con un microscopio simulado</h2>
    <p>Así se ve la página <b>/ui</b> del microscopio. Los botones mueven un motor de mentira y la imagen
    se enfoca y desenfoca como la real. Nada de esto toca el equipo.</p>
    <p style="margin-top:6px">Prueba: enciende la luz, toca «Enfocar automáticamente», o muévete con
    <kbd>W</kbd> <kbd>S</kbd> (cambia el paso con <kbd>A</kbd> <kbd>D</kbd>). Toca una imagen para agrandarla.</p>
  </div>
  <button id="demoReset"><svg class="ic" aria-hidden="true"><use href="#i-refresh"/></svg><span class="lbl">Desenfocar todo</span></button>
</section>
"""
# justo antes del encabezado de la pagina
src = src.replace('<div class="head">', aviso + '<div class="head">', 1)

mock = (aqui / "demo_mock.js").read_text()
post = (aqui / "demo_post.js").read_text()
# el simulador va ANTES del script de la pagina; los ajustes, despues
i = src.index("<script>")
src = src[:i] + "<script>\n" + mock + "\n</script>\n" + src[i:]
j = src.rindex("</script>") + len("</script>")
src = src[:j] + "\n<script>\n" + post + "\n</script>\n" + src[j:]

Path(sys.argv[2]).write_text(src)
print("ok", len(src), "bytes")
