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
  document.getElementById('demoReset').addEventListener('click',()=>{
    for(const m of [0,1]){
      __sim.foco[m]=__sim.pos[m]+(Math.random()<.5?-1:1)*(15+Math.random()*45);
      __simPintar(m);
    }
    toast('Listo: las dos cámaras quedaron desenfocadas');
  });
})();
