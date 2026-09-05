"""Pruebas del conteo de celulas, el filtro de calidad y el autofoco IA.

    cd tests
    SP=$PWD PROY=$PWD/../codigo/MicroscopeOS python3 test_analisis.py

Sin Pi ni hardware. Los campos de celulas se sintetizan con la fisica
que el metodo explota: objetos de FASE, que en la imagen DPC no salen
como manchas sino como RELIEVE (un lobulo claro y uno oscuro por
celula, porque el DPC es la derivada del frente de onda). Es la
diferencia que hace que un umbral de brillo cuente el doble de objetos
de los que hay, y por eso las pruebas se hacen sobre eso y no sobre
discos solidos, que darian un resultado bonito y enganioso.

Lo que NO cubre: que las celulas reales se parezcan a estas gaussianas,
que el diametro configurado corresponda a la muestra, y que el ISP de
la Pi no meta cosas raras en el preview. Eso se ve en el microscopio.
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.environ.get("SP", os.path.dirname(os.path.abspath(__file__))))
import emuladores
emuladores.instalar()
gpio = emuladores.instalar_gpio()
sys.path.insert(0, os.environ.get(
    "PROY", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "codigo", "MicroscopeOS")))

import cv2
import numpy as np

from core import analisis as A
from core.analisis import Contador, ContadorEnVivo
from core.autofocus_ia import AutofocoIA, preparar, LADO

ok = fail = 0
def check(nombre, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print(f"  PASS  {nombre}  {extra}".rstrip())
    else:    fail += 1; print(f"  FAIL  {nombre}  {extra}")


# ---------------- campos sinteticos ----------------
def campo_fase(n, forma=(480, 640), sigma=7.0, separacion=22, semilla=0):
    """Mapa de fase con n celulas gaussianas, sin superponer centros."""
    r = np.random.default_rng(semilla)
    centros, intentos = [], 0
    while len(centros) < n and intentos < 60000:
        intentos += 1
        y = r.uniform(30, forma[0] - 30)
        x = r.uniform(30, forma[1] - 30)
        if all((x - a) ** 2 + (y - b) ** 2 > separacion ** 2
               for a, b in centros):
            centros.append((x, y))
    yy, xx = np.mgrid[0:forma[0], 0:forma[1]]
    fase = np.zeros(forma, np.float32)
    for a, b in centros:
        fase += np.exp(-(((xx - a) ** 2 + (yy - b) ** 2) / (2 * sigma ** 2)))
    return fase, centros


def a_dpc(fase, ruido=1.5, semilla=1, amplitud=90.0):
    """Fase -> imagen DPC: la DERIVADA de la fase, mas ruido de lectura.

    Cada celula queda en relieve, con el centro al mismo gris que el
    fondo. Es lo que ve el microscopio y lo que rompe cualquier umbral
    de brillo.
    """
    r = np.random.default_rng(semilla + 100)
    g = cv2.Sobel(fase, cv2.CV_32F, 1, 0, ksize=5)
    g = g / (np.abs(g).max() + 1e-9) * amplitud
    return np.clip(128 + g + r.normal(0, ruido, fase.shape),
                   0, 255).astype(np.uint8)


def par_medias_aperturas(fase, desenfoque_px=0.0, ruido=1.5, semilla=1):
    """Par (izquierda, derecha) con corrimiento lateral por desenfoque.

    Bajo media apertura el plano desenfocado se proyecta de lado, y
    hacia lados OPUESTOS segun que mitad ilumine: es lo que le da signo
    al desenfoque. Sin eso, ninguna cantidad de red neuronal puede
    saber hacia donde mover el eje.
    """
    r = np.random.default_rng(semilla + 200)
    g = cv2.Sobel(fase, cv2.CV_32F, 1, 0, ksize=5)
    g = g / (np.abs(g).max() + 1e-9) * 90.0
    borroso = max(0.3, abs(desenfoque_px) / 6.0)
    salida = []
    for signo in (+1, -1):
        m = np.float32([[1, 0, signo * desenfoque_px / 2.0], [0, 1, 0]])
        d = cv2.warpAffine(g * signo, m, (fase.shape[1], fase.shape[0]),
                           borderMode=cv2.BORDER_REFLECT)
        d = cv2.GaussianBlur(d, (0, 0), borroso)
        salida.append(np.clip(128 + d + r.normal(0, ruido, fase.shape),
                              0, 255).astype(np.uint8))
    return salida[0], salida[1]


print("\n=== CONTEO: EXACTITUD SEGUN DENSIDAD ===")
for n_pedidas, sep, tolerancia in ((5, 22, 0.25), (20, 22, 0.25),
                                   (40, 22, 0.30), (80, 22, 0.40)):
    fase, centros = campo_fase(n_pedidas, separacion=sep, semilla=n_pedidas)
    res = A.analizar(a_dpc(fase, semilla=n_pedidas), diametro_px=14,
                     escalar=False)
    reales = len(centros)
    error = abs(res["n"] - reales) / reales
    check(f"{reales} celulas -> {res['n']}", error <= tolerancia,
          f"error {error*100:.0f}% (tolerancia {tolerancia*100:.0f}%)")

print("\n=== CONTEO: NO CUENTA LOS DOS LOBULOS DEL RELIEVE ===")
# El error clasico: la imagen DPC pone cada celula como un lobulo claro
# y uno oscuro. Si el suavizado no los funde, el conteo sale al DOBLE y
# la mascara "se ve bien" -- son las celulas, partidas al medio.
fase, centros = campo_fase(30, semilla=77)
res = A.analizar(a_dpc(fase, semilla=77), diametro_px=14, escalar=False)
check("30 celulas no dan ~60", res["n"] < len(centros) * 1.4,
      f"n={res['n']} vs {len(centros)} reales")

print("\n=== FILTRO DE CAMPO VACIO ===")
r = np.random.default_rng(9)
yy, xx = np.mgrid[0:480, 0:640]
vacios = [
    ("ruido debil", np.clip(128 + r.normal(0, 1.5, (480, 640)), 0, 255)),
    ("ruido fuerte", np.clip(128 + r.normal(0, 20, (480, 640)), 0, 255)),
    ("negro (luz apagada)", np.zeros((480, 640))),
    ("saturado", np.full((480, 640), 255.0)),
    # Vinieta + gradiente de la matriz de LEDs, sin ninguna celula: es
    # el caso que enganiaba a un umbral global.
    ("vinieta sin celulas",
     np.clip(90 + 60 * (xx / 640) +
             30 * np.exp(-((xx - 320) ** 2 + (yy - 240) ** 2) / (2 * 250.0 ** 2))
             + r.normal(0, 3, (480, 640)), 0, 255)),
]
for nombre, img in vacios:
    res = A.analizar(img.astype(np.uint8), diametro_px=14, escalar=False)
    check(f"campo vacio: {nombre}", res["n"] == 0 and res["vacio"],
          f"n={res['n']} correlacion={res['correlacion']}")

# Ruido de 16 bits: el mismo caso pero en la escala del TIFF cientifico.
ruido16 = np.clip(8000 + r.normal(0, 300, (480, 640)), 0, 65535).astype(np.uint16)
res16 = A.analizar(ruido16, diametro_px=14, escalar=False)
check("campo vacio: ruido de 16 bits", res16["n"] == 0 and res16["vacio"],
      f"n={res16['n']} correlacion={res16['correlacion']}")

fase, centros = campo_fase(40, semilla=4)
res = A.analizar(a_dpc(fase, semilla=4), diametro_px=14, escalar=False)
check("un campo CON celulas no se marca vacio", not res["vacio"],
      f"n={res['n']}")

# REGRESION: el filtro de campo vacio NO puede depender del diametro
# configurado. Con un cociente entre bandas de frecuencia si dependia
# (la misma foto real daba 0.5 con diametro 14 y 2.3 con diametro 30),
# asi que subir el diametro declaraba vacia una imagen con contenido.
img_real = a_dpc(campo_fase(40, semilla=4)[0], semilla=4)
ruido_puro = np.clip(128 + r.normal(0, 8, (480, 640)), 0, 255).astype(np.uint8)
vacios_por_diametro = [A.analizar(img_real, diametro_px=d, escalar=False)["vacio"]
                       for d in (8, 14, 20, 30, 45)]
check("la decision de campo vacio no depende del diametro",
      not any(vacios_por_diametro), f"vacio por diametro={vacios_por_diametro}")
check("y el ruido sigue dando vacio con cualquier diametro",
      all(A.analizar(ruido_puro, diametro_px=d, escalar=False)["vacio"]
          for d in (8, 14, 20, 30, 45)))

print("\n=== CORRELACION ENTRE PIXELES VECINOS ===")
check("una imagen optica correlaciona alto",
      A.correlacion_vecinos(img_real) > 0.5,
      f"corr={A.correlacion_vecinos(img_real):.3f}")
check("el ruido blanco no correlaciona",
      abs(A.correlacion_vecinos(ruido_puro)) < 0.1,
      f"corr={A.correlacion_vecinos(ruido_puro):.3f}")
check("una imagen constante no rompe la division",
      A.correlacion_vecinos(np.zeros((100, 100), np.uint8)) == 0.0)
# La vinieta es suavisima: sin quitarla, un campo vacio con vinieta
# correlacionaria alto y pasaria por imagen con contenido.
vinietado = np.clip(90 + 60 * (xx / 640) + r.normal(0, 3, (480, 640)),
                    0, 255).astype(np.uint8)
check("la vinieta no infla la correlacion de un campo vacio",
      abs(A.correlacion_vecinos(vinietado)) < 0.1,
      f"corr={A.correlacion_vecinos(vinietado):.3f}")

print("\n=== FILTRO: CONFLUENCIA SE AVISA, NO SE MIENTE ===")
# El fallo peligroso seria reportar 0 celulas en un pozo lleno: la
# curva de poblacion caeria a cero justo cuando el cultivo esta al
# maximo. Se pide que cuente algo Y que lo marque como cota inferior.
fase, centros = campo_fase(400, separacion=13, semilla=400)
res = A.analizar(a_dpc(fase, semilla=400), diametro_px=14, escalar=False)
check("campo confluente no se reporta vacio", not res["vacio"],
      f"n={res['n']} de {len(centros)} reales")
check("campo confluente se marca como confluente", res["confluente"],
      f"separacion={res['separacion']}")

print("\n=== LA ILUMINACION DECIDE SI HAY ALGO QUE CONTAR ===")
# El conteo EN VIVO mira un solo frame con una sola iluminacion: no
# puede hacer DPC. Y una celula sin tenir es un objeto de fase, asi que
# en campo claro su contraste es proporcional al desenfoque y se ANULA
# en el foco. Con luz plena, el contador reporta campo vacio sobre un
# cultivo lleno -- y no es un bug del filtro: la celula no esta en la
# imagen. Lo arregla la iluminacion, no el algoritmo.
fase_ph, centros_ph = campo_fase(40, separacion=24, semilla=3)
_lap = cv2.Laplacian(fase_ph, cv2.CV_32F)
_gx = cv2.Sobel(fase_ph, cv2.CV_32F, 1, 0, ksize=5)
_rng_luz = np.random.default_rng(0)

def campo_claro(desenfoque_px):
    """Transporte de intensidad: contraste proporcional al desenfoque."""
    sig = max(0.3, abs(desenfoque_px) / 6.0)
    img = 128 + (-desenfoque_px * 0.05) * 60 * cv2.GaussianBlur(
        _lap, (0, 0), sig) / (np.abs(_lap).max() + 1e-9) * 20
    return np.clip(img + _rng_luz.normal(0, 1.5, fase_ph.shape),
                   0, 255).astype(np.uint8)

def media_apertura(desenfoque_px, signo=+1):
    """Bajo media apertura aparece el GRADIENTE de fase, con signo."""
    sig = max(0.3, abs(desenfoque_px) / 6.0)
    g = cv2.GaussianBlur(_gx, (0, 0), sig) / (np.abs(_gx).max() + 1e-9) * 90
    return np.clip(128 + signo * g + _rng_luz.normal(0, 1.5, fase_ph.shape),
                   0, 255).astype(np.uint8)

res_claro = A.analizar(campo_claro(0), diametro_px=14, escalar=False)
check("campo claro EN FOCO: la fase es invisible, da campo vacio",
      res_claro["vacio"],
      f"n={res_claro['n']} con {len(centros_ph)} celulas presentes")

res_obl = A.analizar(media_apertura(0), diametro_px=14, escalar=False)
error_obl = abs(res_obl["n"] - len(centros_ph)) / len(centros_ph)
check("media apertura EN FOCO: las cuenta bien (un solo patron estatico)",
      error_obl <= 0.25,
      f"n={res_obl['n']} de {len(centros_ph)} (error {error_obl*100:.0f}%)")

dpc_foco = ((A.imagen_dpc(media_apertura(0, +1), media_apertura(0, -1))
             * 0.5 + 0.5) * 255).astype(np.uint8)
res_dpc = A.analizar(dpc_foco, diametro_px=14, escalar=False)
check("DPC EN FOCO: tambien las cuenta (el camino de las fotos)",
      abs(res_dpc["n"] - len(centros_ph)) / len(centros_ph) <= 0.25,
      f"n={res_dpc['n']} de {len(centros_ph)}")

print("\n=== FILTRO DE FOCO ===")
fase, _ = campo_fase(40, semilla=4)
nitido = a_dpc(fase, semilla=4)
borroso = cv2.GaussianBlur(nitido, (0, 0), 4.0)
n1 = A.analizar(nitido, diametro_px=14, escalar=False)["nitidez"]
n2 = A.analizar(borroso, diametro_px=14, escalar=False)["nitidez"]
check("la nitidez cae al desenfocar", n2 < n1 * 0.6,
      f"enfocado={n1:.4f} desenfocado={n2:.4f}")

print("\n=== INVARIANZA: 8 vs 16 BITS Y RESOLUCION ===")
img8 = a_dpc(fase, semilla=4)
img16 = img8.astype(np.uint16) * 257
check("mismo conteo en 8 y 16 bits",
      A.analizar(img8, diametro_px=14, escalar=False)["n"] ==
      A.analizar(img16, diametro_px=14, escalar=False)["n"])

# El diametro se declara a 640 px de ancho y se reescala solo: el mismo
# numero tiene que servir para el preview y para el TIFF nativo.
fase_g, centros_g = campo_fase(40, forma=(1232, 1640), sigma=7.0 * 2.56,
                               separacion=56, semilla=11)
res_g = A.analizar(a_dpc(fase_g, semilla=11), diametro_px=14, escalar=True)
error = abs(res_g["n"] - len(centros_g)) / len(centros_g)
check("escalado automatico a resolucion mayor", error <= 0.35,
      f"n={res_g['n']} de {len(centros_g)} (d efectivo {res_g['diametro_px']}px)")

print("\n=== CUMULOS: SE ESTIMAN, NO SE DESCARTAN ===")
# Un grumo que el watershed no parte no puede desaparecer del conteo:
# si se descartara, las regiones con MAS celulas serian justo las que
# no se cuentan y la curva se aplanaria cuando el cultivo crece.
yy2, xx2 = np.mgrid[0:200, 0:200]
pegadas = (np.exp(-(((xx2 - 92) ** 2 + (yy2 - 100) ** 2) / (2 * 7.0 ** 2))) +
           np.exp(-(((xx2 - 108) ** 2 + (yy2 - 100) ** 2) / (2 * 7.0 ** 2))))
img_p = a_dpc(pegadas.astype(np.float32), semilla=3)
con = A.analizar(img_p, diametro_px=14, escalar=False, separar=True)["n"]
sin = A.analizar(img_p, diametro_px=14, escalar=False, separar=False)["n"]
check("dos celulas pegadas: watershed las separa", con >= 2,
      f"con separar={con}, sin separar={sin}")
check("sin watershed igual cuenta algo (no las tira)", sin >= 1,
      f"n={sin}")

print("\n=== DIBUJO DEL OVERLAY ===")
fase, _ = campo_fase(20, semilla=5)
img = a_dpc(fase, semilla=5)
res = A.analizar(img, diametro_px=14, escalar=False)
overlay = A.dibujar(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), res)
check("el overlay conserva el tamanio", overlay.shape[:2] == img.shape[:2])
check("el overlay dibuja algo", not np.array_equal(
    overlay, cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)))
# Marca de campo vacio: tiene que decirlo, no dibujar nada y callarse.
vacio_res = A.analizar(np.zeros((200, 200), np.uint8), diametro_px=14,
                       escalar=False)
ov_vacio = A.dibujar(np.zeros((200, 200, 3), np.uint8), vacio_res)
check("overlay de campo vacio no inventa contornos",
      len(vacio_res["contornos"]) == 0 and ov_vacio.any())

print("\n=== CONTEO EN VIVO: MODO MANUAL (medicion congelada) ===")
class ContadorEspia(Contador):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.llamadas = 0
    def analizar(self, gray, **kw):
        self.llamadas += 1
        return super().analizar(gray, **kw)

frame_celulas = cv2.cvtColor(a_dpc(campo_fase(20, semilla=6)[0], semilla=6),
                             cv2.COLOR_GRAY2BGR)
espia_m = ContadorEspia(diametro_px=14)
manual = ContadorEnVivo(espia_m, periodo=0.2, ancho_analisis=320)
check("el modo por defecto es manual", manual.modo == "manual")

manual.activar(0)
# Sin haber medido nunca: no dibuja nada. Estampar "campo vacio" seria
# mentir -- no es que no haya celulas, es que nadie midio todavia.
sin_medir = manual.anotar(0, frame_celulas.copy())
check("manual sin medir: devuelve el frame limpio",
      np.array_equal(sin_medir, frame_celulas) and espia_m.llamadas == 0)

resultado_manual = manual.medir(0, frame_celulas)
check("medir() cuenta y activa la camara",
      resultado_manual["n"] > 0 and manual.activo(0),
      f"n={resultado_manual['n']}")
llamadas_tras_medir = espia_m.llamadas

time.sleep(0.35)   # mas que el periodo: en auto ya habria vuelto a medir
for _ in range(20):
    marcado_m = manual.anotar(0, frame_celulas.copy())
check("manual NO vuelve a medir solo, ni pasado el periodo",
      espia_m.llamadas == llamadas_tras_medir,
      f"llamadas={espia_m.llamadas}")
check("pero sigue dibujando el resultado congelado",
      not np.array_equal(marcado_m, frame_celulas))

# El punto de todo el modo manual: la medicion sigue en pantalla aunque
# la iluminacion haya vuelto a campo claro, donde no habria nada que
# contar. Se simula pasandole frames de campo claro.
frame_claro = cv2.cvtColor(campo_claro(0), cv2.COLOR_GRAY2BGR)
marcado_claro = manual.anotar(0, frame_claro.copy())
check("el dibujo sobrevive al volver a campo claro",
      espia_m.llamadas == llamadas_tras_medir and
      not np.array_equal(marcado_claro, frame_claro))

resultado_2 = manual.medir(0, frame_celulas)
check("volver a medir actualiza", espia_m.llamadas == llamadas_tras_medir + 1,
      f"n={resultado_2['n']}")

manual.desactivar(0)
check("apagar borra el dibujo",
      np.array_equal(manual.anotar(0, frame_celulas.copy()), frame_celulas))

print("\n=== CONTEO EN VIVO: MODO AUTOMATICO ===")
# El stream corre a ~16 fps y una segmentacion cuesta mucho mas que un
# frame. Si se analizara cada uno, el vivo se caeria a la mitad de
# velocidad sin aportar nada: las celulas no se mueven a 16 fps.
espia = ContadorEspia(diametro_px=14)
vivo = ContadorEnVivo(espia, periodo=0.5, ancho_analisis=320, modo="auto")
vivo.activar(0)
frame = frame_celulas
for _ in range(25):
    vivo.anotar(0, frame.copy())
check("con periodo 0.5s, 25 frames seguidos analizan 1 vez",
      espia.llamadas == 1, f"llamadas={espia.llamadas}")

time.sleep(0.55)
vivo.anotar(0, frame.copy())
check("pasado el periodo vuelve a analizar", espia.llamadas == 2,
      f"llamadas={espia.llamadas}")

salida = vivo.anotar(0, frame.copy())
check("el frame anotado vuelve del mismo tamanio",
      salida.shape == frame.shape)

# El frame que llega puede ser una vista de solo lectura del buffer de
# picamera2: dibujar sobre el original en vez de sobre una copia falla
# en la Pi y en ningun lado mas.
solo_lectura = frame.copy()
solo_lectura.setflags(write=False)
marcado = vivo.anotar(0, solo_lectura)
check("no escribe sobre el frame de entrada (buffer de la camara)",
      marcado is not solo_lectura and not np.array_equal(marcado, frame))

vivo.desactivar(0)
check("desactivar() apaga la camara", not vivo.activo(0))
antes = espia.llamadas
sin_marcar = vivo.anotar(0, frame.copy())
check("apagado, devuelve el frame intacto y no analiza",
      espia.llamadas == antes and np.array_equal(sin_marcar, frame))

ultimo = vivo.ultimo(1)
check("ultimo() no filtra contornos ni objetos al JSON",
      ultimo is None or ("contornos" not in ultimo and "objetos" not in ultimo))

print("\n=== REDUCCION DE RESOLUCION ===")
# Analizar un TIFF nativo entero cuesta segundos (los desenfoques son
# con sigmas enormes); reducir no cambia el conteo porque el diametro
# se reescala con el ancho.
grande = cv2.resize(a_dpc(campo_fase(40, semilla=12)[0], semilla=12),
                    (3280, 2464), interpolation=cv2.INTER_LINEAR)
chico, escala = A.reducir(grande, 1200)
check("reducir respeta el ancho pedido", chico.shape[1] == 1200,
      f"{grande.shape} -> {chico.shape}, escala {escala:.2f}")
check("no reduce si ya es chico", A.reducir(img, 1200)[1] == 1.0)

print("\n=== LECTURA DE CAPTURAS Y OVERLAY EN DISCO ===")
tmp = tempfile.mkdtemp()
fase, centros = campo_fase(30, semilla=8)
izq, der = par_medias_aperturas(fase, desenfoque_px=2.0, semilla=8)
cv2.imwrite(os.path.join(tmp, "img_L.png"), izq)
cv2.imwrite(os.path.join(tmp, "img_R.png"), der)
res, imagen = A.analizar_capturas(
    {"_L": os.path.join(tmp, "img_L.png"), "_R": os.path.join(tmp, "img_R.png")},
    Contador(diametro_px=14))
check("analizar_capturas usa el par L/R como DPC", res["n"] > 0,
      f"n={res['n']} de {len(centros)} reales")
ruta_ov = A.guardar_overlay(os.path.join(tmp, "ov.png"), imagen, res)
check("guarda el overlay en disco", os.path.getsize(ruta_ov) > 0)

res1, _ = A.analizar_capturas(os.path.join(tmp, "img_L.png"),
                              Contador(diametro_px=14))
check("analizar_capturas acepta una imagen suelta", res1["n"] > 0,
      f"n={res1['n']}")

print("\n=== CURVA DE POBLACION Y CRECIMIENTO ===")
horas = [i * 0.5 for i in range(24)]
duplicacion_real = 6.0
conteos = [int(10 * 2 ** (t / duplicacion_real)) for t in horas]
ajuste = A.ajustar_crecimiento(horas, conteos)
check("recupera el tiempo de duplicacion",
      abs(ajuste["duplicacion_h"] - duplicacion_real) < 0.3,
      f"{ajuste['duplicacion_h']} h vs {duplicacion_real} h reales")
check("r2 alto en crecimiento exponencial limpio", ajuste["r2"] > 0.99,
      f"r2={ajuste['r2']}")
check("sin puntos suficientes devuelve None",
      A.ajustar_crecimiento([0, 1], [5, 6]) is None)
check("una serie en cero no rompe el ajuste",
      A.ajustar_crecimiento(horas, [0] * 24) is None)

print("\n=== DETECCION DE EVENTOS ===")
serie = [20] * 10 + [3] + [20] * 5
eventos = A.detectar_eventos(list(range(1, 17)), serie)
tipos = [e["tipo"] for e in eventos]
check("detecta la caida brusca", "campo_perdido" in tipos or
      "caida_poblacion" in tipos, f"{eventos}")
check("marca el ciclo correcto",
      any(e["ciclo"] == 11 for e in eventos), f"{eventos}")

# Crecimiento suave: no debe disparar nada. Es el falso positivo que
# haria inutil la deteccion, porque un timelapse ENTERO es crecimiento.
suave = [int(20 * 1.06 ** i) for i in range(30)]
check("el crecimiento normal no dispara eventos",
      len(A.detectar_eventos(list(range(1, 31)), suave)) == 0,
      f"{A.detectar_eventos(list(range(1,31)), suave)}")

# Con pocas celulas, +1 no es un evento: es una celula.
check("con conteos chicos no dispara por ruido de division",
      len(A.detectar_eventos([1, 2, 3, 4, 5], [3, 4, 3, 5, 4])) == 0)

nitidez_serie = [0.05] * 8 + [0.01] + [0.05] * 3
ev_foco = A.detectar_eventos(list(range(1, 13)), [20] * 12,
                             nitidez=nitidez_serie)
check("detecta la perdida de foco",
      any(e["tipo"] == "desenfoque" and e["ciclo"] == 9 for e in ev_foco),
      f"{ev_foco}")

print("\n=== CSV, GRAFICA Y RESUMEN ===")
carpeta = tempfile.mkdtemp()
for ciclo in range(1, 16):
    for cam in (0, 1):
        A.escribir_conteo(carpeta, {
            "timestamp": f"2026090{ciclo//10}_{ciclo:04d}00", "ciclo": ciclo,
            "camara": cam, "n": int(10 * 1.15 ** ciclo) + cam,
            "regiones": 10, "cumulos": 1, "cobertura": 0.12,
            "confluente": 0, "vacio": 0, "separacion": 20.0,
            "nitidez": 0.05})
series = A.leer_conteo(carpeta)
check("el CSV se relee con las dos camaras", set(series) == {0, 1},
      f"camaras={sorted(series)}")
check("todos los ciclos", len(series[0]["ciclos"]) == 15)

resumen = A.resumir_timelapse(carpeta, intervalo_s=1800)
check("el resumen trae las dos camaras", set(resumen["camaras"]) == {"0", "1"})
check("el resumen calcula crecimiento",
      resumen["camaras"]["0"]["crecimiento"]["duplicacion_h"] > 0,
      f"duplicacion={resumen['camaras']['0']['crecimiento']['duplicacion_h']} h")
check("escribe eventos.csv", os.path.exists(resumen["eventos_csv"]))
check("genera poblacion.png",
      resumen["grafica"] and os.path.getsize(resumen["grafica"]) > 0)

vacia = tempfile.mkdtemp()
check("carpeta sin conteo.csv devuelve error, no excepcion",
      "error" in A.resumir_timelapse(vacia))

# Una fila truncada (corte de corriente a mitad de escritura) no puede
# tirar abajo la lectura del resto del experimento.
with open(os.path.join(carpeta, A.CSV_CONTEO), "a") as f:
    f.write("2026090_0000,16,0,")
check("una fila truncada no rompe la lectura",
      len(A.leer_conteo(carpeta)[0]["ciclos"]) == 15)

print("\n=== TIMELAPSE CON CONTEO (integracion) ===")
from core.timelapse import TimelapseManager

class CamaraSintetica:
    """Guarda campos de celulas de verdad, para que el conteo del
    timelapse tenga algo real que leer."""
    def __init__(self):
        self.n = 0
    def capture_image(self, camera_num, folder, filename):
        os.makedirs(folder, exist_ok=True)
        # Poblacion creciente entre ciclos, para que la curva tenga
        # pendiente y el ajuste de crecimiento sea verificable.
        cantidad = 10 + self.n // 4
        fase, _ = campo_fase(cantidad, semilla=self.n)
        self.n += 1
        cv2.imwrite(filename, a_dpc(fase, semilla=self.n))
        return filename

class LuzFake:
    def __init__(self): self.patron = None
    def _s(self, p): self.patron = p
    def on(self): self._s("on")
    def left(self): self._s("left")
    def right(self): self._s("right")
    def top(self): self._s("top")
    def bottom(self): self._s("bottom")
    def off(self): self.patron = None

previo = os.getcwd()
trabajo = tempfile.mkdtemp()
os.chdir(trabajo)
try:
    cam = CamaraSintetica()
    # .png para que cv2 pueda releerlo: el emulador de tifffile no
    # guarda pixeles de verdad.
    import core.timelapse as tl_mod
    tl = TimelapseManager(cam, {0: LuzFake()}, contador=Contador(diametro_px=14))
    original = tl._capturar_secuencial
    def capturar_png(patrones, camaras, ts, estab):
        guardadas = original(patrones, camaras, ts, estab)
        return {c: {s: p.replace(".tif", ".png") for s, p in r.items()}
                for c, r in guardadas.items()}
    # capture_image recibe el .tif; se reescribe la extension despues
    class CamaraPNG(CamaraSintetica):
        def capture_image(self, camera_num, folder, filename):
            return super().capture_image(camera_num, folder,
                                         filename.replace(".tif", ".png"))
    tl.camera = CamaraPNG()
    tl._capturar_secuencial = capturar_png

    tl.start(modo="dpc", interval_seconds=0, duration_seconds=1.2,
             stabilization_time=0, camaras=[0], contar=True, contar_cada=1)
    t_fin = time.time() + 12
    while tl.is_running() and time.time() < t_fin:
        time.sleep(0.05)
    tl.stop()

    carpeta_tl = tl.base_folder
    csv_path = os.path.join(carpeta_tl, A.CSV_CONTEO)
    check("el timelapse escribe conteo.csv", os.path.exists(csv_path))
    if os.path.exists(csv_path):
        s = A.leer_conteo(carpeta_tl)
        check("conteo.csv tiene filas de la camara 0", 0 in s and s[0]["ciclos"],
              f"ciclos={len(s.get(0, {}).get('ciclos', []))}")
        check("todos los ciclos cuentan algo",
              all(n > 0 for n in s[0]["conteos"]), f"{s[0]['conteos']}")
    check("genera poblacion.png al terminar",
          os.path.exists(os.path.join(carpeta_tl, A.PNG_POBLACION)))
    check("genera eventos.csv al terminar",
          os.path.exists(os.path.join(carpeta_tl, A.CSV_EVENTOS)))
    overlays = [f for f in os.listdir(os.path.join(carpeta_tl, "cam0"))
                if f.startswith("conteo_")]
    check("guarda el PNG marcado de cada ciclo", len(overlays) > 0,
          f"{len(overlays)} overlays")

    # Sin contador, el timelapse tiene que correr igual.
    tl2 = TimelapseManager(CamaraPNG(), {0: LuzFake()}, contador=None)
    tl2.start(modo="blanco", interval_seconds=0, duration_seconds=0.3,
              stabilization_time=0, camaras=[0], contar=True)
    t_fin = time.time() + 8
    while tl2.is_running() and time.time() < t_fin:
        time.sleep(0.05)
    tl2.stop()
    check("conteo pedido sin contador: sigue sin conteo y sin romperse",
          not os.path.exists(os.path.join(tl2.base_folder, A.CSV_CONTEO)))
finally:
    os.chdir(previo)

print("\n=== AUTOFOCO IA: PREPROCESADO ===")
fase, _ = campo_fase(30, semilla=15)
izq, der = par_medias_aperturas(fase, desenfoque_px=6.0, semilla=15)
x = preparar(izq, der)
check("forma (1, 2, LADO, LADO)", x.shape == (1, 2, LADO, LADO), str(x.shape))
check("float32", x.dtype == np.float32)
check("normalizado a media ~0 y desvio ~1",
      abs(float(x.mean())) < 0.05 and abs(float(x.std()) - 1) < 0.05,
      f"media={x.mean():.3f} desvio={x.std():.3f}")
# La normalizacion es POR PAR: si fuera por imagen se borraria la
# diferencia entre las dos mitades, que es exactamente la senial.
check("conserva la diferencia entre las dos mitades",
      abs(float(x[0, 0].mean() - x[0, 1].mean())) > 1e-6,
      f"delta medias={float(x[0,0].mean()-x[0,1].mean()):.4f}")

x2 = preparar(izq.astype(np.uint16) * 257, der.astype(np.uint16) * 257)
check("mismo tensor con entrada de 16 bits",
      np.allclose(x, x2, atol=1e-3))

print("\n=== AUTOFOCO IA: DEGRADACION SIN MODELO ===")
ia_sin = AutofocoIA(ruta="/no/existe/modelo.onnx")
check("sin modelo, disponible() es False", not ia_sin.disponible())
check("sin modelo, informa el motivo", bool(ia_sin.error), ia_sin.error)
check("estado() es serializable a JSON",
      isinstance(json.dumps(ia_sin.estado()), str))

print("\n=== AUTOFOCO IA: CAMINO COMPLETO CON PREDICTOR INYECTADO ===")
# El modelo no existe todavia (hace falta grabar pilas en el
# microscopio), pero TODO lo que lo rodea si se puede probar: que mida
# el par, convierta micras a micropasos con el signo correcto, respete
# el tope de seguridad y termine con el ajuste fino analitico.
from core import motor_focus as mf
from core.autofocus import Autofocus, um_por_micropaso

class MotorFalso:
    def __init__(self, microsteps=16):
        self.microsteps = microsteps
        self.position = 0
        self.recorrido = []
    def mover_a(self, pos, delay=0.003, backlash=0, mantener=False):
        self.recorrido.append(pos)
        self.position = int(pos)
        return self.position

class CamaraFoco:
    """Devuelve el par corrido segun lo lejos que este del foco real."""
    def __init__(self, motor, foco_real):
        self.motor, self.foco_real = motor, foco_real
        self.luz = None
    def get_focus_frame(self, camera_num, descartar=1):
        d = self.motor.position - self.foco_real
        izq, der = par_medias_aperturas(fase, desenfoque_px=d * 0.02,
                                        semilla=20)
        return izq if self.luz.patron == "left" else der

motor = MotorFalso()
luz = LuzFake()
foco_real = -320                     # micropasos por debajo del inicio
cam_foco = CamaraFoco(motor, foco_real)
cam_foco.luz = luz

um_paso = um_por_micropaso(motor.microsteps)
def predictor_perfecto(a, b):
    """Predice el desenfoque exacto, en micras."""
    return (motor.position - foco_real) * um_paso

af = Autofocus(cam_foco, {0: motor}, {0: luz},
               ia=AutofocoIA(predictor=predictor_perfecto))
check("con predictor inyectado, disponible() es True", af.ia.disponible())

res = af.ia.enfocar(af, 0, iteraciones=3, fino=False, backlash=0)
check("la IA lleva el eje al foco real", abs(motor.position - foco_real) <= 2,
      f"posicion={motor.position}, foco real={foco_real}")
check("informa el metodo", res["metodo"] == "ia")
check("converge y lo dice", res["convergio"], f"{res['predicciones_um']}")

# Tope de seguridad: sin finales de carrera, una prediccion disparatada
# no puede mandar la plataforma contra el objetivo.
motor2 = MotorFalso()
cam2 = CamaraFoco(motor2, 0); cam2.luz = luz
af2 = Autofocus(cam2, {0: motor2}, {0: luz},
                ia=AutofocoIA(predictor=lambda a, b: 99999.0))
af2.ia.enfocar(af2, 0, iteraciones=1, fino=False, backlash=0, max_um=400)
recorrido_um = abs(motor2.position) * um_paso
check("una prediccion absurda queda acotada por max_um",
      recorrido_um <= 401, f"movio {recorrido_um:.0f} um")

# metodo="ia" sin modelo tiene que fallar claro, no caerse al barrido
# en silencio: si se pide explicitamente, el usuario quiere saber.
af3 = Autofocus(cam_foco, {0: motor}, {0: luz}, ia=AutofocoIA(ruta="/no/hay"))
try:
    af3.enfocar_auto(0, metodo="ia")
    check("metodo='ia' sin modelo levanta error", False)
except RuntimeError as e:
    check("metodo='ia' sin modelo levanta error", True, str(e)[:50])

print("\n=== GRABACION DE PILA DE FOCO ===")
from core.pila_foco import grabar_pila, cargar_pila

motor3 = MotorFalso()
cam3 = CamaraFoco(motor3, 0); cam3.luz = luz
af4 = Autofocus(cam3, {0: motor3}, {0: luz})
destino = tempfile.mkdtemp()
info = grabar_pila(af4, 0, rango=800, puntos=9, carpeta=destino,
                   settle=0, backlash=0, notas="prueba")
check("graba todos los planos", info["planos"] == 9, f"{info}")
check("el eje vuelve al centro", motor3.position == 0,
      f"posicion final={motor3.position}")

with open(os.path.join(destino, "manifiesto.json")) as f:
    manifiesto = json.load(f)
offsets = [p["offset_micropasos"] for p in manifiesto["planos"]]
check("las etiquetas estan centradas en cero", 0 in offsets and
      min(offsets) < 0 < max(offsets), f"offsets={offsets}")
check("etiqueta en micras coherente con el husillo",
      abs(manifiesto["planos"][-1]["offset_um"] -
          offsets[-1] * um_paso) < 1e-6)

pares, etiquetas = cargar_pila(destino)
check("cargar_pila devuelve pares y etiquetas",
      len(pares) == 9 and len(etiquetas) == 9)
check("cada plano trae las DOS medias aperturas",
      all(len(p) == 2 for p in pares))
# Sin las dos mitades no hay signo posible: la etiqueta positiva y la
# negativa se verian identicas.
check("las dos mitades del par son distintas",
      not np.array_equal(pares[0][0], pares[0][1]))

print(f"\n{'='*54}")
print(f"  {ok} PASS   {fail} FAIL")
print(f"{'='*54}")
sys.exit(1 if fail else 0)
