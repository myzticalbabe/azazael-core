const puppeteer = require("puppeteer");
(async () => {
  const b = await puppeteer.launch({ headless: "new",
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    args: ["--no-sandbox"] });
  const p = await b.newPage();
  await p.setViewport({ width: 1400, height: 900, deviceScaleFactor: 1 });
  await p.goto("file:///C:/Users/clint/azazael/dashboard/cain_render.html",
                { waitUntil: "networkidle0" });
  await new Promise(r => setTimeout(r, 1200));
  const box = await p.evaluate(() => {
    const c = document.querySelector(".matrix-card.archetype-card");
    if (!c) return null;
    const r = c.getBoundingClientRect();
    return { x: r.left + window.scrollX, y: r.top + window.scrollY,
             w: r.width, h: r.height };
  });
  if (!box) { console.log("no card"); await b.close(); return; }
  console.log("card " + Math.round(box.w) + "x" + Math.round(box.h));
  await p.evaluate((yy) => window.scrollTo(0, yy), Math.max(0, box.y - 40));
  await new Promise(r => setTimeout(r, 500));
  await p.screenshot({ path: "C:/Users/clint/azazael/dashboard/shot_card.png",
    clip: { x: Math.max(0, box.x - 10), y: Math.max(0, box.y - 40),
            width: Math.min(1380, box.w + 20), height: Math.min(820, box.h + 60) } });
  await b.close();
  console.log("ok");
})();
