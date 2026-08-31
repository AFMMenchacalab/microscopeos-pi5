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
    "blanco": [("", "on")],
    "dpc":    [("_L", "left"), ("_R", "right"),
               ("_T", "top"), ("_B", "bottom")],
}


class TimelapseManager:

    def __init__(self, camera, illuminations):
        self.camera = camera
        self.illuminations = illuminations
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
             stabilization_time, camaras, simultaneo):

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

        self._log(f"Timelapse iniciado | modo={modo} | camaras={camaras} | "
                  f"intervalo={interval_seconds}s | duracion={duration_seconds}s | "
                  f"captura={'simultanea' if simultaneo else 'secuencial'}")

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
              stabilization_time=0.3, camaras=[0, 1], simultaneo=False):
        if self.state == TimelapseState.RUNNING:
            print("Timelapse ya esta corriendo.")
            return
        if modo not in MODOS:
            print(f"Modo invalido: {modo}. Usa 'blanco' o 'dpc'.")
            return

        self.thread = threading.Thread(
            target=self._run,
            args=(modo, interval_seconds, duration_seconds,
                  stabilization_time, camaras, simultaneo),
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
