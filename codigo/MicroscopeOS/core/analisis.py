"""Conteo de celulas, control de calidad de imagen y deteccion de eventos.

POR QUE NO ALCANZA UN UMBRAL DE BRILLO
======================================

Una celula viva sin tenir no absorbe luz. En campo claro perfectamente
enfocada casi no existe (ver el encabezado de core/autofocus.py), y en
DPC no aparece como una mancha mas clara o mas oscura que el fondo: la
imagen DPC es la DERIVADA del frente de onda, asi que cada celula sale
en relieve -- un lobulo claro de un lado y uno oscuro del otro, con el
centro al mismo gris que el fondo.

Umbralizar brillo sobre eso cuenta lobulos, no celulas: da el doble de
objetos, cada uno con forma de media luna, y el centro de masa cae
fuera de la celula.

LO QUE SI FUNCIONA: ENERGIA LOCAL
=================================

    alto_paso = img - desenfoque_grande(img)     # quita vinieta y gradiente
    energia   = desenfoque_celula(alto_paso^2)   # |estructura|^2 a escala celula

El cuadrado vuelve positivos los dos lobulos, y el suavizado a la escala
de la celula los funde en UN solo bulto centrado donde esta la celula.
Sirve igual para las tres formas en que una celula puede aparecer:
relieve bipolar (DPC), sombra lateral (media apertura) o anillo de halo
(campo claro desenfocado). El primer paso ademas mata la vinieta y
cualquier gradiente de iluminacion, que es lo que rompe un umbral global.

El umbral sobre la energia es robusto (mediana + k*MAD), no Otsu: Otsu
SIEMPRE parte la imagen en dos, asi que en un campo vacio inventa
celulas a partir del ruido. Con mediana+MAD, un campo sin nada da cero
objetos, que es la respuesta correcta y ademas es el filtro de "campo
vacio" gratis.

DONDE ESTE METODO DEJA DE SERVIR
================================

Cerca de la confluencia las celulas se tocan y el fondo deja de ser
mayoria, con lo que la estadistica robusta (que asume que el fondo es
mas del 50% de la imagen) se degrada. Eso no se disimula: se reporta
`confluente=True` y el conteo pasa a ser una cota inferior. Contar bien
un campo confluente necesita segmentacion entrenada (Cellpose/StarDist),
que corre sobre las fotos guardadas y no en vivo.
"""

import csv
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from core.autofocus import imagen_dpc

# Todas las escalas en pixeles se declaran referidas a una imagen de este
# ancho (el preview de 640x480). Con `escalar=True` el diametro efectivo
# se ajusta solo al ancho real de la imagen, asi que el MISMO numero vale
# para el vivo de 640 px y para el TIFF de 3280 px: representa un tamanio
# fisico, no una cantidad de pixeles de un sensor concreto.
ANCHO_REFERENCIA = 640

# Nombres de archivo que genera el analisis de un timelapse.
CSV_CONTEO = "conteo.csv"
CSV_EVENTOS = "eventos.csv"
PNG_POBLACION = "poblacion.png"

COLUMNAS_CONTEO = ("timestamp", "ciclo", "camara", "n", "regiones", "cumulos",
                   "cobertura", "confluente", "vacio", "separacion", "nitidez")


# =====================================================================
# LECTURA DE IMAGENES
# =====================================================================
def leer_imagen(ruta):
    """Devuelve la imagen en gris, sea TIFF 16-bit o PNG/JPG de 8.

    Los TIFF cientificos no los abre cv2.imread de forma confiable (16
    bits, sin compresion, una sola pagina), asi que van por tifffile;
    todo lo demas por OpenCV.
    """
    ruta = str(ruta)
    if ruta.lower().endswith((".tif", ".tiff")):
        import tifffile
        img = tifffile.imread(ruta)
    else:
        img = cv2.imread(ruta, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(ruta)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _a_gris(img):
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


# =====================================================================
# METRICAS DE CALIDAD
# =====================================================================
def correlacion_vecinos(gray, sigma_fondo=20.0):
    """Correlacion entre pixeles contiguos. El filtro de campo vacio.

    Es la unica pregunta que hay que contestar antes de segmentar: lo
    que se esta mirando, es una imagen o es ruido de lectura? La
    respuesta esta en la fisica del sistema optico, no en la muestra:

      - el ruido de lectura del sensor es BLANCO, asi que cada pixel es
        independiente de su vecino -> correlacion ~0;
      - cualquier imagen formada por un objetivo esta limitada por
        difraccion, o sea que no puede tener detalle mas fino que la
        mancha de Airy -> pixeles contiguos siempre parecidos, con
        correlacion 0.9 o mas.

    Medido sobre una captura real de la Pi da 0.92-0.99, y 0.35 incluso
    despues de sumarle ruido sintetico tres veces mayor que el propio;
    con ruido gaussiano puro da 0.000 sea cual sea su magnitud y sea de
    8 o de 16 bits. El corte por defecto (0.2) queda en medio de esa
    brecha enorme.

    El paso alto previo saca la vinieta y el gradiente de la matriz de
    LEDs: sin el, un campo VACIO pero con vinieta correlaciona alto (la
    vinieta es suavisima) y se lo tomaria por una imagen con contenido.

    Se usa esto y no una medida de nitidez porque cualquier cociente
    entre bandas de frecuencia depende de a que escala se lo evalue: la
    misma imagen real daba 0.5 con diametro 14 y 2.3 con diametro 30,
    asi que un umbral fijo declaraba vacia una foto real solo por haber
    configurado celulas mas grandes. La correlacion no depende del
    diametro para nada.
    """
    f = gray.astype(np.float32)
    f = f - cv2.GaussianBlur(f, (0, 0), sigma_fondo)
    a = f[:, :-1].ravel()
    b = f[:, 1:].ravel()
    da, db = float(a.std()), float(b.std())
    if da < 1e-9 or db < 1e-9:
        return 0.0            # imagen constante: negro, saturada o tapada
    return float(np.mean((a - a.mean()) * (b - b.mean())) / (da * db))


def nitidez_relativa(gray, diametro):
    """Proporcion de energia fina sobre energia de escala celular.

    Es un proxy de foco INDEPENDIENTE del contraste absoluto: si la
    muestra se pone mas densa la energia sube en las dos bandas y el
    cociente no se mueve, pero si la imagen se desenfoca la banda fina
    se muere primero y el cociente cae. Justamente lo que hace falta
    para distinguir "hay menos celulas" de "se fue el foco" a lo largo
    de un timelapse de 48 h.

    No reemplaza al autofoco: no tiene signo (no dice hacia donde), solo
    sirve para detectar que algo se degrado.
    """
    f = gray.astype(np.float32)
    s1 = max(1.0, diametro / 8.0)
    s2 = max(s1 * 2.0, diametro / 2.0)
    suave1 = cv2.GaussianBlur(f, (0, 0), s1)
    suave2 = cv2.GaussianBlur(f, (0, 0), s2)
    alta = f - suave1
    media = suave1 - suave2
    return float(alta.var() / (media.var() + 1e-9))


# =====================================================================
# SEGMENTACION
# =====================================================================
def mapa_energia(gray, diametro):
    """|alto paso|^2 suavizado a escala de celula. Ver encabezado.

    El sigma del suavizado es diametro/2 y no menos: los dos lobulos del
    relieve bipolar quedan separados por aproximadamente un RADIO, asi
    que suavizar a menos que eso los deja como dos bultos distintos y el
    conteo sale exactamente al doble. Es el error mas facil de cometer
    aca y el mas dificil de ver, porque la imagen de la mascara "se ve
    bien": son las celulas, solo que partidas por la mitad.
    """
    f = gray.astype(np.float32)
    # 3x el diametro: lo bastante grande para no comerse la celula, lo
    # bastante chico para seguir la vinieta y el gradiente de la matriz
    # de LEDs (que en media apertura es fuerte y atraviesa el campo).
    fondo = cv2.GaussianBlur(f, (0, 0), max(2.0, diametro * 3.0))
    alto = f - fondo
    return cv2.GaussianBlur(alto * alto, (0, 0), max(1.5, diametro / 2.0))


def _umbral_energia(energia, k=3.0):
    """Separa objeto de fondo en el mapa de energia. Dos preguntas
    distintas, resueltas con dos herramientas distintas:

    HAY ALGO?  ->  `separacion`, el cociente entre las medias de las dos
    clases que produce Otsu. Un campo con solo ruido de lectura da ~1.1
    (Otsu SIEMPRE parte la imagen, pero si no hay estructura las dos
    mitades se parecen); cualquier campo con celulas da >=4, incluso
    confluente. Es la unica medida de todo el modulo que NO depende de
    que fraccion de la imagen ocupen las celulas, y por eso es la que
    decide si el campo esta vacio.

    DONDE ESTA?  ->  el propio umbral de Otsu.

    La version anterior usaba mediana + k*sigma para las dos cosas y
    fallaba feo en el caso que mas importa: en un cultivo confluente el
    fondo deja de ser mayoria, la mediana se mete DENTRO de las celulas
    y el umbral termina por encima del percentil 99.9 -- mascara vacia,
    conteo cero. Un pozo lleno de celulas se reportaba como campo vacio,
    que es exactamente el error que arruina una curva de poblacion.

    `senial` (pico sobre el umbral de ruido) se conserva solo como
    diagnostico: es informativo cuando el fondo es mayoria y se
    desploma cuando no, asi que sirve para saber en que regimen se esta
    midiendo, pero ya no decide nada.
    """
    med = float(np.median(energia))
    mad = float(np.median(np.abs(energia - med)))
    sigma = 1.4826 * mad
    pico = float(np.percentile(energia, 99.9))
    senial = (pico - med) / (k * sigma) if sigma > 0 else 0.0

    if pico <= med:
        return None, 0.0, senial          # imagen plana: nada que umbralizar

    # Normalizacion recortada al percentil 99.9 y no al maximo: un solo
    # pixel caliente (un rayo cosmico, un pixel muerto) comprime toda la
    # escala y deja a Otsu decidiendo sobre 3 niveles de gris.
    recortada = np.clip(energia, med, pico)
    escala = ((recortada - med) / (pico - med) * 255).astype(np.uint8)
    nivel, _ = cv2.threshold(escala, 0, 255,
                             cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    alta = energia[escala > nivel]
    baja = energia[escala <= nivel]
    if alta.size == 0 or baja.size == 0:
        return None, 0.0, senial
    separacion = float(alta.mean() / (baja.mean() + 1e-9))
    valor = med + (nivel / 255.0) * (pico - med)
    return valor, separacion, senial


def _separar_pegados(energia, mascara, diametro, umbral):
    """Watershed sembrado en los maximos locales de la energia.

    Dos celulas que se tocan dan UNA sola componente conexa pero DOS
    maximos de energia, porque el suavizado es a escala de celula y no
    de par de celulas. Sembrar el watershed en esos maximos las separa;
    si hay un solo maximo, no toca nada.

    Los maximos se buscan sobre una version AUN mas suavizada y solo por
    encima del umbral: sobre la energia cruda, cualquier rizado de ruido
    dentro de una celula es un maximo local, y cada uno de esos se
    convierte en una celula inventada. Es la diferencia entre separar
    dos celulas pegadas y hacer trizas una sola.
    """
    suave = cv2.GaussianBlur(energia, (0, 0), max(1.0, diametro / 3.0))
    k = max(3, int(diametro) | 1)          # impar: distancia minima entre picos
    dilatada = cv2.dilate(suave, np.ones((k, k), np.uint8))
    picos = ((suave >= dilatada - 1e-6) & (mascara > 0) &
             (suave > umbral)).astype(np.uint8)
    n_semillas, semillas = cv2.connectedComponents(picos)
    if n_semillas <= 2:                    # 0 o 1 objeto: nada que separar
        return None

    marcadores = np.zeros(mascara.shape, np.int32)
    marcadores[mascara == 0] = 1           # fondo seguro
    marcadores[picos > 0] = semillas[picos > 0] + 1
    # El resto (mascara sin pico) queda en 0 = territorio a repartir.

    norm = cv2.normalize(energia, None, 0, 255,
                         cv2.NORM_MINMAX).astype(np.uint8)
    cv2.watershed(cv2.cvtColor(norm, cv2.COLOR_GRAY2BGR), marcadores)
    # watershed deja -1 en las fronteras y 1 en el fondo.
    etiquetas = np.where(marcadores > 1, marcadores - 1, 0).astype(np.int32)
    return etiquetas


def _objetos_de_etiquetas(etiquetas, area_min, area_max, nominal,
                          factor_cumulo=1.8):
    """Centroide, area, contorno y CUANTAS CELULAS tiene cada region.

    Un grumo que el watershed no logro partir no se descarta ni se
    cuenta como una sola celula: se estima por area. Descartarlo seria
    lo peor de los dos mundos -- justo las regiones con MAS celulas
    desaparecerian del conteo, y la curva de poblacion se aplanaria
    exactamente cuando el cultivo empieza a crecer.

    La referencia de "cuanto mide una celula" no sale del diametro
    configurado sino de la MEDIANA de las areas encontradas: en un campo
    normal la mayoria de las regiones son celulas sueltas, asi que la
    imagen se calibra sola y el conteo deja de depender de que el
    usuario haya acertado el diametro al pixel. Solo cuando hay muy
    pocas regiones (y por lo tanto no hay estadistica) se cae al valor
    nominal del diametro configurado.
    """
    regiones, contornos = [], []
    descartados = 0
    n = int(etiquetas.max())
    for etiqueta in range(1, n + 1):
        ys, xs = np.nonzero(etiquetas == etiqueta)
        area = xs.size
        if area < area_min or area > area_max:
            descartados += 1
            continue
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        recorte = (etiquetas[y0:y1 + 1, x0:x1 + 1] == etiqueta).astype(np.uint8)
        cs, _ = cv2.findContours(recorte, cv2.RETR_EXTERNAL,
                                 cv2.CHAIN_APPROX_SIMPLE)
        for c in cs:
            contornos.append(c + np.array([[x0, y0]], dtype=c.dtype))
        regiones.append({
            "x": float(xs.mean()), "y": float(ys.mean()),
            "area": int(area), "radio": float(np.sqrt(area / np.pi)),
            "celulas": 1, "cumulo": False,
        })

    if regiones:
        areas = np.array([r["area"] for r in regiones], dtype=float)
        referencia = float(np.median(areas)) if len(areas) >= 5 else nominal
        referencia = max(referencia, 1.0)
        for r in regiones:
            if r["area"] > factor_cumulo * referencia:
                r["celulas"] = max(1, int(round(r["area"] / referencia)))
                r["cumulo"] = True
    return regiones, contornos, descartados


def analizar(gray, diametro_px=14, umbral=3.0, separar=True,
             area_min=0.15, area_max=40.0, escalar=True,
             correlacion_min=0.2):
    """Cuenta objetos y mide la calidad de un campo. Todo en un paso.

    diametro_px  diametro tipico de una celula, referido a 640 px de
                 ancho (ver ANCHO_REFERENCIA). Es la unica perilla que
                 hay que tocar al cambiar de objetivo.
    umbral       sigmas de fondo que se usan para el diagnostico
                 `senial`. El corte objeto/fondo lo pone Otsu, no este
                 numero (ver _umbral_energia).
    separar      watershed para partir celulas pegadas. Encendido por
                 defecto: sin el, un campo medianamente denso da UNA
                 sola region conexa y el conteo se va a cero.
    area_min/max fraccion del area nominal de una celula que se acepta.
                 area_min filtra polvo; area_max solo descarta cosas
                 absurdas (una burbuja, un pelo cruzando el campo), no
                 grumos de celulas -- esos se estiman por area.
    correlacion_min  por debajo de esta correlacion entre pixeles
                 contiguos la imagen se considera ruido puro y no se
                 cuenta nada. Ver correlacion_vecinos().

    Devuelve siempre las mismas claves, tambien cuando no encuentra
    nada: quien consume esto (CSV del timelapse, overlay del vivo) no
    tiene que distinguir casos.
    """
    t0 = time.monotonic()
    gray = _a_gris(gray)
    alto, ancho = gray.shape[:2]

    diametro = float(diametro_px)
    if escalar:
        diametro *= ancho / float(ANCHO_REFERENCIA)
    diametro = max(3.0, diametro)

    energia = mapa_energia(gray, diametro)
    valor, separacion, senial = _umbral_energia(energia, umbral)
    correlacion = correlacion_vecinos(gray)

    resultado = {
        "n": 0, "objetos": [], "contornos": [],
        "regiones": 0, "cumulos": 0, "descartados": 0,
        "cobertura": 0.0, "confluente": False, "vacio": True,
        "separacion": round(float(separacion), 2),
        "senial": round(float(senial), 2),
        "correlacion": round(float(correlacion), 3),
        "nitidez": round(nitidez_relativa(gray, diametro), 4),
        "diametro_px": round(diametro, 1),
        "ms": 0,
    }

    # FILTRO DE CAMPO VACIO
    # ---------------------
    # Otsu SIEMPRE parte la imagen en dos, asi que sobre ruido de
    # lectura puro inventa un par de cientos de "celulas" con toda
    # conviccion. Hay que descartar ese caso ANTES de segmentar, y la
    # cobertura no sirve: un pozo vacio y un cultivo confluente pueden
    # dar la misma. Lo resuelve correlacion_vecinos() -- ver su
    # docstring. Es tambien el filtro de "se apago la luz" y "se quedo
    # la tapa puesta".
    if valor is None or correlacion < correlacion_min:
        resultado["ms"] = int((time.monotonic() - t0) * 1000)
        return resultado

    mascara = (energia > valor).astype(np.uint8)
    # Cierre + apertura a 1/4 de celula: pega el agujero del centro que
    # deja el relieve bipolar y borra los pixeles sueltos del ruido.
    k = max(3, int(diametro / 4) | 1)
    nucleo = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, nucleo)
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_OPEN, nucleo)

    cobertura = float(mascara.mean())
    resultado["cobertura"] = round(cobertura, 4)
    # Regimen donde el conteo deja de ser exacto y pasa a ser una
    # estimacion: o las celulas ocupan medio campo, o el contraste entre
    # las dos clases de Otsu se aplano porque ya casi no queda fondo
    # entre ellas. Medido con campos sinteticos, `separacion` cae de 25
    # (40 celulas) a 3 (300 celulas): por debajo de 4 el conteo empieza
    # a saturar y despues a BAJAR aunque la poblacion suba, que es la
    # forma mas enganiosa de fallar en una curva de crecimiento. Se
    # sigue contando, pero marcado.
    resultado["confluente"] = cobertura > 0.55 or separacion < 4.0

    etiquetas = None
    if separar:
        try:
            etiquetas = _separar_pegados(energia, mascara, diametro, valor)
        except Exception:
            etiquetas = None            # el conteo importa mas que separar
    if etiquetas is None:
        _, etiquetas = cv2.connectedComponents(mascara)

    nominal = np.pi * (diametro / 2.0) ** 2
    objetos, contornos, descartados = _objetos_de_etiquetas(
        etiquetas, area_min * nominal, area_max * nominal, nominal)

    resultado["objetos"] = objetos
    resultado["contornos"] = contornos
    resultado["n"] = int(sum(o["celulas"] for o in objetos))
    resultado["regiones"] = len(objetos)
    resultado["cumulos"] = int(sum(1 for o in objetos if o["cumulo"]))
    # Regiones que no pasaron el filtro de area. Si esto es alto y n es
    # bajo, el diametro configurado no tiene nada que ver con la muestra
    # -- que es la unica forma en que este metodo falla en silencio.
    resultado["descartados"] = descartados
    resultado["vacio"] = not objetos
    resultado["ms"] = int((time.monotonic() - t0) * 1000)
    return resultado


def analizar_dpc(izq, der, **kw):
    """Analiza el par de media apertura como una sola imagen DPC.

    Es el camino preferido para las fotos: el DPC ya convirtio la fase
    en amplitud, asi que la celula tiene contraste real y no depende de
    que la foto haya salido levemente desenfocada para verse.
    """
    dpc = imagen_dpc(_a_gris(izq), _a_gris(der))
    # A 8 bits centrado en gris medio: las metricas son todas relativas,
    # pero asi el overlay se puede dibujar y guardar directo.
    img = ((dpc * 0.5 + 0.5) * 255).astype(np.uint8)
    resultado = analizar(img, **kw)
    resultado["imagen"] = img
    return resultado


def reducir(img, ancho_max=1200):
    """Baja la resolucion antes de analizar. Devuelve (imagen, escala).

    El costo de analizar crece con el area, y los desenfoques a escala
    de celula son con sigmas enormes a resolucion nativa: medido, un
    TIFF de 3280 px tarda ~7 s en una laptop (y la Pi 5 es varias veces
    mas lenta), contra ~0.2 s a 1200 px. Como el diametro de la celula
    se reescala con el ancho, el conteo no cambia por reducir -- solo
    baja la precision del contorno, que no es lo que se esta midiendo.
    """
    if img.shape[1] <= ancho_max:
        return img, 1.0
    escala = img.shape[1] / float(ancho_max)
    nuevo = (ancho_max, max(1, int(round(img.shape[0] / escala))))
    return cv2.resize(img, nuevo, interpolation=cv2.INTER_AREA), escala


def analizar_capturas(rutas, contador=None, ancho_max=1200):
    """Analiza las capturas de UN ciclo de UNA camara.

    `rutas` es {sufijo: ruta} tal como las nombra el timelapse ('_L',
    '_R', '_T', '_B', o '' para los modos de una sola imagen). Si estan
    las dos mitades de un eje se analiza el DPC, que es el camino bueno
    para celulas sin tenir; si no, la imagen suelta.

    Devuelve (resultado, imagen_analizada) para poder guardar el overlay
    sin volver a leer ni reescalar nada.
    """
    contador = contador or Contador()
    if isinstance(rutas, (str, Path)):
        rutas = {"": rutas}

    for a, b in (("_L", "_R"), ("_T", "_B")):
        if rutas.get(a) and rutas.get(b):
            izq, _ = reducir(leer_imagen(rutas[a]), ancho_max)
            der, _ = reducir(leer_imagen(rutas[b]), ancho_max)
            resultado = contador.analizar_dpc(izq, der)
            return resultado, resultado["imagen"]

    ruta = next((r for r in rutas.values() if r), None)
    if ruta is None:
        raise ValueError("sin capturas que analizar")
    img, _ = reducir(leer_imagen(ruta), ancho_max)
    return contador.analizar(img), img


def guardar_overlay(ruta, imagen, resultado):
    """PNG de 8 bits con las celulas marcadas, para mirar el resultado.

    Se guarda a la resolucion REDUCIDA en que se analizo: es la que
    corresponde a los contornos, y ademas un overlay de 8 MP pesaria
    mas que el TIFF cientifico que documenta.
    """
    img = imagen
    if img.dtype != np.uint8:
        img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    cv2.imwrite(str(ruta), dibujar(img.copy(), resultado))
    return str(ruta)


# =====================================================================
# DIBUJO
# =====================================================================
def dibujar(frame, resultado, color=(120, 255, 120), grosor=1,
            etiqueta=True, escala=1.0):
    """Marca los objetos encontrados sobre el frame (BGR o gris).

    `escala` multiplica las coordenadas: el vivo analiza una version
    reducida del frame por costo, pero dibuja sobre el original.
    """
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

    contornos = resultado.get("contornos") or []
    if escala != 1.0:
        contornos = [np.round(c * escala).astype(np.int32) for c in contornos]
    cv2.drawContours(frame, contornos, -1, color, grosor)

    if etiqueta:
        texto = f"{resultado.get('n', 0)} celulas"
        if resultado.get("confluente"):
            texto += " (confluente, minimo)"
        elif resultado.get("vacio"):
            texto = "campo vacio"
        # Fondo negro semitransparente: sobre una imagen de microscopio
        # el texto blanco solo es ilegible tanto en el fondo claro como
        # sobre una celula.
        (tw, th), _ = cv2.getTextSize(texto, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (6, 6), (12 + tw, 16 + th), (0, 0, 0), -1)
        cv2.putText(frame, texto, (9, 13 + th), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, color, 1, cv2.LINE_AA)
    return frame


# =====================================================================
# CONTADOR CONFIGURABLE
# =====================================================================
class Contador:
    """Guarda la configuracion del conteo para que la interfaz la pueda
    cambiar en caliente sin recrear nada."""

    CLAVES = ("diametro_px", "umbral", "separar", "area_min", "area_max")

    def __init__(self, diametro_px=14, umbral=3.0, separar=True,
                 area_min=0.15, area_max=40.0):
        self.diametro_px = float(diametro_px)
        self.umbral = float(umbral)
        self.separar = bool(separar)
        self.area_min = float(area_min)
        self.area_max = float(area_max)

    def config(self):
        return {k: getattr(self, k) for k in self.CLAVES}

    def configurar(self, **kw):
        for k, v in kw.items():
            if k in self.CLAVES and v is not None:
                setattr(self, k, type(getattr(self, k))(v))
        return self.config()

    def analizar(self, gray, **kw):
        opciones = self.config()
        opciones.update(kw)
        return analizar(gray, **opciones)

    def analizar_dpc(self, izq, der, **kw):
        opciones = self.config()
        opciones.update(kw)
        return analizar_dpc(izq, der, **opciones)


class ContadorEnVivo:
    """Conteo sobre el stream, sin frenarlo.

    El stream corre a ~16 fps y una segmentacion cuesta bastante mas que
    un frame. Analizar todos los frames tiraria el vivo a la mitad de
    velocidad para no aportar nada: las celulas no se mueven a 16 fps.

    Asi que se analiza como mucho una vez cada `periodo` segundos y en
    una version reducida del frame, y el resultado se dibuja sobre TODOS
    los frames hasta el siguiente analisis. El vivo sigue fluido y el
    conteo se refresca un par de veces por segundo.

    LA ILUMINACION IMPORTA MAS QUE EL ALGORITMO
    ===========================================

    Esto trabaja sobre UN frame con UNA iluminacion. No puede hacer DPC:
    el DPC es la resta de dos capturas con medias aperturas opuestas, y
    el vivo es un stream continuo con un solo patron encendido.

    Y ahi esta el problema, que es de optica y no de codigo: una celula
    viva sin tenir es un objeto de FASE, y en campo claro su contraste
    es proporcional al desenfoque -- o sea que EN EL FOCO EXACTO
    desaparece. Con la matriz en luz plena, este contador reporta "campo
    vacio" sobre un cultivo lleno, y el filtro de campo vacio no puede
    distinguirlo de un pozo realmente vacio porque en los dos casos no
    hay senial: la celula de verdad no esta en la imagen.

    Medido sobre campos sinteticos de 40 celulas de fase, en foco:

        campo claro (full)      ->   0 celulas, "campo vacio"
        media apertura (left)   ->  40 celulas
        DPC (dos capturas)      ->  40 celulas

    La solucion no cuesta nada: media apertura es UN patron estatico
    (no hay que alternar, no baja los fps) y proyecta la fase de lado,
    que es exactamente el relieve que busca mapa_energia(). Por eso la
    interfaz pone la matriz en "left" al encender el conteo en vivo.

    Con muestras que ABSORBEN (tenidas) nada de esto aplica: se ven bien
    en campo claro y la media apertura tampoco molesta.

    El conteo de las FOTOS no tiene este problema: usa el par L/R
    completo y arma el DPC de verdad (ver analizar_capturas).

    DOS MODOS
    =========

    manual (por defecto)
        No mide por su cuenta. Alguien pide una medicion puntual (ver
        `medir`), y el resultado queda CONGELADO dibujado sobre el vivo
        hasta la siguiente. Asi la iluminacion oblicua se necesita solo
        durante ese instante y despues se puede volver a mirar la
        muestra como se quiera, con el numero todavia en pantalla.

    auto
        Vuelve a medir cada `periodo` segundos, indefinidamente. Util
        mientras se enfoca o se barre el campo, porque el numero
        acompania a lo que se ve. El costo es que la matriz tiene que
        quedarse en media apertura todo el tiempo: si se vuelve a campo
        claro con el modo automatico encendido, la siguiente medicion
        (dentro de `periodo`) no va a encontrar nada y el dibujo se
        vacia solo.

    El default es manual porque las celulas no cambian en segundos:
    medir dos veces por segundo es CPU tirada, y ademas obliga a mirar
    la muestra en oblicua todo el rato.
    """

    def __init__(self, contador=None, periodo=0.6, ancho_analisis=480,
                 modo="manual"):
        self.contador = contador or Contador()
        self.periodo = float(periodo)
        self.ancho_analisis = int(ancho_analisis)
        self.modo = modo if modo in ("manual", "auto") else "manual"
        self._activas = set()
        self._ultimo = {}        # camara -> (resultado, escala, momento)
        self._lock = threading.Lock()

    # --- encendido/apagado por camara ---
    def activar(self, camera_num, activo=True):
        with self._lock:
            if activo:
                self._activas.add(camera_num)
            else:
                self._activas.discard(camera_num)
                self._ultimo.pop(camera_num, None)
        return activo

    def set_modo(self, modo):
        if modo not in ("manual", "auto"):
            raise ValueError(f"modo invalido: {modo}")
        self.modo = modo
        return modo

    def desactivar(self, camera_num):
        return self.activar(camera_num, False)

    def activo(self, camera_num):
        return camera_num in self._activas

    def camaras_activas(self):
        return sorted(self._activas)

    def ultimo(self, camera_num):
        with self._lock:
            entrada = self._ultimo.get(camera_num)
        if not entrada:
            return None
        resultado, _, momento = entrada
        # Sin contornos ni objetos: esto va por JSON a la interfaz y los
        # contornos son cientos de puntos que a nadie le sirven ahi.
        return {k: v for k, v in resultado.items()
                if k not in ("contornos", "objetos", "imagen")} | \
               {"edad_s": round(time.monotonic() - momento, 1)}

    def estado(self):
        return {
            "activas": self.camaras_activas(),
            "modo": self.modo,
            "periodo": self.periodo,
            "config": self.contador.config(),
            "ultimos": {str(c): self.ultimo(c) for c in self.camaras_activas()},
        }

    def _analizar_frame(self, gray):
        """Analiza a resolucion reducida y devuelve (resultado, escala).

        La escala es la que hay que aplicarle a los contornos para
        dibujarlos sobre el frame original.
        """
        escala = 1.0
        if gray.shape[1] > self.ancho_analisis:
            escala = gray.shape[1] / float(self.ancho_analisis)
            gray = cv2.resize(
                gray, (self.ancho_analisis,
                       int(round(gray.shape[0] / escala))),
                interpolation=cv2.INTER_AREA)
        return self.contador.analizar(gray), escala

    def medir(self, camera_num, gray):
        """Medicion puntual: analiza ESTE frame y lo deja congelado.

        Es el modo manual. Quien llama se encarga de que el frame venga
        con la iluminacion correcta (media apertura) y de devolver la
        luz a donde estaba despues -- lo hace el endpoint
        /api/analisis/medir. El resultado queda dibujandose sobre el
        vivo hasta la proxima medicion, aunque para entonces la
        iluminacion ya sea otra.
        """
        resultado, escala = self._analizar_frame(_a_gris(gray))
        with self._lock:
            self._activas.add(camera_num)
            self._ultimo[camera_num] = (resultado, escala, time.monotonic())
        return resultado

    # --- el callback que consume camera.get_preview_frame ---
    def anotar(self, camera_num, frame):
        if camera_num not in self._activas:
            return frame

        with self._lock:
            entrada = self._ultimo.get(camera_num)

        if self.modo == "auto":
            vencido = entrada is None or \
                (time.monotonic() - entrada[2]) >= self.periodo
            if vencido:
                resultado, escala = self._analizar_frame(_a_gris(frame))
                entrada = (resultado, escala, time.monotonic())
                with self._lock:
                    self._ultimo[camera_num] = entrada
        elif entrada is None:
            # Manual sin ninguna medicion todavia: no hay nada que
            # dibujar. Se devuelve el frame limpio en vez de estamparle
            # "campo vacio", que seria mentir -- no es que no haya
            # celulas, es que nadie midio.
            return frame

        resultado, escala, _ = entrada
        # Copia obligatoria: lo que devuelve capture_array() puede ser
        # una vista del buffer de picamera2, y en algunas versiones
        # llega de solo lectura. Dibujar ahi encima o falla (y el vivo
        # se queda sin marcas sin decir por que, porque el gancho traga
        # la excepcion) o le escribe encima a un buffer que la camara
        # todavia esta usando.
        # (.copy() y no ascontiguousarray: ese no copia nada si el array
        # ya es contiguo, que es justo el caso habitual.)
        return dibujar(frame.copy(), resultado, escala=escala)


# =====================================================================
# SERIE TEMPORAL: CRECIMIENTO Y EVENTOS
# =====================================================================
def ajustar_crecimiento(horas, conteos):
    """Ajuste exponencial N = N0 * exp(k*t) por minimos cuadrados en log.

    Devuelve el tiempo de duplicacion, que es como se habla de un
    cultivo. r2 bajo = el cultivo no esta en fase exponencial (lag,
    meseta o murio), asi que el numero no significa nada y hay que
    mirarlo.
    """
    horas = np.asarray(horas, dtype=float)
    conteos = np.asarray(conteos, dtype=float)
    valido = conteos > 0
    if valido.sum() < 4:
        return None
    t, y = horas[valido], np.log(conteos[valido])
    if t.max() - t.min() <= 0:
        return None
    k, b = np.polyfit(t, y, 1)
    pred = k * t + b
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {
        "tasa_por_hora": round(float(k), 5),
        "duplicacion_h": round(float(np.log(2) / k), 2) if k > 0 else None,
        "r2": round(r2, 4),
        "puntos": int(valido.sum()),
    }


def detectar_eventos(ciclos, conteos, nitidez=None, ventana=5,
                     umbral_salto=0.35, umbral_nitidez=0.4, minimo=8):
    """Marca los ciclos donde algo cambio de golpe.

    La linea base es la MEDIANA movil, no la media: un solo ciclo malo
    (una burbuja que paso, un frame movido) desplaza la media y despues
    hace que el ciclo siguiente, que es normal, tambien parezca un
    evento. Con mediana ese ciclo no contamina a los vecinos.

    `minimo` evita el ruido de division: con 3 celulas en el campo,
    pasar a 4 es un +33% y no es un evento, es una celula.
    """
    ciclos = list(ciclos)
    conteos = np.asarray(conteos, dtype=float)
    eventos = []
    if len(ciclos) < 3:
        return eventos

    for i in range(1, len(ciclos)):
        j0 = max(0, i - ventana)
        base_series = conteos[j0:i]
        if base_series.size == 0:
            continue
        base = float(np.median(base_series))
        n = float(conteos[i])

        if base >= minimo and n <= max(1.0, base * 0.15):
            eventos.append({"ciclo": ciclos[i], "tipo": "campo_perdido",
                            "valor": n, "base": round(base, 1)})
            continue
        if base >= minimo:
            cambio = (n - base) / base
            if cambio >= umbral_salto:
                eventos.append({"ciclo": ciclos[i], "tipo": "salto_poblacion",
                                "valor": round(cambio, 3),
                                "base": round(base, 1)})
            elif cambio <= -umbral_salto:
                eventos.append({"ciclo": ciclos[i], "tipo": "caida_poblacion",
                                "valor": round(cambio, 3),
                                "base": round(base, 1)})

    if nitidez is not None:
        nit = np.asarray(nitidez, dtype=float)
        for i in range(1, min(len(ciclos), len(nit))):
            j0 = max(0, i - ventana)
            base = float(np.median(nit[j0:i])) if i > j0 else 0.0
            if base > 0 and nit[i] < base * (1.0 - umbral_nitidez):
                eventos.append({"ciclo": ciclos[i], "tipo": "desenfoque",
                                "valor": round(float(nit[i]), 4),
                                "base": round(base, 4)})
    return eventos


# =====================================================================
# PERSISTENCIA (CSV + grafica), formato del timelapse
# =====================================================================
def escribir_conteo(carpeta, fila):
    """Agrega una fila a conteo.csv, creando la cabecera si hace falta."""
    ruta = os.path.join(carpeta, CSV_CONTEO)
    nuevo = not os.path.exists(ruta) or os.path.getsize(ruta) == 0
    with open(ruta, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_CONTEO)
        if nuevo:
            w.writeheader()
        w.writerow({k: fila.get(k, "") for k in COLUMNAS_CONTEO})
    return ruta


def leer_conteo(carpeta):
    """conteo.csv -> {camara: {ciclos, conteos, nitidez, timestamps}}."""
    ruta = os.path.join(carpeta, CSV_CONTEO)
    series = {}
    if not os.path.exists(ruta):
        return series
    with open(ruta) as f:
        for fila in csv.DictReader(f):
            # Se parsea TODO antes de agregar nada. Si se fuera
            # agregando campo por campo, una fila truncada (un corte de
            # corriente a mitad de escritura) dejaria `ciclos` con un
            # elemento mas que `conteos`, y las dos listas desalineadas
            # hacen que la curva de poblacion quede corrida respecto de
            # los ciclos -- o directamente que matplotlib falle al
            # graficar, horas despues, sin ninguna pista de por que.
            try:
                cam = int(fila["camara"])
                ciclo = int(fila["ciclo"])
                conteo = int(float(fila["n"]))
                nitidez = float(fila["nitidez"] or 0)
                timestamp = fila["timestamp"]
            except (ValueError, KeyError, TypeError):
                continue
            s = series.setdefault(cam, {"ciclos": [], "conteos": [],
                                        "nitidez": [], "timestamps": []})
            s["ciclos"].append(ciclo)
            s["conteos"].append(conteo)
            s["nitidez"].append(nitidez)
            s["timestamps"].append(timestamp)
    return series


def escribir_eventos(carpeta, eventos_por_camara):
    ruta = os.path.join(carpeta, CSV_EVENTOS)
    with open(ruta, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["camara", "ciclo", "tipo", "valor", "base"])
        for cam, eventos in sorted(eventos_por_camara.items()):
            for e in eventos:
                w.writerow([cam, e["ciclo"], e["tipo"], e["valor"], e["base"]])
    return ruta


def graficar_poblacion(carpeta, intervalo_s=None):
    """Curva de poblacion por camara, con los eventos marcados.

    Mismo estilo oscuro que temperatura.png para que las dos graficas de
    un experimento se vean como parte del mismo informe.
    """
    series = leer_conteo(carpeta)
    if not series:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colores = ["#3ddc84", "#4dabf7", "#ffb454", "#ff6b6b"]
    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#0a0e0d")
    ax.set_facecolor("#111816")

    ajustes = {}
    for i, (cam, s) in enumerate(sorted(series.items())):
        color = colores[i % len(colores)]
        ax.plot(s["ciclos"], s["conteos"], color=color, linewidth=2,
                marker="o", markersize=3, label=f"cam{cam}")

        if intervalo_s:
            horas = [c * intervalo_s / 3600.0 for c in s["ciclos"]]
            ajuste = ajustar_crecimiento(horas, s["conteos"])
            if ajuste:
                ajustes[cam] = ajuste

        eventos = detectar_eventos(s["ciclos"], s["conteos"], s["nitidez"])
        por_ciclo = {c: n for c, n in zip(s["ciclos"], s["conteos"])}
        for e in eventos:
            ax.plot(e["ciclo"], por_ciclo.get(e["ciclo"], 0), marker="x",
                    color="#ff6b6b", markersize=9, markeredgewidth=2)

    ax.set_xlabel("Ciclo", color="#5f7269")
    ax.set_ylabel("Celulas detectadas", color="#5f7269")
    titulo = "Poblacion durante el timelapse"
    if ajustes:
        partes = [f"cam{c}: t2x={a['duplicacion_h']}h (r2={a['r2']})"
                  for c, a in ajustes.items() if a.get("duplicacion_h")]
        if partes:
            titulo += "  |  " + "   ".join(partes)
    ax.set_title(titulo, color="#c8d6d0", fontsize=10)
    ax.tick_params(colors="#5f7269")
    ax.legend(facecolor="#111816", labelcolor="#c8d6d0")
    for spine in ax.spines.values():
        spine.set_edgecolor("#1f2e2a")

    plt.tight_layout()
    salida = os.path.join(carpeta, PNG_POBLACION)
    plt.savefig(salida, dpi=120, facecolor=fig.get_facecolor())
    plt.close()
    return salida


def resumir_timelapse(carpeta, intervalo_s=None):
    """Regenera grafica + eventos a partir de conteo.csv ya escrito.

    Se puede llamar sobre un experimento viejo: no necesita las imagenes,
    solo el CSV.
    """
    series = leer_conteo(carpeta)
    if not series:
        return {"error": f"no hay {CSV_CONTEO} en {carpeta}"}

    eventos, ajustes = {}, {}
    for cam, s in series.items():
        eventos[cam] = detectar_eventos(s["ciclos"], s["conteos"], s["nitidez"])
        if intervalo_s:
            horas = [c * intervalo_s / 3600.0 for c in s["ciclos"]]
            ajuste = ajustar_crecimiento(horas, s["conteos"])
            if ajuste:
                ajustes[cam] = ajuste

    return {
        "carpeta": str(carpeta),
        "camaras": {
            str(cam): {
                "ciclos": len(s["ciclos"]),
                "primero": s["conteos"][0] if s["conteos"] else 0,
                "ultimo": s["conteos"][-1] if s["conteos"] else 0,
                "maximo": max(s["conteos"]) if s["conteos"] else 0,
                "crecimiento": ajustes.get(cam),
                "eventos": eventos.get(cam, []),
            } for cam, s in sorted(series.items())
        },
        "eventos_csv": escribir_eventos(carpeta, eventos),
        "grafica": graficar_poblacion(carpeta, intervalo_s),
    }
