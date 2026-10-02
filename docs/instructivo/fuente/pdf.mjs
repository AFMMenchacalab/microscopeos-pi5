import { chromium } from '/opt/node-tools/node_modules/playwright/index.mjs';
const b = await chromium.launch();
const p = await b.newPage();
await p.goto('file://' + process.cwd() + '/instructivo.html');
await p.evaluate(() => document.fonts.ready);
await p.pdf({ path: 'Instructivo_MicroscopeOS.pdf', format: 'Letter', printBackground: true, preferCSSPageSize: true,
  displayHeaderFooter: true, headerTemplate: '<span></span>',
  footerTemplate: '<div style="width:100%;font-size:8px;color:#5b6875;padding:0 16mm;display:flex;justify-content:space-between;font-family:sans-serif"><span>MicroscopeOS · Instructivo de uso</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>' });
await b.close();
