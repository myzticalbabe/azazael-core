const puppeteer = require("puppeteer");
(async () => {
  for (const emu of [false, true]) {
    const b = await puppeteer.launch({
      headless: "new",
      executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
      args: ["--no-sandbox", ...(emu ? [] : ["--force-prefers-reduced-motion=false"])],
    });
    const p = await b.newPage();
    await p.setContent(`<style>
      .x{animation:r linear both;animation-timeline:view();animation-range:entry 0% entry 60%;}
      @keyframes r{from{opacity:0}to{opacity:1}}
      @media (prefers-reduced-motion:reduce){.x{animation:none;opacity:1}}
    </style><div class="x">t</div>`);
    const r = await p.evaluate(() => ({
      reduce: matchMedia("(prefers-reduced-motion: reduce)").matches,
      name: getComputedStyle(document.querySelector(".x")).animationName,
      timeline: getComputedStyle(document.querySelector(".x")).animationTimeline,
    }));
    console.log(emu ? "forced no-reduce" : "default    ", JSON.stringify(r));
    await b.close();
  }
})();
