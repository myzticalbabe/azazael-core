const puppeteer = require("puppeteer");
(async () => {
  const b = await puppeteer.launch({ headless: "new",
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    args: ["--no-sandbox"] });
  const p = await b.newPage();
  await p.setViewport({ width: 1100, height: 720, deviceScaleFactor: 1 });
  await p.goto("file:///C:/Users/clint/azazael/dashboard/cain_render.html",
                { waitUntil: "networkidle0" });
  await new Promise(r => setTimeout(r, 1200));
  // find the first archetype card and frame it
  const y = await p.evaluate(() => {
    const c = document.querySelector(".matrix-card.archetype-card");
    return c ? Math.max(0, c.getBoundingClientRect().top + window.scrollY - 60) : 0;
  });
  await p.evaluate((yy) => window.scrollTo(0, yy), y);
  await new Promise(r => setTimeout(r, 500));
  await p.screenshot({ path: "C:/Users/clint/azazael/dashboard/shot_arch_small.png" });
  await b.close();
  console.log("ok");
})();
