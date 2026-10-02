// ===== Microscopio simulado para la demo =====
// Reemplaza fetch() y EventSource: la pagina real no se toca, solo
// habla con este simulador en vez de con la Raspberry.
(function(){
  const UM_PASO = 5.0;                       // um por paso completo (husillo T6x1)
  const sim = {
    pos:  {0: 0, 1: 0},                      // um
    foco: {0: 32, 1: -9},                    // um: donde esta el foco "de verdad"
    ms:   {0: 16, 1: 16},                    // microstepping manual
    cal:  {0: null, 1: null},
    jog:  null,                              // {motor, dir, hasta}
    luz:  {0: {on: false, modo: 'full', percent: 80}, 1: {on: false, modo: 'full', percent: 80}},
    ocupado: false,
    muestras: {},
    exps: [],                                // experimentos simulados
    papelera: {},
    optica: {0: {objetivo:'20x', aumento:20, na:0.4, aumento_adicional:1, pixel_um:1.12, um_por_pixel_medido:null},
             1: {objetivo:'20x', aumento:20, na:0.4, aumento_adicional:1, pixel_um:1.12, um_por_pixel_medido:null}},
    marca: {activa:true, logo:false, escala:true, datos:true,
            campos:{experimento:true, fecha:true, objetivo:true, escala:true, luz:true, camara:false},
            posicion:'abajo-derecha', tamano:'mediano', estilo:'oscuro'},
  };
  window.__sim = sim;

  // ---------- muestra sintetica ----------
  function rng(seed){return function(){seed|=0;seed=seed+0x6D2B79F5|0;let t=Math.imul(seed^seed>>>15,1|seed);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296;};}
  function crearMuestra(cam){
    const W=640,H=480,c=document.createElement('canvas');c.width=W;c.height=H;
    const g=c.getContext('2d'), r=rng(cam?7919:1237);
    const fondo=g.createRadialGradient(W/2,H/2,60,W/2,H/2,420);
    fondo.addColorStop(0,'#a9b3a6');fondo.addColorStop(1,'#6f786c');
    g.fillStyle=fondo;g.fillRect(0,0,W,H);
    const n=cam?38:55;
    for(let i=0;i<n;i++){
      const x=r()*W,y=r()*H,rx=9+r()*14,ry=7+r()*11,a=r()*Math.PI;
      g.save();g.translate(x,y);g.rotate(a);
      g.beginPath();g.ellipse(0,0,rx+2,ry+2,0,0,Math.PI*2);g.fillStyle='rgba(235,245,230,.55)';g.fill();
      g.beginPath();g.ellipse(0,0,rx,ry,0,0,Math.PI*2);g.fillStyle='rgba(70,82,68,.55)';g.fill();
      g.beginPath();g.ellipse(rx*.15,-ry*.1,rx*.38,ry*.42,0,0,Math.PI*2);g.fillStyle='rgba(40,48,40,.7)';g.fill();
      g.restore();
    }
    for(let i=0;i<160;i++){g.fillStyle='rgba(50,58,50,.5)';g.fillRect(r()*W,r()*H,1.5,1.5);}
    return c;
  }
  const lienzo=document.createElement('canvas');lienzo.width=640;lienzo.height=480;
  const chico=document.createElement('canvas');
  function render(cam){
    const m=sim.muestras[cam]||(sim.muestras[cam]=crearMuestra(cam));
    const d=Math.abs(sim.pos[cam]-sim.foco[cam]);
    const blur=Math.min(18,Math.max(0,d-1.2)*0.5);
    const g=lienzo.getContext('2d');
    if(blur<0.3){g.drawImage(m,0,0);}
    else{
      // desenfoque portable: achicar y volver a agrandar
      const s=1/(1+blur*0.7);chico.width=Math.max(8,640*s|0);chico.height=Math.max(6,480*s|0);
      const gc=chico.getContext('2d');gc.imageSmoothingQuality='high';
      gc.drawImage(m,0,0,chico.width,chico.height);
      g.imageSmoothingQuality='high';g.drawImage(chico,0,0,640,480);
    }
    if(d>3){g.fillStyle='rgba(140,150,138,'+Math.min(.55,d/90)+')';g.fillRect(0,0,640,480);}
    // la luz: apagada = negro; brillo; fondo negro invierte; colores tiñe
    const L=sim.luz[cam];
    if(!L.on){g.fillStyle='#050607';g.fillRect(0,0,640,480);return lienzo;}
    if(L.modo==='ring'){g.globalCompositeOperation='difference';g.fillStyle='#d8dcd6';g.fillRect(0,0,640,480);g.globalCompositeOperation='source-over';}
    if(L.modo==='rheinberg'){g.globalCompositeOperation='multiply';g.fillStyle='#7fa8ff';g.fillRect(0,0,640,480);g.globalCompositeOperation='source-over';}
    if(['left','right','top','bottom'].includes(L.modo)){g.globalCompositeOperation='multiply';g.fillStyle='#b9f5c4';g.fillRect(0,0,640,480);g.globalCompositeOperation='source-over';}
    const k=L.percent/80;
    if(k<1){g.fillStyle='rgba(0,0,0,'+Math.min(.95,1-k)+')';g.fillRect(0,0,640,480);}
    else if(k>1){g.fillStyle='rgba(255,255,255,'+Math.min(.6,(k-1)*.9)+')';g.fillRect(0,0,640,480);}
    return lienzo;
  }
  const pendiente={};
  function pintar(cam){
    if(pendiente[cam])return;pendiente[cam]=true;
    requestAnimationFrame(()=>{
      pendiente[cam]=false;
      const img=document.getElementById('img'+cam), ph=document.getElementById('ph'+cam);
      if(!img)return;
      img.src=render(cam).toDataURL('image/jpeg',0.82);
      img.style.display='block';if(ph)ph.style.display='none';
      if(typeof motorSel!=='undefined'&&motorSel===cam&&window.pintarPosicion)pintarPosicion(sim.pos[cam]);
    });
  }
  window.__simPintar=pintar;
  window.__simMarca=function(fuente){
    const c=document.createElement('canvas');c.width=800;c.height=600;
    const g=c.getContext('2d');g.drawImage(fuente,0,0,800,600);
    const M=sim.marca;if(!M.activa)return c.toDataURL('image/jpeg',.85);
    const f={pequeno:.75,mediano:1,grande:1.45}[M.tamano], fp=Math.round(800*.016*f), m=Math.round(fp*.9), p=Math.round(fp*.55);
    const fondo=M.estilo==='claro'?'rgba(255,255,255,.75)':'rgba(0,0,0,.6)', tinta=M.estilo==='claro'?'#0f1720':'#fff';
    const caja=(pos,w,h)=>{const x=pos.includes('izquierda')?m:800-w-m, y=pos.includes('arriba')?m:600-h-m;
      g.fillStyle=fondo;g.beginPath();g.roundRect(x,y,w,h,fp*.35);g.fill();return [x,y];};
    const pd=M.posicion, pe=M.datos?(pd==='abajo-izquierda'?'abajo-derecha':'abajo-izquierda'):'abajo-izquierda',
          pl=M.datos?(pd==='arriba-izquierda'?'arriba-derecha':'arriba-izquierda'):'abajo-derecha';
    const o=descOptica(0);
    if(M.datos){
      const ls=[];const C=M.campos;
      if(C.experimento)ls.push(['Células día 1',1]);
      if(C.fecha)ls.push([new Date().toLocaleDateString('es',{day:'numeric',month:'short',year:'numeric'})+' · '+new Date().toTimeString().slice(0,5),0]);
      if(C.objetivo)ls.push(['Objetivo '+o.objetivo+' · NA '+o.na,0]);
      if(C.escala)ls.push([o.um_por_pixel+' µm/píxel',0]);
      if(C.luz)ls.push(['Luz: Normal (campo claro)',0]);
      if(C.camara)ls.push(['Cámara 0',0]);
      if(ls.length){
        const al=fp*1.32;let w=0;ls.forEach(l=>{g.font=(l[1]?'bold ':'')+fp+'px DejaVu Sans, sans-serif';w=Math.max(w,g.measureText(l[0]).width);});
        const [x,y]=caja(pd,w+2*p,al*ls.length+2*p-fp*.25);
        ls.forEach((l,i)=>{g.font=(l[1]?'bold ':'')+fp+'px DejaVu Sans, sans-serif';g.fillStyle=tinta;g.textBaseline='top';
          const tw=g.measureText(l[0]).width;g.fillText(l[0],pd.includes('izquierda')?x+p:x+w+p-tw,y+p+i*al);});
      }
    }
    if(M.escala){
      const umpx=o.um_por_pixel*3280/800, tope=800*.2*umpx, L=[1,2,5,10,20,25,50,100,200,250,500].filter(v=>v<=tope).pop()||1, lp=L/umpx;
      g.font='bold '+fp+'px DejaVu Sans, sans-serif';const et=L+' µm', tw=g.measureText(et).width, gr=Math.max(3,fp*.38);
      const w=Math.max(lp,tw)+2*p, h=fp*1.25+gr+2*p;const [x,y]=caja(pe,w,h);
      g.fillStyle=tinta;g.textBaseline='top';g.fillText(et,x+(w-tw)/2,y+p-fp*.1);g.fillRect(x+(w-lp)/2,y+h-p-gr,lp,gr);
    }
    if(M.logo){
      g.font='bold '+Math.round(fp*1.45)+'px DejaVu Sans, sans-serif';
      const a=g.measureText('Microscope').width,bb=g.measureText('OS').width, w=a+bb+2*p, h=fp*1.9+2*p;
      const [x,y]=caja(pl,w,h);g.textBaseline='middle';g.fillStyle=tinta;g.fillText('Microscope',x+p,y+h/2);g.fillStyle='#22c55e';g.fillText('OS',x+p+a,y+h/2);
    }
    return c.toDataURL('image/jpeg',.85);
  };
  window.__simRender=render;

  // ---------- movimiento ----------
  const espera=ms=>new Promise(r=>setTimeout(r,ms));
  function animar(cam,destino,ms){
    return new Promise(res=>{
      const desde=sim.pos[cam],t0=performance.now();
      (function paso(t){
        const u=Math.min(1,(t-t0)/ms);sim.pos[cam]=desde+(destino-desde)*u;pintar(cam);
        if(u<1)requestAnimationFrame(paso);else res();
      })(t0);
    });
  }
  setInterval(()=>{
    const j=sim.jog;if(!j)return;
    if(performance.now()>j.hasta){sim.jog=null;return;}
    const vel=(UM_PASO/sim.ms[j.motor])/(2*0.003);      // um/s, igual que el motor real
    sim.pos[j.motor]+=j.dir*vel*0.05;pintar(j.motor);
  },50);

  async function autofoco(cam,rangoUm,rangoMax){
    const p0=sim.pos[cam],t0=performance.now(),intentos=[];
    let r=rangoUm||40,encontrado=false;
    const max=Math.max(r,rangoMax||r);
    if(sim.cal[cam]){
      encontrado=Math.abs(sim.foco[cam]-p0)<=max/2;
      intentos.push({rango_um:max,metodo:'dpc',encontrado});
      if(encontrado)await animar(cam,sim.foco[cam]+(Math.random()-.5)*.6,700);
      r=max;
    }else{
      while(true){
        await animar(cam,p0-r/2,350);
        await animar(cam,p0+r/2,500+r*12);
        encontrado=Math.abs(sim.foco[cam]-p0)<r/2-2;
        intentos.push({rango_um:r,metodo:'barrido',encontrado});
        if(encontrado||r>=max)break;
        r=Math.min(r*2,max);
      }
      await animar(cam,encontrado?sim.foco[cam]+(Math.random()-.5)*.8:p0,450);
    }
    const desp=sim.pos[cam]-p0;
    return {metodo:sim.cal[cam]?'dpc':'barrido',encontrado,ampliado:intentos.length>1,
      rango_um:r,intentos,desplazamiento_um:+desp.toFixed(2),
      segundos:+((performance.now()-t0)/1000*6).toFixed(1)};   // el real tarda ~6x mas
  }

  // ---------- experimentos simulados ----------
  const OBJ=[['4x',4,.1],['10x',10,.25],['20x',20,.4],['40x',40,.65],['60x',60,.85],['100x',100,1.25]];
  function descOptica(c){
    const o=sim.optica[c], est=o.pixel_um/(o.aumento*o.aumento_adicional), u=o.um_por_pixel_medido||est;
    return Object.assign({},o,{um_por_pixel:+u.toFixed(5),origen_escala:o.um_por_pixel_medido?'medido':'estimado',
      campo_um:[+(3280*u).toFixed(1),+(2464*u).toFixed(1)],sensor:'IMX219',resolucion_px:[3280,2464]});
  }
  const NOMBRE_LUZ={full:'Normal (campo claro)',left:'Relieve DPC, desde la izquierda',right:'Relieve DPC, desde la derecha',
    top:'Relieve DPC, desde arriba',bottom:'Relieve DPC, desde abajo',ring:'Fondo negro (campo oscuro)',rheinberg:'De colores (Rheinberg)'};
  const pad=n=>String(n).padStart(2,'0');
  const isoLocal=d=>d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())+'T'+pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds());
  function slug(t){return (t||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').replace(/[^A-Za-z0-9 _-]+/g,'').trim().replace(/[\s_]+/g,'_').slice(0,50);}
  function foto(cam,cuando,ciclo,expNombre){
    const L=sim.luz[cam];
    return {rel:'cam'+cam+'/'+(ciclo?String(ciclo).padStart(4,'0')+'_':'')+isoLocal(cuando).replace('T','_').replace(/:/g,'-')+'.tif',
      url:render(cam).toDataURL('image/jpeg',0.8),
      meta:{fecha_hora:isoLocal(cuando),camara:{numero:cam,sensor:'IMX219',exposicion_us:12000,ganancia:1.2},
        optica:descOptica(cam),iluminacion:{nombre:L.on?NOMBRE_LUZ[L.modo]:'Apagada',brillo_pct:L.percent},
        foco:{posicion_um:sim.pos[cam]},incubadora:{temperature:37.0},experimento:{nombre:expNombre}}};
  }
  function infoExp(e){
    return {id:e.id,nombre:e.nombre,tipo:e.tipo,inicio:e.inicio,inicio_legible:'',estado:e.estado||null,
      intervalo_s:e.intervalo_s||null,camaras:[...new Set(e.imgs.map(i=>i.rel.slice(3,4)))].sort(),
      n_fotos:e.imgs.length,bytes:e.imgs.length*16163840,portada:e.imgs.length?e.imgs[e.imgs.length-1].rel:null,en_curso:false};
  }
  function semillaExps(){
    // un timelapse de ejemplo con la muestra enfocada
    const g=sim.pos[0];sim.pos[0]=sim.foco[0];sim.pos[1]=sim.foco[1];
    const luz0={...sim.luz[0]},luz1={...sim.luz[1]};
    sim.luz[0]={on:true,modo:'full',percent:80};sim.luz[1]={on:true,modo:'full',percent:80};
    const ini=new Date(Date.now()-3*3600e3);
    const t={id:isoLocal(ini).slice(0,10)+'_'+pad(ini.getHours())+pad(ini.getMinutes())+'_Celulas_dia_1',nombre:'Células día 1',
      tipo:'timelapse',inicio:isoLocal(ini),estado:'completo',intervalo_s:300,imgs:[]};
    for(let k=0;k<6;k++)for(const c of [0,1])t.imgs.push(foto(c,new Date(ini.getTime()+k*300e3),k+1,t.nombre));
    t.imgs.sort((a,b)=>a.rel<b.rel?-1:1);
    sim.exps.push(t);
    sim.pos[0]=g;sim.pos[1]=0;sim.luz[0]=luz0;sim.luz[1]=luz1;
  }
  window.__simSemilla=semillaExps;
  window.__simFoto=(id,rel)=>{const e=sim.exps.find(x=>x.id===id);const f=e&&e.imgs.find(i=>i.rel===rel);return f;};

  // ---------- API simulada ----------
  const json=(d,code)=>new Response(JSON.stringify(d),{status:code||200,headers:{'Content-Type':'application/json'}});
  const ocupado=()=>sim.ocupado?{error:'Autofoco en curso, espera a que termine'}:null;
  window.fetch=async function(url,opt){
    url=String(url);const ruta=url.split('?')[0];
    let b={};try{b=opt&&opt.body?JSON.parse(opt.body):{};}catch(e){}
    if(ruta==='/api/focus/status'){
      const motores={};
      for(const m of [0,1])motores[m]={posicion_um:+sim.pos[m].toFixed(2),microsteps:sim.ms[m],
        uart:'ok',irun_ma:450,calibracion_dpc:sim.cal[m]};
      return json({motores,autofocus:true});
    }
    if(ruta==='/api/focus/move'){
      const o=ocupado();if(o)return json(o);
      const m=b.motor,ump=UM_PASO/sim.ms[m],pasos=Math.round(Math.abs(b.um)/ump);
      await animar(m,sim.pos[m]+(b.direction>0?1:-1)*pasos*ump,Math.min(600,80+pasos*6));
      return json({status:'ok',posicion_um:+sim.pos[m].toFixed(2)});
    }
    if(ruta==='/api/focus/jog'){
      const o=ocupado();if(o)return json(o);
      sim.jog={motor:b.motor,dir:b.direction>0?1:-1,hasta:performance.now()+1500};
      return json({status:'jog',posicion_um:+sim.pos[b.motor].toFixed(2)});
    }
    if(ruta==='/api/focus/jog/stop'){sim.jog=null;return json({status:'stopped'});}
    if(ruta==='/api/focus/config'){
      const o=ocupado();if(o)return json(o);
      sim.ms[b.motor]=b.microsteps;return json({status:'ok',microsteps:b.microsteps});
    }
    if(ruta==='/api/focus/auto'){
      const o=ocupado();if(o)return json({error:'Ya hay un autofoco corriendo'});
      sim.ocupado=true;
      try{return json(await autofoco(b.camera,b.rango_um,b.rango_max_um));}
      finally{sim.ocupado=false;}
    }
    if(ruta==='/api/focus/calibrar'){
      sim.ocupado=true;
      try{
        const r=await autofoco(b.camera,40,200);
        if(!r.encontrado)return json({confiable:false,guardada:false,r2:0.21,puntos_usados:2,puntos:9});
        const f=sim.foco[b.camera];
        await animar(b.camera,f-12,400);await animar(b.camera,f+12,1400);await animar(b.camera,f,400);
        sim.cal[b.camera]={r2:0.987,um_por_pixel:0.61};
        return json({confiable:true,guardada:true,r2:0.987,puntos_usados:9,puntos:9,calibracion:sim.cal[b.camera]});
      }finally{sim.ocupado=false;}
    }
    if(ruta.startsWith('/preview/')){
      const cam=+ruta.split('/')[2];
      return new Promise(res=>render(cam).toBlob(bl=>res(new Response(bl,{status:200})),'image/jpeg',0.82));
    }
    if(ruta==='/status')return json({running:false,camara_activa:motorSel||0,ciclo:0,carpeta:null});
    if(ruta==='/api/analisis/medir'){
      const d=Math.abs(sim.pos[b.camera]-sim.foco[b.camera]);
      return (d>10||!sim.luz[b.camera].on)?json({vacio:true,n:0}):json({n:b.camera?38:55,ms:140});
    }
    if(ruta==='/api/analisis/estado')return json({ultimos:{}});
    if(ruta==='/api/analisis/foto')return json({n:motorSel?38:55,ms:620,overlay:'demo.png'});
    if(ruta==='/timelapse/start')return json({error:'En la demo no se corren timelapses: esto se inicia en el microscopio real'});
    if(ruta.startsWith('/capture/')){
      const q=new URLSearchParams(url.split('?')[1]||''), nombre=(q.get('nombre')||'').trim();
      const ahora=new Date(), dia=isoLocal(ahora).slice(0,10);
      const id=dia+'_'+(slug(nombre)||'Fotos_sueltas');
      let e=sim.exps.find(x=>x.id===id);
      if(!e){e={id,nombre:nombre||'Fotos sueltas',tipo:'fotos',inicio:isoLocal(ahora),imgs:[]};sim.exps.push(e);}
      const partes=ruta.split('/'), cams=partes[2]==='both'?[0,1]:[+partes[2]];
      const nuevas=cams.map(c=>foto(c,ahora,null,e.nombre));
      e.imgs.push(...nuevas);
      return json({saved:nuevas.map(f=>f.rel),experimento:e.id,nombre:e.nombre});
    }
    if(ruta==='/api/experimentos'){
      const l=sim.exps.map(infoExp).sort((a,b)=>a.inicio<b.inicio?1:-1);
      return json({experimentos:l,espacio:{total_bytes:58e9,libre_bytes:31e9-l.reduce((a,x)=>a+x.bytes,0),fotos_que_caben:1900},usb:[]});
    }
    let m=ruta.match(/^\/api\/exp\/([^/]+)(?:\/(\w+))?(?:\/(.*))?$/);
    if(m){
      const id=decodeURIComponent(m[1]), que=m[2], rel=m[3]?decodeURIComponent(m[3]):null;
      const e=sim.exps.find(x=>x.id===id);
      if(!e)return json({error:'Ese experimento ya no existe'});
      if(!que)return json(Object.assign(infoExp(e),{imagenes:e.imgs.map(i=>i.rel)}));
      if(que==='info'){const f=e.imgs.find(i=>i.rel===rel);return json({metadatos:f?f.meta:{},bytes:16163840});}
      if(que==='renombrar'){
        if(!slug(b.nombre))return json({error:'el nombre tiene que tener al menos una letra o número'});
        e.nombre=b.nombre;e.id=e.id.replace(/^(\d{4}-\d{2}-\d{2}(_\d{4})?)_.*$/,'$1')+'_'+slug(b.nombre);
        e.imgs.forEach(i=>i.meta.experimento.nombre=b.nombre);
        return json({id:e.id});
      }
      if(que==='borrar'){const cod=Date.now()+'__'+e.id;sim.papelera[cod]=e;sim.exps=sim.exps.filter(x=>x!==e);return json({codigo:cod});}
      if(que==='usb')return json({error:'En la demo no hay memorias USB'});
    }
    if(ruta==='/api/papelera/restaurar'){const e=sim.papelera[b.codigo];if(!e)return json({error:'ya no está en la papelera'});sim.exps.push(e);delete sim.papelera[b.codigo];return json({id:e.id});}
    if(ruta==='/api/optica'){
      if(opt&&opt.method==='POST'){
        const o=sim.optica[b.camara];
        if(b.objetivo){o.objetivo=b.objetivo;const x=OBJ.find(z=>z[0]===b.objetivo);if(x){o.aumento=x[1];o.na=x[2];}}
        if(b.aumento_adicional)o.aumento_adicional=b.aumento_adicional;
        if(b.um_por_pixel_medido)o.um_por_pixel_medido=b.um_por_pixel_medido;
        if(b.borrar_medido)o.um_por_pixel_medido=null;
        return json({camara:descOptica(b.camara)});
      }
      return json({camaras:{0:descOptica(0),1:descOptica(1)},objetivos:OBJ.map(x=>({nombre:x[0],aumento:x[1],na:x[2]}))});
    }
    if(ruta==='/api/marca'){
      const PRE={ninguna:{activa:false},escala:{activa:true,logo:false,escala:true,datos:false},
        cientifica:{activa:true,logo:false,escala:true,datos:true,campos:{experimento:true,fecha:true,objetivo:true,escala:true,luz:true,camara:true}},
        publicidad:{activa:true,logo:true,escala:true,datos:false}};
      if(opt&&opt.method==='POST'){
        if(b.preset){const p=PRE[b.preset];Object.assign(sim.marca,p);if(p.campos)Object.assign(sim.marca.campos,p.campos);}
        if(b.config){const c={...b.config};if(c.campos){Object.assign(sim.marca.campos,c.campos);delete c.campos;}Object.assign(sim.marca,c);}
      }
      const c=sim.marca;let preset='personalizada';
      if(!c.activa)preset='ninguna';
      else if(!c.logo&&c.escala&&!c.datos)preset='escala';
      else if(!c.logo&&c.escala&&c.datos&&Object.values(c.campos).every(Boolean))preset='cientifica';
      else if(c.logo&&c.escala&&!c.datos)preset='publicidad';
      return json({config:JSON.parse(JSON.stringify(c)),preset,presets:['ninguna','escala','cientifica','publicidad'],
        campos:['experimento','fecha','objetivo','escala','luz','camara'],logo_propio:false,disponible:true});
    }
    if(ruta==='/api/marca/logo')return json({error:'En la demo no se pueden subir archivos'});
    if(ruta==='/light/color_dpc')return json({color:'00FF00'});
    if(ruta==='/light/set'){
      for(const c of b.camaras||[0,1]){sim.luz[c]={on:true,modo:b.modo,percent:b.percent??sim.luz[c].percent};pintar(c);}
      return json({status:'ok',modo:b.modo});
    }
    if(ruta==='/light/off'){
      for(const c of (b.camaras||[0,1])){sim.luz[c].on=false;pintar(c);}
      return json({status:'off'});
    }
    if(ruta==='/light/estado')return json({matrices:{0:{encendida:sim.luz[0].on,modo:sim.luz[0].modo,percent:sim.luz[0].percent},
                                                     1:{encendida:sim.luz[1].on,modo:sim.luz[1].modo,percent:sim.luz[1].percent}}});
    if(ruta==='/files/list')return json({capturas:[],timelapses:[]});
    if(ruta==='/profiles/list')return json({perfiles:['demo']});
    if(ruta==='/api/usb/estado')return json({dispositivos:[],copia:{},evento:0});
    if(ruta==='/api/envio/estado')return json({url:'',pendientes:0,enviados:0});
    if(ruta==='/api/nas/estado')return json({modo:'smb',pendientes:0,enviados:0,activo:false,subcarpeta:'microscopio'});
    if(ruta==='/api/envio/buscar')return json({pcs:[]});
    if(ruta==='/api/nas/buscar')return json({equipos:[]});
    return json({ok:true,status:'ok'});
  };

  // Temperatura/CO2: un stream de mentira, estable cerca del setpoint.
  window.EventSource=function(){
    const es=this;let t=36.4;
    setInterval(()=>{
      t+= (37-t)*0.08+(Math.random()-.5)*0.04;
      es.onmessage&&es.onmessage({data:JSON.stringify({connected:true,temperature:t,setpoint:37,
        pwm:Math.round(90+(37-t)*120),co2:49800+Math.random()*400,co2_setpoint:50000,valve_open:Math.random()<.2,
        co2_duty:12.5,ambient_temp:24.1,humidity:61.2})});
    },1000);
  };
  // El visor no muestra confirm(): en la demo se acepta siempre.
  window.confirm=()=>true;
})();
