# Artículo para HardwareX (borrador)

`hardwarex.tex` es un borrador del artículo de **hardware y software** del
microscopio, con la estructura de HardwareX: tabla de especificaciones,
contexto, descripción, archivos de diseño, lista de materiales, armado,
operación y validación. `hardwarex.pdf` es la versión compilada, para
leerla sin LaTeX.

`hardwarex_es.tex` / `hardwarex_es.pdf` es la **misma versión en español**,
para revisarla. La que se envía es la de inglés: si se corrige algo en una,
hay que pasarlo a la otra.

El análisis de migración celular **no** está aquí: va en otro artículo,
con [pipeline-migracion-celular](https://github.com/AFMMenchacalab/pipeline-migracion-celular).

## Cómo leer el borrador

- Lo que está en **rojo** (`\TODO{...}`) falta. No hay ningún número
  inventado: los valores que aparecen salen del código o de mediciones
  documentadas en el repositorio.
- Los resultados de las pruebas sin hardware (el conteo 0 contra 40
  células, el barrido de nitidez que se va de foco) son de **simulación**
  y el texto lo dice. Hay que confirmarlos con muestras reales.
- La figura de la interfaz es del microscopio simulado; conviene
  reemplazarla por una captura del equipo real con células reales.

## Compilar

```bash
cd docs/articulo
pdflatex hardwarex && bibtex hardwarex && pdflatex hardwarex && pdflatex hardwarex
# versión en español (requiere texlive-lang-spanish)
pdflatex hardwarex_es && bibtex hardwarex_es && pdflatex hardwarex_es && pdflatex hardwarex_es
```

Requiere la clase `elsarticle` (en TeX Live: `texlive-publishers`). También
se puede subir la carpeta a Overleaf junto con `../capturas/web_pagina.png`.

## Lo que falta para poder enviarlo

**Bloqueantes de la revista**

- [ ] **Licencia.** El repositorio no tiene archivo `LICENSE`. HardwareX
      exige licencias abiertas. Sugerencia: GPL-3.0 para software y
      firmware, CERN-OHL-S-2.0 para el diseño de hardware.
- [ ] **Archivos de diseño mecánico** (STL + STEP): soportes de cámara,
      portamuestras, soporte de la matriz de LEDs, piezas de la platina,
      cámara de incubación, diagrama de cableado.
- [ ] **Lista de materiales** con precios, proveedores y números de parte.
- [ ] **Instrucciones de armado** con fotos, paso a paso.
- [ ] **Repositorio aceptado** (Zenodo, OSF o Mendeley Data) con DOI.
      GitHub solo no basta; Zenodo puede archivar una versión del repo
      directamente.

**Validación** (sección 7, cada punto con su figura)

- [ ] Resolución y escala: carta USAF 1951 y portaobjetos micrométrico.
- [ ] Contraste DPC: el mismo campo en campo claro, media apertura y DPC.
- [ ] Autofoco: precisión y repetibilidad desde desenfoques conocidos,
      tiempo por enfoque, rango lineal, fallos con campo vacío y confluente.
- [ ] Mecánica: precisión del paso y juego del husillo.
- [ ] Estabilidad en 48 h: temperatura, CO₂, deriva del foco.
- [ ] Estabilidad de la iluminación en 48 h.
- [ ] Viabilidad celular dentro del equipo contra un control en incubadora.
- [ ] Diafonía entre los dos canales.
- [ ] Ejemplo de aplicación: timelapse de 48 h con segmentación.
- [ ] Cerrar o actualizar `TODO_HW.md` (casi todo figura como pendiente).

**Texto**

- [ ] Autores, afiliación, correo, roles CRediT, financiamiento.
- [ ] Declaración de uso de IA generativa (Elsevier la pide).
- [ ] Comprobar contra la literatura reciente la frase "to our knowledge,
      no open instrument combines…" (fin de la sección 1).
- [ ] Objetivo, lente de tubo y escala medida (sección 2, *Imaging*).
- [ ] Distancia de la matriz a la muestra y NA de iluminación.
- [ ] Descripción de la cámara de incubación (volumen, calefactor, fuente
      de CO₂, humedad).
- [ ] Diagrama de la arquitectura (figura 1; la versión en texto está en
      el README).

## Referencias

`referencias.bib` tiene solo las entradas de `MiBiblio.bib` que cita el
artículo (copiadas tal cual, con las siglas protegidas entre llaves para
que BibTeX no las pase a minúsculas), más dos agregadas al final:

- `Collins2020OFM`: el artículo de OpenFlexure "Robotic microscopy for
  everyone" es de **Biomedical Optics Express 2020**. En `MiBiblio.bib`,
  `Ref041` lo pone en Rev. Sci. Instrum. 2019; conviene corregirlo allá
  también.
- `Liao2016Autofocus`: autofoco con iluminación oblicua de dos LEDs, el
  antecedente más cercano del autofoco de MicroscopeOS.

Verifica ambas contra la fuente antes de enviar.

Otras cosas que vi en `MiBiblio.bib` (no afectan este artículo, pero sí
la tesis):

- `Ref029` y `Ref085` son el mismo Trackoscope con datos distintos
  (PLoS ONE con "Soneji, P." contra eLife con "Soneji, R."). Queda uno.
- `Ref040` y `Ref083` son UC2 con títulos y años distintos. El de Nature
  Communications 2020 es `Ref040`.
- `Ref048` y `Luzhansky2018` tienen el mismo título con autores y revista
  distintos. El correcto parece `Luzhansky2018` (APL Bioengineering).
- `Ref057` y `Ref062` son el mismo artículo (Hu, Becker y Willits, 2023).
- `Ref063` y `Sandvold2023` son el mismo CellTraxx con autores distintos.
- `Boerlage2018`: la clave no coincide con los autores (Müller, Schürmann
  y Guck).
- `Kearns2026OFMLive` (DOI con prefijo 10.64898) y `Sato2026DIY` (PMID):
  no pude verificarlos.
- Muchas entradas `RefNNN` no tienen volumen, páginas ni DOI; HardwareX
  pide referencias completas.
