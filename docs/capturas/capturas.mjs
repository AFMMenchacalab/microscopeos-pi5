// Capturas de la página /ui para el README, con el microscopio simulado
// de docs/instructivo/fuente/demo. Lo llama regenerar.sh; ver ahí.
import { chromium } from '/opt/node-tools/node_modules/playwright/index.mjs';

const [demo, salida] = process.argv.slice(2);
const b = await chromium.launch();

async function abrir(tema, w = 1440, h = 900) {
  const p = await b.newPage({ viewport: { width: w, height: h } });
  p.on('pageerror', e => console.log('error en la página:', e.message));
  await p.addInitScript(t => { try { localStorage.setItem('microscopeos_tema', t); } catch (e) {} }, tema);
  await p.goto('file://' + demo);
  await p.addStyleTag({ content: '.demo{display:none!important} #toast{display:none!important}' });
  await p.waitForTimeout(1500);
  await p.locator('#luzCtl [data-acc=on]').click(); await p.waitForTimeout(800);
  await p.evaluate(() => {
    __sim.pos[0] = __sim.foco[0]; __sim.pos[1] = __sim.foco[1];
    __simPintar(0); __simPintar(1);
  });
  await p.waitForTimeout(3000);
  await p.evaluate(() => window.scrollTo(0, 0)); await p.waitForTimeout(300);
  return p;
}

let p = await abrir('claro');
await p.screenshot({ path: salida + '/web_pagina.png', fullPage: true });

// imagen ampliada con los controles de foco al lado (un poco desenfocada)
await p.evaluate(() => { __sim.pos[0] = __sim.foco[0] + 6; __simPintar(0); });
await p.locator('#view0').click(); await p.waitForTimeout(900);
await p.screenshot({ path: salida + '/web_zoom.png' });
await p.keyboard.press('Escape'); await p.waitForTimeout(400);

await p.evaluate(() => { __sim.pos[0] = __sim.foco[0]; __simPintar(0); });
await p.locator('.expcard').filter({ hasText: 'Células' }).click(); await p.waitForTimeout(1200);
await p.locator('#expVista .exp-hoja').screenshot({ path: salida + '/web_experimento.png' });
await p.keyboard.press('Escape'); await p.waitForTimeout(400);
await p.close();

// marca de agua: el panel es más alto que la pantalla, se recorta arriba
p = await abrir('claro', 1440, 1500);
await p.evaluate(() => abrirAjustes('marcaDetails')); await p.waitForTimeout(900);
await p.locator('#marcaPresets [data-p=cientifica]').click(); await p.waitForTimeout(1200);
await p.evaluate(() => document.getElementById('marcaDetails').scrollIntoView());
await p.waitForTimeout(400);
const r = await p.locator('#marcaDetails').boundingBox();
await p.screenshot({ path: salida + '/web_marca.png',
                     clip: { x: r.x, y: r.y, width: r.width, height: Math.min(r.height, 660) } });
await p.close();

p = await abrir('noche');
await p.screenshot({ path: salida + '/web_noche.png' });
await p.close();

p = await abrir('claro', 390, 844);
await p.screenshot({ path: salida + '/web_celular.png' });
await p.close();

await b.close();
console.log('ok');
