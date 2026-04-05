//! VTE-based ANSI stripping.
//!
//! Uses the `vte` crate state machine to parse terminal sequences and extract
//! printable text. VTE stripping (originated in mish, ostk's experimental predecessor).
//! Kept metadata extraction for future use by fcp-* diagnostic hooks.

use vte::{Params, Perform};

/// ANSI colors detected in output.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum AnsiColor {
    Red,
    BrightRed,
    Yellow,
    BrightYellow,
    Green,
    BrightGreen,
    Cyan,
    BrightCyan,
    Blue,
    BrightBlue,
    Magenta,
    BrightMagenta,
    White,
    BrightWhite,
    Black,
    BrightBlack,
}

/// Metadata extracted from ANSI sequences in a line.
#[derive(Debug, Clone, Default)]
pub struct AnsiMetadata {
    pub colors: Vec<AnsiColor>,
    pub has_cursor_movement: bool,
    pub has_erase: bool,
    pub clean_text: String,
}

/// Strips ANSI escape sequences from text, extracting metadata.
pub struct VteStripper;

impl VteStripper {
    /// Strip ANSI sequences from raw bytes, returning clean text and metadata.
    pub fn strip(bytes: &[u8]) -> AnsiMetadata {
        let mut collector = StripCollector::default();
        let mut parser = vte::Parser::new();
        for &byte in bytes {
            parser.advance(&mut collector, byte);
        }
        AnsiMetadata {
            colors: collector.colors,
            has_cursor_movement: collector.has_cursor_movement,
            has_erase: collector.has_erase,
            clean_text: collector.text,
        }
    }
}

/// Strip ANSI escape sequences from a multi-line string, line by line.
///
/// Returns clean text without any terminal escape codes.
pub fn strip_ansi(raw: &str) -> String {
    raw.lines()
        .map(|line| VteStripper::strip(line.as_bytes()).clean_text)
        .collect::<Vec<_>>()
        .join("\n")
}

/// Strip trailing zsh PROMPT_SP no-newline indicator lines from output.
///
/// When command output doesn't end with a newline, zsh emits PROMPT_EOL_MARK
/// (default "%") followed by spaces and a CR.
pub fn strip_prompt_sp(raw: &str) -> String {
    let lines: Vec<&str> = raw.split('\n').collect();
    let mut end = lines.len();

    while end > 0 {
        let line = lines[end - 1].replace('\r', "");
        let trimmed = line.trim();
        if trimmed.is_empty() || ((trimmed == "%" || trimmed == "#") && line.len() > 2) {
            end -= 1;
        } else {
            break;
        }
    }

    lines[..end].join("\n")
}

#[derive(Default)]
struct StripCollector {
    text: String,
    colors: Vec<AnsiColor>,
    has_cursor_movement: bool,
    has_erase: bool,
    bold: bool,
}

impl StripCollector {
    fn record_color(&mut self, color: AnsiColor) {
        if !self.colors.contains(&color) {
            self.colors.push(color);
        }
    }

    fn map_sgr_color(&mut self, code: u16) {
        let color = match code {
            30 => Some(if self.bold {
                AnsiColor::BrightBlack
            } else {
                AnsiColor::Black
            }),
            31 => Some(if self.bold {
                AnsiColor::BrightRed
            } else {
                AnsiColor::Red
            }),
            32 => Some(if self.bold {
                AnsiColor::BrightGreen
            } else {
                AnsiColor::Green
            }),
            33 => Some(if self.bold {
                AnsiColor::BrightYellow
            } else {
                AnsiColor::Yellow
            }),
            34 => Some(if self.bold {
                AnsiColor::BrightBlue
            } else {
                AnsiColor::Blue
            }),
            35 => Some(if self.bold {
                AnsiColor::BrightMagenta
            } else {
                AnsiColor::Magenta
            }),
            36 => Some(if self.bold {
                AnsiColor::BrightCyan
            } else {
                AnsiColor::Cyan
            }),
            37 => Some(if self.bold {
                AnsiColor::BrightWhite
            } else {
                AnsiColor::White
            }),
            90 => Some(AnsiColor::BrightBlack),
            91 => Some(AnsiColor::BrightRed),
            92 => Some(AnsiColor::BrightGreen),
            93 => Some(AnsiColor::BrightYellow),
            94 => Some(AnsiColor::BrightBlue),
            95 => Some(AnsiColor::BrightMagenta),
            96 => Some(AnsiColor::BrightCyan),
            97 => Some(AnsiColor::BrightWhite),
            _ => None,
        };
        if let Some(c) = color {
            self.record_color(c);
        }
    }
}

impl Perform for StripCollector {
    fn print(&mut self, c: char) {
        self.text.push(c);
    }

    fn execute(&mut self, _byte: u8) {
        // Control characters (like \n, \r) — don't add to clean text
    }

    fn csi_dispatch(
        &mut self,
        params: &Params,
        _intermediates: &[u8],
        _ignore: bool,
        action: char,
    ) {
        match action {
            // SGR — Select Graphic Rendition
            'm' => {
                let mut has_bold = self.bold;
                for param in params.iter() {
                    match param[0] {
                        0 => has_bold = false,
                        1 => has_bold = true,
                        _ => {}
                    }
                }
                self.bold = has_bold;
                for param in params.iter() {
                    let code = param[0];
                    match code {
                        0 | 1 => {}
                        30..=37 | 90..=97 => self.map_sgr_color(code),
                        _ => {}
                    }
                }
            }
            // Cursor movement
            'A' | 'B' | 'C' | 'D' | 'H' | 'f' => {
                self.has_cursor_movement = true;
            }
            // Erase
            'J' | 'K' => {
                self.has_erase = true;
            }
            _ => {}
        }
    }

    fn hook(&mut self, _params: &Params, _intermediates: &[u8], _ignore: bool, _action: char) {}
    fn put(&mut self, _byte: u8) {}
    fn unhook(&mut self) {}
    fn osc_dispatch(&mut self, _params: &[&[u8]], _bell_terminated: bool) {}
    fn esc_dispatch(&mut self, _intermediates: &[u8], _ignore: bool, _byte: u8) {}
}

/// Strip VT100/ANSI escape sequences from raw bytes for non-TTY output.
///
/// Removes ANSI escape sequences and carriage returns (`\r` not followed by `\n`)
/// while preserving all printable text and newlines exactly. Unlike `strip_ansi()`,
/// this operates on raw bytes and preserves byte-exact newline positions — no line
/// deduplication, no line joining. Suitable for piped agent output where escape
/// codes waste tokens but line structure must be preserved.
pub fn strip_vt100_bytes(input: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(input.len());
    let mut i = 0;
    while i < input.len() {
        let b = input[i];
        match b {
            // ESC — start of ANSI escape sequence
            0x1b => {
                i += 1;
                if i < input.len() && input[i] == b'[' {
                    // CSI sequence: ESC [ <params> <final byte>
                    i += 1;
                    // Skip parameter bytes (0x30-0x3F) and intermediate bytes (0x20-0x2F)
                    while i < input.len() && (0x20..=0x3F).contains(&input[i]) {
                        i += 1;
                    }
                    // Skip final byte (0x40-0x7E)
                    if i < input.len() && (0x40..=0x7E).contains(&input[i]) {
                        i += 1;
                    }
                } else if i < input.len() && input[i] == b']' {
                    // OSC sequence: ESC ] ... (terminated by BEL or ST)
                    i += 1;
                    while i < input.len() {
                        if input[i] == 0x07 {
                            // BEL terminator
                            i += 1;
                            break;
                        }
                        if input[i] == 0x1b && i + 1 < input.len() && input[i + 1] == b'\\' {
                            // ST terminator (ESC \)
                            i += 2;
                            break;
                        }
                        i += 1;
                    }
                } else if i < input.len() && (0x40..=0x7E).contains(&input[i]) {
                    // Two-byte escape: ESC + final byte (e.g., ESC M, ESC 7, ESC 8)
                    i += 1;
                } else {
                    // Bare ESC or unrecognized — skip the ESC, re-examine current byte
                }
            }
            // CR — strip unless followed by LF (\r\n → keep both)
            b'\r' => {
                if i + 1 < input.len() && input[i + 1] == b'\n' {
                    out.push(b'\r');
                    out.push(b'\n');
                    i += 2;
                } else {
                    // Lone \r — strip it (cursor return, overwrite artifact)
                    i += 1;
                }
            }
            // Everything else: pass through
            _ => {
                out.push(b);
                i += 1;
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_plain_text_passthrough() {
        let result = VteStripper::strip(b"hello world");
        assert_eq!(result.clean_text, "hello world");
        assert!(result.colors.is_empty());
    }

    #[test]
    fn test_strip_simple_color() {
        let input = b"\x1b[31merror: something broke\x1b[0m";
        let result = VteStripper::strip(input);
        assert_eq!(result.clean_text, "error: something broke");
        assert!(result.colors.contains(&AnsiColor::Red));
    }

    #[test]
    fn test_strip_bold_color() {
        let input = b"\x1b[1;31mFATAL ERROR\x1b[0m";
        let result = VteStripper::strip(input);
        assert_eq!(result.clean_text, "FATAL ERROR");
        assert!(result.colors.contains(&AnsiColor::BrightRed));
    }

    #[test]
    fn test_strip_ansi_multiline() {
        let input = "\x1b[32mok\x1b[0m\n\x1b[31merror\x1b[0m\nplain";
        let result = strip_ansi(input);
        assert_eq!(result, "ok\nerror\nplain");
    }

    #[test]
    fn test_strip_ansi_preserves_plain() {
        assert_eq!(strip_ansi("hello\nworld"), "hello\nworld");
    }

    #[test]
    fn test_strip_ansi_empty() {
        assert_eq!(strip_ansi(""), "");
    }

    #[test]
    fn test_strip_prompt_sp_percent_with_spaces() {
        let input = "hello\n%                                        ";
        let result = strip_prompt_sp(input);
        assert_eq!(result, "hello");
    }

    #[test]
    fn test_strip_prompt_sp_preserves_content() {
        let input = "progress: 50%\ndone";
        let result = strip_prompt_sp(input);
        assert_eq!(result, "progress: 50%\ndone");
    }

    #[test]
    fn test_detect_cursor_movement() {
        let input = b"\x1b[Asome text";
        let result = VteStripper::strip(input);
        assert!(result.has_cursor_movement);
    }

    #[test]
    fn test_detect_erase() {
        let input = b"content\x1b[K";
        let result = VteStripper::strip(input);
        assert!(result.has_erase);
    }

    #[test]
    fn test_mixed_ansi_and_text() {
        let input = b"   Compiling \x1b[32;1mserde\x1b[0m v1.0.195";
        let result = VteStripper::strip(input);
        assert_eq!(result.clean_text, "   Compiling serde v1.0.195");
    }

    // --- strip_vt100_bytes tests ---

    #[test]
    fn test_vt100_bytes_plain_passthrough() {
        let input = b"hello world\n";
        assert_eq!(strip_vt100_bytes(input), input.to_vec());
    }

    #[test]
    fn test_vt100_bytes_strips_sgr() {
        let input = b"\x1b[32mok\x1b[0m\n\x1b[31merror\x1b[0m\n";
        assert_eq!(strip_vt100_bytes(input), b"ok\nerror\n".to_vec());
    }

    #[test]
    fn test_vt100_bytes_strips_erase_and_cursor() {
        let input = b"line1\x1b[K\nline2\x1b[A\n";
        assert_eq!(strip_vt100_bytes(input), b"line1\nline2\n".to_vec());
    }

    #[test]
    fn test_vt100_bytes_strips_lone_cr() {
        // Lone \r (no \n following) should be stripped
        let input = b"progress: 50%\rdone 100%\n";
        assert_eq!(strip_vt100_bytes(input), b"progress: 50%done 100%\n".to_vec());
    }

    #[test]
    fn test_vt100_bytes_preserves_crlf() {
        // \r\n should be preserved (Windows line endings)
        let input = b"line1\r\nline2\r\n";
        assert_eq!(strip_vt100_bytes(input), b"line1\r\nline2\r\n".to_vec());
    }

    #[test]
    fn test_vt100_bytes_preserves_newlines_exactly() {
        let input = b"\n\n\nhello\n\n";
        assert_eq!(strip_vt100_bytes(input), input.to_vec());
    }

    #[test]
    fn test_vt100_bytes_cargo_output() {
        let input = b"   \x1b[32;1mCompiling\x1b[0m serde v1.0.195\n   \x1b[32;1mFinished\x1b[0m dev [unoptimized + debuginfo] target(s)\n";
        let expected = b"   Compiling serde v1.0.195\n   Finished dev [unoptimized + debuginfo] target(s)\n";
        assert_eq!(strip_vt100_bytes(input), expected.to_vec());
    }

    #[test]
    fn test_vt100_bytes_empty() {
        assert_eq!(strip_vt100_bytes(b""), Vec::<u8>::new());
    }

    #[test]
    fn test_vt100_bytes_osc_sequence() {
        // OSC: ESC ] 0 ; title BEL
        let input = b"\x1b]0;my-title\x07hello\n";
        assert_eq!(strip_vt100_bytes(input), b"hello\n".to_vec());
    }

    #[test]
    fn test_vt100_bytes_no_dedup() {
        // Duplicate lines must NOT be removed
        let input = b"aaa\naaa\naaa\n";
        assert_eq!(strip_vt100_bytes(input), input.to_vec());
    }
}
