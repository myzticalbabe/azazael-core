const puppeteer = require('puppeteer');
const { PuppeteerScreenRecorder } = require('puppeteer-screen-recorder');

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  
  await page.setViewport({ width: 1280, height: 720 });
  
  // CAIN Standard: H.264 High profile, CRF 23, medium preset, 4 Mbps
  const recorder = new PuppeteerScreenRecorder(page, {
    videoCrf: 23,
    videoPreset: 'medium',
    videoPixelFormat: 'yuv420p',
    videoBitrate: 4000,
  });
  
  await page.goto('file:///C:/Users/clint/Desktop/SESSION_TEMPLATE/session_report.html');
  await recorder.start('C:/Users/clint/Desktop/SESSION_TEMPLATE/session_report_scrolling.mp4');
  
  await page.evaluate(() => {
    const scrollStep = 30;
    const scrollDelay = 600;
    const scrollInterval = setInterval(() => {
      if (window.scrollY + window.innerHeight >= document.body.scrollHeight) {
        clearInterval(scrollInterval);
        return;
      }
      window.scrollBy(0, scrollStep);
    }, scrollDelay);
  });
  
  await new Promise(r => setTimeout(r, 134000));
  
  await recorder.stop();
  await browser.close();
  
  console.log('Video saved');
})();
