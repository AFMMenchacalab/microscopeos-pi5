"""Entrena el regresor de desenfoque y lo exporta a ONNX para la Pi.

Corre en Colab (GPU gratis) o en cualquier maquina con PyTorch. No
necesita el microscopio: consume las pilas de foco ya grabadas.

    pip install torch onnx
    python entrenar_autofoco.py pilas_foco/*/ --salida autofoco_ia.onnx

QUE ESPERA COMO ENTRADA
=======================

Carpetas generadas por core/pila_foco.py, cada una con su
manifiesto.json. Cada plano aporta un ejemplo: las dos medias aperturas
como dos canales, y el desenfoque real en micras como etiqueta.

CUANTOS DATOS HACEN FALTA
=========================

Una pila de 25 planos son 25 ejemplos: nada. Lo que hace falta es
VARIEDAD, y viene de grabar muchas pilas -- distintos campos, distintas
muestras, distintas densidades, los dos objetivos. Con una sola pila la
red memoriza ese campo y no generaliza a ninguna otra cosa; da un error
de entrenamiento precioso y falla en el microscopio.

Regla practica para arrancar: 20-30 pilas de campos distintos (unas
600 imagenes) para ver si el enfoque funciona; mas para confiar en el.
El recorte aleatorio y el espejado horizontal ayudan, pero no
reemplazan campos nuevos.

POR QUE EL ERROR SE MIDE EN MICRAS Y NO EN LOSS
===============================================

Lo unico que importa es si la prediccion cae dentro de la profundidad
de campo del objetivo. Un MSE de 0.03 no dice nada; "error mediano de
1.8 um" se compara directo contra el objetivo que se esta usando.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np

# El preprocesado es el MISMO archivo que usa la Pi para inferir. Si
# aca se normalizara distinto que alla, el modelo andaria perfecto en
# el entrenamiento y daria basura en el microscopio, sin ningun error
# visible. Por eso se importa en vez de copiarse: subi tambien
# core/autofocus_ia.py junto a este script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from autofocus_ia import LADO, preparar
except ImportError:
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "MicroscopeOS", "core"))
    from autofocus_ia import LADO, preparar


def cargar_datos(carpetas):
    """Todas las pilas -> (X, y, grupos).

    `grupos` marca de que pila viene cada ejemplo, para partir
    entrenamiento/validacion POR PILA y no por imagen. Partir por imagen
    seria hacer trampa: los planos vecinos de una misma pila son casi
    identicos, asi que la validacion tendria copias de lo que se
    entreno y el error saldria optimista.
    """
    import cv2
    X, y, grupos = [], [], []
    for g, carpeta in enumerate(carpetas):
        ruta = os.path.join(carpeta, "manifiesto.json")
        if not os.path.exists(ruta):
            print(f"  (sin manifiesto, se omite) {carpeta}")
            continue
        with open(ruta) as f:
            manifiesto = json.load(f)
        n = 0
        for plano in manifiesto["planos"]:
            imgs = []
            for nombre in plano["archivos"].values():
                img = cv2.imread(os.path.join(carpeta, nombre),
                                 cv2.IMREAD_GRAYSCALE)
                if img is None:
                    break
                imgs.append(img)
            if len(imgs) != 2:
                continue
            X.append(preparar(imgs[0], imgs[1])[0])
            y.append(plano["offset_um"])
            grupos.append(g)
            n += 1
        print(f"  {n:3d} planos  {carpeta}")
    if not X:
        raise SystemExit("no se cargo ninguna pila")
    return (np.stack(X), np.array(y, dtype=np.float32),
            np.array(grupos))


def construir_red(torch, nn):
    """U-Net no: esto es REGRESION, no segmentacion.

    La salida es un solo numero (micras de desenfoque), asi que lo que
    corresponde es un encoder convolucional chico con pooling global. Un
    U-Net tiene decoder para reconstruir una imagen del mismo tamanio,
    que aca no se usa para nada -- seria varias veces mas lento en la
    CPU de la Pi para producir el mismo escalar.
    """
    def bloque(entra, sale):
        return nn.Sequential(
            nn.Conv2d(entra, sale, 3, padding=1),
            nn.BatchNorm2d(sale), nn.ReLU(inplace=True),
            nn.Conv2d(sale, sale, 3, padding=1),
            nn.BatchNorm2d(sale), nn.ReLU(inplace=True),
            nn.MaxPool2d(2))

    return nn.Sequential(
        bloque(2, 16), bloque(16, 32), bloque(32, 64), bloque(64, 96),
        nn.AdaptiveAvgPool2d(1), nn.Flatten(),
        nn.Linear(96, 64), nn.ReLU(inplace=True),
        nn.Dropout(0.2), nn.Linear(64, 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("carpetas", nargs="+", help="carpetas de pilas de foco")
    ap.add_argument("--salida", default="autofoco_ia.onnx")
    ap.add_argument("--epocas", type=int, default=120)
    ap.add_argument("--lote", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    args = ap.parse_args()

    import torch
    import torch.nn as nn

    carpetas = []
    for patron in args.carpetas:
        carpetas += sorted(glob.glob(patron)) if any(
            c in patron for c in "*?[") else [patron]
    print(f"Cargando {len(carpetas)} carpeta(s):")
    X, y, grupos = cargar_datos(carpetas)
    print(f"Total: {len(X)} ejemplos de {len(set(grupos))} pila(s), "
          f"rango {y.min():.0f} a {y.max():.0f} um")

    pilas = sorted(set(grupos.tolist()))
    if len(pilas) < 2:
        print("\nAVISO: una sola pila. No hay validacion honesta posible "
              "y el modelo va a memorizar este campo.\n"
              "Graba mas pilas antes de confiar en el resultado.")
        val = np.zeros(len(X), dtype=bool)
    else:
        rng = np.random.default_rng(0)
        reservadas = set(rng.permutation(pilas)[:max(1, len(pilas) // 5)]
                         .tolist())
        val = np.isin(grupos, list(reservadas))
        print(f"Validacion: {val.sum()} ejemplos de las pilas "
              f"{sorted(reservadas)} (separadas por pila, no por imagen)")

    dispositivo = "cuda" if torch.cuda.is_available() else "cpu"
    red = construir_red(torch, nn).to(dispositivo)
    opt = torch.optim.Adam(red.parameters(), lr=args.lr)
    plan = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epocas)
    # Huber y no MSE: un plano con una burbuja o una vibracion da un
    # error enorme, y con MSE ese unico ejemplo domina el gradiente.
    perdida = nn.HuberLoss(delta=10.0)

    Xt = torch.tensor(X[~val]); yt = torch.tensor(y[~val])[:, None]
    Xv = torch.tensor(X[val]); yv = torch.tensor(y[val])[:, None]

    mejor = float("inf")
    for epoca in range(args.epocas):
        red.train()
        orden = torch.randperm(len(Xt))
        for i in range(0, len(orden), args.lote):
            idx = orden[i:i + args.lote]
            lote_x = Xt[idx].to(dispositivo)
            lote_y = yt[idx].to(dispositivo)
            # Espejado horizontal: invierte el eje del corrimiento, asi
            # que hay que intercambiar los dos canales (izquierda pasa a
            # ser derecha) Y cambiarle el signo a la etiqueta. Sin ese
            # intercambio se le estaria enseniando lo contrario de lo
            # que la fisica dice.
            if torch.rand(1).item() < 0.5:
                lote_x = torch.flip(lote_x, dims=[3])[:, [1, 0]]
                lote_y = -lote_y
            opt.zero_grad()
            salida = red(lote_x)
            costo = perdida(salida, lote_y)
            costo.backward()
            opt.step()
        plan.step()

        if len(Xv) and (epoca + 1) % 10 == 0:
            red.eval()
            with torch.no_grad():
                pred = red(Xv.to(dispositivo)).cpu().numpy().ravel()
            err = np.abs(pred - y[val])
            print(f"  epoca {epoca+1:3d}  error mediano "
                  f"{np.median(err):6.2f} um   p90 {np.percentile(err,90):6.2f} um")
            if np.median(err) < mejor:
                mejor = np.median(err)

    red.eval()
    ejemplo = torch.zeros(1, 2, LADO, LADO)
    torch.onnx.export(
        red.cpu(), ejemplo, args.salida,
        input_names=["par"], output_names=["desenfoque_um"],
        dynamic_axes={"par": {0: "lote"}}, opset_version=17)

    meta = {
        "lado": LADO, "unidad": "um", "pilas": len(pilas),
        "ejemplos": int(len(X)),
        "error_mediano_um": None if mejor == float("inf") else round(mejor, 2),
        "rango_entrenado_um": [float(y.min()), float(y.max())],
    }
    with open(os.path.splitext(args.salida)[0] + ".json", "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\nGuardado: {args.salida}")
    print(f"Copialo a la Pi en MicroscopeOS/profiles/autofoco_ia.onnx "
          f"(junto con el .json) y reinicia el servicio.")
    if mejor != float("inf"):
        print(f"Error mediano en pilas no vistas: {mejor:.2f} um. "
              f"Comparalo con la profundidad de campo de tu objetivo: "
              f"si es mayor, hacen falta mas pilas.")


if __name__ == "__main__":
    main()
