const puppeteer = require('puppeteer');
const path = require('path');
const { pathToFileURL } = require('url');

(async () => {
  let browser;
  try {
    browser = await puppeteer.launch({ headless: "new", args: ['--no-sandbox'] });
    const page = await browser.newPage();
    await page.setViewport({ width: 1920, height: 1080 });

    const htmlPath = path.resolve(__dirname, 'CAIN_injected.html');
    const fileUrl = pathToFileURL(htmlPath).href;
    console.log(`Loading: ${fileUrl}`);
    
    await page.goto(fileUrl, { waitUntil: 'load', timeout: 120000 });
    await page.waitForFunction(() => document.readyState === 'complete');
    await new Promise(r => setTimeout(r, 3000));

    // Get total scrollable height and viewport height
    const dimensions = await page.evaluate(() => {
      return {
        scrollHeight: Math.max(
          document.body.scrollHeight,
          document.documentElement.scrollHeight,
          document.body.offsetHeight,
          document.documentElement.offsetHeight
        ),
        scrollWidth: Math.max(
          document.body.scrollWidth,
          document.documentElement.scrollWidth
        ),
        viewportHeight: window.innerHeight,
        viewportWidth: window.innerWidth,
      };
    });

    console.log(`Document dimensions: ${dimensions.scrollWidth}x${dimensions.scrollHeight}`);
    console.log(`Viewport: ${dimensions.viewportWidth}x${dimensions.viewportHeight}`);

    const scrollableRange = dimensions.scrollHeight - dimensions.viewportHeight;
    
    // Calculate optimal panels:
    // Panel 1 at top, Panel N at bottom, N-1 equal gaps
    // Each panel shows a full viewport (1080px)
    // To cover the full document: scrollableRange / viewportHeight gaps + 1 panel
    
    // Use ceiling to ensure we cover the full document
    const minPanels = Math.ceil(scrollableRange / dimensions.viewportHeight) + 1;
    
    // For smooth scrolling, add ~20% extra panels for overlap
    const extraPanels = Math.ceil(minPanels * 0.2);
    const recommendedPanels = minPanels + extraPanels;
    const gapSize = scrollableRange / (recommendedPanels - 1);

    console.log(`\n=== Panel Count Analysis ===`);
    console.log(`Scrollable range: ${scrollableRange}px`);
    console.log(`Minimum panels (no overlap): ${minPanels}`);
    console.log(`Recommended panels (+20% overlap): ${recommendedPanels}`);
    console.log(`Gap between panels: ${gapSize.toFixed(1)}px (${(gapSize / dimensions.viewportHeight * 100).toFixed(1)}% of viewport)`);
    console.log(`Estimated overlap per panel: ${(dimensions.viewportHeight - gapSize).toFixed(1)}px`);
    
    // Also check what 75 panels would give
    const gap75 = scrollableRange / 74;
    console.log(`\nWith 75 panels: gap=${gap75.toFixed(1)}px, overlap=${(dimensions.viewportHeight - gap75).toFixed(1)}px`);

    const totalFileSizeMB = recommendedPanels * 0.3; // estimate 300KB per PNG
    console.log(`\nEstimated capture size: ${totalFileSizeMB.toFixed(1)} MB for ${recommendedPanels} panels`);
    console.log(`Estimated capture time: ${(recommendedPanels * 0.8 / 60).toFixed(1)} min (at 0.8s per panel)`);

  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  } finally {
    if (browser) await browser.close();
  }
})();
