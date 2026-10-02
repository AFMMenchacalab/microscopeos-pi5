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
    ocupado: false,
    muestras: {},
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
      return d>10?json({vacio:true,n:0}):json({n:b.camera?38:55,ms:140});
    }
    if(ruta==='/api/analisis/estado')return json({ultimos:{}});
    if(ruta==='/api/analisis/foto')return json({n:motorSel?38:55,ms:620,overlay:'demo.png'});
    if(ruta==='/timelapse/start')return json({error:'En la demo no se corren timelapses: esto se inicia en el microscopio real'});
    if(ruta.startsWith('/capture/'))return json({saved:'captura_demo.tif'});
    if(ruta==='/light/color_dpc')return json({color:'00FF00'});
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
