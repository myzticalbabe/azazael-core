const puppeteer = require('puppeteer');
const { PuppeteerScreenRecorder } = require('puppeteer-screen-recorder');

// CAIN Standard recording — captures all CSS animations via browser rendering
// Smooth scroll using requestAnimationFrame for no choppiness
// Duration calculated from word count @ 5.0 WPS

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 10000;

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function recordSession(retryCount = 0) {
  let browser;
  try {
    browser = await puppeteer.launch({
      headless: "new",
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        // --disable-gpu was removed — modern headless Chrome doesn't need it on Windows
        '--disable-extensions',
        '--disable-background-networking',
        '--disable-sync',
        '--no-first-run',
        '--mute-audio',
      ]
    });

    const page = await browser.newPage();
    await page.setViewport({ width: 1920, height: 1080 });

    // Forward browser console logs (including progress from scroll callback) to terminal
    page.on('console', msg => console.log(`  [browser] ${msg.text()}`));

    const htmlPath = 'file:///C:/Users/clint/azazael/src/CODEX/CAIN_injected.html';
    console.log(`Loading: ${htmlPath}`);
    await page.goto(htmlPath, { waitUntil: 'load', timeout: 120000 });

    // Wait for page to fully render
    await page.waitForFunction(() => document.readyState === 'complete');
    
    // Wait for all CSS animations to initialize
    await new Promise(r => setTimeout(r, 5000));

    // Get total page dimensions
    const totalHeight = await page.evaluate(() => document.body.scrollHeight);
    const viewportHeight = 1080;
    const scrollDistance = totalHeight - viewportHeight;
    console.log(`Page: ${totalHeight}px, Viewport: ${viewportHeight}px, Scrollable: ${scrollDistance}px`);

    // Calculate word count and duration @ 5.0 WPS
    const wordCount = await page.evaluate(() => {
      return document.body.innerText.split(/\s+/).filter(w => w.length > 0).length;
    });
    const WORDS_PER_SECOND = 30.0;
    const totalDurationMs = Math.max(60000, (wordCount / WORDS_PER_SECOND) * 1000);
    console.log(`Words: ${wordCount}, Duration: ${(totalDurationMs / 1000).toFixed(0)}s`);

    const outputPath = 'C:/Users/clint/Desktop/CAIN_session_final.mp4';

    const recorder = new PuppeteerScreenRecorder(page, {
      videoCrf: 26,
      videoPreset: 'medium',
      videoPixelFormat: 'yuv420p',
      videoBitrate: 800,
      fps: 30,
    });

    console.log(`Recording to: ${outputPath}`);
    await recorder.start(outputPath);

    if (scrollDistance <= 0) {
      // Content fits in viewport — just record static for duration
      console.log('Content fits viewport, recording static...');
      await new Promise(r => setTimeout(r, totalDurationMs));
    } else {
      // Smooth scroll: use requestAnimationFrame for butter-smooth motion
      const fps = 30;
      const totalFrames = Math.ceil(totalDurationMs / (1000 / fps));
      const pxPerFrame = scrollDistance / totalFrames;
      const frameTimeMs = 1000 / fps;

      console.log(`Scroll: ${totalFrames} frames, ${pxPerFrame.toFixed(2)}px/frame @ ${fps}fps`);

      await page.evaluate(({ pxPerFrame, totalFrames, frameTimeMs, scrollDistance }) => {
        return new Promise((resolve) => {
          let frameNum = 0;
          let lastTime = performance.now();

          function scroll(timestamp) {
            try {
              const elapsed = timestamp - lastTime;
              if (elapsed >= frameTimeMs) {
                frameNum++;
                const targetY = Math.min(frameNum * pxPerFrame, scrollDistance);
                window.scrollTo(0, targetY);
                lastTime = timestamp;
                // Log progress every 1000 frames
                if (frameNum % 1000 === 0) {
                  console.log(`Scroll progress: ${frameNum}/${totalFrames} frames (${Math.round(frameNum/totalFrames*100)}%)`);
                }
              }
              if (frameNum >= totalFrames || window.scrollY + window.innerHeight >= document.body.scrollHeight - 10) {
                window.scrollTo(0, scrollDistance);
                resolve();
              } else {
                requestAnimationFrame(scroll);
              }
            } catch (e) {
              // If scroll callback fails, resolve anyway to avoid hanging
              console.error('Scroll error:', e.message);
              resolve();
            }
          }

          requestAnimationFrame(scroll);
        });
      }, { pxPerFrame, totalFrames, frameTimeMs, scrollDistance });
    }

    // Hold at bottom for 3 seconds
    await new Promise(r => setTimeout(r, 3000));

    await recorder.stop();
    await browser.close();

    console.log(`Video saved: ${outputPath}`);
    return true;
  } catch (err) {
    if (browser) {
      try { await browser.close(); } catch(e) {}
    }
    console.error(`Recording attempt ${retryCount + 1} failed: ${err.message}`);
    if (retryCount < MAX_RETRIES - 1) {
      console.log(`Retrying in ${RETRY_DELAY_MS / 1000}s...`);
      await sleep(RETRY_DELAY_MS);
      return recordSession(retryCount + 1);
    }
    throw err;
  }
}

(async () => {
  try {
    await recordSession();
    console.log('Recording completed successfully.');
    process.exit(0);
  } catch (err) {
    console.error('All recording attempts failed:', err.message);
    process.exit(1);
  }
})();
