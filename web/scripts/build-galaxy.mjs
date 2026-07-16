// Build the themed galaxy with the network-galaxy v2 skill (generic nodes/edges),
// then make it a chrome-less, non-interactive background: hide the header/search/
// controls/legend/boot UI, keep the auto-orbiting scene + amber filaments + labels.
// Output: web/public/galaxy.html
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const here = fileURLToPath(new URL(".", import.meta.url));
const out = join(here, "..", "public", "galaxy.html");
const nodes = join(here, "..", ".nodes.json");
const edges = join(here, "..", ".edges.json");
const config = join(here, "..", ".config.json");

// Prefer the user's authoritative source; fall back to the installed skill.
const candidates = [
  join(homedir(), "Projects/network-galaxy/scripts/build_galaxy.mjs"),
  join(homedir(), ".claude/skills/network-galaxy/scripts/build_galaxy.mjs"),
];
const builder = candidates.find((p) => existsSync(p));
if (!builder) throw new Error("network-galaxy builder not found");

execFileSync("node", [join(here, "gen-galaxy.mjs")], { stdio: "inherit" });
execFileSync("node", [builder, "--nodes", nodes, "--edges", edges, "--config", config, "-o", out], {
  stdio: "inherit",
});

const bgStyle = `
<style id="bg-mode">
  /* Background mode: hide interactive chrome, keep the orbiting galaxy + labels. */
  #tabs, #search, #controls, #legend, #hovercard, #boot, #hints, #sublabel,
  .panel, .hovercard, .hud, header, .logo, .livechip, .beta, .speed, .toggle,
  .gauge, .searchwrap { display: none !important; }
  #stage { pointer-events: none !important; }
  body { background: #05070d !important; }
  #overlay .l { opacity: 0.6; }
</style>`;

let html = readFileSync(out, "utf8");
html = html.replace("</body>", `${bgStyle}\n</body>`);
writeFileSync(out, html);
console.log(`galaxy background written: ${out}`);
