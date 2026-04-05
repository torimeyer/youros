//! Sphere Navigator — interactive overlay for cross-sphere linking.
//!
//! Galaxy view: spheres as labeled entries, cursor navigation, peek panel.
//! j-key to draw joints between needles across spheres.

use std::path::Path;

use serde_json::Value;

use crate::commands::refine;
use crate::fcp_screen::protocol::{self, Color};

use ratatui::text::Line;

/// Truncate a string to at most `max` chars, appending "..." if shortened.
/// Safe for multi-byte UTF-8 — never slices mid-codepoint.
fn truncate_title(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        let truncated: String = s.chars().take(max.saturating_sub(3)).collect();
        format!("{truncated}...")
    }
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/// Navigator view level.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ViewLevel {
    /// Galaxy: list of spheres + isolated count.
    Galaxy,
    /// Sphere: list of member needles within one sphere.
    SphereDetail,
    /// Isolated: list of needles with no joints.
    IsolatedList,
    /// Needle: full detail view of a single needle.
    NeedleDetail,
}

/// In-progress joint (source selected, waiting for target).
#[derive(Debug, Clone)]
pub struct PendingJoint {
    pub source_id: String,
    pub source_title: String,
}

/// Full navigator state.
#[derive(Debug, Clone)]
pub struct SphereNavState {
    pub level: ViewLevel,
    pub spheres: Vec<SphereEntry>,
    pub isolated_count: usize,
    pub cursor: usize,
    pub pending_joint: Option<PendingJoint>,
    /// When viewing a sphere's members, index into spheres[].
    pub focused_sphere: Option<usize>,
    /// Needle entries when in SphereDetail or IsolatedList.
    pub detail_needles: Vec<NeedleEntry>,
    /// Cached isolated needle entries.
    pub isolated_needles: Vec<NeedleEntry>,
    /// Focused needle detail (when in NeedleDetail view).
    pub focused_needle: Option<NeedleDetailData>,
    /// What view level we came from (to restore on back).
    pub detail_return_level: Option<ViewLevel>,
    /// Active search query (None = not searching).
    pub search: Option<String>,
    /// Filtered indices into the current list (spheres/detail/isolated).
    pub search_matches: Vec<usize>,
    /// Which match the cursor is on (index into search_matches).
    pub search_cursor: usize,
    /// Time filter: index into TIME_WINDOWS.
    pub time_window: usize,
}

/// Rendered sphere summary for galaxy view.
#[derive(Debug, Clone)]
pub struct SphereEntry {
    pub point_id: String,
    pub point_title: String,
    pub member_count: usize,
    pub priority: String,
    pub joint_count: usize,
}

/// Full needle detail for the detail view.
#[derive(Debug, Clone)]
pub struct NeedleDetailData {
    pub id: String,
    pub title: String,
    pub priority: String,
    pub status: String,
    pub created_at: String,
    pub joints: Vec<String>,   // "→NNN depends-on →MMM" style
    pub hay: Vec<String>,      // linked hay straws
}

/// Time windows for temporal filter: (label, days). 0 = all time.
pub const TIME_WINDOWS: &[(&str, u64)] = &[
    ("7d", 7),
    ("30d", 30),
    ("90d", 90),
    ("all", 0),
];

/// Rendered needle for sphere detail view.
#[derive(Debug, Clone)]
pub struct NeedleEntry {
    pub id: String,
    pub title: String,
    pub priority: String,
    /// Cluster label (set by embedding clustering, None if unclustered).
    pub cluster: Option<String>,
    /// ISO 8601 creation timestamp.
    pub created_at: String,
}

// ---------------------------------------------------------------------------
// Build state from .ostk/
// ---------------------------------------------------------------------------

/// Load sphere topology from disk.
pub fn build_state(root: &Path) -> SphereNavState {
    let needles = crate::read_needles(root).unwrap_or_default();
    let open: Vec<&Value> = needles.iter()
        .filter(|v| v.get("status").and_then(|s| s.as_str()) == Some("open"))
        .collect();

    let (_joints, adj) = refine::build_joint_graph(&needles);
    let spheres = refine::find_spheres(&adj);

    // Count edges per sphere from adjacency map (find_spheres leaves joints empty)
    let sphere_edge_counts: Vec<usize> = spheres.iter().map(|s| {
        let members: std::collections::HashSet<&str> = s.needles.iter().map(|n| n.as_str()).collect();
        let mut count = 0usize;
        for nid in &s.needles {
            if let Some(neighbors) = adj.get(nid) {
                count += neighbors.iter().filter(|n| members.contains(n.as_str())).count();
            }
        }
        count / 2 // each edge counted twice
    }).collect();

    // Needle id → (title, priority, created_at)
    let mut needle_info: std::collections::HashMap<String, (String, String, String)> = std::collections::HashMap::new();
    for n in &open {
        let id = n.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let title = n.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let pri = n.get("priority").and_then(|v| v.as_str()).unwrap_or("P2").to_string();
        let created = n.get("created_at").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if !id.is_empty() {
            needle_info.insert(id, (title, pri, created));
        }
    }

    // Count needles that belong to any sphere
    let mut in_sphere: std::collections::HashSet<String> = std::collections::HashSet::new();
    for s in &spheres {
        for nid in &s.needles {
            in_sphere.insert(nid.clone());
        }
    }
    let isolated_count = open.len().saturating_sub(in_sphere.len());

    // Build isolated needle entries (sorted by priority)
    let mut isolated_needles: Vec<NeedleEntry> = needle_info.iter()
        .filter(|(id, _)| !in_sphere.contains(id.as_str()))
        .map(|(id, (title, pri, created))| NeedleEntry {
            id: id.clone(),
            title: title.clone(),
            priority: pri.clone(),
            cluster: None,
            created_at: created.clone(),
        })
        .collect();
    // Cluster isolated needles by embedding similarity when available
    #[cfg(feature = "embeddings")]
    cluster_by_embeddings(&mut isolated_needles);

    #[cfg(not(feature = "embeddings"))]
    isolated_needles.sort_by(|a, b| a.priority.cmp(&b.priority).then(a.id.cmp(&b.id)));

    // Build sphere entries (skip singletons — they're "isolated")
    let mut entries: Vec<SphereEntry> = spheres.iter().enumerate()
        .filter(|(_, s)| s.needles.len() > 1)
        .map(|(i, s)| {
            let (title, pri, _created) = needle_info.get(&s.point)
                .cloned()
                .unwrap_or_else(|| (s.point.clone(), "P2".to_string(), String::new()));
            SphereEntry {
                point_id: s.point.clone(),
                point_title: title,
                member_count: s.needles.len(),
                priority: pri,
                joint_count: sphere_edge_counts[i],
            }
        })
        .collect();

    // Sort: P0 first, then by member count descending
    entries.sort_by(|a, b| {
        a.priority.cmp(&b.priority)
            .then(b.member_count.cmp(&a.member_count))
    });

    SphereNavState {
        level: ViewLevel::Galaxy,
        spheres: entries,
        isolated_count,
        cursor: 0,
        pending_joint: None,
        focused_sphere: None,
        detail_needles: Vec::new(),
        isolated_needles,
        focused_needle: None,
        detail_return_level: None,
        search: None,
        search_matches: Vec::new(),
        search_cursor: 0,
        time_window: TIME_WINDOWS.len() - 1, // default: "all"
    }
}

/// Load needle entries for a specific sphere.
pub fn load_sphere_detail(state: &mut SphereNavState, root: &Path, sphere_idx: usize) {
    let needles = crate::read_needles(root).unwrap_or_default();
    let (_joints, adj) = refine::build_joint_graph(&needles);
    let spheres = refine::find_spheres(&adj);

    // Find the sphere matching our entry's point_id
    let point_id = &state.spheres[sphere_idx].point_id;
    let sphere = spheres.iter().find(|s| &s.point == point_id);

    state.detail_needles.clear();

    if let Some(sphere) = sphere {
        let mut entries: Vec<NeedleEntry> = sphere.needles.iter()
            .filter_map(|nid| {
                needles.iter().find(|n| {
                    n.get("id").and_then(|v| v.as_str()) == Some(nid)
                }).map(|n| NeedleEntry {
                    id: nid.clone(),
                    title: n.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                    priority: n.get("priority").and_then(|v| v.as_str()).unwrap_or("P2").to_string(),
                    cluster: None,
                    created_at: n.get("created_at").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                })
            })
            .collect();
        entries.sort_by(|a, b| {
            let a_is_point = a.id == *point_id;
            let b_is_point = b.id == *point_id;
            b_is_point.cmp(&a_is_point)
                .then(a.priority.cmp(&b.priority))
        });
        state.detail_needles = entries;
    }

    state.focused_sphere = Some(sphere_idx);
    state.level = ViewLevel::SphereDetail;
    state.cursor = 0;
}

/// Load full detail for a specific needle and enter NeedleDetail view.
pub fn load_needle_detail(state: &mut SphereNavState, root: &Path, needle_id: &str) {
    let needles = crate::read_needles(root).unwrap_or_default();
    let needle = needles.iter().find(|n| {
        n.get("id").and_then(|v| v.as_str()) == Some(needle_id)
    });

    let Some(needle) = needle else {
        return;
    };

    let id = needle_id.to_string();
    let title = needle.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let priority = needle.get("priority").and_then(|v| v.as_str()).unwrap_or("P2").to_string();
    let status = needle.get("status").and_then(|v| v.as_str()).unwrap_or("open").to_string();
    let created_at = needle.get("created_at").and_then(|v| v.as_str()).unwrap_or("").to_string();

    // Collect joints from the needle's fields
    let mut joints = Vec::new();
    if let Some(deps) = needle.get("depends_on").and_then(|v| v.as_array()) {
        for d in deps {
            if let Some(s) = d.as_str() { joints.push(format!("depends-on {s}")); }
        }
    }
    if let Some(blocks) = needle.get("blocks").and_then(|v| v.as_array()) {
        for b in blocks {
            if let Some(s) = b.as_str() { joints.push(format!("blocks {s}")); }
        }
    }

    // Collect hay from audit that references this needle
    let hay = crate::kernel::intelligence::read_hay(root)
        .unwrap_or_default()
        .into_iter()
        .filter(|h| h.straw.contains(&id))
        .map(|h| {
            let straw = if h.straw.len() > 120 {
                format!("{}...", &h.straw.chars().take(117).collect::<String>())
            } else {
                h.straw
            };
            straw
        })
        .take(5)
        .collect();

    state.detail_return_level = Some(state.level.clone());
    state.focused_needle = Some(NeedleDetailData {
        id, title, priority, status, created_at, joints, hay,
    });
    state.level = ViewLevel::NeedleDetail;
}

/// Update needle priority on disk.
pub fn set_needle_priority(root: &Path, needle_id: &str, new_priority: &str) {
    let _ = std::process::Command::new("ostk")
        .args(["work", "refine", needle_id, "--priority", new_priority])
        .current_dir(root)
        .output();
}

/// Close a needle on disk.
pub fn close_needle(root: &Path, needle_id: &str) {
    let _ = std::process::Command::new("ostk")
        .args(["work", "close", needle_id])
        .current_dir(root)
        .output();
}

// ---------------------------------------------------------------------------
// Embedding clustering (feature-gated)
// ---------------------------------------------------------------------------

/// Cluster needles by embedding similarity using leader algorithm.
/// Assigns cluster labels to each needle, then sorts by cluster.
#[cfg(feature = "embeddings")]
fn cluster_by_embeddings(needles: &mut Vec<NeedleEntry>) {
    if needles.is_empty() {
        return;
    }

    if !crate::squasher::embeddings::model_available() {
        needles.sort_by(|a, b| a.priority.cmp(&b.priority).then(a.id.cmp(&b.id)));
        return;
    }

    // Embed all titles
    let titles: Vec<&str> = needles.iter().map(|n| n.title.as_str()).collect();
    let embeddings = match crate::squasher::embeddings::embed_batch(&titles) {
        Ok(e) => e,
        Err(_) => {
            needles.sort_by(|a, b| a.priority.cmp(&b.priority).then(a.id.cmp(&b.id)));
            return;
        }
    };

    // Leader algorithm: cluster by cosine similarity
    let threshold = 0.70f32; // looser than squasher (0.85) — we want broad groups
    // clusters: (representative_index, centroid, member_indices)
    let mut clusters: Vec<(usize, Vec<f32>, Vec<usize>)> = Vec::new();

    for (i, emb) in embeddings.iter().enumerate() {
        let mut best_sim = -1.0f32;
        let mut best_cluster = None;

        for (ci, (_, centroid, _)) in clusters.iter().enumerate() {
            let sim = crate::squasher::embeddings::cosine_similarity(emb, centroid);
            if sim > best_sim {
                best_sim = sim;
                best_cluster = Some(ci);
            }
        }

        if best_sim > threshold {
            if let Some(ci) = best_cluster {
                clusters[ci].2.push(i);
            }
        } else {
            clusters.push((i, emb.clone(), vec![i]));
        }
    }

    // Sort clusters: largest first
    clusters.sort_by(|a, b| b.2.len().cmp(&a.2.len()));

    // Assign cluster labels and reorder needles
    let mut labeled: Vec<(NeedleEntry, usize)> = Vec::with_capacity(needles.len());
    for (cluster_idx, (rep_idx, _, members)) in clusters.iter().enumerate() {
        // Derive label from the representative needle's title (first significant word)
        let rep_title = &needles[*rep_idx].title;
        let label = rep_title.split_whitespace()
            .find(|w| w.len() > 3)
            .unwrap_or("group")
            .to_uppercase();
        let label = if members.len() > 1 {
            format!("{} ({})", label, members.len())
        } else {
            String::new() // singletons don't get a label
        };

        for &idx in members {
            let mut entry = needles[idx].clone();
            if members.len() > 1 {
                entry.cluster = Some(label.clone());
            }
            labeled.push((entry, cluster_idx));
        }
    }

    // Sort by cluster group (keep members together), then by priority within cluster
    labeled.sort_by(|a, b| a.1.cmp(&b.1).then(a.0.priority.cmp(&b.0.priority)));
    *needles = labeled.into_iter().map(|(n, _)| n).collect();
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

/// Indices of isolated needles visible under the current time filter.
pub fn filtered_isolated_indices(state: &SphereNavState) -> Vec<usize> {
    state.isolated_needles.iter().enumerate()
        .filter(|(_, n)| passes_time_filter(state, &n.created_at))
        .map(|(i, _)| i)
        .collect()
}

/// Number of visible items at current level (respects time filter).
pub fn item_count(state: &SphereNavState) -> usize {
    match state.level {
        ViewLevel::Galaxy => state.spheres.len() + if state.isolated_count > 0 { 1 } else { 0 },
        ViewLevel::SphereDetail => state.detail_needles.len(),
        ViewLevel::IsolatedList => filtered_isolated_indices(state).len(),
        ViewLevel::NeedleDetail => 0, // no cursor navigation in detail view
    }
}

pub fn cursor_up(state: &mut SphereNavState) {
    if state.level == ViewLevel::IsolatedList {
        let visible = filtered_isolated_indices(state);
        if let Some(pos) = visible.iter().position(|&i| i == state.cursor) {
            if pos > 0 {
                state.cursor = visible[pos - 1];
            }
        } else if let Some(&first) = visible.first() {
            state.cursor = first;
        }
    } else if state.cursor > 0 {
        state.cursor -= 1;
    }
}

pub fn cursor_down(state: &mut SphereNavState) {
    if state.level == ViewLevel::IsolatedList {
        let visible = filtered_isolated_indices(state);
        if let Some(pos) = visible.iter().position(|&i| i == state.cursor) {
            if pos + 1 < visible.len() {
                state.cursor = visible[pos + 1];
            }
        } else if let Some(&first) = visible.first() {
            state.cursor = first;
        }
    } else {
        let max = item_count(state).saturating_sub(1);
        if state.cursor < max {
            state.cursor += 1;
        }
    }
}

pub fn back(state: &mut SphereNavState) {
    match state.level {
        ViewLevel::NeedleDetail => {
            state.focused_needle = None;
            if let Some(ret) = state.detail_return_level.take() {
                state.level = ret;
            } else {
                state.level = ViewLevel::Galaxy;
            }
        }
        ViewLevel::SphereDetail => {
            state.level = ViewLevel::Galaxy;
            if let Some(idx) = state.focused_sphere {
                state.cursor = idx;
            }
            state.focused_sphere = None;
            state.detail_needles.clear();
        }
        ViewLevel::IsolatedList => {
            state.level = ViewLevel::Galaxy;
            state.cursor = state.spheres.len();
        }
        ViewLevel::Galaxy => {}
    }
}

/// Get the needle ID under the cursor (if applicable).
pub fn cursor_needle_id(state: &SphereNavState) -> Option<String> {
    match state.level {
        ViewLevel::Galaxy => {
            if state.cursor < state.spheres.len() {
                Some(state.spheres[state.cursor].point_id.clone())
            } else {
                None
            }
        }
        ViewLevel::SphereDetail => {
            state.detail_needles.get(state.cursor).map(|n| n.id.clone())
        }
        ViewLevel::IsolatedList => {
            state.isolated_needles.get(state.cursor).map(|n| n.id.clone())
        }
        ViewLevel::NeedleDetail => {
            state.focused_needle.as_ref().map(|n| n.id.clone())
        }
    }
}

// ---------------------------------------------------------------------------
// Temporal filter
// ---------------------------------------------------------------------------

/// Narrow time window (< key). Clamps cursor to visible range.
pub fn time_window_narrower(state: &mut SphereNavState) {
    if state.time_window > 0 {
        state.time_window -= 1;
        clamp_cursor_to_visible(state);
    }
}

/// Widen time window (> key). Clamps cursor to visible range.
pub fn time_window_wider(state: &mut SphereNavState) {
    if state.time_window < TIME_WINDOWS.len() - 1 {
        state.time_window += 1;
        clamp_cursor_to_visible(state);
    }
}

/// After a time filter change, ensure cursor points to a visible item.
fn clamp_cursor_to_visible(state: &mut SphereNavState) {
    if state.level == ViewLevel::IsolatedList {
        let visible = filtered_isolated_indices(state);
        if !visible.contains(&state.cursor) {
            state.cursor = visible.first().copied().unwrap_or(0);
        }
    }
}

/// Current window label.
pub fn time_window_label(state: &SphereNavState) -> &'static str {
    TIME_WINDOWS[state.time_window].0
}

/// Check if a needle passes the time filter.
pub fn passes_time_filter(state: &SphereNavState, created_at: &str) -> bool {
    let (_, days) = TIME_WINDOWS[state.time_window];
    if days == 0 {
        return true; // "all" — no filter
    }
    if created_at.is_empty() {
        return true; // no timestamp — include by default
    }
    // Parse ISO 8601 date prefix (YYYY-MM-DD)
    let now = now_date_str();
    let cutoff = subtract_days(&now, days);
    created_at >= cutoff.as_str()
}

/// Days since creation, capped at 365. Returns None if unparseable.
pub fn days_since(created_at: &str) -> Option<u64> {
    if created_at.len() < 10 {
        return None;
    }
    let now = now_date_str();
    let c = ymd_to_epoch_days(&created_at[..10])?;
    let n = ymd_to_epoch_days(&now[..10])?;
    Some(n.saturating_sub(c) as u64)
}

/// Heat color: recent = bright green, older = dim gray.
pub fn heat_color(created_at: &str) -> Color {
    match days_since(created_at) {
        Some(d) if d <= 1 => Color::Green,
        Some(d) if d <= 7 => Color::Yellow,
        Some(d) if d <= 30 => Color::Cyan,
        _ => Color::Gray,
    }
}

/// Current date as YYYY-MM-DD from system time (UTC).
fn now_date_str() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    epoch_secs_to_ymd(secs)
}

/// Convert epoch seconds to YYYY-MM-DD string (civil calendar, UTC).
fn epoch_secs_to_ymd(secs: u64) -> String {
    // Algorithm from Howard Hinnant's civil_from_days
    let days = (secs / 86400) as i64;
    let z = days + 719468;
    let era = z.div_euclid(146097);
    let doe = z.rem_euclid(146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{:04}-{:02}-{:02}", y, m, d)
}

/// Parse YYYY-MM-DD to epoch days using civil_to_days (Hinnant).
fn ymd_to_epoch_days(s: &str) -> Option<i64> {
    if s.len() < 10 { return None; }
    let y: i64 = s[0..4].parse().ok()?;
    let m: u32 = s[5..7].parse().ok()?;
    let d: u32 = s[8..10].parse().ok()?;
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y.rem_euclid(400) as u64;
    let m_adj = if m > 2 { m - 3 } else { m + 9 } as u64;
    let doy = (153 * m_adj + 2) / 5 + d as u64 - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some(era * 146097 + doe as i64 - 719468)
}

fn subtract_days(date: &str, days: u64) -> String {
    if let Some(epoch_days) = ymd_to_epoch_days(date) {
        let target = epoch_days - days as i64;
        epoch_secs_to_ymd((target * 86400) as u64)
    } else {
        "2000-01-01".to_string()
    }
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

/// Start a search — enters search mode with empty query.
pub fn search_start(state: &mut SphereNavState) {
    state.search = Some(String::new());
    state.search_matches.clear();
    state.search_cursor = 0;
}

/// Append a character to the search query and recompute matches.
pub fn search_push(state: &mut SphereNavState, ch: char) {
    if let Some(ref mut query) = state.search {
        query.push(ch);
        recompute_matches(state);
    }
}

/// Remove last character from search query.
pub fn search_pop(state: &mut SphereNavState) {
    if let Some(ref mut query) = state.search {
        query.pop();
        recompute_matches(state);
    }
}

/// Jump cursor to the current match and close search.
pub fn search_confirm(state: &mut SphereNavState) {
    if let Some(idx) = state.search_matches.get(state.search_cursor) {
        state.cursor = *idx;
    }
    state.search = None;
    state.search_matches.clear();
    state.search_cursor = 0;
}

/// Cancel search without moving cursor.
pub fn search_cancel(state: &mut SphereNavState) {
    state.search = None;
    state.search_matches.clear();
    state.search_cursor = 0;
}

/// Cycle to next match.
pub fn search_next(state: &mut SphereNavState) {
    if !state.search_matches.is_empty() {
        state.search_cursor = (state.search_cursor + 1) % state.search_matches.len();
        if let Some(&idx) = state.search_matches.get(state.search_cursor) {
            state.cursor = idx;
        }
    }
}

fn recompute_matches(state: &mut SphereNavState) {
    let query = match &state.search {
        Some(q) if !q.is_empty() => q.to_lowercase(),
        _ => {
            state.search_matches.clear();
            state.search_cursor = 0;
            return;
        }
    };

    state.search_matches = match state.level {
        ViewLevel::Galaxy => {
            state.spheres.iter().enumerate()
                .filter(|(_, s)| {
                    s.point_id.to_lowercase().contains(&query)
                    || s.point_title.to_lowercase().contains(&query)
                })
                .map(|(i, _)| i)
                .collect()
        }
        ViewLevel::SphereDetail => {
            state.detail_needles.iter().enumerate()
                .filter(|(_, n)| {
                    n.id.to_lowercase().contains(&query)
                    || n.title.to_lowercase().contains(&query)
                })
                .map(|(i, _)| i)
                .collect()
        }
        ViewLevel::IsolatedList => {
            state.isolated_needles.iter().enumerate()
                .filter(|(_, n)| {
                    n.id.to_lowercase().contains(&query)
                    || n.title.to_lowercase().contains(&query)
                })
                .map(|(i, _)| i)
                .collect()
        }
        ViewLevel::NeedleDetail => Vec::new(), // no search in detail view
    };

    state.search_cursor = 0;
    if let Some(&idx) = state.search_matches.first() {
        state.cursor = idx;
    }
}

/// Whether search mode is active.
pub fn is_searching(state: &SphereNavState) -> bool {
    state.search.is_some()
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

/// Render the navigator as overlay lines.
pub fn render(state: &SphereNavState, width: u16) -> Vec<Line<'static>> {
    // Title max: width minus fixed columns (cursor 3 + id 6 + priority 5 + padding 10)
    let title_max = (width as usize).saturating_sub(24).max(15);
    let mut lines = match state.level {
        ViewLevel::Galaxy => render_galaxy(state, title_max),
        ViewLevel::SphereDetail => render_sphere_detail(state, title_max),
        ViewLevel::IsolatedList => render_isolated(state, title_max),
        ViewLevel::NeedleDetail => render_needle_detail(state, width as usize),
    };
    // Append search bar if active
    if let Some(ref query) = state.search {
        let match_info = if state.search_matches.is_empty() && !query.is_empty() {
            " (no matches)".to_string()
        } else if !state.search_matches.is_empty() {
            format!(" ({}/{})", state.search_cursor + 1, state.search_matches.len())
        } else {
            String::new()
        };
        lines.push(protocol::line_from_spans(vec![
            protocol::colored("  /", Color::Yellow),
            protocol::plain(query),
            protocol::dim(match_info),
        ]));
    }
    lines
}

fn render_galaxy(state: &SphereNavState, title_max: usize) -> Vec<Line<'static>> {
    let tw = time_window_label(state);
    let tw_display = if tw == "all" { String::new() } else { format!("  [< {tw} >]") };
    let mut lines = vec![
        protocol::line_from_spans(vec![
            protocol::colored("  Sphere Navigator  ", Color::Cyan),
            protocol::colored(tw_display, Color::Yellow),
        ]),
        protocol::line_plain(""),
    ];

    if state.spheres.is_empty() && state.isolated_count == 0 {
        lines.push(protocol::line_from_spans(vec![protocol::dim("  (no open needles)")]));
    }

    for (i, sphere) in state.spheres.iter().enumerate() {
        let cursor = if i == state.cursor { "▸" } else { " " };
        let pri_color = match sphere.priority.as_str() {
            "P0" => Color::Red, "P1" => Color::Yellow, _ => Color::Gray
        };
        let title = truncate_title(&sphere.point_title, title_max);

        lines.push(protocol::line_from_spans(vec![
            protocol::colored(format!(" {cursor} "), Color::Cyan),
            protocol::colored(format!("{:<6}", sphere.point_id), Color::Cyan),
            protocol::colored(format!("[{}] ", sphere.priority), pri_color),
            protocol::plain(title),
            protocol::dim(format!("  ({} needles, {} joints)", sphere.member_count, sphere.joint_count)),
        ]));
    }

    // Isolated needles line
    if state.isolated_count > 0 {
        let i = state.spheres.len();
        let cursor = if i == state.cursor { "▸" } else { " " };
        lines.push(protocol::line_from_spans(vec![
            protocol::colored(format!(" {cursor} "), Color::Cyan),
            protocol::dim(format!("{} isolated needles (no joints)", state.isolated_count)),
        ]));
    }

    // Pending joint indicator
    if let Some(pj) = &state.pending_joint {
        lines.push(protocol::line_plain(""));
        lines.push(protocol::line_from_spans(vec![
            protocol::colored("  ┊ joint from: ", Color::Yellow),
            protocol::colored(&pj.source_id, Color::Cyan),
            protocol::plain(format!(" {}", pj.source_title)),
        ]));
        lines.push(protocol::line_from_spans(vec![
            protocol::colored("  ┊ navigate to target, press j to complete", Color::Yellow),
        ]));
    }

    lines.push(protocol::line_plain(""));
    lines.push(protocol::line_from_spans(vec![
        protocol::dim("  ↑↓ navigate  Enter drill  j joint  / search  <> time  Esc back  q close"),
    ]));

    lines
}

fn render_sphere_detail(state: &SphereNavState, title_max: usize) -> Vec<Line<'static>> {
    let sphere_title = state.focused_sphere
        .and_then(|idx| state.spheres.get(idx))
        .map(|s| format!("  {} — {} ({} members)  ", s.point_id, s.point_title, s.member_count))
        .unwrap_or_else(|| "  Sphere Detail  ".to_string());

    let mut lines = vec![
        protocol::line_styled(sphere_title, Color::Cyan),
        protocol::line_plain(""),
    ];

    for (i, needle) in state.detail_needles.iter().enumerate() {
        let cursor = if i == state.cursor { "▸" } else { " " };
        let pri_color = match needle.priority.as_str() {
            "P0" => Color::Red, "P1" => Color::Yellow, _ => Color::Gray
        };
        let is_point = state.focused_sphere
            .and_then(|idx| state.spheres.get(idx))
            .map(|s| s.point_id == needle.id)
            .unwrap_or(false);
        let marker = if is_point { "◉" } else { "○" };

        let title = truncate_title(&needle.title, title_max);

        lines.push(protocol::line_from_spans(vec![
            protocol::colored(format!(" {cursor} {marker} "), Color::Cyan),
            protocol::colored(format!("{:<6}", needle.id), Color::Cyan),
            protocol::colored(format!("[{}] ", needle.priority), pri_color),
            protocol::plain(title),
        ]));
    }

    // Pending joint indicator
    if let Some(pj) = &state.pending_joint {
        lines.push(protocol::line_plain(""));
        lines.push(protocol::line_from_spans(vec![
            protocol::colored("  ┊ joint from: ", Color::Yellow),
            protocol::colored(&pj.source_id, Color::Cyan),
            protocol::plain(format!(" {}", pj.source_title)),
        ]));
        lines.push(protocol::line_from_spans(vec![
            protocol::colored("  ┊ press j on target to link", Color::Yellow),
        ]));
    }

    lines.push(protocol::line_plain(""));
    lines.push(protocol::line_from_spans(vec![
        protocol::dim("  ↑↓ navigate  j joint  Esc back  q close"),
    ]));

    lines
}

fn render_isolated(state: &SphereNavState, title_max: usize) -> Vec<Line<'static>> {
    // Filter by time window
    let filtered_indices: Vec<usize> = state.isolated_needles.iter().enumerate()
        .filter(|(_, n)| passes_time_filter(state, &n.created_at))
        .map(|(i, _)| i)
        .collect();

    let tw = time_window_label(state);
    let tw_display = if tw == "all" { String::new() } else { format!("  [< {tw} >]") };
    let mut lines = vec![
        protocol::line_from_spans(vec![
            protocol::colored(
                format!("  Isolated Needles ({}/{})  ", filtered_indices.len(), state.isolated_needles.len()),
                Color::Cyan,
            ),
            protocol::colored(tw_display, Color::Yellow),
        ]),
        protocol::line_plain(""),
    ];

    // Show a scrollable window
    let visible = 20usize;
    // Map cursor to position within filtered list
    let cursor_in_filtered = filtered_indices.iter().position(|&i| i == state.cursor);
    let cursor_pos = cursor_in_filtered.unwrap_or(0);
    let start = cursor_pos.saturating_sub(visible / 2);
    let end = (start + visible).min(filtered_indices.len());
    let start = end.saturating_sub(visible);

    let mut last_cluster: Option<String> = None;
    for &i in filtered_indices.iter().take(end).skip(start) {
        let needle = &state.isolated_needles[i];

        // Show cluster header when group changes
        if needle.cluster != last_cluster {
            if let Some(ref label) = needle.cluster {
                if last_cluster.is_some() {
                    lines.push(protocol::line_plain("")); // gap between clusters
                }
                lines.push(protocol::line_from_spans(vec![
                    protocol::colored(format!("   ── {label} ──"), Color::Yellow),
                ]));
            }
            last_cluster = needle.cluster.clone();
        }

        let cursor = if i == state.cursor { "▸" } else { " " };
        let id_color = heat_color(&needle.created_at);
        let pri_color = match needle.priority.as_str() {
            "P0" => Color::Red, "P1" => Color::Yellow, _ => Color::Gray
        };
        let title = truncate_title(&needle.title, title_max);

        lines.push(protocol::line_from_spans(vec![
            protocol::colored(format!(" {cursor} "), Color::Cyan),
            protocol::colored(format!("{:<6}", needle.id), id_color),
            protocol::colored(format!("[{}] ", needle.priority), pri_color),
            protocol::plain(title),
        ]));
    }

    if filtered_indices.len() > visible {
        lines.push(protocol::line_from_spans(vec![
            protocol::dim(format!("  [{}/{} shown]", visible, filtered_indices.len())),
        ]));
    }

    if let Some(pj) = &state.pending_joint {
        lines.push(protocol::line_plain(""));
        lines.push(protocol::line_from_spans(vec![
            protocol::colored("  ┊ joint from: ", Color::Yellow),
            protocol::colored(&pj.source_id, Color::Cyan),
            protocol::plain(format!(" {}", pj.source_title)),
        ]));
        lines.push(protocol::line_from_spans(vec![
            protocol::colored("  ┊ press j on target to link", Color::Yellow),
        ]));
    }

    lines.push(protocol::line_plain(""));
    lines.push(protocol::line_from_spans(vec![
        protocol::dim("  ↑↓ navigate  j joint  Esc back  q close"),
    ]));

    lines
}

// ---------------------------------------------------------------------------
fn render_needle_detail(state: &SphereNavState, width: usize) -> Vec<Line<'static>> {
    let Some(ref needle) = state.focused_needle else {
        return vec![protocol::line_styled("  (no needle selected)  ", Color::Red)];
    };

    let pri_color = match needle.priority.as_str() {
        "P0" => Color::Red, "P1" => Color::Yellow, _ => Color::Gray
    };
    let status_color = match needle.status.as_str() {
        "open" => Color::Green, "closed" => Color::Gray, _ => Color::Yellow
    };
    let age = days_since(&needle.created_at)
        .map(|d| format!("{d}d ago"))
        .unwrap_or_default();

    let mut lines = vec![
        protocol::line_from_spans(vec![
            protocol::colored(format!("  {}  ", needle.id), Color::Cyan),
            protocol::colored(format!("[{}] ", needle.priority), pri_color),
            protocol::colored(format!("[{}] ", needle.status), status_color),
            protocol::dim(age),
        ]),
        protocol::line_plain(""),
    ];

    // Full title — word-wrapped
    let max_line = width.saturating_sub(4);
    let title = &needle.title;
    if title.len() <= max_line {
        lines.push(protocol::line_from_spans(vec![protocol::plain(format!("  {title}"))]));
    } else {
        for chunk in title.as_bytes().chunks(max_line) {
            let s = String::from_utf8_lossy(chunk);
            lines.push(protocol::line_from_spans(vec![protocol::plain(format!("  {s}"))]));
        }
    }

    // Joints
    if !needle.joints.is_empty() {
        lines.push(protocol::line_plain(""));
        lines.push(protocol::line_styled("  Joints", Color::Yellow));
        for j in &needle.joints {
            lines.push(protocol::line_from_spans(vec![
                protocol::plain(format!("    {j}")),
            ]));
        }
    }

    // Hay
    if !needle.hay.is_empty() {
        lines.push(protocol::line_plain(""));
        lines.push(protocol::line_styled("  Hay", Color::Yellow));
        for h in &needle.hay {
            lines.push(protocol::line_from_spans(vec![
                protocol::dim(format!("    {h}")),
            ]));
        }
    }

    lines.push(protocol::line_plain(""));
    lines.push(protocol::line_from_spans(vec![
        protocol::dim("  p priority  x close  j joint  Esc back"),
    ]));

    lines
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_state() -> SphereNavState {
        SphereNavState {
            level: ViewLevel::Galaxy,
            spheres: vec![
                SphereEntry {
                    point_id: "→888".into(),
                    point_title: "kernel handler for glob/grep".into(),
                    member_count: 3, priority: "P0".into(), joint_count: 2,
                },
                SphereEntry {
                    point_id: "→975".into(),
                    point_title: "agent loop hangs".into(),
                    member_count: 126, priority: "P1".into(), joint_count: 744,
                },
            ],
            isolated_count: 5,
            cursor: 0,
            pending_joint: None,
            focused_sphere: None,
            detail_needles: Vec::new(),
            isolated_needles: vec![
                NeedleEntry { id: "→100".into(), title: "isolated one".into(), priority: "P2".into(), cluster: None, created_at: "2026-04-03T10:00:00Z".into() },
                NeedleEntry { id: "→101".into(), title: "isolated two".into(), priority: "P2".into(), cluster: None, created_at: "2026-04-01T10:00:00Z".into() },
                NeedleEntry { id: "→102".into(), title: "isolated three".into(), priority: "P1".into(), cluster: None, created_at: "2026-03-01T10:00:00Z".into() },
                NeedleEntry { id: "→103".into(), title: "isolated four".into(), priority: "P2".into(), cluster: None, created_at: "2026-01-01T10:00:00Z".into() },
                NeedleEntry { id: "→104".into(), title: "isolated five".into(), priority: "P2".into(), cluster: None, created_at: "2025-06-01T10:00:00Z".into() },
            ],
            focused_needle: None,
            detail_return_level: None,
            search: None,
            search_matches: Vec::new(),
            search_cursor: 0,
            time_window: TIME_WINDOWS.len() - 1, // "all"
        }
    }

    fn render_to_text(s: &SphereNavState) -> String {
        render(s, 120).iter()
            .map(|l| l.spans.iter().map(|s| s.content.as_ref()).collect::<String>())
            .collect::<Vec<_>>().join("\n")
    }

    #[test] fn item_count_galaxy() { assert_eq!(item_count(&sample_state()), 3); }
    #[test] fn item_count_no_isolated() { let mut s = sample_state(); s.isolated_count = 0; assert_eq!(item_count(&s), 2); }
    #[test] fn item_count_isolated() { let mut s = sample_state(); s.level = ViewLevel::IsolatedList; assert_eq!(item_count(&s), 5); }

    #[test] fn cursor_clamps_bottom() { let mut s = sample_state(); for _ in 0..10 { cursor_down(&mut s); } assert_eq!(s.cursor, 2); }
    #[test] fn cursor_clamps_top() { let mut s = sample_state(); cursor_up(&mut s); assert_eq!(s.cursor, 0); }
    #[test] fn cursor_round_trip() { let mut s = sample_state(); cursor_down(&mut s); cursor_down(&mut s); cursor_up(&mut s); assert_eq!(s.cursor, 1); }

    #[test] fn id_galaxy_sphere() { assert_eq!(cursor_needle_id(&sample_state()), Some("→888".into())); }
    #[test] fn id_galaxy_isolated_none() { let mut s = sample_state(); s.cursor = 2; assert_eq!(cursor_needle_id(&s), None); }
    #[test] fn id_isolated_list() { let mut s = sample_state(); s.level = ViewLevel::IsolatedList; s.cursor = 2; assert_eq!(cursor_needle_id(&s), Some("→102".into())); }

    #[test] fn back_detail() { let mut s = sample_state(); s.level = ViewLevel::SphereDetail; s.focused_sphere = Some(1); s.cursor = 5; back(&mut s); assert_eq!(s.level, ViewLevel::Galaxy); assert_eq!(s.cursor, 1); }
    #[test] fn back_isolated() { let mut s = sample_state(); s.level = ViewLevel::IsolatedList; s.cursor = 3; back(&mut s); assert_eq!(s.level, ViewLevel::Galaxy); assert_eq!(s.cursor, 2); }
    #[test] fn back_galaxy_noop() { let mut s = sample_state(); s.cursor = 1; back(&mut s); assert_eq!(s.cursor, 1); }

    #[test] fn render_galaxy_content() { let t = render_to_text(&sample_state()); assert!(t.contains("→888")); assert!(t.contains("isolated")); assert!(t.contains("2 joints")); }
    #[test] fn render_cursor_visible() { let mut s = sample_state(); s.cursor = 1; assert!(render_to_text(&s).contains("▸")); }
    #[test] fn render_pending_joint() { let mut s = sample_state(); s.pending_joint = Some(PendingJoint { source_id: "→888".into(), source_title: "k".into() }); assert!(render_to_text(&s).contains("joint from")); }
    #[test] fn render_isolated() { let mut s = sample_state(); s.level = ViewLevel::IsolatedList; assert!(render_to_text(&s).contains("→100")); }
    #[test] fn render_detail_marks_point() {
        let mut s = sample_state();
        s.level = ViewLevel::SphereDetail; s.focused_sphere = Some(0);
        s.detail_needles = vec![
            NeedleEntry { id: "→888".into(), title: "kernel".into(), priority: "P0".into(), cluster: None, created_at: "2026-04-03T10:00:00Z".into() },
            NeedleEntry { id: "→889".into(), title: "spawn".into(), priority: "P0".into(), cluster: None, created_at: "2026-04-02T10:00:00Z".into() },
        ];
        let t = render_to_text(&s); assert!(t.contains("◉")); assert!(t.contains("→889"));
    }

    // ── search ──

    #[test] fn search_start_sets_mode() { let mut s = sample_state(); search_start(&mut s); assert!(is_searching(&s)); assert_eq!(s.search, Some(String::new())); }
    #[test] fn search_cancel_clears() { let mut s = sample_state(); search_start(&mut s); search_push(&mut s, 'k'); search_cancel(&mut s); assert!(!is_searching(&s)); assert!(s.search_matches.is_empty()); }

    #[test] fn search_finds_sphere_by_title() {
        let mut s = sample_state();
        search_start(&mut s);
        search_push(&mut s, 'a'); search_push(&mut s, 'g'); search_push(&mut s, 'e'); search_push(&mut s, 'n'); search_push(&mut s, 't');
        assert_eq!(s.search_matches, vec![1]); // "agent loop hangs" is index 1
        assert_eq!(s.cursor, 1);
    }

    #[test] fn search_finds_by_id() {
        let mut s = sample_state();
        search_start(&mut s);
        search_push(&mut s, '9'); search_push(&mut s, '7'); search_push(&mut s, '5');
        assert_eq!(s.search_matches, vec![1]);
    }

    #[test] fn search_confirm_keeps_cursor() {
        let mut s = sample_state();
        search_start(&mut s);
        search_push(&mut s, 'a'); search_push(&mut s, 'g'); search_push(&mut s, 'e'); search_push(&mut s, 'n'); search_push(&mut s, 't');
        search_confirm(&mut s);
        assert!(!is_searching(&s));
        assert_eq!(s.cursor, 1);
    }

    #[test] fn search_next_cycles() {
        let mut s = sample_state();
        s.level = ViewLevel::IsolatedList;
        search_start(&mut s);
        search_push(&mut s, 'i'); search_push(&mut s, 's'); search_push(&mut s, 'o'); // "iso" matches all 5
        assert_eq!(s.search_matches.len(), 5);
        search_next(&mut s);
        assert_eq!(s.search_cursor, 1);
        assert_eq!(s.cursor, 1);
    }

    #[test] fn search_backspace() {
        let mut s = sample_state();
        search_start(&mut s);
        search_push(&mut s, 'x'); search_push(&mut s, 'y'); search_push(&mut s, 'z');
        assert!(s.search_matches.is_empty()); // no matches for "xyz"
        search_pop(&mut s); search_pop(&mut s); search_pop(&mut s); // back to empty
        assert_eq!(s.search.as_deref(), Some(""));
    }

    #[test] fn render_shows_search_bar() {
        let mut s = sample_state();
        search_start(&mut s);
        search_push(&mut s, 'k'); search_push(&mut s, 'e'); search_push(&mut s, 'r');
        let t = render_to_text(&s);
        assert!(t.contains("/ker"), "should show search bar: {t}");
    }

    // ── temporal filter ──

    #[test] fn time_window_default_is_all() {
        let s = sample_state();
        assert_eq!(time_window_label(&s), "all");
    }

    #[test] fn time_window_narrows_and_widens() {
        let mut s = sample_state();
        time_window_narrower(&mut s); // all → 90d
        assert_eq!(time_window_label(&s), "90d");
        time_window_narrower(&mut s); // 90d → 30d
        assert_eq!(time_window_label(&s), "30d");
        time_window_narrower(&mut s); // 30d → 7d
        assert_eq!(time_window_label(&s), "7d");
        time_window_narrower(&mut s); // 7d → 7d (clamped)
        assert_eq!(time_window_label(&s), "7d");
        time_window_wider(&mut s); // 7d → 30d
        assert_eq!(time_window_label(&s), "30d");
        time_window_wider(&mut s);
        time_window_wider(&mut s);
        time_window_wider(&mut s); // clamped at all
        assert_eq!(time_window_label(&s), "all");
    }

    #[test] fn passes_filter_all_includes_everything() {
        let s = sample_state(); // time_window = "all"
        assert!(passes_time_filter(&s, "2020-01-01T00:00:00Z"));
        assert!(passes_time_filter(&s, ""));
    }

    #[test] fn passes_filter_empty_timestamp_always_passes() {
        let mut s = sample_state();
        s.time_window = 0; // 7d
        assert!(passes_time_filter(&s, "")); // no timestamp = include
    }

    #[test] fn heat_color_recent_is_green() {
        // Today's date — should be green
        let today = format!("{}T10:00:00Z", &now_date_str()[..10]);
        assert_eq!(heat_color(&today), Color::Green);
    }

    #[test] fn heat_color_empty_is_gray() {
        assert_eq!(heat_color(""), Color::Gray);
    }

    #[test] fn days_since_returns_none_for_short_string() {
        assert_eq!(days_since("bad"), None);
    }

    #[test] fn days_since_today_is_zero_or_one() {
        let today = format!("{}T10:00:00Z", &now_date_str()[..10]);
        let d = days_since(&today).unwrap();
        assert!(d <= 1, "days since today should be 0 or 1, got {d}");
    }

    #[test] fn render_galaxy_shows_time_filter() {
        let mut s = sample_state();
        s.time_window = 1; // 30d
        let t = render_to_text(&s);
        assert!(t.contains("30d"), "should show time filter: {t}");
    }

    #[test] fn render_isolated_shows_time_filter() {
        let mut s = sample_state();
        s.level = ViewLevel::IsolatedList;
        s.time_window = 0; // 7d
        let t = render_to_text(&s);
        assert!(t.contains("7d"), "should show time filter: {t}");
    }

    #[test] fn date_epoch_ordering_preserved() {
        let a = ymd_to_epoch_days("2026-01-01").unwrap();
        let b = ymd_to_epoch_days("2026-04-03").unwrap();
        let c = ymd_to_epoch_days("2025-06-01").unwrap();
        assert!(b > a, "April > January");
        assert!(a > c, "2026 > 2025");
        // Verify actual day difference
        assert_eq!(b - a, 92, "Jan 1 to Apr 3 = 92 days");
    }

    // ── edge cases from review ──

    #[test] fn truncate_title_ascii() {
        assert_eq!(truncate_title("short", 50), "short");
        assert_eq!(truncate_title(&"x".repeat(60), 50), format!("{}...", "x".repeat(47)));
    }

    #[test] fn truncate_title_emoji_safe() {
        let emoji_title = "🔥 fix the daemon 🚀 launch it now please extend this far enough";
        let result = truncate_title(emoji_title, 20);
        assert!(result.ends_with("..."));
        // Must not panic — the old &s[..N] would panic here
        assert!(result.chars().count() <= 20);
    }

    #[test] fn truncate_title_cjk_safe() {
        let cjk = "日本語テスト文字列がここに入る長い文字列です";
        let result = truncate_title(cjk, 10);
        assert!(result.ends_with("..."));
        assert!(result.chars().count() <= 10);
    }

    #[test] fn cursor_nav_with_time_filter() {
        let mut s = sample_state();
        s.level = ViewLevel::IsolatedList;
        s.time_window = 0; // 7d — only →100 (today) and →101 (3 days ago) pass
        // Cursor starts at 0 which should be visible
        let visible = filtered_isolated_indices(&s);
        assert!(visible.len() <= 2, "7d window should filter most: {:?}", visible);
        // Navigate down — should only visit visible items
        cursor_down(&mut s);
        assert!(visible.contains(&s.cursor), "cursor should be on visible item");
    }

    #[test] fn cursor_nav_all_filtered_out() {
        let mut s = sample_state();
        s.level = ViewLevel::IsolatedList;
        // Set all created_at to very old dates
        for n in &mut s.isolated_needles {
            n.created_at = "2020-01-01T00:00:00Z".into();
        }
        s.time_window = 0; // 7d — nothing passes
        let visible = filtered_isolated_indices(&s);
        assert!(visible.is_empty());
        // Navigation should not panic
        cursor_down(&mut s);
        cursor_up(&mut s);
        assert_eq!(item_count(&s), 0);
    }

    #[test] fn time_window_change_clamps_cursor() {
        let mut s = sample_state();
        s.level = ViewLevel::IsolatedList;
        s.cursor = 4; // →104 from 2025 — old
        time_window_narrower(&mut s); // all → 90d
        time_window_narrower(&mut s); // 90d → 30d
        time_window_narrower(&mut s); // 30d → 7d
        // →104 is now filtered out, cursor should clamp to a visible item
        let visible = filtered_isolated_indices(&s);
        if !visible.is_empty() {
            assert!(visible.contains(&s.cursor), "cursor clamped to visible: cursor={}, visible={:?}", s.cursor, visible);
        }
    }

    #[test] fn search_in_isolated_with_time_filter() {
        let mut s = sample_state();
        s.level = ViewLevel::IsolatedList;
        s.time_window = 0; // 7d
        search_start(&mut s);
        search_push(&mut s, 'i'); search_push(&mut s, 's'); search_push(&mut s, 'o');
        // Search should find items regardless of time filter (search operates on full list)
        // but cursor should land on a matching item
        assert!(!s.search_matches.is_empty(), "search should find matches");
        search_confirm(&mut s);
        assert!(!is_searching(&s));
    }

    #[test] fn sphere_detail_with_empty_sphere() {
        // Simulates load_sphere_detail when sphere vanishes from disk
        let mut s = sample_state();
        s.detail_needles = vec![
            NeedleEntry { id: "→old".into(), title: "stale".into(), priority: "P2".into(), cluster: None, created_at: "".into() },
        ];
        // After load_sphere_detail clears detail_needles (even if sphere not found),
        // the level is set to SphereDetail with empty list
        s.detail_needles.clear();
        s.level = ViewLevel::SphereDetail;
        s.cursor = 0;
        // Should render without panic
        let t = render_to_text(&s);
        assert!(!t.is_empty());
        assert_eq!(item_count(&s), 0);
        assert_eq!(cursor_needle_id(&s), None);
    }
}
