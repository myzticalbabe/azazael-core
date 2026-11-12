//! judge_rs — swarm-arena shard (Rust): evidence-heuristic pairwise judge.
//! Reads pairs JSON from stdin: [{"pair":"...","x":"...","y":"..."}]
//! Writes verdicts JSON to stdout: [{"pair":"...","winner":"X|Y","sx":f,"sy":f}]
//! No dependencies, no network, fully deterministic. The orchestrator blinds
//! position by randomizing which candidate is X/Y per pair.

use std::io::Read;

fn count_pat(hay: &str, pat: &str) -> usize {
    hay.matches(pat).count()
}

/// Count #NNNN task references (# + 3-4 digits).
fn count_task_refs(t: &str) -> usize {
    let b = t.as_bytes();
    let mut n = 0;
    let mut i = 0;
    while i + 4 < b.len() {
        if b[i] == b'#'
            && b[i + 1].is_ascii_digit()
            && b[i + 2].is_ascii_digit()
            && b[i + 3].is_ascii_digit()
        {
            // 3 digits always count; 4th digit optional
            n += 1;
            i += 4;
            while i < b.len() && b[i].is_ascii_digit() {
                i += 1;
            }
        } else {
            i += 1;
        }
    }
    n
}

/// Count 7-hex commit hashes (heuristic: 7+ lowercase hex run bounded by non-hex).
fn count_hashes(t: &str) -> usize {
    let b = t.as_bytes();
    let mut n = 0;
    let mut i = 0;
    while i < b.len() {
        if b[i].is_ascii_hexdigit() && (b[i].is_ascii_digit() || b[i].is_ascii_lowercase()) {
            let mut j = i;
            while j < b.len()
                && b[j].is_ascii_hexdigit()
                && (b[j].is_ascii_digit() || b[j].is_ascii_lowercase())
            {
                j += 1;
            }
            if j - i >= 7 {
                n += 1;
            }
            i = j.max(i + 1);
        } else {
            i += 1;
        }
    }
    n
}

fn score(t: &str) -> f64 {
    let mut s = 0.0;
    if t.contains("Root cause:") {
        s += 3.0;
    }
    if t.contains("Fix:") {
        s += 3.0;
    }
    if t.contains("Evidence:") {
        s += 2.0;
    }
    s += (count_task_refs(t).min(5)) as f64 * 1.0;
    s += (count_pat(t, "PMB event id").min(3)) as f64 * 2.0;
    s += (count_pat(t, "2026-10-").min(3)) as f64 * 1.0;
    s += (count_pat(t, "$").min(3)) as f64 * 1.0;
    if t.contains("committed") {
        s += 1.0;
    }
    s += (count_hashes(t).min(3)) as f64 * 1.0;
    s
}

/// Minimal JSON parser for the known input shape:
/// [{"pair":"P","x":"X","y":"Y"}, ...] with \" \\ \n \t \r escapes.
/// String-aware: braces inside string values never affect object depth.
/// (The naive brace counter died on pair 2 — candidate text contains {}.)
fn parse_pairs(src: &str) -> Vec<(String, String, String)> {
    let b = src.as_bytes();
    let mut i = 0;
    let mut out = Vec::new();
    // skip to '['
    while i < b.len() && b[i] != b'[' {
        i += 1;
    }
    i += 1;
    while i < b.len() {
        // skip whitespace/commas, stop at ']'
        while i < b.len() && (b[i] == b' ' || b[i] == b'\n' || b[i] == b'\r' || b[i] == b'\t' || b[i] == b',') {
            i += 1;
        }
        if i >= b.len() || b[i] == b']' {
            break;
        }
        if b[i] != b'{' {
            i += 1;
            continue;
        }
        // parse one object: sequence of "key" : "string" pairs
        let mut pair = String::new();
        let mut x = String::new();
        let mut y = String::new();
        i += 1; // '{'
        loop {
            while i < b.len() && b[i] != b'"' && b[i] != b'}' {
                i += 1;
            }
            if i >= b.len() || b[i] == b'}' {
                i += 1;
                break;
            }
            let (key, ni) = read_json_str(src, i);
            i = ni;
            while i < b.len() && (b[i] == b' ' || b[i] == b':') {
                i += 1;
            }
            let (val, ni2) = read_json_str(src, i);
            i = ni2;
            match key.as_str() {
                "pair" => pair = val,
                "x" => x = val,
                "y" => y = val,
                _ => {}
            }
        }
        out.push((pair, x, y));
    }
    out
}

/// Read a JSON string starting at the opening quote at byte `i`.
/// Returns (decoded, byte index just past the closing quote).
fn read_json_str(src: &str, i: usize) -> (String, usize) {
    let b = src.as_bytes();
    let mut s = String::new();
    let mut j = i + 1; // skip opening quote
    while j < b.len() {
        let c = b[j];
        if c == b'\\' && j + 1 < b.len() {
            let e = b[j + 1];
            match e {
                b'n' => s.push('\n'),
                b't' => s.push('\t'),
                b'r' => s.push('\r'),
                b'"' => s.push('"'),
                b'\\' => s.push('\\'),
                b'/' => s.push('/'),
                b'u' => {
                    // \uXXXX — decode BMP, else placeholder
                    if j + 5 < b.len() {
                        if let Ok(cp) = u32::from_str_radix(&src[j + 2..j + 6], 16) {
                            s.push(char::from_u32(cp).unwrap_or('?'));
                            j += 4;
                        } else {
                            s.push('?');
                        }
                    }
                }
                _ => s.push(e as char),
            }
            j += 2;
        } else if c == b'"' {
            return (s, j + 1);
        } else {
            // copy UTF-8 char as-is
            let ch = src[j..].chars().next().unwrap();
            s.push(ch);
            j += ch.len_utf8();
        }
    }
    (s, j)
}

fn esc_out(s: &str) -> String {
    let mut o = String::with_capacity(s.len() + 2);
    for c in s.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            '\n' => o.push_str("\\n"),
            '\r' => o.push_str("\\r"),
            '\t' => o.push_str("\\t"),
            c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
            c => o.push(c),
        }
    }
    o
}

fn main() {
    let mut src = String::new();
    std::io::stdin().read_to_string(&mut src).unwrap();
    let pairs = parse_pairs(&src);
    let mut out = String::from("[");
    for (k, (pair, x, y)) in pairs.iter().enumerate() {
        let sx = score(x);
        let sy = score(y);
        let winner = if sx > sy {
            "X"
        } else if sy > sx {
            "Y"
        } else if x.len() >= y.len() {
            "X"
        } else {
            "Y"
        };
        if k > 0 {
            out.push(',');
        }
        out.push_str(&format!(
            "{{\"pair\":\"{}\",\"winner\":\"{}\",\"sx\":{:.2},\"sy\":{:.2}}}",
            esc_out(pair),
            winner,
            sx,
            sy
        ));
    }
    out.push(']');
    println!("{}", out);
}
