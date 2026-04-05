---
title: seo-audit
created_at: 2026-03-30T17:56:39Z
status: draft
implements: [920, 921, 922]
---

# SEO Audit — ostk.ai + needle-bench.cc
## Progress: Phase 1 complete. Technical fixes applied. Data corrected 2026-03-30.

Needles: →920 (audit), →921 (strategy), →922 (content)

## Part 1: Technical Audit (→920)

### needle-bench.cc

Pre-existing ✅: meta description default, OG title/desc/type/url, twitter:card, lang="en"

| Issue | Severity | Status |
|-------|----------|--------|
| No robots.txt | P0 | **DONE** — created `public/robots.txt` |
| No sitemap | P0 | **DONE** — astro.config.mjs updated, `@astrojs/sitemap` installed |
| No canonical URLs | P1 | **DONE** — added to Base.astro |
| No OG image/site_name | P1 | **DONE** — added to Base.astro |
| Twitter card=summary | P1 | **DONE** — upgraded to summary_large_image |
| No JSON-LD | P1 | **DONE** — WebSite on all pages, Dataset on index. Base accepts jsonLd prop |
| about.astro no description | P1 | **DONE** |
| JS-rendered leaderboard | P2 | **DONE** — noscript fallback added |
| Stale numbers (34→32 benchmarks, 1768→1664 runs, 22→23/26 improved, +33→+34pp) | P1 | **DONE** — corrected across index, about, leaderboard |
| No 404 page | P2 | **TODO** — needs file creation |
| No OG image file | P2 | **TODO** — need /public/og-default.png (1200×630) |

### ostk.ai

Pre-existing ✅: canonical URLs, OG tags with image, twitter summary_large_image, JSON-LD SoftwareApplication, sitemap, robots.txt, favicon.svg, rel="noopener noreferrer", lang="en", meta description default

| Issue | Severity | Status |
|-------|----------|--------|
| Index page no description prop | P0 | **DONE** — added to index.astro |
| No 404 page | P2 | **TODO** — needs file creation |
| Verify og-image.png exists | P2 | **TODO** — referenced in Base.astro but unverified |
| Insights page monolithic | P2 | **TODO** — 15+ insights on one page, consider splitting |

### Shared remaining

1. Create OG images for both sites (1200×630)
2. Create 404 pages for both sites

---

## Part 1b: Remaining technical fixes

| Item | Site | Status |
|------|------|--------|
| Index page description prop | ostk.ai | **DONE** — added to index.astro |
| Create OG images (1200×630) | both | **TODO** |
| Create 404 pages | both | **TODO** |
| Verify og-image.png exists | ostk.ai | **TODO** |

---

## Part 2: Content Strategy (→921)

### High-intent queries to target

| # | Query cluster | Intent | Site | Page |
|---|--------------|--------|------|------|
| 1 | "ai agent benchmark" / "llm coding benchmark" | High | needle-bench | / + new /methodology/ |
| 2 | "claude vs gpt coding" / "best ai for coding" | High | needle-bench | New /insights/model-comparison/ |
| 3 | "reduce ai token usage" / "save tokens llm" | Med | ostk.ai | New /insights/token-compression/ |
| 4 | "ai agent coordination" / "multi agent orchestration" | Med | ostk.ai | New /insights/agent-coordination/ |
| 5 | "ai agent security" / "llm agent trust" | Med | ostk.ai | /security + new insight |
| 6 | "run ai agents locally" / "local llm agent" | High | ostk.ai | New /insights/local-first/ |
| 7 | "ai coding agent comparison" | High | needle-bench | New /insights/coding-accuracy/ |
| 8 | "benchmark ai debugging" / "ai bug fixing" | Med | needle-bench | New /insights/debugging-benchmarks/ |
| 9 | "deepseek vs claude" / "open source llm benchmark" | High | needle-bench | New /insights/open-vs-closed/ |
| 10 | "gpg signed software" / "verified ai tools" | Low-Med | ostk.ai | /security |

### Cross-site linking

- needle-bench → ostk.ai: "Powered by ostk" footer, methodology → ostk docs
- ostk.ai → needle-bench: /bench links to full leaderboard, insights cite bench data
- Both link to os-tack GitHub org

### Content hub architecture

**needle-bench.cc (new pages):**
```
/insights/               ← hub
/insights/model-comparison/   ← "claude vs gpt vs deepseek on real bugs"
/insights/coding-accuracy/    ← "which AI writes the most accurate patches"
/insights/open-vs-closed/     ← "open vs closed source models on debugging"
/insights/debugging-benchmarks/ ← "how to benchmark AI bug-fixing"
```

**ostk.ai (new pages):**
```
/insights/token-compression/  ← "80% fewer tokens with invisible context"
/insights/agent-coordination/ ← "multi-agent coordination via filesystem"
/insights/local-first/        ← "run AI agents locally — no cloud required"
/insights/gpg-trust-model/      ← "GPG web of trust for AI agents"
```

---

## Part 3: Execution

### Phase 1: Technical fixes (→920) — COMPLETE

**needle-bench.cc ✅:**
- [x] `npm install @astrojs/sitemap` — installed
- [x] `astro.config.mjs` — sitemap integration wired
- [x] `public/robots.txt` — created
- [x] `Base.astro` — canonical URLs, OG image/site_name, twitter summary_large_image, JSON-LD WebSite, jsonLd prop
- [x] `about.astro` — meta description added
- [x] `index.astro` — JSON-LD Dataset schema, expanded description, noscript fallback
- [x] Data corrected: 32 benchmarks, 1,664 runs, +34pp, 23/26 improved (was 34/1,768/+33/22)
- [x] Dataset JSON-LD contentUrl fixed to `/scores.json` (was `/data/leaderboard.json` — didn't exist)
- [x] Build verified clean, sitemap generating

**TODO (P2):**
- [x] Create `~/projects/needle-bench/public/robots.txt`
- [x] Add `@astrojs/sitemap` to needle-bench
- [x] Fix needle-bench Base.astro (canonical, OG, JSON-LD, twitter)
- [x] Correct stale data across needle-bench (32 benchmarks, 1664 runs, +34pp)
- [x] Add description to `~/projects/ostk-site/src/pages/index.astro` Base tag
- [x] Commit + push needle-bench changes
- [ ] Create `~/projects/needle-bench/src/pages/404.astro`
- [ ] Create `~/projects/ostk-site/src/pages/404.astro`
- [ ] Create/verify OG images for both sites (1200×630)

### Phase 2: Content production (→922) — IN PROGRESS

Shipped:
- [x] needle-bench.cc/insights/ — hub page
- [x] needle-bench.cc/insights/model-comparison/ — 26 models, all data, Article JSON-LD
- [x] needle-bench.cc/insights/open-vs-closed/ — open vs closed analysis, Article JSON-LD
- [x] needle-bench.cc/insights/difficulty-tiers/ — build-time computed tier stats, Article JSON-LD
- [x] ostk.ai/insights/context-injection/ — +34pp analysis, cost comparison
- [x] ostk.ai/insights/local-first/ — Devstral 24B, local setup, architecture
- [x] ostk.ai insights page — deep dives section added

Remaining:
- [x] OG images (1200×630) for both sites
- [x] 404 pages for both sites

### Phase 3: Measure — NOT STARTED

- Submit sitemaps to Google Search Console
- Monitor indexed page count
- Core Web Vitals check
- Track impressions/clicks on target queries
