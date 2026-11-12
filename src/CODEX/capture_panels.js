const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');

// CAIN 75-Panel Capture — exactly 75 full-viewport screenshots (1920×1080)
// Divides the scrollable range (totalHeight - 1080) into 74 equal gaps
// Panel 1: top of document, Panel 75: bottom of document
// Uses the session-injected enhanced template (design improvements active)

(async () => {
  const PANEL_COUNT = 75;
  const VIEWPORT_W = 1920;
  const VIEWPORT_H = 1080;
  
  let browser;
  try {
    browser = await puppeteer.launch({ headless: "new", args: ['--no-sandbox'] });
    const page = await browser.newPage();
    await page.setViewport({ width: VIEWPORT_W, height: VIEWPORT_H });

    // Use the session-injected enhanced template (design improvements active)
    const htmlPath = path.resolve(__dirname, 'CAIN_injected.html');
    const fileUrl = pathToFileURL(htmlPath).href;
    console.log(`Loading: ${fileUrl}`);
    await page.goto(fileUrl, { waitUntil: 'load', timeout: 120000 });
    await page.waitForFunction(() => document.readyState === 'complete');
    await new Promise(r => setTimeout(r, 5000));

    // Get total scrollable height
    const totalHeight = await page.evaluate(() => {
      return Math.max(
        document.body.scrollHeight,
        document.documentElement.scrollHeight,
        document.body.offsetHeight,
        document.documentElement.offsetHeight
      );
    });

    // Scrollable range: from position 0 to position (totalHeight - VIEWPORT_H)
    const scrollableRange = totalHeight - VIEWPORT_H;
    const stepSize = scrollableRange / (PANEL_COUNT - 1); // 74 gaps between 75 panels

    console.log(`Document height: ${totalHeight}px`);
    console.log(`Scrollable range: ${scrollableRange}px`);
    console.log(`Step size: ${stepSize.toFixed(1)}px per panel`);

    const outputDir = path.resolve(__dirname, 'SESSION_TEMPLATE', 'screenshots');
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    // Clean old panels from a prior run to avoid count inflation
    const existingFiles = fs.readdirSync(outputDir).filter(f => f.match(/^panel_\d+\.png$/));
    for (const f of existingFiles) {
      fs.unlinkSync(path.join(outputDir, f));
    }
    console.log(`Cleaned ${existingFiles.length} old panels from ${outputDir}`);

    let captured = 0;
    for (let i = 0; i < PANEL_COUNT; i++) {
      const scrollTop = Math.round(i * stepSize);
      
      await page.evaluate((top) => window.scrollTo(0, top), scrollTop);
      await new Promise(r => setTimeout(r, 700));

      const filename = `panel_${i + 1}.png`;
      const filepath = path.join(outputDir, filename);

      await page.screenshot({ path: filepath, type: 'png' });
      
      captured++;
      if ((i + 1) % 10 === 0 || i === 0 || i === PANEL_COUNT - 1) {
        console.log(`  [${i + 1}/${PANEL_COUNT}] ${filename} — scroll @ ${scrollTop}px`);
      }
    }

    // Verify panel count
    const files = fs.readdirSync(outputDir).filter(f => f.endsWith('.png'));
    console.log(`\nDone! ${files.length} panels saved to ${outputDir}`);
    
    if (files.length !== PANEL_COUNT) {
      console.warn(`WARNING: Expected ${PANEL_COUNT} panels but found ${files.length}`);
    }
  } catch (err) {
    console.error(`Capture failed: ${err.message}`);
    throw err;
  } finally {
    if (browser) {
      await browser.close();
      console.log('Browser closed.');
    }
  }
})();
