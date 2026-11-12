const puppeteer = require('puppeteer');
const { PuppeteerScreenRecorder } = require('puppeteer-screen-recorder');

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  
  await page.setViewport({ width: 1280, height: 720 });
  
  // CAIN Standard: H.264 High profile, CRF 23, medium preset, 4 Mbps
  // Established from reverse engineering 12+ CODEX videos:
  //   - Small videos (PIL+ffmpeg) use H.264 High with 2 B-frames -> micro-sized
  //   - CAIN MP4 (puppeteer default) used Constrained Baseline with 0 B-frames -> 477 MB
  //   - Key: medium preset + CRF 23 + High profile enables inter-frame compression
  // See LESSONS_MASTER.md #01-#02 for encoding rationale
  const recorder = new PuppeteerScreenRecorder(page, {
    videoCrf: 23,
    videoPreset: 'medium',
    videoPixelFormat: 'yuv420p',
    videoBitrate: 4000,
  });
  
  await page.goto('file:///C:/Users/clint/Desktop/SESSION_TEMPLATE/consequences_ultimate.html');
  await recorder.start('C:/Users/clint/Desktop/SESSION_TEMPLATE/consequences_ultimate_scrolling.mp4');
  
  let scrollComplete = false;
  
  await page.evaluate(() => {
    const scrollStep = 30;
    const scrollDelay = 600;
    
    const scrollInterval = setInterval(() => {
      if (window.scrollY + window.innerHeight >= document.body.scrollHeight - 50) {
        scrollComplete = true;
        clearInterval(scrollInterval);
        return;
      }
      window.scrollBy(0, scrollStep);
    }, scrollDelay);
  });
  
  // Wait for scroll to complete (with safety timeout)
  while (!scrollComplete) {
    await new Promise(r => setTimeout(r, 500));
    const isComplete = await page.evaluate(() => {
      return window.scrollY + window.innerHeight >= document.body.scrollHeight - 50;
    });
    if (isComplete) break;
  }
  
  // Hold at end for a moment
  await new Promise(r => setTimeout(r, 2000));
  
  await recorder.stop();
  await browser.close();
  
  console.log('Video saved: consequences_ultimate_scrolling.mp4');
})();
