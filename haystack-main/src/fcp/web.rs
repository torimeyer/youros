//! fcp-web — web reading intelligence for agents (→842).
//!
//! Gives agents the ability to read web pages, extract content, and follow links.
//! Like fcp-rust gives Rust intelligence, fcp-web gives web intelligence.
//!
//! Two tiers:
//! - Tier 1 (always): reqwest + HTML tag stripping + readability extraction
//! - Tier 2 (HUMANFILE opt-in): headless Chromium for JS-rendered pages + screenshots
//!
//! Registered as kernel tools in agent_loop execute_tool.

/// Fetch a URL and return cleaned text content.
///
/// Strips HTML tags, scripts, styles, and navigation boilerplate.
/// Returns the main content as readable text.
pub async fn web_read(url: &str) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(10))
        .timeout(std::time::Duration::from_secs(30))
        .user_agent("ostk/2.2 (llmOS web reader)")
        .redirect(reqwest::redirect::Policy::limited(5))
        .build()
        .map_err(|e| format!("http client: {e}"))?;

    let response = client.get(url).send().await
        .map_err(|e| format!("fetch {url}: {e}"))?;

    let status = response.status();
    if !status.is_success() {
        return Err(format!("HTTP {status} for {url}"));
    }

    let content_type = response.headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();

    // Non-HTML: return raw text (JSON, plain text, etc.)
    if !content_type.contains("html") {
        let body = response.text().await
            .map_err(|e| format!("read body: {e}"))?;
        // Truncate very large responses
        let max = 100_000;
        if body.len() > max {
            return Ok(format!("{}\n\n[truncated at {max} chars]", &body[..max]));
        }
        return Ok(body);
    }

    let html = response.text().await
        .map_err(|e| format!("read body: {e}"))?;

    Ok(extract_readable_content(&html, url))
}

/// Fetch a URL and return HTTP status + headers summary.
pub async fn web_status(url: &str) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(10))
        .timeout(std::time::Duration::from_secs(15))
        .user_agent("ostk/2.2")
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| format!("http client: {e}"))?;

    let response = client.head(url).send().await
        .map_err(|e| format!("fetch {url}: {e}"))?;

    let status = response.status();
    let mut result = format!("HTTP {} {}\n", status.as_u16(), status.canonical_reason().unwrap_or(""));

    // Key headers
    for key in ["content-type", "content-length", "location", "server", "x-powered-by"] {
        if let Some(val) = response.headers().get(key)
            && let Ok(v) = val.to_str()
        {
            result.push_str(&format!("{key}: {v}\n"));
        }
    }

    Ok(result)
}

/// Extract links from an HTML page.
pub async fn web_links(url: &str) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(10))
        .timeout(std::time::Duration::from_secs(30))
        .user_agent("ostk/2.2")
        .build()
        .map_err(|e| format!("http client: {e}"))?;

    let html = client.get(url).send().await
        .map_err(|e| format!("fetch {url}: {e}"))?
        .text().await
        .map_err(|e| format!("read body: {e}"))?;

    let links = extract_links(&html, url);
    if links.is_empty() {
        return Ok("no links found".into());
    }
    Ok(links.join("\n"))
}

// ---------------------------------------------------------------------------
// HTML processing (Tier 1 — no external deps beyond reqwest)
// ---------------------------------------------------------------------------

/// Extract readable content from HTML as structured markdown with metadata header.
///
/// Output format (consensus from multi-model review):
/// ```
/// url: <canonical URL>
/// title: <page title>
/// description: <meta description>
/// ---
/// # Heading
/// paragraph text
/// - list item
/// ```code
/// fn example() {}
/// ```
/// ```
fn extract_readable_content(html: &str, url: &str) -> String {
    // ── 1. Extract metadata from <head> ──────────────────────────
    let title = extract_meta_tag(html, "<title>", "</title>")
        .unwrap_or_default();
    let description = extract_meta_attr(html, "name=\"description\"")
        .or_else(|| extract_meta_attr(html, "property=\"og:description\""))
        .unwrap_or_default();
    let canonical = extract_meta_attr(html, "rel=\"canonical\"")
        .unwrap_or_else(|| url.to_string());

    // ── 2. Remove junk blocks ────────────────────────────────────
    let mut body = html.to_string();
    for tag in &["script", "style", "noscript", "svg", "nav", "footer",
                 "aside", "iframe", "form"] {
        body = remove_tag_blocks(&body, tag);
    }
    // Also strip common noise: cookie banners, popups
    for class_hint in &["cookie", "popup", "modal", "banner", "sidebar"] {
        body = remove_blocks_by_class(&body, class_hint);
    }

    // ── 3. Convert HTML structure to markdown ─────────────────────
    let markdown = html_to_markdown(&body);

    // ── 4. Clean up ──────────────────────────────────────────────
    let cleaned = decode_entities(&markdown);

    // Collapse 3+ consecutive blank lines to 2
    let mut prev_blank = 0u32;
    let mut lines: Vec<&str> = Vec::new();
    for line in cleaned.lines() {
        if line.trim().is_empty() {
            prev_blank += 1;
            if prev_blank <= 2 { lines.push(""); }
        } else {
            prev_blank = 0;
            lines.push(line);
        }
    }
    let body_text = lines.join("\n").trim().to_string();

    // ── 5. Assemble output ───────────────────────────────────────
    let max_body = 30_000; // ~6k tokens
    let truncated = if body_text.len() > max_body {
        format!("{}\n\n[truncated]", &body_text[..max_body])
    } else {
        body_text
    };

    let mut output = String::new();
    output.push_str(&format!("url: {canonical}\n"));
    if !title.is_empty() {
        output.push_str(&format!("title: {title}\n"));
    }
    if !description.is_empty() {
        output.push_str(&format!("description: {description}\n"));
    }
    output.push_str("---\n");
    output.push_str(&truncated);
    output
}

/// Convert HTML body to markdown, preserving structure.
fn html_to_markdown(html: &str) -> String {
    let mut out = String::with_capacity(html.len());
    let chars = html.chars();
    let mut in_tag = false;
    let mut tag_buf = String::new();
    let mut in_pre = false;
    let mut list_depth: u32 = 0;

    for ch in chars {
        if ch == '<' {
            in_tag = true;
            tag_buf.clear();
            continue;
        }
        if ch == '>' && in_tag {
            in_tag = false;
            let tag_lower = tag_buf.to_lowercase();
            let tag_name = tag_lower.split_whitespace().next().unwrap_or("");

            match tag_name {
                // Headings → markdown #
                "h1" => out.push_str("\n# "),
                "h2" => out.push_str("\n## "),
                "h3" => out.push_str("\n### "),
                "h4" | "h5" | "h6" => out.push_str("\n### "), // flatten deep headings
                "/h1" | "/h2" | "/h3" | "/h4" | "/h5" | "/h6" => out.push('\n'),

                // Code blocks
                "pre" => { in_pre = true; out.push_str("\n```\n"); }
                "/pre" => { in_pre = false; out.push_str("\n```\n"); }
                "code" if !in_pre => out.push('`'),
                "/code" if !in_pre => out.push('`'),

                // Lists
                "ul" | "ol" => { list_depth += 1; out.push('\n'); }
                "/ul" | "/ol" => { list_depth = list_depth.saturating_sub(1); out.push('\n'); }
                "li" => {
                    let indent = "  ".repeat(list_depth.saturating_sub(1) as usize);
                    out.push_str(&format!("\n{indent}- "));
                }
                "/li" => {}

                // Block elements → newlines
                "p" | "div" | "section" | "article" | "main" | "blockquote" => out.push('\n'),
                "/p" | "/div" | "/section" | "/article" | "/main" | "/blockquote" => out.push('\n'),
                "br" | "br/" | "br /" => out.push('\n'),
                "hr" | "hr/" | "hr /" => out.push_str("\n---\n"),

                // Table structure
                "table" => out.push('\n'),
                "/table" => out.push('\n'),
                "tr" => out.push_str("\n| "),
                "/tr" => out.push_str(" |"),
                "th" | "td" => out.push_str(" | "),
                "/th" | "/td" => {}
                "thead" => {}
                "/thead" => {
                    // After header row, add separator
                    out.push_str("\n|---");
                }

                // Images → alt text
                _ if tag_name == "img" => {
                    if let Some(alt) = extract_attr(&tag_buf, "alt")
                        && !alt.is_empty()
                    {
                        out.push_str(&format!("[image: {alt}]"));
                    }
                }

                // Skip all other tags silently
                _ => {}
            }
            continue;
        }
        if in_tag {
            tag_buf.push(ch);
            continue;
        }
        // Regular text
        out.push(ch);
    }
    out
}

/// Remove all occurrences of <tag ...>...</tag> blocks.
fn remove_tag_blocks(html: &str, tag: &str) -> String {
    let mut text = html.to_string();
    let open = format!("<{}", tag);
    let close = format!("</{tag}>");
    loop {
        let lower = text.to_lowercase();
        let start = lower.find(&open);
        let end = lower.find(&close);
        match (start, end) {
            (Some(s), Some(e)) if e > s => {
                text = format!("{}{}", &text[..s], &text[e + close.len()..]);
            }
            _ => break,
        }
    }
    text
}

/// Remove blocks whose class/id contains a hint string (cookie, popup, etc).
fn remove_blocks_by_class(html: &str, class_hint: &str) -> String {
    // Simple heuristic: find <div class="...cookie..." and remove to </div>
    let lower = html.to_lowercase();
    let mut result = html.to_string();
    let pattern = "class=\"".to_string();
    let mut search_pos = 0;
    while let Some(class_pos) = lower[search_pos..].find(&pattern) {
        let abs_pos = search_pos + class_pos + pattern.len();
        if let Some(end_quote) = lower[abs_pos..].find('"') {
            let class_value = &lower[abs_pos..abs_pos + end_quote];
            if class_value.contains(class_hint) {
                // Find the enclosing tag start
                if let Some(tag_start) = lower[..search_pos + class_pos].rfind('<') {
                    // Find the tag name
                    let tag_text = &lower[tag_start + 1..search_pos + class_pos];
                    let tag_name = tag_text.split_whitespace().next().unwrap_or("div");
                    let close = format!("</{tag_name}>");
                    if let Some(close_pos) = lower[tag_start..].find(&close) {
                        let remove_end = tag_start + close_pos + close.len();
                        result = format!("{}{}", &result[..tag_start], &result[remove_end..]);
                        // Restart search (positions shifted)
                        return remove_blocks_by_class(&result, class_hint);
                    }
                }
            }
            search_pos = abs_pos + end_quote;
        } else {
            break;
        }
    }
    result
}

/// Extract content between two tags (e.g. <title>...</title>).
fn extract_meta_tag(html: &str, open: &str, close: &str) -> Option<String> {
    let lower = html.to_lowercase();
    let start = lower.find(open)? + open.len();
    let end = lower[start..].find(close)?;
    let content = html[start..start + end].trim().to_string();
    if content.is_empty() { None } else { Some(decode_entities(&content)) }
}

/// Extract content attribute from a meta tag matching a selector.
fn extract_meta_attr(html: &str, selector: &str) -> Option<String> {
    let lower = html.to_lowercase();
    let pos = lower.find(selector)?;
    // Find the enclosing <meta ...> tag
    let tag_start = lower[..pos].rfind('<')?;
    let tag_end = lower[tag_start..].find('>')? + tag_start;
    let tag = &html[tag_start..=tag_end];
    // Extract content="..." or href="..."
    extract_attr(tag, "content").or_else(|| extract_attr(tag, "href"))
}

/// Extract an attribute value from a tag string.
fn extract_attr(tag: &str, attr_name: &str) -> Option<String> {
    let lower = tag.to_lowercase();
    let pattern = format!("{attr_name}=\"");
    let pos = lower.find(&pattern)? + pattern.len();
    let end = tag[pos..].find('"')?;
    let value = tag[pos..pos + end].trim().to_string();
    if value.is_empty() { None } else { Some(value) }
}

/// Decode common HTML entities.
fn decode_entities(text: &str) -> String {
    text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
        .replace("&#x27;", "'")
        .replace("&#x2F;", "/")
}

/// Extract href links from HTML, resolving relative URLs.
fn extract_links(html: &str, base_url: &str) -> Vec<String> {
    let mut links = Vec::new();
    let lower = html.to_lowercase();
    let mut pos = 0;

    while let Some(href_pos) = lower[pos..].find("href=\"") {
        let start = pos + href_pos + 6; // skip past href="
        if let Some(end) = html[start..].find('"') {
            let href = &html[start..start + end];
            // Skip anchors, javascript, mailto
            if !href.starts_with('#')
                && !href.starts_with("javascript:")
                && !href.starts_with("mailto:")
            {
                let resolved = if href.starts_with("http") {
                    href.to_string()
                } else if href.starts_with("//") {
                    format!("https:{href}")
                } else if href.starts_with('/') {
                    // Resolve against base domain
                    if let Some(domain_end) = base_url.find("//")
                        .and_then(|p| base_url[p+2..].find('/').map(|e| p + 2 + e))
                    {
                        format!("{}{href}", &base_url[..domain_end])
                    } else {
                        format!("{base_url}{href}")
                    }
                } else {
                    format!("{base_url}/{href}")
                };
                if !links.contains(&resolved) {
                    links.push(resolved);
                }
            }
            pos = start + end;
        } else {
            break;
        }
    }

    links
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // ── Metadata extraction ──────────────────────────────────────

    #[test]
    fn metadata_header_includes_title_and_url() {
        let html = r#"<html><head><title>My Page</title></head><body><p>content</p></body></html>"#;
        let result = extract_readable_content(html, "https://example.com/page");
        assert!(result.starts_with("url:"), "should start with url: {result}");
        assert!(result.contains("title: My Page"), "result: {result}");
        assert!(result.contains("---"), "should have separator");
    }

    #[test]
    fn metadata_includes_description() {
        let html = r#"<html><head><title>T</title><meta name="description" content="A description"></head><body>x</body></html>"#;
        let result = extract_readable_content(html, "https://x.com");
        assert!(result.contains("description: A description"), "result: {result}");
    }

    // ── Structure preservation ────────────────────────────────────

    #[test]
    fn headings_become_markdown() {
        let html = "<h1>Title</h1><h2>Section</h2><h3>Sub</h3><p>text</p>";
        let md = html_to_markdown(html);
        assert!(md.contains("# Title"), "md: {md}");
        assert!(md.contains("## Section"), "md: {md}");
        assert!(md.contains("### Sub"), "md: {md}");
    }

    #[test]
    fn lists_become_markdown() {
        let html = "<ul><li>one</li><li>two</li><li>three</li></ul>";
        let md = html_to_markdown(html);
        assert!(md.contains("- one"), "md: {md}");
        assert!(md.contains("- two"), "md: {md}");
        assert!(md.contains("- three"), "md: {md}");
    }

    #[test]
    fn code_blocks_preserved() {
        let html = "<pre><code>fn main() { println!(\"hello\"); }</code></pre>";
        let md = html_to_markdown(html);
        assert!(md.contains("```"), "should have fenced code block: {md}");
        assert!(md.contains("fn main()"), "should preserve code: {md}");
    }

    #[test]
    fn inline_code_preserved() {
        let html = "<p>Use <code>cargo test</code> to run tests.</p>";
        let md = html_to_markdown(html);
        assert!(md.contains("`cargo test`"), "should wrap in backticks: {md}");
    }

    #[test]
    fn img_alt_text_extracted() {
        let html = r#"<img src="photo.jpg" alt="A sunset over the ocean">"#;
        let md = html_to_markdown(html);
        assert!(md.contains("[image: A sunset over the ocean]"), "md: {md}");
    }

    // ── Junk removal ─────────────────────────────────────────────

    #[test]
    fn strips_script_and_style() {
        let html = "<style>body{color:red}</style><script>alert(1)</script><p>Hello</p>";
        let result = extract_readable_content(html, "https://x.com");
        assert!(result.contains("Hello"), "result: {result}");
        assert!(!result.contains("alert"), "should strip script: {result}");
        assert!(!result.contains("color:red"), "should strip style: {result}");
    }

    #[test]
    fn strips_nav_footer_aside() {
        let html = "<nav>menu</nav><main><p>Content</p></main><footer>copy</footer><aside>ad</aside>";
        let result = extract_readable_content(html, "https://x.com");
        assert!(result.contains("Content"), "result: {result}");
        assert!(!result.contains("menu"), "should strip nav: {result}");
        assert!(!result.contains("copy"), "should strip footer: {result}");
        assert!(!result.contains("ad"), "should strip aside: {result}");
    }

    #[test]
    fn strips_cookie_banner_by_class() {
        let html = r#"<div class="cookie-banner">Accept cookies</div><p>Real content</p>"#;
        let result = extract_readable_content(html, "https://x.com");
        assert!(result.contains("Real content"), "result: {result}");
        assert!(!result.contains("Accept cookies"), "should strip cookie banner: {result}");
    }

    // ── Entity decoding ──────────────────────────────────────────

    #[test]
    fn decodes_entities() {
        let html = "<p>A &amp; B &lt; C &gt; D</p>";
        let result = extract_readable_content(html, "https://x.com");
        assert!(result.contains("A & B"), "result: {result}");
    }

    // ── Links ────────────────────────────────────────────────────

    #[test]
    fn extract_links_absolute() {
        let html = r#"<a href="https://example.com/page">Link</a>"#;
        let links = extract_links(html, "https://base.com");
        assert_eq!(links, vec!["https://example.com/page"]);
    }

    #[test]
    fn extract_links_relative() {
        let html = r#"<a href="/about">About</a>"#;
        let links = extract_links(html, "https://example.com/page");
        assert_eq!(links, vec!["https://example.com/about"]);
    }

    #[test]
    fn extract_links_skips_anchors() {
        let html = r##"<a href="#section">Anchor</a><a href="javascript:void(0)">JS</a>"##;
        let links = extract_links(html, "https://example.com");
        assert!(links.is_empty(), "should skip anchors and javascript: {links:?}");
    }

    #[test]
    fn extract_links_deduplicates() {
        let html = r#"<a href="/page">A</a><a href="/page">B</a>"#;
        let links = extract_links(html, "https://example.com");
        assert_eq!(links.len(), 1, "should deduplicate");
    }

    // ── Helper functions ─────────────────────────────────────────

    #[test]
    fn extract_attr_finds_value() {
        assert_eq!(extract_attr(r#"<img src="x.jpg" alt="photo">"#, "alt"), Some("photo".into()));
        assert_eq!(extract_attr(r#"<meta content="desc">"#, "content"), Some("desc".into()));
        assert_eq!(extract_attr(r#"<div class="foo">"#, "id"), None);
    }

    #[test]
    fn remove_tag_blocks_removes_nested() {
        let html = "<div>keep<script>remove</script>keep2</div>";
        let result = remove_tag_blocks(html, "script");
        assert!(result.contains("keep"));
        assert!(result.contains("keep2"));
        assert!(!result.contains("remove"));
    }
}
