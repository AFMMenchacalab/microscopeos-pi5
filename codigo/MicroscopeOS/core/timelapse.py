import json
import time
import os
import threading
from datetime import datetime
from enum import Enum
from pathlib import Path

from core.experimentos import Experimentos, nombre_foto


class TimelapseState(Enum):
    STOPPED = 0
    RUNNING = 1
    ERROR = 2


MODOS = {
    "blanco":    [("", "on")],
    "dpc":       [("_L", "left"), ("_R", "right"),
                  ("_T", "top"), ("_B", "bottom")],
    # oscuro y rheinberg agregados 2026-08-31 junto con el firmware nuevo
    # de las matrices (ver core/illumination.py y su README). ring() y
    # rheinberg() aceptan llamarse sin argumentos (colores por defecto),
    # que es como los invoca getattr(luz, metodo_luz)() mas abajo.
    "oscuro":    [("", "ring")],
    "rheinberg": [("", "rheinberg")],
}


# Cuanto puede alejarse el foco, en total, de donde estaba al iniciar el
# timelapse. La deriva real (temperatura, evaporacion) es de decenas de
# um en un experimento; si un autofoco pide ir mas alla, es mas probable
# un error que una deriva, y sin finales de carrera (TODO_HW 1.5) un
# error acumulado puede llevar el objetivo contra la muestra.
DERIVA_MAXIMA_UM = 250.0


class TimelapseManager:

    def __init__(self, camera, illuminations, autofocus=None, contador=None,
                 enviador=None, respaldo_nas=None, experimentos=None):
        self.camera = camera
        # Donde se crean las carpetas (core/experimentos.py). Sin uno
        # explicito, datos/ en el directorio de trabajo.
        self.experimentos = experimentos or Experimentos(raiz=Path("datos"))
        self.illuminations = illuminations
        # Instancia de core.autofocus.Autofocus, o None si no hay
        # motores de enfoque conectados. Opcional a proposito: el
        # timelapse tiene que seguir corriendo igual sin ellos.
        self.autofocus = autofocus
        # core.analisis.Contador para contar celulas por ciclo. Tambien
        # opcional: un timelapse sin conteo tiene que seguir andando.
        self.contador = contador
        # core.envio.EnviadorPC, o None. Si el timelapse se inicia con
        # enviar_pc=True, cada imagen guardada se encola para mandarla a
        # la computadora que segmenta en vivo (ver core/envio.py).
        self.enviador = enviador
        self.enviar_pc = False
        # core.respaldo_nas.RespaldoNAS, o None: copia de respaldo de cada
        # imagen en el NAS, en paralelo con el envio a la PC.
        self.respaldo_nas = respaldo_nas
        self.respaldar_nas = False
        self.state = TimelapseState.STOPPED
        self.thread = None
        self.base_folder = None
        self.ciclo_actual = 0

    def _log(self, mensaje):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        linea = f"[{ts}] {mensaje}"
        print(linea)
        if self.base_folder:
            try:
                with open(os.path.join(self.base_folder, "timelapse.log"), "a") as f:
                    f.write(linea + "\n")
            except Exception:
                pass

    def _log_temp(self, ciclo, timestamp):
        """Lee temperatura del controlador y la loguea en temp.csv"""
        try:
            from temperature_controller import temperature_controller
            temp = temperature_controller.temperature
            sp   = temperature_controller.setpoint
            pwm  = temperature_controller.pwm
            if temp is None:
                return
            linea = f"{timestamp},{ciclo},{temp:.2f},{sp:.1f},{pwm}\n"
            with open(os.path.join(self.base_folder, "temperatura.csv"), "a") as f:
                # Escribir cabecera si el archivo es nuevo
                if os.path.getsize(os.path.join(self.base_folder, "temperatura.csv")) == 0:
                    f.write("timestamp,ciclo,temperatura,setpoint,pwm\n")
                f.write(linea)
            self._log(f"  temp: {temp:.2f}°C (sp={sp:.1f}, pwm={pwm})")
        except Exception as e:
            self._log(f"  temp: no disponible ({e})")

    def _graficar_temperatura(self):
        """Genera temperatura.png al finalizar el timelapse."""
        try:
            import csv
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            csv_path = os.path.join(self.base_folder, "temperatura.csv")
            if not os.path.exists(csv_path):
                return

            ciclos, temps, setpoints = [], [], []
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ciclos.append(int(row["ciclo"]))
                    temps.append(float(row["temperatura"]))
                    setpoints.append(float(row["setpoint"]))

            if not ciclos:
                return

            fig, ax = plt.subplots(figsize=(10, 4))
            fig.patch.set_facecolor("#0a0e0d")
            ax.set_facecolor("#111816")

            ax.plot(ciclos, temps, color="#3ddc84", linewidth=2, label="Temperatura")
            ax.plot(ciclos, setpoints, color="#ffb454", linewidth=1.5,
                    linestyle="--", label="Setpoint")

            ax.set_xlabel("Ciclo", color="#5f7269")
            ax.set_ylabel("°C", color="#5f7269")
            ax.set_title("Temperatura durante timelapse", color="#c8d6d0")
            ax.tick_params(colors="#5f7269")
            ax.legend(facecolor="#111816", labelcolor="#c8d6d0")
            for spine in ax.spines.values():
                spine.set_edgecolor("#1f2e2a")

            plt.tight_layout()
            out = os.path.join(self.base_folder, "temperatura.png")
            plt.savefig(out, dpi=120, facecolor=fig.get_facecolor())
            plt.close()
            self._log(f"Gráfica guardada: {out}")

        except Exception as e:
            self._log(f"Error generando gráfica: {e}")

    def _autoenfocar(self, camaras, ciclo, ts, opciones):
        """Reenfoca cada camara antes de capturar el ciclo.

        Por que tiene sentido en un timelapse largo: en 48 h el foco se
        va solo (dilatacion termica del montaje, la incubadora ciclando
        entre 37 grados y ambiente, evaporacion que baja el nivel del
        medio). Sin esto, un timelapse que empieza enfocado puede
        terminar borroso sin que nadie se entere hasta revisar las
        imagenes.

        Cada resultado se registra ademas en autofoco.csv: la deriva de
        la posicion de foco a lo largo del experimento es un dato en si
        mismo (y sirve para decidir si hace falta reenfocar cada ciclo o
        cada 20).
        """
        if self.autofocus is None:
            return
        for cam in camaras:
            if not self.autofocus.disponible(cam):
                continue
            motor = self.autofocus.motores.get(cam)
            previa_um = motor.posicion_um
            ancla_um = self._ancla_um.setdefault(cam, previa_um)
            try:
                r = self.autofocus.enfocar_auto(cam, **opciones)
                deriva = motor.posicion_um - ancla_um
                if abs(deriva) > DERIVA_MAXIMA_UM:
                    with motor.resolucion():
                        motor.mover_a(int(round(previa_um / motor.um_por_micropaso())),
                                      backlash=64)
                    r["encontrado"] = False
                    r["aviso"] = (f"pedia ir a {deriva:+.0f} um del inicio "
                                  f"(tope {DERIVA_MAXIMA_UM:.0f}); se quedo donde estaba")
                self._log(f"  autofoco cam{cam} [{r.get('metodo', '?')}]: "
                          f"{motor.posicion_um - ancla_um:+.1f} um desde el inicio "
                          f"(mov {motor.posicion_um - previa_um:+.1f} um) "
                          + (f"nitidez={r['nitidez']:.1f} "
                             if r.get("nitidez") is not None else
                             f"corrimiento={r['corrimiento_px']:+.2f}px "
                             if r.get("corrimiento_px") is not None else "")
                          + (f"rango {r['rango_um']:.0f} um (ampliado) "
                             if r.get("ampliado") else "")
                          + (f"[{r['aviso']}] " if r.get("aviso") else "")
                          + f"{r['segundos']}s"
                          + ("" if r.get("encontrado", not r.get("fuera_de_rango"))
                             else "  [NO ENCONTRO EL FOCO -- esta foto puede salir borrosa]"))
                self._log_autofoco(ciclo, ts, cam, r, motor.posicion_um - ancla_um)
            except Exception as e:
                self._log(f"  autofoco cam{cam}: ERROR -> {e}")

    def _log_autofoco(self, ciclo, ts, cam, r, deriva_um=None):
        try:
            path = os.path.join(self.base_folder, "autofoco.csv")
            nuevo = not os.path.exists(path) or os.path.getsize(path) == 0
            with open(path, "a") as f:
                if nuevo:
                    f.write("timestamp,ciclo,camara,metodo,posicion,"
                            "desplazamiento,nitidez,corrimiento_px,"
                            "fuera_de_rango,deriva_um,desplazamiento_um,"
                            "rango_um,encontrado\n")
                encontrado = r.get("encontrado", not r.get("fuera_de_rango"))
                f.write(f"{ts},{ciclo},{cam},{r.get('metodo', '')},"
                        f"{r['posicion']},{r['desplazamiento']},"
                        f"{r.get('nitidez', '')},"
                        f"{r.get('corrimiento_px', '')},"
                        f"{int(bool(r.get('fuera_de_rango')))},"
                        f"{'' if deriva_um is None else round(deriva_um, 2)},"
                        f"{r.get('desplazamiento_um', '')},"
                        f"{r.get('rango_um', '')},{int(bool(encontrado))}\n")
        except Exception:
            pass

    def _contar_ciclo(self, guardadas, ciclo, ts, opciones):
        """Cuenta celulas en las capturas que se acaban de guardar.

        Va DESPUES de capturar y no antes: analiza exactamente las
        imagenes que quedan en el experimento, asi que el numero de
        conteo.csv siempre se puede volver a verificar sobre el TIFF que
        esta al lado. Si en cambio se capturara aparte para contar, el
        CSV describiria una imagen que no existe.

        Nunca interrumpe el timelapse: si el analisis falla se anota en
        el log y el ciclo sigue. Perder un punto de la curva de
        poblacion es molesto; perder una hora de capturas, no.
        """
        if self.contador is None:
            return
        from core import analisis

        opciones = dict(opciones or {})
        ancho_max = int(opciones.pop("ancho_max", 1200))
        overlay = bool(opciones.pop("overlay", True))

        for cam, rutas in sorted(guardadas.items()):
            if not rutas:
                continue
            try:
                resultado, imagen = analisis.analizar_capturas(
                    rutas, self.contador, ancho_max=ancho_max)
            except Exception as e:
                self._log(f"  conteo cam{cam}: ERROR -> {e}")
                continue

            fila = dict(resultado, timestamp=ts, ciclo=ciclo, camara=cam)
            fila["confluente"] = int(bool(resultado["confluente"]))
            fila["vacio"] = int(bool(resultado["vacio"]))
            try:
                analisis.escribir_conteo(self.base_folder, fila)
                if overlay:
                    analisis.guardar_overlay(
                        os.path.join(self.base_folder, f"cam{cam}",
                                     f"conteo_{ts}.png"),
                        imagen, resultado)
            except Exception as e:
                self._log(f"  conteo cam{cam}: no se pudo guardar -> {e}")

            aviso = ""
            if resultado["vacio"]:
                aviso = "  [CAMPO VACIO O SIN LUZ]"
            elif resultado["confluente"]:
                aviso = "  [CONFLUENTE: el conteo es una cota inferior]"
            self._log(f"  conteo cam{cam}: {resultado['n']} celulas "
                      f"({resultado['regiones']} regiones, "
                      f"{resultado['cumulos']} cumulos, "
                      f"{resultado['ms']} ms){aviso}")

    def _resumir_analisis(self, intervalo_s):
        """Curva de poblacion + eventos, al terminar el timelapse."""
        if self.contador is None or not self.base_folder:
            return
        try:
            from core import analisis
            resumen = analisis.resumir_timelapse(self.base_folder, intervalo_s)
            if "error" in resumen:
                return
            for cam, info in resumen["camaras"].items():
                crec = info.get("crecimiento") or {}
                self._log(
                    f"cam{cam}: {info['primero']} -> {info['ultimo']} celulas"
                    + (f", duplicacion {crec['duplicacion_h']} h "
                       f"(r2={crec['r2']})" if crec.get("duplicacion_h")
                       else "")
                    + f", {len(info['eventos'])} evento(s)")
            if resumen.get("grafica"):
                self._log(f"Grafica guardada: {resumen['grafica']}")
        except Exception as e:
            self._log(f"Error generando resumen de conteo: {e}")

    def _capturar_secuencial(self, patrones, camaras, ts, stabilization_time):
        """Una camara y una matriz encendida a la vez.

        Es el comportamiento del montaje de Pi 4 y el unico seguro si los
        dos canales opticos no estan aislados entre si.

        Devuelve {camara: {sufijo: ruta}} con lo que efectivamente se
        guardo, para que el conteo de celulas analice esas mismas
        imagenes en vez de volver a capturar.
        """
        guardadas = {cam: {} for cam in camaras}
        for cam in camaras:
            cam_folder = os.path.join(self.base_folder, f"cam{cam}")
            luz = self.illuminations.get(cam)

            for sufijo, metodo_luz in patrones:
                try:
                    if luz is not None:
                        getattr(luz, metodo_luz)()
                        time.sleep(stabilization_time)

                    filename = os.path.join(
                        cam_folder, nombre_foto(ts, self.ciclo_actual, sufijo))
                    self.camera.capture_image(
                        camera_num=cam,
                        folder=cam_folder,
                        filename=filename,
                        meta=self._meta_foto(sufijo))
                    guardadas[cam][sufijo] = filename

                    self._log(f"  cam{cam}{sufijo}: OK")

                except Exception as e:
                    self._log(f"  cam{cam}{sufijo}: ERROR -> {e}")
                finally:
                    if luz is not None:
                        try:
                            luz.off()
                        except Exception:
                            pass
        return guardadas

    def _capturar_simultaneo(self, patrones, camaras, ts, stabilization_time):
        """Las dos camaras disparan a la vez, con las dos matrices encendidas.

        Solo posible en Pi 5 (dos puertos CSI nativos). Reduce el ciclo DPC
        de 8 capturas secuenciales a 4 pasos paralelos.

        # TODO-HW: requiere que la matriz de cam0 no ilumine el sensor de
        # cam1 ni viceversa. Si hay diafonia optica, esto contamina los
        # datos sin dar ningun error. Ver TODO_HW.md (prioridad 2).
        """
        guardadas = {cam: {} for cam in camaras}
        for sufijo, metodo_luz in patrones:
            luces = [self.illuminations.get(cam) for cam in camaras]
            try:
                encendidas = False
                for luz in luces:
                    if luz is not None:
                        getattr(luz, metodo_luz)()
                        encendidas = True
                if encendidas:
                    time.sleep(stabilization_time)

                filenames = {
                    cam: os.path.join(self.base_folder, f"cam{cam}",
                                      nombre_foto(ts, self.ciclo_actual, sufijo))
                    for cam in camaras
                }
                self.camera.capture_both(
                    folder=self.base_folder,
                    filenames=filenames,
                    camera_nums=camaras,
                    meta=self._meta_foto(sufijo))

                for cam in camaras:
                    guardadas[cam][sufijo] = filenames[cam]
                    self._log(f"  cam{cam}{sufijo}: OK")

            except Exception as e:
                self._log(f"  cam*{sufijo}: ERROR -> {e}")
            finally:
                for luz in luces:
                    if luz is not None:
                        try:
                            luz.off()
                        except Exception:
                            pass
        return guardadas

    def _enviar(self, ruta, camara=""):
        """Encola un archivo ya guardado para mandarlo a la PC y/o
        respaldarlo en el NAS. Nunca frena ni interrumpe el timelapse: cada
        destino corre en su propio hilo y, si la red falla, reintenta solo."""
        exp = os.path.basename(self.base_folder)
        for activo, destino, nombre in ((self.enviar_pc, self.enviador, "envio"),
                                        (self.respaldar_nas, self.respaldo_nas, "nas")):
            if activo and destino is not None:
                try:
                    destino.encolar(ruta, exp, camara)
                except Exception as e:
                    self._log(f"  {nombre}: no se pudo encolar {ruta} -> {e}")

    def _meta_foto(self, sufijo):
        """Lo que cada foto del timelapse agrega a sus metadatos."""
        canales = {"_L": "izquierda", "_R": "derecha", "_T": "arriba", "_B": "abajo"}
        meta = {"experimento": {"nombre": self.nombre_experimento,
                                "id": os.path.basename(self.base_folder),
                                "tipo": "timelapse", "ciclo": self.ciclo_actual}}
        if sufijo:
            meta["canal_dpc"] = canales.get(sufijo, sufijo)
        return meta

    def _escribir_metadatos(self, modo, interval_seconds, duration_seconds,
                            camaras, nombre):
        """experimento.json: lo que la PC necesita para agrupar las fotos
        de un mismo ciclo (sufijos del modo) sin adivinar por el nombre."""
        meta = {
            "modo": modo,
            "sufijos": [s for s, _ in MODOS[modo]],
            "intervalo_s": interval_seconds,
            "duracion_s": duration_seconds,
            "camaras": list(camaras),
            # color de los patrones DPC (la longitud de onda importa para
            # reconstruir la fase en la PC); "FFFFFF" = blanco
            "color_dpc": next((getattr(l, "color_dpc", None) or "FFFFFF"
                               for l in self.illuminations.values() if l is not None), None),
            # color del campo claro de cada camara ("FFFFFF" = blanco)
            "color_campo": {str(n): getattr(l, "color_campo", None) or "FFFFFF"
                            for n, l in self.illuminations.items() if l is not None},
        }
        self.experimentos.actualizar(self.base_folder, **meta)
        return os.path.join(self.base_folder, "experimento.json")

    def _run(self, modo, interval_seconds, duration_seconds,
             stabilization_time, camaras, simultaneo,
             autofocus, autofocus_cada, autofocus_opts,
             contar, contar_cada, contar_opts,
             carpeta_raiz="", nombre=""):

        self.state = TimelapseState.RUNNING
        patrones = MODOS[modo]

        # Carpeta del experimento: datos/AAAA-MM-DD_HHMM_<nombre>, o en
        # la memoria USB (carpeta_raiz) bajo MicroscopeOS/.
        raiz = os.path.join(carpeta_raiz, "MicroscopeOS") if carpeta_raiz else None
        self.base_folder = str(self.experimentos.crear_timelapse(nombre, raiz=raiz))
        self.nombre_experimento = self.experimentos.info(self.base_folder)["nombre"]
        self.ciclo_actual = 0
        self._detenido = False
        for cam in camaras:
            os.makedirs(os.path.join(self.base_folder, f"cam{cam}"), exist_ok=True)
        self._enviar(self._escribir_metadatos(modo, interval_seconds,
                                              duration_seconds, camaras, nombre))

        # Crear CSV con cabecera
        csv_path = os.path.join(self.base_folder, "temperatura.csv")
        with open(csv_path, "w") as f:
            f.write("timestamp,ciclo,temperatura,setpoint,pwm\n")

        if autofocus and self.autofocus is None:
            self._log("Autofoco pedido pero no hay motores de enfoque "
                      "disponibles -- se continua sin autofoco.")
            autofocus = False
        # Posicion de cada eje al primer autofoco (um): referencia para
        # DERIVA_MAXIMA_UM y para la columna deriva_um de autofoco.csv.
        self._ancla_um = {}
        if contar and self.contador is None:
            self._log("Conteo de celulas pedido pero no hay contador "
                      "disponible -- se continua sin conteo.")
            contar = False

        self._log(f"Timelapse iniciado | modo={modo} | camaras={camaras} | "
                  f"intervalo={interval_seconds}s | duracion={duration_seconds}s | "
                  f"captura={'simultanea' if simultaneo else 'secuencial'} | "
                  f"autofoco={'cada ' + str(autofocus_cada) + ' ciclo(s)' if autofocus else 'no'} | "
                  f"conteo={'cada ' + str(contar_cada) + ' ciclo(s)' if contar else 'no'} | "
                  f"destino={os.path.abspath(self.base_folder)} | "
                  f"envio_pc={'si' if self.enviar_pc and self.enviador else 'no'} | "
                  f"respaldo_nas={'si' if self.respaldar_nas and self.respaldo_nas else 'no'}")

        start_time = time.monotonic()
        next_capture_time = start_time
        ciclo = 0

        while self.state == TimelapseState.RUNNING:
            current_time = time.monotonic()

            if current_time - start_time >= duration_seconds:
                break

            if current_time >= next_capture_time:
                ciclo += 1
                self.ciclo_actual = ciclo
                ahora = datetime.now()
                ts = ahora.strftime("%Y%m%d_%H%M%S")
                self._log(f"--- Ciclo {ciclo} ({ts}) ---")

                # Registrar temperatura al inicio de cada ciclo
                self._log_temp(ciclo, ts)

                # El autofoco va ANTES de las capturas del ciclo y
                # despues del log de temperatura: mueve la plataforma y
                # deja las camaras en modo preview, asi que tiene que
                # terminar antes de que se dispare la primera foto.
                if autofocus and (ciclo - 1) % max(1, autofocus_cada) == 0:
                    self._autoenfocar(camaras, ciclo, ts, autofocus_opts)

                if simultaneo:
                    guardadas = self._capturar_simultaneo(
                        patrones, camaras, ahora, stabilization_time)
                else:
                    guardadas = self._capturar_secuencial(
                        patrones, camaras, ahora, stabilization_time)

                for cam, rutas in sorted(guardadas.items()):
                    for sufijo, _ in patrones:
                        if sufijo in rutas:
                            self._enviar(rutas[sufijo], f"cam{cam}")

                if contar and (ciclo - 1) % max(1, contar_cada) == 0:
                    self._contar_ciclo(guardadas, ciclo, ts, contar_opts)

                next_capture_time += interval_seconds

            else:
                time.sleep(0.05)

        for luz in self.illuminations.values():
            try:
                luz.off()
            except Exception:
                pass

        self._log(f"Timelapse finalizado. Ciclos completados: {ciclo}")
        self._graficar_temperatura()
        if contar:
            self._resumir_analisis(interval_seconds)
        try:
            self.experimentos.finalizar(
                self.base_folder, fin=datetime.now().isoformat(timespec="seconds"),
                estado="detenido antes de tiempo" if self._detenido else "completo",
                ciclos=ciclo)
        except Exception as e:
            self._log(f"No se pudo cerrar experimento.json: {e}")
        for extra in ("experimento.json", "LEEME.txt", "temperatura.csv",
                      "autofoco.csv", "conteo.csv"):
            ruta = os.path.join(self.base_folder, extra)
            if os.path.exists(ruta):
                self._enviar(ruta)
        self.state = TimelapseState.STOPPED

    def start(self, modo="blanco", interval_seconds=300, duration_seconds=3600,
              stabilization_time=0.3, camaras=[0, 1], simultaneo=False,
              autofocus=False, autofocus_cada=1, autofocus_opts=None,
              contar=False, contar_cada=1, contar_opts=None,
              carpeta_raiz="", enviar_pc=False, nombre="", respaldar_nas=False):
        """carpeta_raiz: donde crear timelapse_<fecha> ("" = directorio
        de trabajo, o el punto de montaje de una memoria USB).
        enviar_pc: mandar cada imagen a la PC de segmentacion mientras
        corre (requiere un EnviadorPC configurado)."""
        if self.state == TimelapseState.RUNNING:
            print("Timelapse ya esta corriendo.")
            return
        if modo not in MODOS:
            print(f"Modo invalido: {modo}. Usa uno de: {', '.join(MODOS)}.")
            return

        self.enviar_pc = bool(enviar_pc)
        self.respaldar_nas = bool(respaldar_nas)
        self.thread = threading.Thread(
            target=self._run,
            args=(modo, interval_seconds, duration_seconds,
                  stabilization_time, camaras, simultaneo,
                  autofocus, autofocus_cada, autofocus_opts or {},
                  contar, contar_cada, contar_opts or {},
                  carpeta_raiz, nombre),
            daemon=True
        )
        self.thread.start()

    def stop(self):
        if self.state == TimelapseState.RUNNING:
            self._detenido = True
            self.state = TimelapseState.STOPPED
            if self.thread is not None:
                self.thread.join()

    def is_running(self):
        return self.state == TimelapseState.RUNNING
