// Deterministic mock "Pack universe" for the network-galaxy background (v2 skill).
// Domains = constellations, OpenCrab Packs = hubs (glowing cores + scribble tangles),
// evidence = particles, relations = amber filament arcs. All fictional, visual only.
import { writeFileSync } from "node:fs";

function mulberry32(a) {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rnd = mulberry32(20260716);
const ri = (a, b) => a + Math.floor(rnd() * (b - a + 1));
const pareto = (shape) => Math.pow(1 - rnd(), -1 / shape);
const sample2 = (arr) => {
  let a = arr[Math.floor(rnd() * arr.length)];
  let b = arr[Math.floor(rnd() * arr.length)];
  while (b === a) b = arr[Math.floor(rnd() * arr.length)];
  return [a, b];
};

// domain (constellation) -> OpenCrab Packs (hubs), echoing the reference galaxy
const DOMAINS = {
  "정책·법령 · POLICY & LAW": ["KOREA_CARD_BENEFIT", "POLICY_DRIFT", "WELFARE_INDEX"],
  "시장·공시 · MARKET": ["ECOMMERCE_SALES", "SOCIAL_MEDIA_TRENDS", "OPEN_MARKET"],
  "과학·논문 · SCIENCE": ["EARTHQUAKE_INSIGHTS", "GENOME_ATLAS", "NEMOTRON_PERSONAS"],
  "의료·가이드 · MEDICAL": ["CLINICAL_GUIDELINE", "INSURANCE_CLAIM"],
  "문화·K · CULTURE": ["KPOP_IDOL", "MICHELIN_2026", "K_BEAUTY"],
  "엔지니어링 · ENGINEERING": ["REVIT_2026_SDK", "MULTICLASS_DRONE", "3D_MODELING"],
  "표준·규격 · STANDARDS": ["SEMICONDUCTOR_STD", "OPENCRAB_GLOBAL"],
};
// one bright "sun" pack + a few large, mostly small (like real packs)
const SIZES = {
  EARTHQUAKE_INSIGHTS: 820, KPOP_IDOL: 420, ECOMMERCE_SALES: 360,
  KOREA_CARD_BENEFIT: 300, CLINICAL_GUIDELINE: 260, REVIT_2026_SDK: 240,
};

const nodes = [];
const edges = [];
const byHub = {};
for (const [group, hubs] of Object.entries(DOMAINS)) {
  for (const hub of hubs) {
    const n = SIZES[hub] ?? ri(24, 150);
    const members = [];
    for (let i = 0; i < n; i++) {
      const id = `ev_${hub.toLowerCase()}_${String(i).padStart(4, "0")}`;
      nodes.push({
        id,
        hub,
        hubName: hub.replace(/_/g, " ") + " PACK",
        group,
        value: Math.max(0, Math.floor(pareto(1.6)) - 1),
        sub: ri(1, 40),
        flag: rnd() < 0.72 ? 1 : 0, // 1 = verified citation
      });
      members.push(id);
    }
    byHub[hub] = members;
  }
}
// orphans = open Knowledge Requests, scattered dim below the band
for (let i = 0; i < 130; i++) {
  nodes.push({ id: `kr_pending_${String(i).padStart(4, "0")}`, value: ri(0, 3), sub: ri(1, 10), flag: 0 });
}
const hubIds = Object.keys(byHub);
// intra-pack relations (dense) + cross-pack relations (sparse, weighted) → filament arcs
for (const members of Object.values(byHub)) {
  const rounds = Math.floor(members.length * 1.5);
  for (let k = 0; k < rounds; k++) {
    const [a, b] = sample2(members);
    edges.push({ source: a, target: b, weight: ri(1, 4) });
  }
}
for (let k = 0; k < 900; k++) {
  const [h1, h2] = sample2(hubIds);
  const a = byHub[h1][Math.floor(rnd() * byHub[h1].length)];
  const b = byHub[h2][Math.floor(rnd() * byHub[h2].length)];
  edges.push({ source: a, target: b, weight: ri(1, 9) });
}

const config = {
  title: "OpenOyster",
  tag: "PACK UNIVERSE",
  subtitle: "Evidence Galaxy",
  nodeLabel: "EVIDENCE",
  hubLabel: "PACK",
  orphanLabel: "KNOWLEDGE REQUEST",
  otherLabel: "PENDING",
  valueLabel: "Citations",
  subValueLabel: "Beliefs",
  flagLabel: "Verified quote",
  flagTrueText: "verified",
  flagFalseText: "unverified",
  nodeUnit: "evidence",
  edgeUnit: "relations",
  positive: "#ffb54d",
  negative: "#8593a8",
  accent: "#f0a441",
  showGauge: false,
  drift: true,
  backdropPlanet: true,
  linkCap: 1800,
  coreToggleLabel: "Core packs",
  coreThreshold: 6,
  stats: [
    { label: "packs", value: hubIds.length },
    { label: "evidence", value: nodes.length },
    { label: "domains", value: Object.keys(DOMAINS).length },
  ],
  footer: "MOCK · OpenCrab Pack universe — visual only, not real data",
  seed: 20260716,
};

const here = new URL(".", import.meta.url);
writeFileSync(new URL("../.nodes.json", here), JSON.stringify(nodes));
writeFileSync(new URL("../.edges.json", here), JSON.stringify(edges));
writeFileSync(new URL("../.config.json", here), JSON.stringify(config));
console.log(`nodes=${nodes.length} edges=${edges.length} packs=${hubIds.length} domains=${Object.keys(DOMAINS).length}`);
