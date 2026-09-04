import time
import os
import threading
from datetime import datetime
from enum import Enum


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


class TimelapseManager:

    def __init__(self, camera, illuminations, autofocus=None):
        self.camera = camera
        self.illuminations = illuminations
        # Instancia de core.autofocus.Autofocus, o None si no hay
        # motores de enfoque conectados. Opcional a proposito: el
        # timelapse tiene que seguir corriendo igual sin ellos.
        self.autofocus = autofocus
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
            try:
                r = self.autofocus.enfocar_auto(cam, **opciones)
                self._log(f"  autofoco cam{cam} [{r.get('metodo', '?')}]: "
                          f"pos={r['posicion']} "
                          f"(mov {r['desplazamiento']:+d}) "
                          + (f"nitidez={r['nitidez']:.1f} "
                             if "nitidez" in r else
                             f"corrimiento={r['corrimiento_px']:+.2f}px ")
                          + f"{r['segundos']}s"
                          + ("  [MAXIMO EN EL BORDE DEL RANGO]"
                             if r.get("fuera_de_rango") else ""))
                self._log_autofoco(ciclo, ts, cam, r)
            except Exception as e:
                self._log(f"  autofoco cam{cam}: ERROR -> {e}")

    def _log_autofoco(self, ciclo, ts, cam, r):
        try:
            path = os.path.join(self.base_folder, "autofoco.csv")
            nuevo = not os.path.exists(path) or os.path.getsize(path) == 0
            with open(path, "a") as f:
                if nuevo:
                    f.write("timestamp,ciclo,camara,metodo,posicion,"
                            "desplazamiento,nitidez,corrimiento_px,"
                            "fuera_de_rango\n")
                f.write(f"{ts},{ciclo},{cam},{r.get('metodo', '')},"
                        f"{r['posicion']},{r['desplazamiento']},"
                        f"{r.get('nitidez', '')},"
                        f"{r.get('corrimiento_px', '')},"
                        f"{int(bool(r.get('fuera_de_rango')))}\n")
        except Exception:
            pass

    def _capturar_secuencial(self, patrones, camaras, ts, stabilization_time):
        """Una camara y una matriz encendida a la vez.

        Es el comportamiento del montaje de Pi 4 y el unico seguro si los
        dos canales opticos no estan aislados entre si.
        """
        for cam in camaras:
            cam_folder = os.path.join(self.base_folder, f"cam{cam}")
            luz = self.illuminations.get(cam)

            for sufijo, metodo_luz in patrones:
                try:
                    if luz is not None:
                        getattr(luz, metodo_luz)()
                        time.sleep(stabilization_time)

                    filename = os.path.join(
                        cam_folder, f"img_{ts}{sufijo}.tif")
                    self.camera.capture_image(
                        camera_num=cam,
                        folder=cam_folder,
                        filename=filename)

                    self._log(f"  cam{cam}{sufijo}: OK")

                except Exception as e:
                    self._log(f"  cam{cam}{sufijo}: ERROR -> {e}")
                finally:
                    if luz is not None:
                        try:
                            luz.off()
                        except Exception:
                            pass

    def _capturar_simultaneo(self, patrones, camaras, ts, stabilization_time):
        """Las dos camaras disparan a la vez, con las dos matrices encendidas.

        Solo posible en Pi 5 (dos puertos CSI nativos). Reduce el ciclo DPC
        de 8 capturas secuenciales a 4 pasos paralelos.

        # TODO-HW: requiere que la matriz de cam0 no ilumine el sensor de
        # cam1 ni viceversa. Si hay diafonia optica, esto contamina los
        # datos sin dar ningun error. Ver TODO_HW.md (prioridad 2).
        """
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
                                      f"img_{ts}{sufijo}.tif")
                    for cam in camaras
                }
                self.camera.capture_both(
                    folder=self.base_folder,
                    filenames=filenames,
                    camera_nums=camaras)

                for cam in camaras:
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

    def _run(self, modo, interval_seconds, duration_seconds,
             stabilization_time, camaras, simultaneo,
             autofocus, autofocus_cada, autofocus_opts):

        self.state = TimelapseState.RUNNING
        patrones = MODOS[modo]

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.base_folder = f"timelapse_{stamp}"
        os.makedirs(self.base_folder, exist_ok=True)
        for cam in camaras:
            os.makedirs(os.path.join(self.base_folder, f"cam{cam}"), exist_ok=True)

        # Crear CSV con cabecera
        csv_path = os.path.join(self.base_folder, "temperatura.csv")
        with open(csv_path, "w") as f:
            f.write("timestamp,ciclo,temperatura,setpoint,pwm\n")

        if autofocus and self.autofocus is None:
            self._log("Autofoco pedido pero no hay motores de enfoque "
                      "disponibles -- se continua sin autofoco.")
            autofocus = False

        self._log(f"Timelapse iniciado | modo={modo} | camaras={camaras} | "
                  f"intervalo={interval_seconds}s | duracion={duration_seconds}s | "
                  f"captura={'simultanea' if simultaneo else 'secuencial'} | "
                  f"autofoco={'cada ' + str(autofocus_cada) + ' ciclo(s)' if autofocus else 'no'}")

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
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
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
                    self._capturar_simultaneo(patrones, camaras, ts,
                                              stabilization_time)
                else:
                    self._capturar_secuencial(patrones, camaras, ts,
                                              stabilization_time)

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
        self.state = TimelapseState.STOPPED

    def start(self, modo="blanco", interval_seconds=300, duration_seconds=3600,
              stabilization_time=0.3, camaras=[0, 1], simultaneo=False,
              autofocus=False, autofocus_cada=1, autofocus_opts=None):
        if self.state == TimelapseState.RUNNING:
            print("Timelapse ya esta corriendo.")
            return
        if modo not in MODOS:
            print(f"Modo invalido: {modo}. Usa uno de: {', '.join(MODOS)}.")
            return

        self.thread = threading.Thread(
            target=self._run,
            args=(modo, interval_seconds, duration_seconds,
                  stabilization_time, camaras, simultaneo,
                  autofocus, autofocus_cada, autofocus_opts or {}),
            daemon=True
        )
        self.thread.start()

    def stop(self):
        if self.state == TimelapseState.RUNNING:
            self.state = TimelapseState.STOPPED
            if self.thread is not None:
                self.thread.join()

    def is_running(self):
        return self.state == TimelapseState.RUNNING
