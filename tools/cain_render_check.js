/**
 * render_check.js — Chrome DevTools inspection of the document BEFORE encoding.
 *
 * Static checks cannot see a console error, a failed request, a stylesheet that
 * silently did not apply, or a table that renders 3px tall. The document was
 * rendered twice before it was ever loaded in a browser. This loads it in a real
 * Chrome, with the DevTools Protocol attached, and reports what the browser
 * actually did.
 *
 * Captures: console errors/warnings, uncaught exceptions, failed requests,
 * stylesheet application, layout faults (zero-size, offscreen, overflow),
 * and paints a full-page screenshot so the result can be LOOKED at.
 */
const puppeteer = require("puppeteer");
const path = require("path");
const fs = require("fs");

const HTML = process.argv[2] || "C:\\Users\\clint\\azazael\\dashboard\\cain_render.html";
const SHOT = process.argv[3] || "C:\\Users\\clint\\azazael\\dashboard\\cain_render_full.png";
const VIEW = { width: 1920, height: 1080 };

(async () => {
  // Same Chrome the renderer used; puppeteer-core ships no bundled Chromium here.
const CHROME = process.env.CAIN_CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const browser = await puppeteer.launch({
  headless: "new", executablePath: CHROME, args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.setViewport(VIEW);

  const R = { console: [], errors: [], failed: [], responses: [], checks: [] };
  const ck = (n, ok, d) => { R.checks.push({ n, ok, d: String(d).slice(0, 150) }); };

  page.on("console", (m) => {
    const t = m.type();
    if (t === "error" || t === "warning")
      R.console.push({ type: t, text: m.text().slice(0, 240) });
  });
  page.on("pageerror", (e) => R.errors.push(String(e).slice(0, 240)));
  page.on("requestfailed", (r) =>
    R.failed.push({ url: r.url().slice(0, 120), err: r.failure() && r.failure().errorText }));
  page.on("response", (r) => {
    if (r.status() >= 400) R.responses.push({ url: r.url().slice(0, 120), status: r.status() });
  });

  await page.goto("file:///" + HTML.replace(/\\/g, "/"), { waitUntil: "networkidle0", timeout: 60000 });
  await new Promise((r) => setTimeout(r, 2500));

  // ---- what the browser actually laid out -------------------------------
  const dom = await page.evaluate(() => {
    const out = {};
    const q = (s) => Array.from(document.querySelectorAll(s));
    out.title = document.title;
    out.h2 = q("h2").length;
    out.sessionBlocks = q(".session-20260930").length;
    out.svgFigures = q("svg").length;
    out.tables = q("table").length;
    out.rows = q("tr").length;
    out.docHeight = document.documentElement.scrollHeight;
    out.docWidth = document.documentElement.scrollWidth;
    out.horizontalOverflow = document.documentElement.scrollWidth > window.innerWidth + 2;

    // elements with no rendered area
    out.zeroSize = q(".session-20260930, .matrix-card, table")
      .filter((e) => { const r = e.getBoundingClientRect(); return r.height < 2 || r.width < 2; })
      .map((e) => e.tagName + "." + (e.className || "").toString().slice(0, 30));

    // clipped content: a node taller than its own box with overflow hidden
    out.clipped = q("td, h3, h4")
      .filter((e) => e.scrollHeight > e.clientHeight + 6 && getComputedStyle(e).overflow === "hidden")
      .map((e) => e.tagName + ":" + e.textContent.trim().slice(0, 24));

    // did the reveal CSS actually attach?
    const b = document.querySelector(".session-20260930");
    out.revealApplied = b ? getComputedStyle(b).animationTimeline : "(no block)";
    out.revealName = b ? getComputedStyle(b).animationName : "(no block)";

    // text nodes that failed to render at all
    out.reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
    out.revealCSS = !!document.getElementById("session-reveal");
    out.unstyled = q("td").filter((e) => getComputedStyle(e).display === "none").length;

    // the marks I chose, as the browser decoded them
    const txt = document.body.innerText;
    out.marks = {
      thinking: (txt.match(/\u{1F914}/gu) || []).length,
      angry: (txt.match(/\u{1F620}/gu) || []).length,
      content: (txt.match(/\u{1F60C}/gu) || []).length,
      grimace: (txt.match(/\u{1F62C}/gu) || []).length,
      pensive: (txt.match(/\u{1F614}/gu) || []).length,
      flat: (txt.match(/\u{1F610}/gu) || []).length,
      blank: (txt.match(/\u{1F636}/gu) || []).length,
      templateFace: (txt.match(/[\u{1F630}\u{1F61F}\u{1F622}\u{1F628}\u{1F611}\u{1F612}\u{1F644}\u{1F634}\u{1F608}]/gu) || []).length,
    };
    // count how many h2 sections actually have a session block
    const heads = q("h2");
    out.headsWithBlock = heads.filter((h) => {
      let n = h.nextElementSibling, k = 0;
      while (n && n.tagName !== "H2" && k++ < 40)
        if (n.className && String(n.className).includes("session-20260930")) return true;
        else n = n.nextElementSibling;
      return false;
    }).length;
    return out;
  });

  await page.screenshot({ path: SHOT, fullPage: true });

  // ---- verdict -----------------------------------------------------------
  ck("no uncaught exceptions", R.errors.length === 0, R.errors.join(" | "));
  ck("no console errors", !R.console.some((c) => c.type === "error"),
     R.console.filter((c) => c.type === "error").map((c) => c.text).join(" | "));
  ck("no failed requests", R.failed.length === 0,
     R.failed.map((f) => f.err + " " + f.url).join(" | "));
  ck("no HTTP >= 400", R.responses.length === 0,
     R.responses.map((r) => r.status + " " + r.url).join(" | "));
  ck("28 h2 sections", dom.h2 === 28, dom.h2);
  ck("all 28 have a session block", dom.headsWithBlock === dom.h2,
     dom.headsWithBlock + "/" + dom.h2);
  ck("session blocks rendered", dom.sessionBlocks >= 28, dom.sessionBlocks);
  ck("svg figures present", dom.svgFigures > 0, dom.svgFigures);
  ck("nothing zero-size", dom.zeroSize.length === 0, dom.zeroSize.length + " " + dom.zeroSize.slice(0, 2));
  ck("no clipped text", dom.clipped.length === 0, dom.clipped.length + " " + dom.clipped.slice(0, 2));
  ck("no horizontal overflow", !dom.horizontalOverflow, dom.docWidth + "px wide");
  // Headless Chrome reports prefers-reduced-motion: reduce, so the reveal is
  // SUPPOSED to be off here. Assert the gating, not the attachment: the CSS
  // must exist, and the animation must be off exactly when reduced motion is on.
  ck("reveal CSS present in source", dom.revealCSS, "found=" + dom.revealCSS);
  ck("reveal gated on reduced-motion",
     dom.reduceMotion ? (dom.revealName === "none") : (dom.revealName !== "none"),
     "reduce=" + dom.reduceMotion + " name=" + dom.revealName);
  ck("no template face remains", dom.marks.templateFace === 0, "template faces x" + dom.marks.templateFace);

  const pass = R.checks.filter((c) => c.ok).length;
  console.log("=".repeat(74));
  console.log("CHROME DEVTOOLS RENDER CHECK");
  console.log("=".repeat(74));
  for (const c of R.checks)
    console.log("  [" + (c.ok ? "PASS" : "FAIL") + "] " + c.n.padEnd(34) + c.d);
  console.log("-".repeat(74));
  console.log("  doc " + dom.docHeight + "px tall, " + dom.docWidth + "px wide, " + dom.rows + " table rows");
  console.log("  marks " + JSON.stringify(dom.marks));
  console.log("  reveal: " + dom.revealApplied + " / " + dom.revealName);
  console.log("  console warnings: " + R.console.filter(c => c.type === "warning").length);
  console.log("  screenshot: " + SHOT);
  console.log("  " + pass + "/" + R.checks.length + " passed");
  fs.writeFileSync(path.join(path.dirname(SHOT), "cain_rendercheck.json"),
                   JSON.stringify({ R, dom }, null, 1));
  await browser.close();
  process.exit(pass === R.checks.length ? 0 : 1);
})();


