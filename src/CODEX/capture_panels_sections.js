const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');

// CAIN Section Panel Capture — one screenshot per H2 section
// Each panel gets a numbered tag in the upper-left corner (e.g. "01", "02"...)
// All content within the section must be visible in the screenshot.
// Output: Desktop/FACEBOOK/CAIN_PANELS/panel_XX.png

(async () => {
  const VIEWPORT_W = 1920;
  const VIEWPORT_H = 1080;
  const OVERLAY_SIZE = 48; // px — small numbered tag

  let browser;
  try {
    browser = await puppeteer.launch({ headless: "new", args: ['--no-sandbox'] });
    const page = await browser.newPage();
    await page.setViewport({ width: VIEWPORT_W, height: VIEWPORT_H });

    const htmlPath = path.resolve('C:/Users/clint/azazael/src/CODEX/CAIN_injected.html');
    const fileUrl = pathToFileURL(htmlPath).href;
    console.log(`Loading: ${fileUrl}`);
    await page.goto(fileUrl, { waitUntil: 'load', timeout: 120000 });
    await page.waitForFunction(() => document.readyState === 'complete');
    await new Promise(r => setTimeout(r, 5000));

    // Find all H2 sections
    const h2Data = await page.evaluate(() => {
      const h2s = document.querySelectorAll('h2');
      const results = [];
      h2s.forEach((h2, i) => {
        const rect = h2.getBoundingClientRect();
        const scrollTop = h2.getBoundingClientRect().top + window.scrollY;
        // Get section text (strip emoji for filename logging)
        const text = h2.textContent.trim().substring(0, 60);
        results.push({
          index: i + 1,
          text: text,
          scrollY: Math.max(0, scrollTop - 20), // 20px padding above H2
          innerText: h2.innerText
        });
      });
      return results;
    });

    console.log(`Found ${h2Data.length} H2 sections`);
    h2Data.forEach(h => console.log(`  [${h.index}] @ ${h.scrollY}px: ${h.text}`));

    const outputDir = 'C:/Users/clint/Desktop/FACEBOOK/CAIN_PANELS';
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }

    // Clean old panels
    const existingFiles = fs.readdirSync(outputDir).filter(f => f.match(/^panel_\d+\.png$/));
    for (const f of existingFiles) {
      fs.unlinkSync(path.join(outputDir, f));
    }
    if (existingFiles.length) console.log(`Cleaned ${existingFiles.length} old panels`);

    // Inject overlay CSS before capturing
    await page.addStyleTag({
      content: `
        .cain-panel-overlay {
          position: fixed !important;
          top: 12px !important;
          left: 14px !important;
          z-index: 999999 !important;
          width: ${OVERLAY_SIZE}px !important;
          height: ${OVERLAY_SIZE}px !important;
          background: rgba(139, 0, 0, 0.92) !important;
          border: 2px solid #ff69b4 !important;
          border-radius: 8px !important;
          display: flex !important;
          align-items: center !important;
          justify-content: center !important;
          color: #fff !important;
          font-family: 'Courier New', monospace !important;
          font-size: 16px !important;
          font-weight: bold !important;
          box-shadow: 0 0 12px rgba(255, 105, 180, 0.6) !important;
          pointer-events: none !important;
        }
      `
    });

    let captured = 0;
    for (const h of h2Data) {
      // Scroll to section
      await page.evaluate((y) => window.scrollTo(0, y), h.scrollY);
      await new Promise(r => setTimeout(r, 600)); // wait for render + animations

      // Inject numbered overlay
      const numStr = String(h.index).padStart(2, '0');
      await page.evaluate((num) => {
        // Remove any existing overlay
        const existing = document.querySelector('.cain-panel-overlay');
        if (existing) existing.remove();
        // Create new overlay
        const el = document.createElement('div');
        el.className = 'cain-panel-overlay';
        el.textContent = num;
        document.body.appendChild(el);
      }, numStr);

      await new Promise(r => setTimeout(r, 200));

      const filename = `panel_${numStr}.png`;
      const filepath = path.join(outputDir, filename);
      await page.screenshot({ path: filepath, type: 'png' });
      captured++;
      console.log(`  [${numStr}/${h2Data.length}] ${h.text} — ${filepath}`);
    }

    // Verify
    const files = fs.readdirSync(outputDir).filter(f => f.endsWith('.png'));
    console.log(`\nDone! ${files.length} panels saved to ${outputDir}`);
    if (files.length !== h2Data.length) {
      console.warn(`WARNING: Expected ${h2Data.length} panels but found ${files.length}`);
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
