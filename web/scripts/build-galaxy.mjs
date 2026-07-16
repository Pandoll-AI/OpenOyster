// Build the themed galaxy and turn it into a chrome-less, non-interactive
// background: hide the header/search/controls/legend UI, keep the auto-orbiting
// starfield + projected Pack labels. Output: web/public/galaxy.html
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import { join } from "node:path";

const here = fileURLToPath(new URL(".", import.meta.url));
const out = join(here, "..", "public", "galaxy.html");
const data = join(here, "..", ".galaxy.json");
const builder = join(homedir(), ".claude/skills/network-galaxy/scripts/build_galaxy.mjs");

execFileSync("node", [join(here, "gen-galaxy.mjs")], { stdio: "inherit" });
execFileSync("node", [builder, "--data", data, "-o", out], { stdio: "inherit" });

const bgStyle = `
<style id="bg-mode">
  /* Background mode: hide interactive chrome, keep the orbiting galaxy + labels. */
  #tabs, #search, #controls, #legend, #hovercard, #boot, .panel, .hovercard,
  header, .logo, .livechip, .beta, .speed, .toggle { display: none !important; }
  #stage { pointer-events: none !important; }
  body { background: #05070d !important; }
  /* labels a touch dimmer so hero copy stays dominant */
  #overlay .l { opacity: 0.62; }
</style>`;

let html = readFileSync(out, "utf8");
html = html.replace("</body>", `${bgStyle}\n</body>`);
writeFileSync(out, html);
console.log(`galaxy background written: ${out}`);
