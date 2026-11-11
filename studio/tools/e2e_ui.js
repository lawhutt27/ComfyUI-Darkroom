// Browser end-to-end check of the Darkroom Studio interface (Playwright).
//
//   python -m darkroom_studio --port 7860 &           # start the app
//   node tools/e2e_ui.js [photo] [lut.cube] [shots_dir] [url]
//
// Drives: upload, slider + commit, undo/redo, prompt, 100% zoom, reset, preset,
// LUT import, full-resolution export and scopes. Prints the status bar after each
// step and any browser-side errors. Not part of pytest (needs a running server).
const { chromium } = require('playwright');
const [PHOTO = 'samples/D5300_14bit.NEF', LUT = 'tools/teal_test.cube', SHOTS = '/tmp/darkroom_e2e', URL = 'http://127.0.0.1:7860/'] = process.argv.slice(2);
require('fs').mkdirSync(SHOTS, { recursive: true });
const shot = (p, n) => p.screenshot({ path: `${SHOTS}/e2e_${n}.png` });
const status = async p => (await p.locator('#dr-status').innerText()).replace(/\s+/g, ' ');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1680, height: 1050 } });
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(URL, { waitUntil: 'load', timeout: 60000 });
  await p.waitForTimeout(4000);
  const results = {};
  // upload NEF
  await p.locator('input[type=file]').first().setInputFiles(PHOTO);
  await p.waitForFunction(() => document.querySelector('#dr-status').innerText.includes('loaded '), null, { timeout: 90000 });
  results.upload = await status(p); await shot(p, '1_upload');
  // slider: exposure number box
  const expo = p.locator('label:has-text("Exposure")').first().locator('xpath=ancestor::div[contains(@class,"block")][1]');
  const range = expo.locator('input[type=range]');
  await range.focus();
  for (let i = 0; i < 6; i++) await p.keyboard.press('ArrowRight');
  await range.dispatchEvent('pointerup'); await range.dispatchEvent('mouseup');
  await p.waitForTimeout(4000);
  results.slider = await status(p);
  // undo / redo
  await p.click('button:has-text("Undo")'); await p.waitForTimeout(3500); results.undo = await status(p);
  await p.click('button:has-text("Redo")'); await p.waitForTimeout(3500); results.redo = await status(p);
  // prompt
  await p.fill('#dr-prompt textarea', 'Give this image a Kodak Portra-inspired appearance with soft highlights');
  await p.click('#dr-apply'); await p.waitForTimeout(6000);
  results.prompt = (await p.locator('text=Interpreted with').first().innerText()).slice(0, 80);
  await shot(p, '2_prompt');
  // zoom 100%
  await p.click('label:has-text("100%")'); await p.waitForTimeout(8000);
  results.zoom = await status(p); await shot(p, '3_zoom100');
  await p.click('label:has-text("Fit")'); await p.waitForTimeout(4000);
  // view mode Original
  await p.click('label:has-text("Original")'); await p.waitForTimeout(3000); await shot(p, '4_original');
  await p.click('label:has-text("Before / After")'); await p.waitForTimeout(3000);
  // reset
  await p.click('button:has-text("Reset")'); await p.waitForTimeout(3500); results.reset = await status(p);
  // preset
  await p.locator('span:has-text("Presets")').first().click(); await p.waitForTimeout(800);
  console.log(JSON.stringify(results, null, 1));
  const dd = p.getByLabel('Preset library');
  await dd.click(); await p.waitForTimeout(800);
  await p.locator('li:has-text("1970s independent film")').first().click(); await p.waitForTimeout(800);
  await p.click('button:has-text("Apply preset")'); await p.waitForTimeout(5000);
  results.preset = await status(p); await shot(p, '5_preset');
  // LUT import
  await p.locator('.label-wrap').filter({hasText: /^LUT\s*▼?$/}).first().click(); await p.waitForTimeout(800);
  const lutInput = p.locator('input[type=file][accept*=".cube"]').first();
  await lutInput.setInputFiles(LUT); await p.waitForTimeout(6000);
  results.lut = (await p.locator('text=3D LUT').first().innerText().catch(()=> 'no lut text')).slice(0, 80);
  // export TIFF
  await p.click('button:has-text("Export full resolution")');
  await p.waitForFunction(() => document.querySelector('#dr-status').innerText.includes('Exported'), null, { timeout: 180000 });
  results.export = await status(p); await shot(p, '6_export');
  // scopes
  await p.locator('span:has-text("Scopes")').first().click(); await p.waitForTimeout(1500); await shot(p, '7_scopes');
  console.log(JSON.stringify(results, null, 1));
  console.log('page errors:', errs.length, errs.slice(0, 3));
  await b.close();
})();
