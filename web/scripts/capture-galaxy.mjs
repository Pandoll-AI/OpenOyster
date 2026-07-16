// Capture a high-resolution static screenshot of the chrome-less galaxy scene.
//
// galaxy.html ships with an always-on `<style id="bg-mode">` block that hides all
// interactive chrome (nav, search, controls, boot) and leaves only the orbiting
// WebGL galaxy + constellation labels. So a plain full-page screenshot already
// yields a clean background frame — we just wait for the scene to settle.
//
// Output: public/galaxy-bg.png  (converted to .webp by the caller via cwebp)
//
// Run with the globally-installed playwright (resolved below):
//   node scripts/capture-galaxy.mjs
// Override the global path with PLAYWRIGHT_ROOT if it lives elsewhere.
//
// Env overrides: GX_W, GX_H (viewport), GX_DSF (deviceScaleFactor), GX_WAIT (ms), GX_OUT
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";

// playwright isn't a local dep (it's an asset-gen-only tool). Resolve it from the
// global install — createRequire with a base dir lets us find it without NODE_PATH,
// which ESM ignores for bare specifiers.
const PLAYWRIGHT_ROOT =
  process.env.PLAYWRIGHT_ROOT || "/Users/sjlee/.npm-global/lib/node_modules/";
const requireFromGlobal = createRequire(PLAYWRIGHT_ROOT);
const pw = await import(
  pathToFileURL(requireFromGlobal.resolve("playwright")).href
);
// playwright's entry is CJS — named exports may land on the default namespace.
const chromium = pw.chromium || pw.default?.chromium;

const __dirname = dirname(fileURLToPath(import.meta.url));
const galaxyPath = resolve(__dirname, "../public/galaxy.html");
const out = process.env.GX_OUT || resolve(__dirname, "../public/galaxy-bg.png");

// Software WebGL (SwiftShader) renders every frame on the CPU, so keep the raw
// pixel count modest — a 2560x1440 @1x frame is already crisp behind the veil.
const W = Number(process.env.GX_W || 2560);
const H = Number(process.env.GX_H || 1440);
const DSF = Number(process.env.GX_DSF || 1);
const WAIT = Number(process.env.GX_WAIT || 6500);

const browser = await chromium.launch({
  headless: true,
  // SwiftShader software WebGL — reliable in headless with no real GPU.
  args: [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--hide-scrollbars",
  ],
});
const page = await browser.newPage({
  viewport: { width: W, height: H },
  deviceScaleFactor: DSF,
});

const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

await page.goto(pathToFileURL(galaxyPath).href, { waitUntil: "networkidle", timeout: 45000 });

// Let the three.js drift animation orbit into a pleasing frame and fonts settle.
await page.waitForTimeout(WAIT);

// Sanity: a <canvas> must exist and have non-zero size (WebGL actually rendered).
const canvasOk = await page.evaluate(() => {
  const c = document.querySelector("canvas");
  return !!c && c.width > 0 && c.height > 0;
});
if (!canvasOk) {
  console.error("WARN: no sized <canvas> found — WebGL may not have rendered.");
}

await page.screenshot({
  path: out,
  type: "png",
  fullPage: false,
  animations: "disabled",
  timeout: 120000,
});

console.log(`captured ${W}x${H} @${DSF}x  ->  ${out}`);
if (errors.length) console.error("page errors:\n" + errors.join("\n"));

await browser.close();
