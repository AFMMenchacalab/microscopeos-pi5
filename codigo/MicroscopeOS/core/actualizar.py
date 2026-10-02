"""Actualizar MicroscopeOS desde GitHub con un boton.

La Pi tiene el repositorio clonado (README: ~/MicroscopeOS). Actualizar es
traer la rama principal (master) y reiniciar el servidor, pero sin perder
nada de lo que solo esta en la Pi:

- Fotos y experimentos (datos/) y los ajustes que el programa crea
  (profiles/*.json nuevos) no estan en git: git no los toca.
- Ajustes que SI estan en git y el programa reescribe (config.json,
  perfiles guardados en profiles/): se guarda su contenido, se actualiza
  y se vuelve a escribir. Gana siempre lo de la Pi.
- Cambios hechos a mano en el codigo: se guardan con `git stash` (nada se
  borra; `git stash list` los muestra) antes de actualizar. Ya paso una
  vez que hubo arreglos que solo vivian en la Pi.
- Commits locales que no estan en GitHub: quedan en una rama
  respaldo/AAAAMMDD-HHMM antes de mover master.

Si el codigo nuevo no compila, se vuelve a la version anterior.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

RAIZ_CODIGO = Path(__file__).resolve().parent.parent   # codigo/MicroscopeOS
RAMA = os.environ.get("MICROSCOPEOS_RAMA", "master")
REMOTO = "origin"
REVISAR_CADA_S = 30 * 60      # no preguntar a GitHub mas seguido que esto
# Archivos versionados que el programa reescribe con ajustes del usuario,
# relativos a codigo/MicroscopeOS.
AJUSTES_USUARIO = ("config.json", "profiles/")


class ErrorActualizar(Exception):
    pass


class Actualizador:
    def __init__(self, raiz=RAIZ_CODIGO, rama=RAMA, remoto=REMOTO):
        self.raiz_codigo = Path(raiz)
        self.rama = rama
        self.remoto = remoto
        self._lock = threading.Lock()
        self._ultima = None          # (time.time(), resultado de revisar)
        self.repo = None
        try:
            self.repo = Path(self._git("rev-parse", "--show-toplevel",
                                       cwd=self.raiz_codigo))
        except ErrorActualizar:
            pass

    # ---------------- git ----------------
    def _git(self, *args, cwd=None, timeout=60):
        try:
            r = subprocess.run(["git", *args], cwd=cwd or self.repo,
                               capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ErrorActualizar(f"git {args[0]}: {e}")
        if r.returncode != 0:
            raise ErrorActualizar((r.stderr or r.stdout).strip()
                                  or f"git {args[0]} fallo")
        return r.stdout.strip()

    def _commit(self, ref):
        sha, fecha, titulo = self._git(
            "log", "-1", "--format=%h%x1f%cI%x1f%s", ref).split("\x1f")
        return {"commit": sha, "fecha": fecha, "titulo": titulo}

    def _remota(self):
        return f"{self.remoto}/{self.rama}"

    # ---------------- consultas ----------------
    def version(self):
        """La version instalada (sin red)."""
        if self.repo is None:
            return {"git": False}
        try:
            return {"git": True, "rama": self._git("rev-parse", "--abbrev-ref", "HEAD"),
                    **self._commit("HEAD")}
        except ErrorActualizar as e:
            return {"git": False, "error": str(e)}

    def _cambios_locales(self):
        """Archivos versionados modificados en la Pi, separados en
        ajustes del usuario y codigo tocado a mano."""
        prefijo = os.path.relpath(self.raiz_codigo, self.repo).replace(os.sep, "/")
        prefijo = "" if prefijo == "." else prefijo + "/"
        ajustes, codigo = [], []
        # Rutas relativas a la raiz del repo, sin comillas aunque tengan
        # espacios ("profiles/Alex prueba 1.json").
        salida = self._git("-c", "core.quotePath=false", "diff", "--name-only", "HEAD")
        for ruta in salida.splitlines():
            rel = ruta[len(prefijo):] if ruta.startswith(prefijo) else None
            if rel is not None and any(rel == a or (a.endswith("/") and rel.startswith(a))
                                       for a in AJUSTES_USUARIO):
                ajustes.append(ruta)
            else:
                codigo.append(ruta)
        return ajustes, codigo

    def revisar(self, forzar=False):
        """Pregunta a GitHub si hay version nueva. Guarda el resultado
        REVISAR_CADA_S para que abrir la pagina no haga un fetch cada vez."""
        if not forzar and self._ultima and time.time() - self._ultima[0] < REVISAR_CADA_S:
            return self._ultima[1]
        actual = self.version()
        if not actual.get("git"):
            return {**actual, "hay_nueva": False,
                    "error": actual.get("error") or "Esta copia no se instalo con git"}
        try:
            self._git("fetch", "--quiet", self.remoto, self.rama, timeout=45)
        except ErrorActualizar as e:
            return {**actual, "hay_nueva": False,
                    "error": "No se pudo revisar (¿hay internet?)", "detalle": str(e)}
        remota = self._remota()
        nuevos = self._git("log", "--format=%h%x1f%cI%x1f%s", f"HEAD..{remota}")
        cambios = [dict(zip(("commit", "fecha", "titulo"), l.split("\x1f")))
                   for l in nuevos.splitlines() if l]
        locales = int(self._git("rev-list", "--count", f"{remota}..HEAD") or 0)
        ajustes, codigo = self._cambios_locales()
        res = {**actual, "hay_nueva": bool(cambios), "nuevos": cambios[:30],
               "n_nuevos": len(cambios), "ultima": self._commit(remota),
               "commits_locales": locales, "codigo_modificado": codigo,
               "revisado": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self._ultima = (time.time(), res)
        return res

    # ---------------- actualizar ----------------
    def actualizar(self):
        """Trae la ultima version. No reinicia: eso lo decide quien llama
        (reiniciar()), despues de responder a la pagina."""
        if not self._lock.acquire(blocking=False):
            raise ErrorActualizar("Ya se esta actualizando")
        try:
            return self._actualizar()
        finally:
            self._lock.release()

    def _actualizar(self):
        if self.repo is None:
            raise ErrorActualizar("Esta copia no se instalo con git")
        self._git("fetch", "--quiet", self.remoto, self.rama, timeout=90)
        remota = self._remota()
        antes = self._git("rev-parse", "HEAD")
        rama_antes = self._git("rev-parse", "--abbrev-ref", "HEAD")
        destino = self._git("rev-parse", remota)
        if antes == destino:
            return {"actualizado": False, "mensaje": "Ya tienes la versión más nueva",
                    **self._commit("HEAD")}
        sello = time.strftime("%Y%m%d-%H%M")
        notas = []

        # 1. Ajustes del usuario que estan en git: guardar el contenido.
        ajustes, codigo = self._cambios_locales()
        guardados = {}
        for ruta in ajustes:
            try:
                guardados[ruta] = (self.repo / ruta).read_bytes()
            except OSError:
                pass
        if ajustes:
            self._git("checkout", "--", *ajustes)

        # 2. Codigo cambiado a mano: a un stash (recuperable).
        if codigo:
            self._git("stash", "push", "-m", f"MicroscopeOS: antes de actualizar {sello}",
                      "--", *codigo)
            notas.append("Había cambios hechos a mano en el código; se guardaron "
                         "aparte (git stash list).")

        # 3. Commits que solo estan en la Pi: rama de respaldo.
        if int(self._git("rev-list", "--count", f"{remota}..HEAD") or 0):
            self._git("branch", f"respaldo/{sello}", "HEAD")
            notas.append(f"Había cambios guardados solo en este microscopio; "
                         f"quedaron en la rama respaldo/{sello}.")

        try:
            self._git("checkout", "--quiet", "-B", self.rama, remota)
            for ruta, datos in guardados.items():
                (self.repo / ruta).write_bytes(datos)
            self._compila()
        except ErrorActualizar as e:
            # Volver a como estaba.
            try:
                if rama_antes != "HEAD":
                    self._git("checkout", "--quiet", "-B", rama_antes, antes)
                else:
                    self._git("checkout", "--quiet", antes)
                for ruta, datos in guardados.items():
                    (self.repo / ruta).write_bytes(datos)
            except ErrorActualizar:
                pass
            raise ErrorActualizar(f"La versión nueva tiene un problema; se dejó "
                                  f"la anterior. ({e})")

        dependencias = self._dependencias(antes)
        if dependencias:
            notas.append(dependencias)
        self._ultima = None
        return {"actualizado": True, "antes": antes[:7], **self._commit("HEAD"),
                "notas": notas}

    # Compila en memoria (sin .pyc): compileall se fia de los .pyc viejos
    # si coinciden fecha y tamano, y un archivo roto podia pasar.
    _COMPILAR = ("import sys, pathlib\n"
                 "for raiz in sys.argv[1:]:\n"
                 "    p = pathlib.Path(raiz)\n"
                 "    for f in ([p] if p.is_file() else sorted(p.rglob('*.py'))):\n"
                 "        compile(f.read_bytes(), str(f), 'exec')\n")

    def _compila(self):
        r = subprocess.run([sys.executable, "-c", self._COMPILAR,
                            str(self.raiz_codigo / "core"), str(self.raiz_codigo / "server"),
                            str(self.raiz_codigo / "run_web.py")],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise ErrorActualizar((r.stdout + r.stderr).strip()[-400:] or "no compila")

    def _dependencias(self, antes):
        """Si cambiaron las librerias necesarias, instalarlas en el venv."""
        req = "docs/requirements.txt"
        try:
            if not self._git("diff", "--name-only", antes, "HEAD", "--", req):
                return None
        except ErrorActualizar:
            return None
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                            str(self.repo / req)], capture_output=True, text=True,
                           timeout=600)
        if r.returncode != 0:
            return ("No se pudieron instalar algunas librerías nuevas; si algo "
                    "falla, conecta la Pi a internet y vuelve a actualizar.")
        return "Se instalaron las librerías nuevas."


def reiniciar(antes_de_salir=None, espera_s=1.5):
    """Reinicia el servidor con el codigo nuevo, en otro hilo para que la
    respuesta HTTP alcance a salir.

    Con systemd (INVOCATION_ID existe) se sale con codigo de error y
    systemd lo vuelve a levantar (Restart=on-failure). Sin systemd (se
    lanzo a mano) el proceso se reemplaza a si mismo."""
    def _hilo():
        time.sleep(espera_s)
        if antes_de_salir:
            try:
                antes_de_salir()
            except Exception:
                pass
        if os.environ.get("INVOCATION_ID"):
            os._exit(75)
        os.execv(sys.executable, [sys.executable, *sys.argv])
    threading.Thread(target=_hilo, daemon=True).start()
