// ===== Ajustes de la demo, despues del script de la pagina =====
(function(){
  // En la demo la imagen siempre esta "en vivo": se redibuja con cada
  // movimiento del motor simulado. El boton solo cambia la etiqueta.
  window.toggleLive=function(n){
    live[n]=!live[n];
    const btn=document.getElementById('liveBtn'+n);
    btn.classList.toggle('active',live[n]);
    btn.querySelector('.lbl').textContent=live[n]?'Detener vivo':'En vivo';
    document.getElementById('liveTag'+n).style.display=live[n]?'flex':'none';
  };
  [0,1].forEach(n=>{toggleLive(n);__simPintar(n);});
  // Galeria: las miniaturas son imagenes generadas, no archivos del servidor.
  __simSemilla();
  const urlReal=window.urlExp;
  window.urlExp=function(id,que,rel,extra){
    if((que==='mini')&&rel){const f=__simFoto(id,rel);return f?f.url:'';}
    return urlReal(id,que,rel,extra);
  };
  // Vista previa de la marca de agua dibujada en el navegador.
  window.vistaMarca=function(){
    const img=document.getElementById('marcaVista');
    __simPintar(0);
    img.src=__simMarca(__simRender(0));
  };
  // Las descargas no funcionan dentro de la demo: avisarlo en vez de nada.
  document.addEventListener('click',e=>{
    const a=e.target.closest&&e.target.closest('a[download]');
    if(a){e.preventDefault();toast('En el microscopio esto descarga el archivo; en la demo no hay archivos reales');}
  },true);
  cargarGaleria();
  document.getElementById('demoReset').addEventListener('click',()=>{
    for(const m of [0,1]){
      __sim.foco[m]=__sim.pos[m]+(Math.random()<.5?-1:1)*(15+Math.random()*45);
      __simPintar(m);
    }
    toast('Listo: las dos cámaras quedaron desenfocadas');
  });
})();
