"""DPC (y opcionalmente fase) a partir de las 4 capturas de un ciclo.

Por que existe: un ciclo DPC guarda 4 TIFF de 16 MB por camara (L, R, T,
B). Un experimento de una noche con dos camaras llego a ~60 GB. Las 4
crudas solo sirven para calcular de ellas el relieve, asi que al terminar
cada ciclo se calcula el DPC, se comprueba que quedo bien escrito en disco
y recien entonces se borran las crudas (si se pidio).

Lo que se guarda, por camara y por ciclo (mismo nombre que las crudas,
con otro sufijo). Tamanos medidos con una captura real de cam0
(3280x2464, 2026-10-01):

    ..._dpcLR.tif   DPC izquierda-derecha  (L-R)/(L+R)      ~10.6 MB
    ..._dpcTB.tif   DPC arriba-abajo       (T-B)/(T+B)      ~10.6 MB
    ..._suma.tif    campo claro (L+R+T+B)/4, a 1640 px      ~2.8 MB
    ..._fase.tif    fase en radianes (opcional)             ~16 MB
    ..._dpc.jpg     vista previa en color de los dos ejes   ~1 MB (opcional)

En total ~25 MB contra 65 MB de las 4 crudas. La suma es lo unico que no
se puede volver a sacar del DPC (el cociente descarta el brillo): es la
foto "normal" del campo, y sirve para ver burbujas, desenfoque o una luz
que falla. La fase, en cambio, sale entera de dpcLR y dpcTB, asi que la
PC la puede calcular despues con la calibracion que haga falta.

Formato de los TIFF: uint16 con el cero en 32768, porque el formato ImageJ
(el que usa core/metadatos.py para que Fiji abra la escala sola) no admite
enteros con signo. Comprimidos sin perdida (deflate con predictor, que
Fiji abre). Para volver al valor fisico:

    dpc  = (pixel - 32768) / 4096            -> rango [-1, 1]
    fase = (pixel - 32768) * 1e-4            -> radianes, rango +-3.27

Por que 1/4096 y no 1/32767: el ruido de cada pixel del DPC es ~0.008
(medido en los huecos de la muestra de cam0), o sea ~33 cuentas con este
paso. Cuantizar a 1/4096 suma un error del 1 % de ese ruido, y los bits
de mas que guardaba 1/32767 eran ruido que no se comprime: con 1/4096 el
TIFF comprimido pesa 10.6 MB en vez de 13. La formula queda escrita
dentro de cada TIFF (metadatos, clave "dpc" o "fase", con "cero" y
"escala" para leerla sin parsear texto).

DPC: cada imagen se divide primero por su propio fondo (una version muy
suavizada de si misma). Sin eso la diferencia de brillo entre las dos
mitades de la matriz -- que en el montaje real llega a casi el doble --
domina el cociente y tapa el relieve.

Fase: deconvolucion de Tikhonov con las funciones de transferencia de
objeto debil (Tian & Waller, Opt. Express 23, 11394, 2015), modelando la
fuente LED por LED. Sirve para ver y segmentar; los radianes absolutos NO
son confiables todavia: dependen de la regularizacion y faltan capturas de
fondo sin muestra para corregir las frecuencias bajas. Por eso viene
apagada por defecto.
"""

import os
import time

import cv2
import numpy as np

from core import metadatos

CERO = 32768
ESCALA_DPC = 1.0 / 4096         # valor DPC por cuenta (ver arriba)
ESCALA_FASE = 1e-4              # radianes por cuenta

SUFIJOS_CRUDAS = ("_L", "_R", "_T", "_B")

# Geometria de la matriz (Waveshare ESP32-S3-Matrix). El paso entre LEDs
# sale del plano del fabricante; la distancia a la muestra la midio Alex
# en el montaje de cam0 (2026-10-01). Cada mitad son 4 columnas o filas,
# como en dpc_matrix.ino.
MATRIZ = {"n": 8, "paso_mm": 2.65, "distancia_mm": 27.0}

# Orientacion de los ejes respecto de la pupila (LR, TB) y signo global.
# El montaje ve la matriz reflejada (ver README de la matriz), asi que no
# se deduce sobre el papel: se fijo con el ajuste a una muestra real de
# cam0 (2026-10-01). Si se remonta una matriz, revisar.
SIGNOS_FASE = (1, 1, 1)

OPCIONES = {
    "borrar_crudas": True,
    "fase": False,
    "jpg": True,
    "jpg_ancho": 1640,          # 0 = resolucion completa
    "jpg_calidad": 90,
    "suma": True,
    "suma_ancho": 1640,         # 0 = resolucion completa
    "comprimir": True,          # deflate sin perdida
    "sigma_fondo_px": 150,
    "regularizacion": 1e-2,
}


# ---------------------------------------------------------------- DPC

def fondo(img, sigma_px=150, reduccion=8):
    """Iluminacion de fondo: la imagen muy suavizada.

    Se suaviza sobre una copia reducida `reduccion` veces: el resultado es
    el mismo (el fondo no tiene detalle fino) y en la Pi tarda una
    fraccion de lo que tardaria a resolucion completa.
    """
    h, w = img.shape
    chica = cv2.resize(img, (max(1, w // reduccion), max(1, h // reduccion)),
                       interpolation=cv2.INTER_AREA)
    chica = cv2.GaussianBlur(chica, (0, 0), sigma_px / reduccion,
                             borderType=cv2.BORDER_REFLECT)
    return cv2.resize(chica, (w, h), interpolation=cv2.INTER_LINEAR)


def calcular_dpc(L, R, T, B, sigma_fondo_px=150):
    """Devuelve (lr, tb) en float32, rango [-1, 1]."""
    norm = []
    for img in (L, R, T, B):
        x = np.asarray(img, dtype=np.float32)
        norm.append(x / np.maximum(fondo(x, sigma_fondo_px), 1e-6))
    l, r, t, b = norm
    lr = (l - r) / np.maximum(l + r, 1e-6)
    tb = (t - b) / np.maximum(t + b, 1e-6)
    return np.clip(lr, -1, 1), np.clip(tb, -1, 1)


def calcular_suma(L, R, T, B, ancho=0):
    """Campo claro: el promedio de las 4 medias aperturas (juntas son la
    apertura entera). Devuelve (uint16, factor de reduccion).

    Se reduce con INTER_AREA, que promedia: a 1640 px cada pixel es la
    media de 2x2, asi que no se pierde senal, solo detalle fino que el
    DPC ya guarda a resolucion completa.
    """
    s = np.asarray(L, dtype=np.float32) + R
    s += T
    s += B
    s *= 0.25
    factor = 1.0
    if ancho and s.shape[1] > ancho:
        factor = s.shape[1] / ancho
        alto = max(1, round(s.shape[0] / factor))
        s = cv2.resize(s, (ancho, alto), interpolation=cv2.INTER_AREA)
    return np.clip(np.rint(s), 0, 65535).astype(np.uint16), factor


def salidas(opciones=None):
    """Sufijos de los TIFF que deja cada ciclo. Van a experimento.json:
    con las crudas borradas, es lo que la PC busca para saber que un
    ciclo esta completo."""
    op = dict(OPCIONES, **(opciones or {}))
    return (["_dpcLR", "_dpcTB"] + (["_suma"] if op["suma"] else [])
            + (["_fase"] if op["fase"] else []))


def bytes_por_ciclo(opciones=None, forma=(2464, 3280)):
    """Lo que ocupa en disco un ciclo de UNA camara, para avisar antes de
    empezar si no alcanza el espacio. Con algo de margen sobre lo medido
    (ver arriba): mejor sobrar que quedarse sin disco a mitad de la noche."""
    op = dict(OPCIONES, **(opciones or {}))
    crudo = forma[0] * forma[1] * 2
    comp = 0.75 if op["comprimir"] else 1.0         # medido: 0.66 (DPC), 0.69 (suma)
    total = 2 * crudo * comp
    if op["suma"]:
        lado = min(1.0, op["suma_ancho"] / forma[1]) if op["suma_ancho"] else 1.0
        total += crudo * lado ** 2 * comp
    if op["fase"]:
        total += crudo
    if op["jpg"]:
        total += 1.5e6
    if not op["borrar_crudas"]:
        total += len(SUFIJOS_CRUDAS) * crudo
    return int(total)


def a_uint16(x, escala):
    return np.clip(np.rint(x / escala) + CERO, 0, 65535).astype(np.uint16)


def desde_uint16(v, escala):
    return (np.asarray(v, dtype=np.float32) - CERO) * escala


def vista_color(lr, tb, ancho=0):
    """RGB de 8 bits: L-R en rojo/cian, T-B en verde/magenta."""
    rgb = np.stack([lr, tb, -(lr + tb) / 2], -1)
    m = float(np.percentile(np.abs(rgb[::4, ::4]), 99.5)) or 1.0
    img = np.clip((rgb / m + 1) * 127.5, 0, 255).astype(np.uint8)
    if ancho and img.shape[1] > ancho:
        alto = round(img.shape[0] * ancho / img.shape[1])
        img = cv2.resize(img, (ancho, alto), interpolation=cv2.INTER_AREA)
    return img


# ---------------------------------------------------------------- fase

def longitud_onda_um(color_hex):
    """Longitud de onda efectiva segun el color de los LEDs de la matriz."""
    try:
        c = int(str(color_hex).lstrip("#"), 16)
        r, g, b = (c >> 16) & 255, (c >> 8) & 255, c & 255
    except (TypeError, ValueError):
        return 0.55
    canales = [(r, 0.630), (g, 0.525), (b, 0.465)]
    encendidos = [l for v, l in canales if v > 0]
    return encendidos[0] if len(encendidos) == 1 else 0.55


class ModeloFase:
    """Funciones de transferencia de fase de los dos pares de mitades.

    Dependen solo del tamano de la imagen y de la optica, asi que se
    calculan una vez y se reusan en todos los ciclos (es la parte cara).
    """

    def __init__(self, forma, um_por_pixel, na, lambda_um,
                 matriz=MATRIZ, pad=256, signos=SIGNOS_FASE):
        self.pad = pad
        self.signos = signos
        ny, nx = forma[0] + 2 * pad, forma[1] + 2 * pad
        self.forma_pad = (ny, nx)
        fy = np.fft.fftfreq(ny, um_por_pixel)[:, None]
        fx = np.fft.fftfreq(nx, um_por_pixel)[None, :]
        pupila = (np.hypot(fx, fy) <= na / lambda_um).astype(np.float64)

        c = (np.arange(matriz["n"]) - (matriz["n"] - 1) / 2) * matriz["paso_mm"]
        lx, ly = np.meshgrid(c, c)
        rr = np.sqrt(lx ** 2 + ly ** 2 + matriz["distancia_mm"] ** 2)
        na_x, na_y = (lx / rr).ravel(), (ly / rr).ravel()
        df_x, df_y = 1 / (nx * um_por_pixel), 1 / (ny * um_por_pixel)

        def fuente(mask):
            s = np.zeros((ny, nx))
            ix = np.round(na_x[mask] / lambda_um / df_x).astype(int) % nx
            iy = np.round(na_y[mask] / lambda_um / df_y).astype(int) % ny
            np.add.at(s, (iy, ix), 1.0)
            return s

        def transferencia(sa, sb):
            i0 = ((sa + sb) * pupila).sum()
            fsp = np.fft.fft2((sa - sb) * pupila) * np.conj(np.fft.fft2(pupila))
            h = 2j * np.fft.ifft2(1j * fsp.imag) / i0
            # La fase es real: alcanza con la mitad del espectro (rfft2).
            return h[:, :nx // 2 + 1].astype(np.complex64)

        self.h_lr = signos[0] * transferencia(fuente(na_x < 0), fuente(na_x > 0))
        self.h_tb = signos[1] * transferencia(fuente(na_y < 0), fuente(na_y > 0))
        self.potencia = (np.abs(self.h_lr) ** 2 + np.abs(self.h_tb) ** 2).astype(np.float32)

    def reconstruir(self, lr, tb, regularizacion=1e-2):
        p = self.pad
        d_lr = np.fft.rfft2(np.pad(lr, p, mode="reflect"))
        d_tb = np.fft.rfft2(np.pad(tb, p, mode="reflect"))
        num = np.conj(self.h_lr) * d_lr
        del d_lr
        num += np.conj(self.h_tb) * d_tb
        del d_tb
        num /= self.potencia + regularizacion
        fase = np.fft.irfft2(num, s=self.forma_pad)[p:-p, p:-p].astype(np.float32)
        fase *= self.signos[2]
        # Cero en el fondo: el valor mas frecuente (los huecos sin
        # celulas son planos y suelen ser la moda).
        hist, bordes = np.histogram(fase[::4, ::4], bins=400)
        k = int(np.argmax(hist))
        return fase - np.float32((bordes[k] + bordes[k + 1]) / 2)


_modelos = {}


def modelo_fase(forma, meta):
    """ModeloFase para esta imagen, reusando el ultimo si nada cambio."""
    optica = meta.get("optica") or {}
    umpx = optica.get("um_por_pixel")
    na = optica.get("na")
    if not umpx or not na:
        raise ValueError("la imagen no trae optica en los metadatos "
                         "(um_por_pixel y na); no se puede calcular la fase")
    color = (meta.get("iluminacion") or {}).get("color")
    lam = longitud_onda_um(color)
    clave = (tuple(forma), round(umpx, 6), round(na, 4), lam)
    if clave not in _modelos:
        _modelos.clear()            # uno solo en memoria: son ~200 MB
        _modelos[clave] = ModeloFase(forma, umpx, na, lam)
    return _modelos[clave], {"um_por_pixel": umpx, "na": na, "lambda_um": lam}


# ---------------------------------------------------------------- ciclo

def _escribir_verificado(ruta, img, meta, comprimir=False):
    """Escribe el TIFF y lo vuelve a leer. Solo si lo leido es identico a
    lo calculado se considera guardado: es lo que autoriza a borrar las
    crudas."""
    import tifffile
    tmp = ruta + ".parcial"
    metadatos.escribir(tmp, img, meta, comprimir=comprimir)
    leido = tifffile.imread(tmp)
    if leido.shape != img.shape or leido.dtype != img.dtype or not np.array_equal(leido, img):
        os.remove(tmp)
        raise IOError(f"{ruta}: lo leido no coincide con lo escrito")
    os.replace(tmp, ruta)
    return ruta


def procesar_ciclo(rutas, opciones=None):
    """Procesa las 4 capturas de UN ciclo de UNA camara.

    rutas: {"_L": ruta, "_R": ..., "_T": ..., "_B": ...}
    Devuelve {"archivos": [...], "borradas": [...], "ms": int, ...}.
    Si algo falla lanza la excepcion y NO borra nada.
    """
    import tifffile
    op = dict(OPCIONES, **(opciones or {}))
    faltan = [s for s in SUFIJOS_CRUDAS if not rutas.get(s)]
    if faltan:
        raise ValueError(f"faltan capturas: {' '.join(faltan)}")

    t0 = time.monotonic()
    meta = metadatos.leer(rutas["_L"])
    imgs = [tifffile.imread(rutas[s]) for s in SUFIJOS_CRUDAS]
    lr, tb = calcular_dpc(*imgs, sigma_fondo_px=op["sigma_fondo_px"])
    suma = calcular_suma(*imgs, ancho=op["suma_ancho"]) if op["suma"] else None
    del imgs

    base = rutas["_L"][:-len("_L.tif")]
    meta.pop("canal_dpc", None)
    if "iluminacion" in meta:
        meta["iluminacion"]["patron"] = "DPC (L, R, T, B)"
        meta["iluminacion"]["nombre"] = "Relieve DPC calculado de 4 capturas"
    origen = [os.path.basename(rutas[s]) for s in SUFIJOS_CRUDAS]

    comp = bool(op["comprimir"])
    archivos = []
    for eje, datos in (("LR", lr), ("TB", tb)):
        m = dict(meta, dpc={
            "eje": eje,
            "formula": "(L-R)/(L+R)" if eje == "LR" else "(T-B)/(T+B)",
            "valor": f"(pixel - {CERO}) / {round(1 / ESCALA_DPC)}",
            "cero": CERO, "escala": ESCALA_DPC,
            "sigma_fondo_px": op["sigma_fondo_px"],
            "de": origen})
        archivos.append(_escribir_verificado(
            f"{base}_dpc{eje}.tif", a_uint16(datos, ESCALA_DPC), m, comp))

    if suma is not None:
        img, factor = suma
        m = dict(meta, suma={
            "formula": "(L+R+T+B)/4",
            "reduccion": round(factor, 4),
            "de": origen})
        m["iluminacion"] = dict(meta.get("iluminacion") or {},
                                nombre="Campo claro (suma de las 4 capturas DPC)")
        if (meta.get("optica") or {}).get("um_por_pixel"):
            # La foto reducida tiene pixeles mas grandes: que Fiji y la
            # PC midan bien en micras.
            m["optica"] = dict(meta["optica"],
                               um_por_pixel=meta["optica"]["um_por_pixel"] * factor)
        archivos.append(_escribir_verificado(f"{base}_suma.tif", img, m, comp))
        del suma, img

    info = {}
    if op["fase"]:
        modelo, info = modelo_fase(lr.shape, meta)
        fase = modelo.reconstruir(lr, tb, op["regularizacion"])
        m = dict(meta, fase={
            "unidad": "rad",
            "valor": f"(pixel - {CERO}) * {ESCALA_FASE}",
            "cero": CERO, "escala": ESCALA_FASE,
            "metodo": "Tikhonov, objeto debil (Tian & Waller 2015)",
            "regularizacion": op["regularizacion"],
            "matriz_led": MATRIZ, **info,
            "aviso": "util para ver y segmentar; radianes absolutos no calibrados",
            "de": origen})
        archivos.append(_escribir_verificado(
            f"{base}_fase.tif", a_uint16(fase, ESCALA_FASE), m, comp))
        info["fase_p99_rad"] = round(float(np.percentile(fase[::4, ::4], 99)), 3)
        del fase

    if op["jpg"]:
        ruta = f"{base}_dpc.jpg"
        rgb = vista_color(lr, tb, op["jpg_ancho"])
        if not cv2.imwrite(ruta, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, int(op["jpg_calidad"])]):
            raise IOError(f"no se pudo escribir {ruta}")
        archivos.append(ruta)

    borradas = []
    if op["borrar_crudas"]:
        for s in SUFIJOS_CRUDAS:
            os.remove(rutas[s])
            borradas.append(rutas[s])

    return dict(info, archivos=archivos, borradas=borradas,
                ms=int((time.monotonic() - t0) * 1000))
