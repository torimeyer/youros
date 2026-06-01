# getyouros.com holding page

A single-page holding site for `getyouros.com`, designed to (a) start ranking for the search term "youros" on Google, (b) catch interested visitors with an email capture, and (c) give us analytics to measure whether `youros.com` traffic-leak to HugeDomains is a real problem.

See the plan at `~/.claude/plans/i-bought-it-on-groovy-sun.md` for context.

## Files

- `index.html` — the holding page. Sunset-gradient wordmark (v7 logo lifted from `/tmp/myos-logo-v7.html` and adapted to "y[sunset-O]uros"), dark navy background (`#020617` from `app/src/index.css`), brand colors (pink `#ec4899`, purple `#8b5cf6`, amber `#fbbf24`). Real "Download youros" CTA pointing at `https://github.com/os-tack/youros/releases/latest`. Swap the href to a dedicated `/install` page once one exists.
- `favicon.svg` — just the sunset disc. Renders crisp at any size.
- `robots.txt` — allow all crawlers, points to sitemap
- `sitemap.xml` — single-URL sitemap for faster Google indexing

## CTA wiring

The "Download youros" button has a `<!-- TODO -->` comment above it in `index.html`. Today it links to GitHub releases (real, works now). When you have a real install page or hosted installer, update the `href`. Two reasonable evolutions:

- **Direct curl-pipe** — host `install.sh` on Cloudflare Pages alongside `index.html` and link the CTA to `/install` (a page with `curl -sSL https://getyouros.com/install.sh | bash` copy-pasteable in a code block)
- **GitHub releases** (current default) — keeps you off the hook for hosting binaries, lets the GH UI handle platform-specific downloads

## Deploy to Cloudflare Pages

The fastest path. Free, HTTPS, deploys in under a minute.

```bash
# Install wrangler if not present
npm install -g wrangler

# From this directory:
cd marketing/getyouros
wrangler pages deploy . --project-name=getyouros
```

Or via the Cloudflare dashboard:

1. **Workers & Pages → Create → Pages → Upload assets**
2. Drag this directory's contents in
3. Project name: `getyouros`
4. **Custom domains → Set up a custom domain → `getyouros.com`** (Cloudflare auto-issues the cert)
5. Also add `www.getyouros.com` with a redirect to the apex

## Configure `your-os.net` → `getyouros.com` (301 redirect)

In the Cloudflare dashboard for `your-os.net`:

1. **Rules → Redirect Rules → Create rule**
2. Name: `your-os.net → getyouros.com`
3. **When incoming requests match:** Custom filter expression:
   ```
   (http.host eq "your-os.net") or (http.host eq "www.your-os.net")
   ```
4. **Then:** Dynamic redirect, status **301**
5. **Target URL expression:**
   ```
   concat("https://getyouros.com", http.request.uri.path)
   ```
6. Preserve query string: **enabled**
7. **Deploy**

## Configure Cloudflare Email Routing for `hello@getyouros.com`

The holding page's "Get notified" button links to `hello@getyouros.com`. Cloudflare Email Routing forwards that to your real inbox for free.

In the Cloudflare dashboard for `getyouros.com`:

1. **Email → Email Routing → Get started**
2. Cloudflare auto-adds MX + TXT records
3. **Custom addresses → Create address:** `hello` → forward to your real email (e.g., `you@example.com`)
4. Verify the destination via the email Cloudflare sends

## Enable Cloudflare Web Analytics

Free, server-side, no JS required (Pages includes it automatically when deployed via Cloudflare Pages).

1. **Analytics & Logs → Web Analytics**
2. The `getyouros.com` site appears automatically once Pages is connected
3. Tracks: page views, visitor sources (direct/search/referral), top countries

If you want the granular JS-based analytics instead, paste the snippet from **Web Analytics → Add a site** into `index.html` just before `</body>`.

## Verify Google Search Console

The critical signal for measuring `youros` search demand:

1. Go to [search.google.com/search-console](https://search.google.com/search-console)
2. **Add property → URL prefix:** `https://getyouros.com/`
3. Verification method: **DNS TXT record** (since you control Cloudflare DNS)
4. Copy the TXT value Google gives you
5. In Cloudflare DNS for `getyouros.com`: add a TXT record with that value at `@`
6. Click verify in Search Console
7. **Submit sitemap:** in Search Console → Sitemaps → Add `https://getyouros.com/sitemap.xml`

The signal to watch: **Performance → Queries → search "youros"**. If monthly impressions grow but click-through stays low, traffic is leaking to the squatter.

## Verification commands

After everything is set up:

```bash
# Redirect works, preserves path + query
curl -sI 'https://your-os.net/test?q=1'
# expect: HTTP/2 301, location: https://getyouros.com/test?q=1

# www variant also redirects
curl -sI 'https://www.your-os.net/'

# Canonical loads with valid cert
curl -sI 'https://getyouros.com/'
# expect: HTTP/2 200, content-type: text/html

# Holding page contains "youros" for Google
curl -s 'https://getyouros.com/' | grep -i 'youros'

# Sitemap is reachable
curl -sI 'https://getyouros.com/sitemap.xml'
```

## After 30 days, check

- Cloudflare Web Analytics: direct / search / referral breakdown
- Search Console → Performance: impressions and clicks for query "youros"
- Search Console → Performance: position for query "youros" — should be #1 within 60–90 days if the page is indexed

## When to revisit buying `youros.com`

From the plan's decision rule:

> If Search Console shows >100 monthly searches for "youros" by month 3 AND `direct` traffic share to `getyouros.com` is <30%, the leak is real and the $2–5k purchase becomes worth it.

HugeDomains contact: `1-303-893-0552` or via the make-offer form on their site.
