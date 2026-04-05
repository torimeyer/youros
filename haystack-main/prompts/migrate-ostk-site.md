Update os-tack/ostk-site to use correct org references.

Working directory: ~/projects/ostk-site

Files to check and update:
- src/layouts/Base.astro
- src/pages/bench.astro
- src/pages/docs.astro
- src/pages/insights.astro
- src/pages/legal/privacy.astro
- src/pages/legal/terms.astro
- src/pages/security.astro

Replace all occurrences of:
  os-tack/haystack    →  os-tack/haystack
  find-the-needle/haystack  →  os-tack/find-the-needle  (if present)
  os-tack             →  os-tack

Then: git add -A && git commit -m "chore: update repo references to os-tack org" && git push
Report: files changed.
