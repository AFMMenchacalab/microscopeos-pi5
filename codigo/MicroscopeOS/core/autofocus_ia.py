"""Autofoco aprendido: inferencia del desenfoque a partir del par DPC.

QUE APORTA SOBRE EL METODO ANALITICO
====================================

El autofoco de dos etapas (core/autofocus.py) funciona, pero tiene dos
limites conocidos:

  - necesita una calibracion por objetivo (la recta corrimiento-vs-Z);
  - esa recta solo vale CERCA del foco. Mas lejos el corrimiento satura
    y la etapa 1 se queda corta, asi que hacen falta varias iteraciones
    o directamente cae al barrido a ciegas.

Una red entrenada sobre pilas de foco reales aprende la curva completa,
saturacion incluida, y puede arrancar desde mucho mas lejos de una sola
vez. La etapa 2 (parabola sobre Tenengrad del DPC) se conserva igual:
cerca del foco sigue siendo mas precisa que cualquier regresion, porque
mide en vez de estimar.

ESTADO
======

El codigo de inferencia esta completo y probado con un predictor de
mentira (ver tests/test_analisis.py); el MODELO no existe todavia,
porque hace falta grabar pilas de foco en el microscopio y eso pide
hardware y una muestra. Sin archivo de modelo, `disponible()` devuelve
False y `Autofocus.enfocar_auto()` sigue usando el metodo analitico sin
enterarse. Nada de esto se activa solo.

El camino completo es:

  1. core/pila_foco.py     grabar pilas (necesita el microscopio)
  2. extras/ia/entrenar_autofoco.py   entrenar y exportar (Colab)
  3. dejar el .onnx en profiles/  ->  este modulo lo levanta al arrancar

PREPROCESADO
============

`preparar()` es la unica definicion del preprocesado y la comparten el
entrenamiento y la inferencia -- el script de Colab importa esta misma
funcion. Si entrenamiento e inferencia normalizaran distinto, el modelo
funcionaria perfecto en la laptop y daria basura en la Pi, sin ningun
error visible. Por eso este archivo no importa nada de core/: tiene que
poder subirse solo a Colab.
"""

import json
import os
from pathlib import Path

import cv2
import numpy as np

# Lado de la entrada de la red. Chico a proposito: la senial de
# desenfoque es de escala grande (un corrimiento global entre las dos
# mitades), no textura fina, asi que 224 alcanza y la inferencia en la
# CPU de la Pi se mantiene en decimas de segundo.
LADO = 224

MODELO_POR_DEFECTO = (Path(__file__).resolve().parent.parent /
                      "profiles" / "autofoco_ia.onnx")


def preparar(izq, der):
    """Par de medias aperturas -> tensor (1, 2, LADO, LADO) float32.

    Las dos imagenes van como DOS CANALES de la misma entrada, no
    restadas ni concatenadas al costado: el desenfoque con signo esta en
    la relacion entre ambas, y darle las dos crudas deja que la red
    aprenda que combinacion le sirve en vez de imponerle el DPC.

    La normalizacion es por PAR y no por imagen: se usa la media y el
    desvio de las dos juntas, para no borrar el desbalance de brillo
    entre mitades... pero se conserva la diferencia RELATIVA, que es la
    senial. Normalizar cada mitad por separado la destruiria.
    """
    par = []
    for img in (izq, der):
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        par.append(cv2.resize(img.astype(np.float32), (LADO, LADO),
                              interpolation=cv2.INTER_AREA))
    x = np.stack(par, axis=0)
    media = float(x.mean())
    desvio = float(x.std())
    if desvio > 1e-6:
        x = (x - media) / desvio
    else:
        x = x - media
    return x[None, ...].astype(np.float32)


class AutofocoIA:
    """Envoltorio de inferencia. Degrada a "no disponible" en silencio.

    Se construye siempre, aunque no haya modelo ni onnxruntime: el
    servicio arranca solo en cada boot de la Pi y no puede depender de
    que un archivo opcional exista.
    """

    def __init__(self, ruta=None, predictor=None):
        self.ruta = Path(ruta or MODELO_POR_DEFECTO)
        self.error = None
        self.meta = {}
        # predictor inyectable: para poder probar TODO el camino
        # (medir -> predecir -> mover -> ajuste fino) sin un modelo
        # entrenado, que es lo unico que no se puede fabricar sin
        # hardware. Recibe (izq, der) y devuelve micras con signo.
        self._predictor = predictor
        self._sesion = None
        if predictor is None:
            self._cargar()

    def _cargar(self):
        if not self.ruta.exists():
            self.error = f"sin modelo en {self.ruta}"
            return
        try:
            import onnxruntime
        except ImportError:
            self.error = "onnxruntime no instalado"
            return
        try:
            self._sesion = onnxruntime.InferenceSession(
                str(self.ruta), providers=["CPUExecutionProvider"])
            self._entrada = self._sesion.get_inputs()[0].name
        except Exception as e:
            self.error = f"no se pudo cargar el modelo: {e}"
            self._sesion = None
            return
        meta = self.ruta.with_suffix(".json")
        if meta.exists():
            try:
                self.meta = json.loads(meta.read_text())
            except Exception:
                pass

    def disponible(self):
        return self._predictor is not None or self._sesion is not None

    def estado(self):
        return {
            "disponible": self.disponible(),
            "modelo": str(self.ruta),
            "error": self.error,
            "meta": self.meta,
        }

    def predecir(self, izq, der):
        """Desenfoque estimado en MICRAS, con signo."""
        if self._predictor is not None:
            return float(self._predictor(izq, der))
        if self._sesion is None:
            raise RuntimeError(self.error or "modelo no disponible")
        salida = self._sesion.run(None, {self._entrada: preparar(izq, der)})
        return float(np.ravel(salida[0])[0])

    # -----------------------------------------------------------------
    def enfocar(self, autofocus, camera_num, iteraciones=2, eje="lr",
                settle=0.25, roi=1.0, delay=0.003, backlash=400,
                max_um=400.0, tolerancia_um=1.0, fino=True,
                rango_fino_um=8.0, puntos_fino=7):
        """Etapa 1 con la red + etapa 2 analitica (la de siempre).

        No reemplaza al autofoco existente: reemplaza SOLO la estimacion
        gruesa. El ajuste fino sigue siendo la parabola sobre Tenengrad
        del DPC, que cerca del foco mide en vez de predecir y no puede
        equivocarse por una muestra distinta a las del entrenamiento.
        """
        import time
        from core.autofocus import EJES_DPC, micropasos_por_um

        motor = autofocus.motores.get(camera_num)
        if motor is None:
            raise RuntimeError(f"cam{camera_num} no tiene motor de enfoque")
        luz = autofocus.illuminations.get(camera_num)
        if luz is None:
            raise RuntimeError(f"cam{camera_num} no tiene matriz")
        metodo_a, metodo_b, _ = EJES_DPC[eje]

        t0 = time.monotonic()
        inicio = motor.position
        historial = []
        try:
            for _ in range(max(1, int(iteraciones))):
                getattr(luz, metodo_a)()
                time.sleep(settle)
                a = autofocus._frame(camera_num, roi)
                getattr(luz, metodo_b)()
                time.sleep(settle)
                b = autofocus._frame(camera_num, roi)

                um = self.predecir(a, b)
                # Tope duro: sin finales de carrera (TODO_HW.md 1.5) una
                # prediccion disparatada -- una muestra que no se parece
                # a nada del entrenamiento -- no puede convertirse en un
                # viaje de la plataforma contra el objetivo.
                um = max(-max_um, min(max_um, um))
                historial.append(round(um, 2))
                if abs(um) <= tolerancia_um:
                    break
                pasos = micropasos_por_um(-um, motor.microsteps)
                if pasos == 0:
                    break
                motor.mover_a(motor.position + pasos, delay=delay,
                              backlash=backlash)
        finally:
            try:
                luz.off()
            except Exception:
                pass

        resultado = {
            "metodo": "ia",
            "posicion": motor.position,
            "desplazamiento": motor.position - inicio,
            "desplazamiento_um": round(
                (motor.position - inicio) *
                (1000.0 / (200 * motor.microsteps)), 2),
            "predicciones_um": historial,
            "convergio": bool(historial and
                              abs(historial[-1]) <= tolerancia_um),
        }

        if fino:
            from core.autofocus import micropasos_por_um as _mpu
            resultado["fino"] = autofocus._ajuste_fino(
                camera_num, motor, eje,
                _mpu(rango_fino_um, motor.microsteps), puntos_fino,
                delay, settle, roi, backlash)
            resultado["posicion"] = motor.position
            resultado["desplazamiento"] = motor.position - inicio

        resultado["segundos"] = round(time.monotonic() - t0, 1)
        return resultado
