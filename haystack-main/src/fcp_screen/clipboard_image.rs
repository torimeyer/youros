//! Clipboard image detection — platform-specific (→823).
//!
//! Checks if the system clipboard contains image data.
//! If so, saves it to a temp file and returns the path.
//! If not, returns the clipboard text content (for normal paste).
//!
//! macOS: osascript to check clipboard info + extract PNGf data
//! Linux X11: xclip -selection clipboard -t image/png
//! Linux Wayland: wl-paste --type image/png

use std::path::PathBuf;
use std::process::Command;

pub enum ClipboardContent {
    Image(PathBuf),
    Text(String),
    Empty,
}

/// Grab clipboard content — prefers image, falls back to text.
pub fn grab_clipboard_image() -> ClipboardContent {
    #[cfg(target_os = "macos")]
    { grab_macos() }

    #[cfg(target_os = "linux")]
    { grab_linux() }

    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    { grab_text_fallback() }
}

#[cfg(target_os = "macos")]
fn grab_macos() -> ClipboardContent {
    // Check if clipboard has image data (PNGf class)
    let check = Command::new("osascript")
        .arg("-e")
        .arg("try\nset ci to clipboard info\nrepeat with x in ci\nif (first item of x) is «class PNGf» then return \"PNG\"\nend repeat\nreturn \"NONE\"\non error\nreturn \"NONE\"\nend try")
        .output();

    match check {
        Ok(out) if String::from_utf8_lossy(&out.stdout).trim() == "PNG" => {
            // Extract PNG data to temp file
            let tmp = std::env::temp_dir().join(format!("ostk-clipboard-{}.png", std::process::id()));
            let tmp_str = tmp.to_string_lossy().to_string();
            let script = format!(
                "set the_file to POSIX file \"{tmp_str}\"\n\
                 set the_data to the clipboard as «class PNGf»\n\
                 set fd to open for access the_file with write permission\n\
                 set eof fd to 0\n\
                 write the_data to fd\n\
                 close access fd"
            );
            let result = Command::new("osascript")
                .arg("-e")
                .arg(&script)
                .output();

            match result {
                Ok(out) if out.status.success() && tmp.exists() => {
                    // Verify the file has content
                    if std::fs::metadata(&tmp).map(|m| m.len() > 0).unwrap_or(false) {
                        return ClipboardContent::Image(tmp);
                    }
                    let _ = std::fs::remove_file(&tmp);
                }
                _ => {
                    let _ = std::fs::remove_file(&tmp);
                }
            }
        }
        _ => {}
    }

    // Fall back to text
    grab_text_fallback()
}

#[cfg(target_os = "linux")]
fn grab_linux() -> ClipboardContent {
    // Try xclip first (X11), then wl-paste (Wayland)
    let tmp = std::env::temp_dir().join(format!("ostk-clipboard-{}.png", std::process::id()));

    // Try xclip
    if let Ok(out) = Command::new("xclip")
        .args(["-selection", "clipboard", "-t", "image/png", "-o"])
        .output()
    {
        if out.status.success() && !out.stdout.is_empty() {
            if std::fs::write(&tmp, &out.stdout).is_ok() {
                return ClipboardContent::Image(tmp);
            }
        }
    }

    // Try wl-paste (Wayland fallback)
    if let Ok(out) = Command::new("wl-paste")
        .args(["--type", "image/png"])
        .output()
    {
        if out.status.success() && !out.stdout.is_empty() {
            if std::fs::write(&tmp, &out.stdout).is_ok() {
                return ClipboardContent::Image(tmp);
            }
        }
    }

    grab_text_fallback()
}

/// Get clipboard as text (cross-platform fallback).
fn grab_text_fallback() -> ClipboardContent {
    #[cfg(target_os = "macos")]
    {
        if let Ok(out) = Command::new("pbpaste").output()
            && out.status.success()
        {
            let text = String::from_utf8_lossy(&out.stdout).to_string();
            if text.is_empty() {
                return ClipboardContent::Empty;
            }
            return ClipboardContent::Text(text);
        }
    }

    #[cfg(target_os = "linux")]
    {
        // Try xclip then wl-paste for text
        if let Ok(out) = Command::new("xclip")
            .args(["-selection", "clipboard", "-o"])
            .output()
        {
            if out.status.success() {
                let text = String::from_utf8_lossy(&out.stdout).to_string();
                if !text.is_empty() {
                    return ClipboardContent::Text(text);
                }
            }
        }
        if let Ok(out) = Command::new("wl-paste").output() {
            if out.status.success() {
                let text = String::from_utf8_lossy(&out.stdout).to_string();
                if !text.is_empty() {
                    return ClipboardContent::Text(text);
                }
            }
        }
    }

    ClipboardContent::Empty
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clipboard_content_variants() {
        // Verify enum variants exist and can be constructed
        let _img = ClipboardContent::Image(PathBuf::from("/tmp/test.png"));
        let _txt = ClipboardContent::Text("hello".into());
        let _empty = ClipboardContent::Empty;
    }

    #[test]
    fn grab_text_fallback_returns_something() {
        // Should return Text or Empty — never panic
        let result = grab_text_fallback();
        match result {
            ClipboardContent::Text(_) | ClipboardContent::Empty => {}
            ClipboardContent::Image(_) => panic!("text fallback should never return Image"),
        }
    }
}
