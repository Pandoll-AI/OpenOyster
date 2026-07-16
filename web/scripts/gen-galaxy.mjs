// Deterministic mock "Pack universe" dataset for the network-galaxy background.
// Constellations = knowledge domains, planets = OpenCrab Packs (mock, echoing the
// reference galaxy), particles = evidence/beliefs orbiting each Pack. All fictional.
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
const pick = (arr) => arr[Math.floor(rnd() * arr.length)];

// group id, display name, amber-family color
const groups = [
  ["policy", "정책·법령 · POLICY & LAW", "#f0a24e"],
  ["market", "시장·공시 · MARKET", "#ffb765"],
  ["science", "과학·논문 · SCIENCE", "#e88a3c"],
  ["medical", "의료·가이드 · MEDICAL", "#f5c07a"],
  ["culture", "문화·K · CULTURE", "#ff9d52"],
  ["engineering", "엔지니어링 · ENGINEERING", "#d4762e"],
  ["standards", "표준·규격 · STANDARDS", "#ffcf8a"],
];

// hub id, display name (Pack), group index — mock OpenCrab Packs (echo the reference)
const hubs = [
  ["KOREA_CARD_BENEFIT", "KOREA_CARD_BENEFIT ONTOLOGY PACK", 0],
  ["WELFARE_PROJECT_INDEX", "복지 프로젝트 인덱스 PACK", 0],
  ["EARTHQUAKE_INSIGHTS", "EARTHQUAKE_INSIGHTS SUBSTACK PACK", 2],
  ["MICHELIN_2026", "MICHELIN_2026 ONTOLOGY PACK", 4],
  ["KPOP_IDOL", "KPOP_IDOL ONTOLOGY PACK", 4],
  ["K_BEAUTY", "K-뷰티 성분·제품 PACK", 4],
  ["ECOMMERCE_SALES", "E-COMMERCE SALES ANALYTICS PACK", 1],
  ["SOCIAL_MEDIA_TRENDS", "SOCIAL_MEDIA TRENDS PACK", 1],
  ["MULTICLASS_DRONE", "MULTI-CLASS DRONE DETECTION PACK", 5],
  ["3D_MODELING", "3D 모델링 온톨로지 PACK", 5],
  ["NEMOTRON_PERSONAS", "NEMOTRON_PERSONAS KOREA PACK", 2],
  ["REVIT_2026_SDK", "REVIT_2026 SDK ONTOLOGY PACK", 5],
  ["OPENCRAB_GLOBAL", "오픈크랩 글로벌 리포트 PACK", 6],
  ["CLINICAL_GUIDELINE", "임상 가이드라인 v3 PACK", 3],
  ["INSURANCE_CLAIM", "보험 급여기준 온톨로지 PACK", 3],
  ["POLICY_DRIFT", "정책 변화 감시 PACK", 0],
  ["SEMICONDUCTOR_STD", "반도체 공정 표준 PACK", 6],
  ["OWN_ONTOLOGY", "OWN ONTOLOGY PACK", 6],
];

const groupColors = groups.map((g) => g[2]);
const groupDefs = groups.map((g, gi) => ({
  id: g[0],
  name: g[1],
  color: g[2],
  hubs: hubs.filter((h) => h[2] === gi).map((h) => h[0]),
}));

const hubDefs = hubs.map((h) => {
  const size = 120 + Math.floor(rnd() * 520);
  const verified = Math.floor(size * (0.6 + rnd() * 0.35));
  return {
    id: h[0],
    name: h[1],
    size,
    gaugePct: Math.round((verified / size) * 1000) / 10,
    metrics: {
      Evidence: size,
      Verified: `${verified} (${Math.round((verified / size) * 100)}%)`,
      "Neo4j nodes": 40 + Math.floor(rnd() * 900),
      Provenance: pick(["crawl+parse", "OCR+CLIP", "human-reviewed", "promotion pkg"]),
    },
    top: [],
    deps: [],
  };
});

// evidence particles orbiting each pack + a dim scatter of unlinked evidence
const nodes = [];
hubs.forEach((h, hi) => {
  const count = 18 + Math.floor(rnd() * 44);
  for (let i = 0; i < count; i++) {
    const value = Math.floor(rnd() * rnd() * 260);
    const flag = rnd() > 0.28 ? 1 : 0; // 1 = verified citation, 0 = unverified
    nodes.push([`ev_${h[0].toLowerCase()}_${i}`, hi, value, Math.floor(rnd() * 40), flag]);
  }
});
for (let i = 0; i < 140; i++) {
  nodes.push([`kr_pending_${i}`, -1, Math.floor(rnd() * 60), 0, 0]); // orphan = open Knowledge Requests
}

const galaxy = {
  config: {
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
    positive: "#ffb765",
    negative: "#6b5a3a",
    coreToggleLabel: "Core packs",
    coreThreshold: 6,
    stats: [
      { label: "packs", value: hubs.length },
      { label: "evidence", value: nodes.length },
      { label: "domains", value: groups.length },
    ],
    footer: "MOCK · OpenCrab Pack universe — visual only, not real data",
    seed: 20260716,
    groupColors,
  },
  groups: groupDefs,
  hubs: hubDefs,
  nodes,
};

writeFileSync(new URL("../.galaxy.json", import.meta.url), JSON.stringify(galaxy));
console.log(`galaxy.json: ${groups.length} groups, ${hubs.length} packs, ${nodes.length} evidence nodes`);
