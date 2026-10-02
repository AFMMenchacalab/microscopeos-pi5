import { chromium } from '/opt/node-tools/node_modules/playwright/index.mjs';
const demo = 'file://' + process.cwd() + '/ui/microscopeos_demo.html';
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
await p.addInitScript(() => { try { localStorage.setItem('microscopeos_tema', 'claro'); localStorage.setItem('microscopeos_paso_z','1'); } catch (e) {} });
await p.goto(demo); await p.addStyleTag({ content: '.demo{display:none!important}' });
await p.waitForTimeout(900);
const shot = async (sel, name, pad=0) => { const el = p.locator(sel).first(); await el.scrollIntoViewIfNeeded(); await p.waitForTimeout(150); await el.screenshot({ path: 'manual/img/' + name + '.png' }); };
await p.screenshot({ path: 'manual/img/pagina.png' });
await shot('.guia', 'guia');
await shot('.head', 'encabezado');
await shot('#sec-luz', 'luz');
await p.locator('#luzCtl [data-acc=on]').click(); await p.waitForTimeout(400);
await shot('#sec-luz', 'luz_encendida'); await p.waitForTimeout(2700);
// desenfocar un poco cam0 para que se note
await p.evaluate(() => { __sim.pos[0] = 14; __sim.pos[1] = -9.3; __simPintar(0); __simPintar(1); });
await p.waitForTimeout(400);
await shot('#sec-ver', 'ver');
await p.evaluate(() => pintarPosicion(__sim.pos[0]));
await shot('#sec-foco', 'foco');
await shot('.zfoco[data-donde=panel] .zpasos', 'pasos');
await shot('.zfoco[data-donde=panel] .zteclas', 'teclas');
await shot('#afBtn', 'boton_af');
await shot('#sec-foto', 'foto');
await shot('#sec-tl', 'timelapse');
// imagenes de foco: lejos, cerca, enfocado
for (const [n, z] of [['borroso', 30], ['casi', 6], ['nitido', 0]]) {
  await p.evaluate(z => { __sim.pos[0] = __sim.foco[0] + z; __simPintar(0); }, z);
  await p.waitForTimeout(300);
  await shot('#view0', 'muestra_' + n);
}
// zoom
await p.evaluate(() => { __sim.pos[0] = __sim.foco[0] + 8; __simPintar(0); });
await p.locator('#view0').click(); await p.waitForTimeout(600);
await p.screenshot({ path: 'manual/img/zoom.png' });
await p.keyboard.press('Escape');
// mensajes
await p.evaluate(() => toast('No encontré el foco (busqué 100 µm hacia cada lado). Me quedé donde estaba: acércate un poco a mano y vuelve a intentar.'));
await p.waitForTimeout(400);
await shot('#toast', 'toast_noenc');
await b.close();
console.log('ok');
