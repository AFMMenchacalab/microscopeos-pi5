"""Grabacion de pilas de foco: el dataset para entrenar un autofoco IA.

POR QUE ESTO EXISTE ANTES QUE EL MODELO
=======================================

Un autofoco aprendido resuelve el punto debil del metodo de dos etapas
que ya esta en core/autofocus.py: ese necesita una calibracion por
objetivo, y la relacion corrimiento-vs-desenfoque solo es lineal cerca
del foco (mas lejos satura, y la etapa 1 se queda corta). Una red que
mire el par de medias aperturas puede aprender la curva ENTERA, incluida
la zona saturada, y no necesita recalibrarse al cambiar de objetivo si
se la entreno con varios.

Pero para entrenarla hacen falta pares (imagen, desenfoque real), y ese
dato no se puede inventar ni sacar de imagenes sueltas: hay que
grabarlo moviendo el eje Z a posiciones conocidas. Eso es lo que hace
este modulo, y es el unico paso que REQUIERE el microscopio. El resto
(entrenar, exportar) corre en cualquier lado -- ver
extras/ia/entrenar_autofoco.py.

DE DONDE SALE LA ETIQUETA
=========================

Del propio motor: si el usuario deja la muestra enfocada a ojo y desde
ahi se barre Z, el desenfoque de cada plano es su distancia al centro,
en micropasos, que se convierte a micras con la geometria del husillo.
La etiqueta es exacta salvo por lo bien que el usuario haya enfocado y
por el juego mecanico -- por eso cada posicion se alcanza SIEMPRE desde
el mismo sentido (backlash), igual que en el autofoco.

Si la camara ya tiene calibracion DPC, conviene correr un autofoco
antes de grabar: el centro queda puesto por el metodo y no por el ojo,
y las etiquetas salen mejor.

QUE SE GRABA
============

Las dos medias aperturas (izquierda y derecha) de cada plano, no una
imagen sola. Una celula sin tenir es un objeto de FASE: en una sola
imagen de campo claro no hay nada que ver en el foco, y el signo del
desenfoque no esta en ninguna imagen individual. Vive en la DIFERENCIA
entre las dos mitades. Una red alimentada con una sola imagen podria
como mucho aprender cuanto desenfoque hay, nunca hacia donde.
"""

import json
import os
import time
from datetime import datetime

import cv2
import numpy as np

from core.autofocus import EJES_DPC, um_por_micropaso


def grabar_pila(autofocus, camera_num, rango=1600, puntos=25, eje="lr",
                carpeta=None, settle=0.25, backlash=400, delay=0.003,
                centro=None, notas=None, progreso=None):
    """Barre Z y guarda el par de medias aperturas de cada plano.

    rango    recorrido total en micropasos, repartido a los dos lados
             del centro. Conviene que cubra bastante mas que el rango
             lineal: la gracia de la red es justamente aprender la zona
             donde el metodo analitico satura.
    puntos   planos del barrido. Con 25 y un rango de 1600 quedan 64
             micropasos (20 um a 1/16) entre planos.
    centro   posicion que se considera EN FOCO. Por defecto la actual,
             que es lo que corresponde si el usuario acaba de enfocar.

    Devuelve el manifiesto (tambien escrito como JSON en la carpeta).
    """
    motor = autofocus.motores.get(camera_num)
    if motor is None:
        raise RuntimeError(f"cam{camera_num} no tiene motor de enfoque")
    luz = autofocus.illuminations.get(camera_num)
    if luz is None:
        raise RuntimeError(f"cam{camera_num} no tiene matriz de iluminacion")
    if eje not in EJES_DPC:
        raise ValueError(f"eje invalido: {eje}")
    metodo_a, metodo_b, _ = EJES_DPC[eje]

    puntos = max(3, int(puntos))
    centro = int(motor.position if centro is None else centro)
    paso = max(1, int(round(rango / (puntos - 1))))
    inicio = centro - paso * (puntos - 1) // 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    carpeta = carpeta or f"pilas_foco/cam{camera_num}_{stamp}"
    os.makedirs(carpeta, exist_ok=True)

    um = um_por_micropaso(motor.microsteps)
    manifiesto = {
        "camara": camera_num,
        "timestamp": stamp,
        "eje": eje,
        "centro": centro,
        "microsteps": motor.microsteps,
        "um_por_micropaso": round(um, 4),
        "rango_micropasos": paso * (puntos - 1),
        "paso_micropasos": paso,
        "notas": notas or "",
        "planos": [],
    }

    t0 = time.monotonic()
    try:
        for i in range(puntos):
            objetivo = inicio + i * paso
            # Mismo backlash que el autofoco: si los planos se
            # alcanzaran desde lados distintos, la etiqueta tendria un
            # error sistematico del tamanio del juego del husillo
            # (5-20 um), que es mas grande que la precision que se le
            # va a pedir a la red.
            motor.mover_a(objetivo, delay=delay, backlash=backlash)
            time.sleep(settle)

            getattr(luz, metodo_a)()
            time.sleep(settle)
            a = autofocus.camera.get_focus_frame(camera_num)
            getattr(luz, metodo_b)()
            time.sleep(settle)
            b = autofocus.camera.get_focus_frame(camera_num)

            nombre_a = f"z{i:03d}_{metodo_a}.png"
            nombre_b = f"z{i:03d}_{metodo_b}.png"
            cv2.imwrite(os.path.join(carpeta, nombre_a), _a8(a))
            cv2.imwrite(os.path.join(carpeta, nombre_b), _a8(b))

            offset = objetivo - centro
            manifiesto["planos"].append({
                "indice": i,
                "posicion": objetivo,
                "offset_micropasos": offset,
                "offset_um": round(offset * um, 3),
                "archivos": {metodo_a: nombre_a, metodo_b: nombre_b},
            })
            if progreso:
                progreso(i + 1, puntos, objetivo)
    finally:
        try:
            luz.off()
        except Exception:
            pass
        # Volver al centro SIEMPRE, incluso si el barrido se corto: la
        # muestra tiene que quedar como estaba, no en el ultimo plano
        # del barrido, que puede estar muy desenfocado.
        try:
            motor.mover_a(centro, delay=delay, backlash=backlash)
        except Exception:
            pass

    manifiesto["segundos"] = round(time.monotonic() - t0, 1)
    ruta = os.path.join(carpeta, "manifiesto.json")
    with open(ruta, "w") as f:
        json.dump(manifiesto, f, indent=2)

    return {"carpeta": carpeta, "manifiesto": ruta,
            "planos": len(manifiesto["planos"]),
            "rango_um": round(manifiesto["rango_micropasos"] * um, 1),
            "segundos": manifiesto["segundos"]}


def _a8(gray):
    """A 8 bits sin normalizar por imagen.

    Normalizar cada plano por su cuenta seria un error sutil pero fatal
    para el dataset: el contraste CAMBIA con el desenfoque, y esa es
    justamente la senial. Reescalar cada imagen a su propio minimo y
    maximo la borraria y la red aprenderia sobre una pista que no
    existe. Se recorta al rango del sensor y listo.
    """
    if gray.dtype == np.uint8:
        return gray
    return np.clip(gray / 257.0 if gray.dtype == np.uint16 else gray,
                   0, 255).astype(np.uint8)


def cargar_pila(carpeta):
    """Lee un manifiesto y devuelve (pares, offsets_um).

    pares: lista de (imagen_a, imagen_b) en gris; offsets en micras, con
    signo. Es lo que consume el script de entrenamiento.
    """
    with open(os.path.join(carpeta, "manifiesto.json")) as f:
        manifiesto = json.load(f)
    pares, offsets = [], []
    for plano in manifiesto["planos"]:
        imgs = []
        for nombre in plano["archivos"].values():
            img = cv2.imread(os.path.join(carpeta, nombre),
                             cv2.IMREAD_GRAYSCALE)
            if img is None:
                break
            imgs.append(img)
        if len(imgs) == 2:
            pares.append(tuple(imgs))
            offsets.append(plano["offset_um"])
    return pares, np.array(offsets, dtype=np.float32)
