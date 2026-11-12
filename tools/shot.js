const puppeteer = require("puppeteer");
(async () => {
  const b = await puppeteer.launch({ headless: "new",
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    args: ["--no-sandbox"] });
  const p = await b.newPage();
  await p.setViewport({ width: 1600, height: 1000, deviceScaleFactor: 1.5 });
  await p.goto("file:///C:/Users/clint/azazael/dashboard/cain_render.html",
                { waitUntil: "networkidle0" });
  await new Promise(r => setTimeout(r, 1500));
  for (const [name, y] of [["top", 0], ["sins", 3000], ["archetypes", 22000]]) {
    await p.evaluate((yy) => window.scrollTo(0, yy), y);
    await new Promise(r => setTimeout(r, 600));
    await p.screenshot({ path: `C:/Users/clint/azazael/dashboard/shot_${name}.png` });
  }
  await b.close();
  console.log("shots written");
})();
