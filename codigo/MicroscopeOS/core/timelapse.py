import json
import queue
import time
import os
import subprocess
import threading
from datetime import datetime
from enum import Enum
from pathlib import Path

from core.experimentos import Experimentos, nombre_foto, agregar_nota


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

# Reanudar despues de un corte de luz. Mientras corre un timelapse, este
# archivo (en datos/) dice donde y como seguirlo; se borra solo cuando el
# timelapse termina o se detiene desde la pagina. Si al arrancar el
# servidor todavia esta, es que la Pi se apago a mitad de camino.
ARCHIVO_REANUDAR = ".timelapse_en_curso.json"
# Cuanto esperar al arrancar a que la hora este bien (la Pi no tiene pila
# de reloj: despues de un corte arranca con la hora del ultimo apagado
# hasta que la red la corrige) y a que aparezca la carpeta (una memoria
# USB tarda en montarse).
ESPERA_REANUDAR_S = 180

# temperatura.csv: una fila por ciclo. Las columnas de CO2 y humedad se
# agregaron despues; los lectores aceptan archivos viejos sin ellas.
CABECERA_TEMP = ("timestamp,ciclo,temperatura,setpoint,pwm,"
                 "co2_ppm,co2_setpoint_ppm,humedad\n")


def reloj_sincronizado():
    """True si systemd dice que la hora ya vino de la red. Sin timedatectl
    (fuera de la Pi) no hay forma de saberlo y se da por buena."""
    try:
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "yes"
    except Exception:
        return True


def vista_previa(rutas, modo, size=800, base=None):
    """JPEG de un ciclo para mirar el timelapse desde la pagina.

    rutas: {sufijo: archivo} de una camara. En relieve DPC muestra el
    relieve, no una de las cuatro fotos sueltas: si core/dpc.py ya proceso
    el ciclo, su vista a color (base + "_dpc.jpg") o el _dpcLR.tif (las
    crudas pueden estar ya borradas); si no, (L-R)/(L+R) de las crudas.
    En los demas modos, la foto tal cual.
    """
    import cv2
    import numpy as np
    import tifffile
    from core.autofocus import imagen_dpc

    def leer(ruta):
        img = tifffile.imread(str(ruta))
        if img.ndim == 3:
            img = img[..., 0] if img.shape[-1] == 1 else cv2.cvtColor(img[..., :3], cv2.COLOR_RGB2GRAY)
        h, w = img.shape[:2]
        escala = size / max(h, w)
        if escala < 1:   # achicar ANTES de calcular: en la Pi, 16 MP pesan
            img = cv2.resize(img, (max(1, int(w * escala)), max(1, int(h * escala))),
                             interpolation=cv2.INTER_AREA)
        return img.astype(np.float32)

    def jpeg(img8):
        ok, buf = cv2.imencode(".jpg", img8, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()

    if modo == "dpc" and base:
        if os.path.isfile(base + "_dpc.jpg"):
            img = cv2.imread(base + "_dpc.jpg", cv2.IMREAD_COLOR)
            if img is not None:
                h, w = img.shape[:2]
                if max(h, w) > size:
                    e = size / max(h, w)
                    img = cv2.resize(img, (max(1, int(w * e)), max(1, int(h * e))),
                                     interpolation=cv2.INTER_AREA)
                return jpeg(img)
        if os.path.isfile(base + "_dpcLR.tif"):
            from core import dpc as dpc_mod
            v = dpc_mod.desde_uint16(leer(base + "_dpcLR.tif"), dpc_mod.ESCALA_DPC)
            tope = float(np.percentile(np.abs(v), 99.5)) or 1.0
            return jpeg((127.5 + 127.5 * np.clip(v / tope, -1, 1)).astype(np.uint8))

    if modo == "dpc" and "_L" in rutas and "_R" in rutas:
        dpc = imagen_dpc(leer(rutas["_L"]), leer(rutas["_R"]))
        tope = float(np.percentile(np.abs(dpc), 99.5)) or 1.0
        img8 = (127.5 + 127.5 * np.clip(dpc / tope, -1, 1)).astype(np.uint8)
    else:
        ruta = rutas.get("") or rutas.get("_L") or next(iter(rutas.values()))
        img = leer(ruta)
        lo, hi = np.percentile(img, (0.5, 99.5))
        img8 = (np.clip((img - lo) / max(hi - lo, 1.0), 0, 1) * 255).astype(np.uint8)
    return jpeg(img8)


class TimelapseManager:

    def __init__(self, camera, illuminations, autofocus=None, contador=None,
                 enviador=None, respaldo_nas=None, experimentos=None,
                 archivo_estado=None):
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
        # Para reanudar despues de un corte de luz (ver ARCHIVO_REANUDAR).
        self.archivo_estado = Path(archivo_estado or
                                   Path(self.experimentos.raiz) / ARCHIVO_REANUDAR)
        self.config = None            # parametros con los que se inicio
        self.inicio_wall = None       # time.time() del inicio original
        self.reanudaciones = []       # cuando se reanudo (ISO)
        self.reanudando = None        # texto mientras espera para reanudar
        self.proxima_wall = None      # time.time() de la proxima foto
        # Ultimo ciclo guardado de cada camara: {cam: {ciclo, hora, rutas}}
        # para la vista previa de la pagina.
        self.ultimas = {}
        # Pausa: mientras esta en pausa no se toman fotos, pero el
        # experimento sigue abierto y la hora de termino no cambia (es
        # la que se eligio al empezar). Sirve para abrir la incubadora y
        # agregar un farmaco o cambiar el medio sin cortar el timelapse.
        self.pausado = False
        self.pausas = []              # [[inicio_iso, fin_iso|None], ...]
        # core.alertas.Alertas, o None: avisa por correo/Telegram si el
        # autofoco falla seguido, si una captura falla o si el timelapse
        # se cae por un error.
        self.alertas = None
        self._fallos_af = {}

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
            tc = temperature_controller
            temp = tc.temperature
            sp   = tc.setpoint
            pwm  = tc.pwm
            if temp is None:
                return
            num = lambda v, fmt: "" if v is None else format(float(v), fmt)
            linea = (f"{timestamp},{ciclo},{temp:.2f},{sp:.1f},{pwm},"
                     f"{num(getattr(tc, 'co2', None), '.0f')},"
                     f"{num(getattr(tc, 'co2_setpoint', None), '.0f')},"
                     f"{num(getattr(tc, 'humidity', None), '.1f')}\n")
            ruta = os.path.join(self.base_folder, "temperatura.csv")
            with open(ruta, "a") as f:
                # Escribir cabecera si el archivo es nuevo
                if os.path.getsize(ruta) == 0:
                    f.write(CABECERA_TEMP)
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
                encontrado = r.get("encontrado", not r.get("fuera_de_rango"))
                self._contar_fallo_af(cam, ciclo, None if encontrado else
                                      r.get("aviso") or "no encontró el foco")
            except Exception as e:
                self._log(f"  autofoco cam{cam}: ERROR -> {e}")
                self._contar_fallo_af(cam, ciclo, f"error: {e}")

    def _contar_fallo_af(self, cam, ciclo, motivo):
        """Lleva la cuenta de autofocos fallidos seguidos por camara y
        avisa a core.alertas cuando se acumulan (uno suelto es normal: un
        campo vacio, una burbuja)."""
        if motivo is None:
            if self._fallos_af.get(cam) and self.alertas is not None:
                self.alertas.resuelto(f"autofoco_cam{cam}",
                                      f"El autofoco de la cámara {cam} volvió a encontrar el foco.")
            self._fallos_af[cam] = 0
            return
        n = self._fallos_af.get(cam, 0) + 1
        self._fallos_af[cam] = n
        if self.alertas is not None:
            self.alertas.autofoco_fallo(cam, n, ciclo, motivo,
                                        getattr(self, "nombre_experimento", ""))

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
                    self._avisar_captura(cam, sufijo, e)
                finally:
                    if luz is not None:
                        try:
                            luz.off()
                        except Exception:
                            pass
        return guardadas

    def _avisar_captura(self, cam, sufijo, error):
        if self.alertas is not None:
            self.alertas.captura_fallo(cam, sufijo, error,
                                       getattr(self, "nombre_experimento", ""))

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
                self._avisar_captura("*", sufijo, e)
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

    def _procesar_dpc(self, cola, opciones):
        """Hilo: calcula el DPC de cada ciclo y borra las 4 crudas.

        Corre aparte para que el calculo (unos segundos por camara en la
        Pi, mas si se pide la fase) no corra el horario de las capturas.
        Las crudas solo se borran si core.dpc.procesar_ciclo pudo escribir
        Y volver a leer los resultados; si algo falla se conservan y, si
        habia envio a la PC/NAS, se mandan ellas en lugar del DPC.
        """
        from core import dpc
        borrar = opciones.get("borrar_crudas", dpc.OPCIONES["borrar_crudas"])
        while True:
            item = cola.get()
            if item is None:
                return
            cam, rutas, ciclo = item
            try:
                r = dpc.procesar_ciclo(rutas, opciones)
            except Exception as e:
                self._log(f"  dpc cam{cam} ciclo {ciclo}: ERROR -> {e} "
                          f"(se conservan las crudas)")
                if borrar:
                    for sufijo in dpc.SUFIJOS_CRUDAS:
                        if rutas.get(sufijo):
                            self._enviar(rutas[sufijo], f"cam{cam}")
                continue
            for ruta in r["archivos"]:
                self._enviar(ruta, f"cam{cam}")
            # La vista previa de la pagina pasa a la del DPC calculado.
            u = self.ultimas.get(cam)
            if u and u["ciclo"] == ciclo:
                self.ultimas[cam] = dict(u, dpc=True)
            self._log(f"  dpc cam{cam} ciclo {ciclo}: {len(r['archivos'])} archivo(s), "
                      f"{len(r['borradas'])} crudas borradas, {r['ms']} ms"
                      + (f", fase p99={r['fase_p99_rad']} rad" if "fase_p99_rad" in r else ""))

    def _pendientes_dpc(self, camaras, opciones):
        """Al reanudar: ciclos que quedaron con las 4 crudas sin procesar
        (la luz se corto con el DPC en la cola o a medio escribir). Las
        crudas solo se borran al final, asi que si estan las 4, el ciclo
        hay que (re)hacerlo."""
        from core import dpc
        pendientes = []
        for cam in camaras:
            carpeta = Path(self.base_folder) / f"cam{cam}"
            for pl in sorted(carpeta.glob("*_L.tif")):
                base = str(pl)[:-len("_L.tif")]
                rutas = {s: f"{base}{s}.tif" for s in dpc.SUFIJOS_CRUDAS}
                if not all(os.path.isfile(r) for r in rutas.values()):
                    continue
                completo = all(os.path.isfile(f"{base}{s}.tif") for s in dpc.salidas(opciones))
                if opciones.get("borrar_crudas", True) or not completo:
                    n = int(pl.name[:4]) if pl.name[:4].isdigit() else 0
                    pendientes.append((cam, rutas, n))
        return pendientes

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
                            camaras, nombre, dpc_opts=None):
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
        if dpc_opts is not None:
            # Que se calculo de las 4 crudas y si se borraron (core/dpc.py)
            from core import dpc
            meta["dpc_procesado"] = dict(dpc.OPCIONES, **dpc_opts)
            if meta["dpc_procesado"].get("borrar_crudas", True):
                # "sufijos" sigue diciendo lo que se captura (_L _R _T _B);
                # esto es lo que queda en disco y le llega a la PC.
                meta["dpc_procesado"]["sufijos"] = dpc.salidas(dpc_opts)
            meta["dpc_procesado"]["valor_dpc"] = (
                f"(pixel - {dpc.CERO}) / {round(1 / dpc.ESCALA_DPC)}")
        self.experimentos.actualizar(self.base_folder, **meta)
        return os.path.join(self.base_folder, "experimento.json")

    # ---------- reanudar despues de un corte de luz ----------
    def _guardar_estado(self, ciclo):
        """Escribe ARCHIVO_REANUDAR. Se llama al iniciar y despues de cada
        ciclo; con fsync, porque justo lo que importa es que sobreviva a
        un corte de luz."""
        datos = {"carpeta": os.path.abspath(self.base_folder),
                 "inicio_ts": self.inicio_wall, "ciclo": ciclo,
                 "ultimo_ts": time.time(), "reanudaciones": self.reanudaciones,
                 "params": self.config, "pausado": self.pausado,
                 "pausas": self.pausas}
        try:
            self.archivo_estado.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.archivo_estado.with_name(self.archivo_estado.name + ".tmp")
            with open(tmp, "w") as f:
                json.dump(datos, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.archivo_estado)
        except Exception as e:
            self._log(f"No se pudo guardar el estado para reanudar: {e}")

    def _borrar_estado(self):
        try:
            self.archivo_estado.unlink()
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[timelapse] no se pudo borrar {self.archivo_estado}: {e}")

    def _ultimas_de_disco(self, camaras):
        """El ultimo ciclo guardado de cada camara, leido de la carpeta:
        al reanudar, la pagina muestra la foto de antes del corte hasta
        que se toma la siguiente."""
        ultimas = {}
        for cam in camaras:
            carpeta = Path(self.base_folder) / f"cam{cam}"
            fotos = [p for p in carpeta.glob("*.tif") if p.name[:4].isdigit()]
            if not fotos:
                continue
            n = max(int(p.name[:4]) for p in fotos)
            del_ciclo = [p for p in fotos if int(p.name[:4]) == n]
            rutas, base, dpc = {}, None, False
            for p in del_ciclo:
                for suf in ("_dpcLR", "_dpcTB", "_suma", "_fase", "_L", "_R", "_T", "_B"):
                    if p.stem.endswith(suf):
                        base = str(p)[:-len(suf + ".tif")]
                        if suf.startswith("_dpc"):
                            dpc = True
                        elif len(suf) == 2:
                            rutas[suf] = str(p)
                        break
                else:
                    rutas[""] = str(p)
            hora = datetime.fromtimestamp(max(p.stat().st_mtime for p in del_ciclo))
            ultimas[cam] = {"ciclo": n, "hora": hora.isoformat(timespec="seconds"),
                            "rutas": rutas, "base": base, "dpc": dpc}
        return ultimas

    def reanudar_pendiente(self, espera_s=ESPERA_REANUDAR_S, sincronizado=None,
                           en_hilo=True):
        """Si la Pi se apago con un timelapse a medias, lo sigue.

        Se llama al arrancar el servidor. Espera (en un hilo, sin frenar
        el arranque) a que la hora venga de la red y a que exista la
        carpeta; despues sigue en la MISMA carpeta, con la numeracion de
        fotos donde quedo y respetando la duracion original: si mientras
        estuvo apagada ya se cumplio, cierra el experimento y no saca
        nada mas.
        """
        if not self.archivo_estado.is_file():
            return None
        if en_hilo:
            t = threading.Thread(target=self.reanudar_pendiente,
                                 kwargs={"espera_s": espera_s, "sincronizado": sincronizado,
                                         "en_hilo": False}, daemon=True)
            t.start()
            return t
        try:
            datos = json.loads(self.archivo_estado.read_text())
            p = datos["params"]
            carpeta = datos["carpeta"]
            inicio = float(datos["inicio_ts"])
            if p.get("modo") not in MODOS:
                raise ValueError(f"modo invalido {p.get('modo')!r}")
        except Exception as e:
            print(f"[timelapse] estado para reanudar ilegible, se descarta: {e}")
            self._borrar_estado()
            return None
        sincronizado = sincronizado or reloj_sincronizado
        ultimo = float(datos.get("ultimo_ts") or inicio)
        self.reanudando = "Reanudando el timelapse después de un corte de luz…"
        print(f"[timelapse] quedo un timelapse a medias en {carpeta}: reanudando")
        try:
            limite = time.monotonic() + espera_s
            while time.monotonic() < limite and not (sincronizado() and time.time() >= ultimo):
                time.sleep(1)
            while time.monotonic() < limite and not os.path.isdir(carpeta):
                time.sleep(1)
            if not os.path.isdir(carpeta):
                print(f"[timelapse] no aparecio {carpeta} (¿memoria USB?): no se reanuda")
                self._borrar_estado()
                return None
            # Con la hora mal (sin red), al menos no retroceder.
            ahora = max(time.time(), ultimo)
            if self.is_running():
                # Alguien inicio otro mientras esperabamos: ese manda (y
                # ya escribio su propio archivo de estado).
                self.experimentos.finalizar(
                    carpeta, estado="interrumpido por un corte de luz",
                    fin=datetime.fromtimestamp(ultimo).isoformat(timespec="seconds"))
                return None
            if ahora - inicio >= p["duration_seconds"]:
                fin = datetime.fromtimestamp(inicio + p["duration_seconds"])
                self.experimentos.finalizar(
                    carpeta, fin=fin.isoformat(timespec="seconds"),
                    estado="terminó mientras la Raspberry estaba apagada (corte de luz)",
                    ciclos=int(datos.get("ciclo", 0)))
                print("[timelapse] la duracion se cumplio durante el corte: se cierra")
                self._borrar_estado()
                return None
            datos["ahora_ts"] = ahora
            self._lanzar(p, reanudar=datos)
            return datos
        finally:
            self.reanudando = None

    def resumen(self):
        """Lo que la pagina muestra de un timelapse en curso."""
        p = self.config or {}
        iso = (lambda t: datetime.fromtimestamp(t).isoformat(timespec="seconds")
               if t else None)
        return {
            "nombre": getattr(self, "nombre_experimento", None),
            "modo": p.get("modo"),
            "intervalo_s": p.get("interval_seconds"),
            "duracion_s": p.get("duration_seconds"),
            "camaras": p.get("camaras"),
            "inicio": iso(self.inicio_wall),
            "fin_previsto": iso(self.inicio_wall + p["duration_seconds"])
                            if self.inicio_wall and p else None,
            "proxima": iso(self.proxima_wall),
            "reanudaciones": list(self.reanudaciones),
            "ciclo": self.ciclo_actual,
            "pausado": self.pausado,
            "pausas": [list(x) for x in self.pausas],
            "carpeta": os.path.basename(self.base_folder) if self.base_folder else None,
            "ultimas": {str(c): {"ciclo": u["ciclo"], "hora": u["hora"],
                                 "dpc": bool(u.get("dpc"))}
                        for c, u in sorted(self.ultimas.items())},
        }

    # ---------- el timelapse ----------
    def _run(self, p, reanudar=None):
        """Envoltorio de _run_interno: si algo inesperado tumba el hilo,
        el experimento queda cerrado con el motivo, se avisa (alertas) y
        el estado vuelve a parado, en vez de quedar "corriendo" para
        siempre sin tomar fotos."""
        try:
            self._run_interno(p, reanudar)
        except Exception as e:
            import traceback
            self._log(f"El timelapse se detuvo por un error: {e}\n{traceback.format_exc()}")
            for luz in self.illuminations.values():
                try:
                    luz.off()
                except Exception:
                    pass
            try:
                if self.base_folder:
                    self.experimentos.finalizar(
                        self.base_folder, fin=datetime.now().isoformat(timespec="seconds"),
                        estado=f"se detuvo por un error: {e}", ciclos=self.ciclo_actual)
            except Exception:
                pass
            if self.alertas is not None:
                self.alertas.timelapse_error(getattr(self, "nombre_experimento", ""), e)
            self._borrar_estado()
            self.proxima_wall = None
            self.state = TimelapseState.ERROR
        finally:
            if self.alertas is not None and self.state != TimelapseState.ERROR:
                self.alertas.timelapse_fin(getattr(self, "nombre_experimento", ""),
                                           self.ciclo_actual, bool(getattr(self, "_detenido", False)))

    def _run_interno(self, p, reanudar=None):
        modo = p["modo"]
        interval_seconds = p["interval_seconds"]
        duration_seconds = p["duration_seconds"]
        stabilization_time = p["stabilization_time"]
        camaras = p["camaras"]
        simultaneo = p["simultaneo"]
        autofocus, autofocus_cada = p["autofocus"], p["autofocus_cada"]
        autofocus_opts = p["autofocus_opts"]
        contar, contar_cada, contar_opts = p["contar"], p["contar_cada"], p["contar_opts"]
        dpc_opts = p.get("dpc_opts")

        self.config = p
        patrones = MODOS[modo]

        if reanudar is None:
            # Carpeta del experimento: datos/AAAA-MM-DD_HHMM_<nombre>, o en
            # la memoria USB (carpeta_raiz) bajo MicroscopeOS/.
            raiz = (os.path.join(p["carpeta_raiz"], "MicroscopeOS")
                    if p["carpeta_raiz"] else None)
            self.base_folder = str(self.experimentos.crear_timelapse(p["nombre"], raiz=raiz))
            ciclo = 0
            self.inicio_wall = time.time()
            self.reanudaciones = []
            self.ultimas = {}
            self.pausado = False
            self.pausas = []
        else:
            self.base_folder = reanudar["carpeta"]
            ciclo = int(reanudar.get("ciclo", 0))
            self.inicio_wall = float(reanudar["inicio_ts"])
            self.reanudaciones = list(reanudar.get("reanudaciones") or []) + [
                datetime.now().isoformat(timespec="seconds")]
            # Si estaba en pausa cuando se corto la luz, sigue en pausa.
            self.pausado = bool(reanudar.get("pausado"))
            self.pausas = [list(x) for x in reanudar.get("pausas") or []]
            # self.ultimas ya lo cargo _lanzar desde el disco
        self.nombre_experimento = self.experimentos.info(self.base_folder)["nombre"]
        self.ciclo_actual = ciclo
        for cam in camaras:
            os.makedirs(os.path.join(self.base_folder, f"cam{cam}"), exist_ok=True)
        # Procesar el DPC al terminar cada ciclo (solo tiene sentido en
        # modo dpc): un hilo aparte con su cola.
        if modo != "dpc":
            dpc_opts = None
        cola_dpc = hilo_dpc = None
        if dpc_opts is not None:
            cola_dpc = queue.Queue()
            hilo_dpc = threading.Thread(target=self._procesar_dpc,
                                        args=(cola_dpc, dpc_opts), daemon=True)
            hilo_dpc.start()
        borrar_crudas = bool(dpc_opts is not None
                             and dpc_opts.get("borrar_crudas", True))
        self._enviar(self._escribir_metadatos(modo, interval_seconds,
                                              duration_seconds, camaras, p["nombre"],
                                              dpc_opts))
        if reanudar is not None:
            self.experimentos.actualizar(self.base_folder, estado="en curso",
                                         reanudaciones=self.reanudaciones)

        # Crear CSV con cabecera (al reanudar, se sigue agregando al mismo)
        csv_path = os.path.join(self.base_folder, "temperatura.csv")
        if reanudar is None or not os.path.exists(csv_path):
            with open(csv_path, "w") as f:
                f.write(CABECERA_TEMP)

        if autofocus and self.autofocus is None:
            self._log("Autofoco pedido pero no hay motores de enfoque "
                      "disponibles -- se continua sin autofoco.")
            autofocus = False
        # Posicion de cada eje al primer autofoco (um): referencia para
        # DERIVA_MAXIMA_UM y para la columna deriva_um de autofoco.csv.
        # Al reanudar se vuelve a tomar: los motores cuentan desde cero
        # despues de reiniciar.
        self._ancla_um = {}
        self._fallos_af = {}
        if contar and self.contador is None:
            self._log("Conteo de celulas pedido pero no hay contador "
                      "disponible -- se continua sin conteo.")
            contar = False

        self._log(f"Timelapse {'reanudado' if reanudar else 'iniciado'} | modo={modo} | camaras={camaras} | "
                  f"intervalo={interval_seconds}s | duracion={duration_seconds}s | "
                  f"captura={'simultanea' if simultaneo else 'secuencial'} | "
                  f"autofoco={'cada ' + str(autofocus_cada) + ' ciclo(s)' if autofocus else 'no'} | "
                  f"conteo={'cada ' + str(contar_cada) + ' ciclo(s)' if contar else 'no'} | "
                  f"destino={os.path.abspath(self.base_folder)} | "
                  f"envio_pc={'si' if self.enviar_pc and self.enviador else 'no'} | "
                  f"respaldo_nas={'si' if self.respaldar_nas and self.respaldo_nas else 'no'} | "
                  f"dpc={'no' if dpc_opts is None else ('calcular y borrar crudas' if borrar_crudas else 'calcular')}"
                  + (" + fase" if dpc_opts and dpc_opts.get("fase") else ""))

        if reanudar is not None and cola_dpc is not None:
            pendientes = self._pendientes_dpc(camaras, dpc_opts)
            if pendientes:
                self._log(f"DPC pendiente de antes del corte: {len(pendientes)} captura(s), "
                          f"se procesan ahora")
            for item in pendientes:
                cola_dpc.put(item)

        # Los tiempos van contra el inicio ORIGINAL: al reanudar, la
        # duracion no vuelve a empezar y las fotos siguen en la misma
        # grilla (inicio + k*intervalo).
        transcurrido = 0.0
        if reanudar is not None:
            transcurrido = max(0.0, float(reanudar.get("ahora_ts") or time.time())
                               - self.inicio_wall)
        start_time = time.monotonic() - transcurrido
        next_capture_time = time.monotonic()
        tras_reanudar = None
        forzar_af = False
        if reanudar is not None:
            esperadas = int(transcurrido // interval_seconds) + 1
            self._log(f"Reanudado despues de un corte de luz o reinicio: "
                      f"{transcurrido / 60:.1f} min desde el inicio, {ciclo} ciclo(s) hechos, "
                      f"se perdieron ~{max(0, esperadas - ciclo)} foto(s). "
                      f"Se toma una ahora y se sigue cada {interval_seconds}s.")
            # Una foto apenas vuelve (con autofoco: la temperatura y el
            # reinicio pueden haber movido el foco); despues, la grilla.
            tras_reanudar = start_time + esperadas * interval_seconds
            if tras_reanudar - time.monotonic() < interval_seconds / 2:
                tras_reanudar += interval_seconds
            forzar_af = True
        self.proxima_wall = time.time() + (next_capture_time - time.monotonic())
        self._guardar_estado(ciclo)

        en_pausa = self.pausado
        while self.state == TimelapseState.RUNNING:
            current_time = time.monotonic()

            if current_time - start_time >= duration_seconds:
                break

            if self.pausado:
                en_pausa = True
                self.proxima_wall = None
                time.sleep(0.2)
                continue
            if en_pausa:
                # Recien sale de la pausa: una foto ya (con autofoco, por
                # si movieron la placa) y despues cada intervalo desde ahi.
                # Las anclas de deriva se reinician: si alguien enfoco a
                # mano durante la pausa, esa es la referencia nueva.
                en_pausa = False
                try:
                    self.camera.stop_preview()
                except Exception:
                    pass
                next_capture_time = current_time
                tras_reanudar = None
                forzar_af = True
                self._ancla_um = {}

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
                if autofocus and (forzar_af or (ciclo - 1) % max(1, autofocus_cada) == 0):
                    self._autoenfocar(camaras, ciclo, ts, autofocus_opts)
                forzar_af = False

                if simultaneo:
                    guardadas = self._capturar_simultaneo(
                        patrones, camaras, ahora, stabilization_time)
                else:
                    guardadas = self._capturar_secuencial(
                        patrones, camaras, ahora, stabilization_time)

                for cam, rutas in guardadas.items():
                    if rutas:
                        una = next(iter(rutas.items()))
                        self.ultimas[cam] = {"ciclo": ciclo,
                                             "hora": ahora.isoformat(timespec="seconds"),
                                             "rutas": dict(rutas),
                                             "base": una[1][:-len(una[0] + ".tif")],
                                             "dpc": False}

                # Si las crudas se van a borrar no se mandan: la cola de
                # envio lee el archivo mas tarde y ya no estaria. Se manda
                # el DPC que sale de ellas (ver _procesar_dpc).
                if not borrar_crudas:
                    for cam, rutas in sorted(guardadas.items()):
                        for sufijo, _ in patrones:
                            if sufijo in rutas:
                                self._enviar(rutas[sufijo], f"cam{cam}")

                # El conteo va antes del DPC: analiza las crudas, que
                # despues se borran.
                if contar and (ciclo - 1) % max(1, contar_cada) == 0:
                    self._contar_ciclo(guardadas, ciclo, ts, contar_opts)

                if cola_dpc is not None:
                    for cam, rutas in sorted(guardadas.items()):
                        cola_dpc.put((cam, dict(rutas), ciclo))
                    if cola_dpc.qsize() > 2 * len(camaras):
                        self._log(f"  dpc: {cola_dpc.qsize()} ciclos esperando -- "
                                  f"el calculo no alcanza a seguir el intervalo")

                if tras_reanudar is not None:
                    next_capture_time, tras_reanudar = tras_reanudar, None
                else:
                    next_capture_time += interval_seconds
                self.proxima_wall = time.time() + (next_capture_time - time.monotonic())
                self._guardar_estado(ciclo)

            else:
                time.sleep(0.05)

        for luz in self.illuminations.values():
            try:
                luz.off()
            except Exception:
                pass

        if hilo_dpc is not None:
            if not cola_dpc.empty():
                self._log(f"Terminando el DPC de {cola_dpc.qsize()} captura(s) pendientes...")
            cola_dpc.put(None)
            hilo_dpc.join()

        if self.pausas and self.pausas[-1][1] is None:
            self.pausas[-1][1] = datetime.now().isoformat(timespec="seconds")
            self._guardar_pausas()
        self.pausado = False
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
        # Termino (o lo detuvieron): ya no hay nada que reanudar.
        self._borrar_estado()
        self.proxima_wall = None
        self.state = TimelapseState.STOPPED

    def _lanzar(self, p, reanudar=None):
        self.enviar_pc = bool(p.get("enviar_pc"))
        self.respaldar_nas = bool(p.get("respaldar_nas"))
        # RUNNING ya desde aca (y no recien dentro del hilo): asi un segundo
        # start() o el /status de la pagina no ven "parado" en el medio.
        self._detenido = False
        if reanudar is not None:
            # Antes de lanzar el hilo, para que la pagina muestre desde ya
            # la ultima foto de antes del corte.
            self.base_folder = reanudar["carpeta"]
            self.ultimas = self._ultimas_de_disco(p["camaras"])
        self.state = TimelapseState.RUNNING
        self.thread = threading.Thread(target=self._run, args=(p, reanudar), daemon=True)
        self.thread.start()

    def start(self, modo="blanco", interval_seconds=300, duration_seconds=3600,
              stabilization_time=0.3, camaras=[0, 1], simultaneo=False,
              autofocus=False, autofocus_cada=1, autofocus_opts=None,
              contar=False, contar_cada=1, contar_opts=None,
              carpeta_raiz="", enviar_pc=False, nombre="", respaldar_nas=False,
              dpc_opts=None):
        """carpeta_raiz: donde crear timelapse_<fecha> ("" = directorio
        de trabajo, o el punto de montaje de una memoria USB).
        enviar_pc: mandar cada imagen a la PC de segmentacion mientras
        corre (requiere un EnviadorPC configurado).
        dpc_opts: en modo dpc, calcular el DPC de cada ciclo y (por
        defecto) borrar las 4 crudas; None = guardar solo las crudas.
        Claves en core.dpc.OPCIONES."""
        if self.state == TimelapseState.RUNNING:
            print("Timelapse ya esta corriendo.")
            return
        if modo not in MODOS:
            print(f"Modo invalido: {modo}. Usa uno de: {', '.join(MODOS)}.")
            return

        # Todo lo necesario para reanudarlo tal cual (va al archivo de
        # estado, asi que tiene que ser JSON).
        p = {"modo": modo, "interval_seconds": interval_seconds,
             "duration_seconds": duration_seconds,
             "stabilization_time": stabilization_time, "camaras": list(camaras),
             "simultaneo": bool(simultaneo), "autofocus": bool(autofocus),
             "autofocus_cada": autofocus_cada, "autofocus_opts": autofocus_opts or {},
             "contar": bool(contar), "contar_cada": contar_cada,
             "contar_opts": contar_opts or {}, "carpeta_raiz": carpeta_raiz or "",
             "nombre": nombre or "", "enviar_pc": bool(enviar_pc),
             "respaldar_nas": bool(respaldar_nas),
             "dpc_opts": dict(dpc_opts) if dpc_opts is not None else None}
        self._lanzar(p)

    # ---------- pausa y notas ----------
    def pausar(self, autor=""):
        """Deja de tomar fotos sin cerrar el experimento. Devuelve False
        si no hay timelapse o ya estaba en pausa."""
        if not self.is_running() or self.pausado:
            return False
        self.pausado = True
        self.pausas.append([datetime.now().isoformat(timespec="seconds"), None])
        self._log(f"En pausa{' (' + autor + ')' if autor else ''}")
        self._nota_auto("pausa", "Timelapse en pausa", autor)
        self._guardar_estado(self.ciclo_actual)
        self._guardar_pausas()
        return True

    def continuar(self, autor=""):
        """Sale de la pausa: toma una foto enseguida y sigue."""
        if not self.is_running() or not self.pausado:
            return False
        if self.pausas and self.pausas[-1][1] is None:
            self.pausas[-1][1] = datetime.now().isoformat(timespec="seconds")
        self.pausado = False
        self._log(f"Sale de la pausa{' (' + autor + ')' if autor else ''}")
        self._nota_auto("reanudar", "Timelapse reanudado", autor)
        self._guardar_estado(self.ciclo_actual)
        self._guardar_pausas()
        return True

    def _guardar_pausas(self):
        try:
            self.experimentos.actualizar(self.base_folder, pausas=self.pausas)
        except Exception:
            pass

    def _nota_auto(self, tipo, texto, autor):
        try:
            agregar_nota(self.base_folder, texto, autor=autor,
                         ciclo=self.ciclo_actual, tipo=tipo)
        except Exception as e:
            self._log(f"No se pudo guardar la nota: {e}")

    def agregar_nota(self, texto, autor=""):
        """Nota con hora en el experimento en curso (notas.csv)."""
        if not self.base_folder or not self.is_running():
            raise ValueError("No hay un timelapse en curso")
        nota = agregar_nota(self.base_folder, texto, autor=autor,
                            ciclo=self.ciclo_actual)
        self._log(f"Nota{' de ' + autor if autor else ''}: {nota['texto']}")
        return nota

    def stop(self):
        if self.state == TimelapseState.RUNNING:
            self._detenido = True
            self.state = TimelapseState.STOPPED
            if self.thread is not None:
                self.thread.join()

    def is_running(self):
        return self.state == TimelapseState.RUNNING

    def en_pausa(self):
        return self.is_running() and self.pausado
