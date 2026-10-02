"""Boton de actualizar: core/actualizar.py contra repositorios git reales.

Arma un "GitHub" falso (repositorio bare), una copia de desarrollo que
sube versiones y una copia "Pi" donde corre el programa. No usa red.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.join(AQUI, "..", "codigo", "MicroscopeOS"))

from core import actualizar as A

ok = fail = 0
def check(nombre, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"  PASS  {nombre}")
    else:    fail += 1; print(f"  FAIL  {nombre}  {extra}")

def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()

tmp = Path(tempfile.mkdtemp(prefix="actualizar_"))
os.environ.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                  GIT_COMMITTER_EMAIL="t@t")
remoto = tmp / "github.git"
git(tmp, "init", "-q", "--bare", "-b", "master", str(remoto))
dev = tmp / "dev"
git(tmp, "clone", "-q", str(remoto), str(dev))
git(dev, "checkout", "-q", "-b", "master")
codigo = dev / "codigo" / "MicroscopeOS"
for d in ("core", "server", "profiles"):
    (codigo / d).mkdir(parents=True)
(codigo / "core" / "x.py").write_text("VERSION = 1\n")
(codigo / "server" / "api.py").write_text("pass\n")
(codigo / "run_web.py").write_text("pass\n")
(codigo / "config.json").write_text('{"exposure": 12000}\n')
(codigo / "profiles" / "default.json").write_text('{"a": 1}\n')
git(dev, "add", "-A"); git(dev, "commit", "-q", "-m", "Primera version")
git(dev, "push", "-q", "-u", "origin", "master")

pi = tmp / "pi"
git(tmp, "clone", "-q", str(remoto), str(pi))
pi_cod = pi / "codigo" / "MicroscopeOS"
act = A.Actualizador(raiz=pi_cod)

print("\n=== VERSION Y REVISAR ===")
v = act.version()
check("version instalada", v["git"] and v["rama"] == "master" and v["titulo"] == "Primera version", v)
r = act.revisar(forzar=True)
check("sin cambios: no hay nueva", r["hay_nueva"] is False and r["n_nuevos"] == 0, r)
check("una copia sin git lo dice", "error" in A.Actualizador(raiz=tmp).revisar(forzar=True))

def subir(texto, titulo, archivo="core/x.py"):
    (codigo / archivo).write_text(texto)
    git(dev, "commit", "-q", "-am", titulo)
    git(dev, "push", "-q", "origin", "master")

subir("VERSION = 2\n", "Autofoco mejor")
subir("VERSION = 3\n", "Luz mas rapida")
check("el resultado se guarda un rato (no pregunta a GitHub cada vez)", act.revisar()["hay_nueva"] is False)
r = act.revisar(forzar=True)
check("detecta la version nueva con sus cambios", r["hay_nueva"] and r["n_nuevos"] == 2
      and [c["titulo"] for c in r["nuevos"]] == ["Luz mas rapida", "Autofoco mejor"], r)

print("\n=== ACTUALIZAR SIN PERDER NADA DE LA PI ===")
(pi_cod / "config.json").write_text('{"exposure": 30000}\n')          # ajuste del usuario
(pi_cod / "profiles" / "default.json").write_text('{"a": 99}\n')
(pi_cod / "server" / "api.py").write_text("pass  # arreglo a mano\n")   # codigo tocado
(pi_cod / "datos").mkdir()
(pi_cod / "datos" / "foto.tif").write_text("TIF")                      # sin versionar
git(pi, "commit", "-q", "--allow-empty", "-m", "commit solo en la Pi")
r = act.actualizar()
check("actualiza a la ultima", r["actualizado"] and (pi_cod / "core" / "x.py").read_text() == "VERSION = 3\n", r)
check("los ajustes del usuario se conservan", (pi_cod / "config.json").read_text() == '{"exposure": 30000}\n'
      and (pi_cod / "profiles" / "default.json").read_text() == '{"a": 99}\n')
check("las fotos siguen ahi", (pi_cod / "datos" / "foto.tif").read_text() == "TIF")
stash = git(pi, "stash", "list")
check("el codigo tocado a mano queda guardado en un stash", "antes de actualizar" in stash
      and (pi_cod / "server" / "api.py").read_text() == "pass\n", stash)
git(pi, "stash", "pop")
check("y se puede recuperar", "arreglo a mano" in (pi_cod / "server" / "api.py").read_text())
git(pi, "checkout", "-q", "--", "codigo/MicroscopeOS/server/api.py")
ramas = git(pi, "branch", "--list", "respaldo/*")
check("el commit que solo estaba en la Pi queda en una rama de respaldo", "respaldo/" in ramas
      and "commit solo en la Pi" in git(pi, "log", "-1", "--format=%s", ramas.strip()), ramas)
check("queda en master igual que GitHub", git(pi, "rev-parse", "HEAD") == git(dev, "rev-parse", "HEAD"))
check("con todo al dia avisa y no hace nada", act.actualizar()["actualizado"] is False)

print("\n=== SI LA VERSION NUEVA ESTA ROTA, NO SE INSTALA ===")
antes = git(pi, "rev-parse", "HEAD")
subir("def roto(:\n", "Version con error")
r = None
try:
    act.actualizar()
except A.ErrorActualizar as e:
    r = str(e)
check("avisa del problema", r and "anterior" in r, r)
check("y se queda en la version que funcionaba", git(pi, "rev-parse", "HEAD") == antes
      and (pi_cod / "core" / "x.py").read_text() == "VERSION = 3\n")
check("con los ajustes del usuario intactos", (pi_cod / "config.json").read_text() == '{"exposure": 30000}\n')

print("\n=== OTRA RAMA EN LA PI ===")
subir("VERSION = 4\n", "Arreglo")
git(pi, "checkout", "-q", "-b", "claude/pruebas")
r = act.actualizar()
check("desde otra rama tambien vuelve a master actualizado", r["actualizado"]
      and git(pi, "rev-parse", "--abbrev-ref", "HEAD") == "master"
      and (pi_cod / "core" / "x.py").read_text() == "VERSION = 4\n", r)

print("\n=== API ===")
import types, emuladores  # noqa: E402
emuladores.instalar()
from fastapi.testclient import TestClient  # noqa: E402
from server.api import create_app  # noqa: E402
import server.api as _api  # noqa: E402

class TL:
    corriendo = False
    def is_running(self): return self.corriendo
class Cam:
    gain = 1.2
    def stop(self): pass
reinicios = []
_api.reiniciar = lambda f=None: reinicios.append(f)
tl = TL()
subir("VERSION = 5\n", "Ultima")
cl = TestClient(create_app(Cam(), {}, tl, actualizador=act))
check("GET /api/version", cl.get("/api/version").json()["commit"] == git(pi, "rev-parse", "--short", "HEAD"))
check("GET /api/actualizacion avisa", cl.get("/api/actualizacion", params={"forzar": True}).json()["hay_nueva"])
tl.corriendo = True
check("no actualiza con un timelapse en curso", "error" in cl.post("/api/actualizar").json() and not reinicios)
tl.corriendo = False
r = cl.post("/api/actualizar").json()
check("actualiza y pide reiniciar", r.get("actualizado") and len(reinicios) == 1, r)
r = cl.post("/api/actualizar").json()
check("sin nada nuevo no reinicia", r.get("actualizado") is False and len(reinicios) == 1, r)

print("\n" + "=" * 50)
print(f"PASS: {ok}   FAIL: {fail}")
print("=" * 50)
sys.exit(1 if fail else 0)
