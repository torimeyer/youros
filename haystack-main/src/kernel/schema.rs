//! Generate MCP tool schemas from .language signature strings (→1157 Phase 5).
//!
//! Parses `.language` signatures like `(cmd, cwd?, timeout?) → (output)` into
//! JSON Schema objects suitable for MCP `tool_definitions()`.
//!
//! This eliminates hardcoded tool schemas in dispatch.rs — tools are defined
//! once in `.language`, and the MCP surface is generated from it.

use serde_json::{json, Value};

use crate::language::LanguageEntry;
use crate::serve::types::ToolDefinition;

/// Generate tool definitions from .language entries.
///
/// Only includes verbs with momentum >= threshold (resident tools).
/// The rest are deferred and available via `ostk_verbs`.
///
/// Process primitives (shell, spawn, interact, session, lock) get enriched
/// schemas with enum constraints. Everything else uses signature parsing.
pub fn tool_definitions_from_language(entries: &[LanguageEntry], threshold: f64) -> Vec<ToolDefinition> {
    entries
        .iter()
        .filter(|e| e.momentum >= threshold)
        .filter(|e| !e.is_signal()) // skip [inferred/control] verbs
        .map(|e| {
            let input_schema = enriched_schema(&e.verb)
                .unwrap_or_else(|| schema_from_signature(&e.signature));
            ToolDefinition {
                name: e.verb.clone(),
                description: e.doc.clone(),
                input_schema,
            }
        })
        .collect()
}

/// Schema enrichment for tools that need enum constraints or richer types
/// than the signature parser can express. Returns None for tools that
/// should use the default signature-based schema.
fn enriched_schema(verb: &str) -> Option<Value> {
    match verb {
        "interact" => Some(json!({
            "type": "object",
            "properties": {
                "alias": { "type": "string", "description": "Process alias" },
                "action": { "type": "string", "enum": ["read_tail", "send_input", "send_signal", "kill", "status"], "description": "Action to perform" },
                "input": { "type": "string", "description": "Input to send (for send_input/send_signal)" },
                "lines": { "type": "integer", "description": "Number of lines to read (for read_tail)", "default": 50 },
                "timeout": { "type": "integer", "description": "Timeout in seconds" }
            },
            "required": ["alias", "action"]
        })),
        "session" => Some(json!({
            "type": "object",
            "properties": {
                "action": { "type": "string", "enum": ["list", "create", "close"], "description": "Action" },
                "name": { "type": "string", "description": "Session name (for create/close)" }
            },
            "required": ["action"]
        })),
        "lock" => Some(json!({
            "type": "object",
            "properties": {
                "action": { "type": "string", "enum": ["create", "release", "watch", "status"], "description": "Lock action" },
                "name": { "type": "string", "description": "Lock name" },
                "timeout": { "type": "integer", "description": "Watch timeout in seconds", "default": 300 }
            },
            "required": ["action", "name"]
        })),
        _ => None,
    }
}

/// Parse a .language signature into JSON Schema.
///
/// Format: `(param1, param2?, param3?) → (output_desc)`
/// - `?` suffix = optional parameter (not in "required")
/// - All params default to type "string"
/// - Integer-named params (timeout, lines, before_turn) get type "integer"
/// - Boolean-named params (raw, semantic, json) get type "boolean"
///
/// Examples:
///   `(cmd, cwd?, timeout?) → (output)` → required: ["cmd"], optional: cwd, timeout
///   `() → (state)` → no properties (empty schema)
///   `(query) → (matches)` → required: ["query"]
pub fn schema_from_signature(sig: &str) -> Value {
    let input_part = sig.split(['→', '-']).next().unwrap_or(sig).trim();

    // Strip parens
    let inner = input_part
        .trim_start_matches('(')
        .trim_end_matches(')')
        .trim();

    if inner.is_empty() {
        return json!({
            "type": "object",
            "properties": {}
        });
    }

    let mut properties = serde_json::Map::new();
    let mut required = Vec::new();

    for param in inner.split(',') {
        let param = param.trim();
        if param.is_empty() {
            continue;
        }

        let (name, optional) = if param.ends_with('?') {
            (&param[..param.len() - 1], true)
        } else {
            (param, false)
        };

        let param_type = infer_type(name);
        properties.insert(
            name.to_string(),
            json!({ "type": param_type }),
        );

        if !optional {
            required.push(Value::String(name.to_string()));
        }
    }

    let mut schema = json!({
        "type": "object",
        "properties": properties,
    });

    if !required.is_empty() {
        schema["required"] = Value::Array(required);
    }

    schema
}

/// Infer JSON Schema type from parameter name.
fn infer_type(name: &str) -> &'static str {
    match name {
        "timeout" | "lines" | "before_turn" | "count" | "last_n" => "integer",
        "raw" | "semantic" | "json" | "dry_run" | "fix_rewrites" => "boolean",
        _ => "string",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_schema_from_signature_with_required_and_optional() {
        let schema = schema_from_signature("(cmd, cwd?, timeout?) → (output)");
        let props = schema["properties"].as_object().unwrap();
        assert!(props.contains_key("cmd"));
        assert!(props.contains_key("cwd"));
        assert!(props.contains_key("timeout"));
        let required = schema["required"].as_array().unwrap();
        assert_eq!(required.len(), 1);
        assert_eq!(required[0], "cmd");
    }

    #[test]
    fn test_schema_from_signature_empty() {
        let schema = schema_from_signature("() → (state)");
        let props = schema["properties"].as_object().unwrap();
        assert!(props.is_empty());
        assert!(schema.get("required").is_none());
    }

    #[test]
    fn test_schema_from_signature_all_required() {
        let schema = schema_from_signature("(query, path) → (matches)");
        let required = schema["required"].as_array().unwrap();
        assert_eq!(required.len(), 2);
    }

    #[test]
    fn test_schema_type_inference() {
        assert_eq!(infer_type("timeout"), "integer");
        assert_eq!(infer_type("raw"), "boolean");
        assert_eq!(infer_type("query"), "string");
        assert_eq!(infer_type("cmd"), "string");
    }

    #[test]
    fn test_schema_from_signature_integer_params() {
        let schema = schema_from_signature("(alias, action, lines?) → (output)");
        let props = schema["properties"].as_object().unwrap();
        assert_eq!(props["lines"]["type"], "integer");
        assert_eq!(props["alias"]["type"], "string");
    }

    #[test]
    fn test_tool_definitions_from_language() {
        let entries = vec![
            LanguageEntry {
                verb: "boot".into(),
                tier: 1,
                layer: "kernel".into(),
                last_gen: 0,
                half_life: 0,
                momentum: 1.0,
                resolution: "internal".into(),
                signature: "() → (state)".into(),
                doc: "read state, report identity".into(),
                spec: String::new(),
            },
            LanguageEntry {
                verb: "low_momentum".into(),
                tier: 3,
                layer: "user".into(),
                last_gen: 0,
                half_life: 0,
                momentum: 0.1, // below threshold
                resolution: "ostk low".into(),
                signature: "() → ()".into(),
                doc: "low".into(),
                spec: String::new(),
            },
        ];

        let tools = tool_definitions_from_language(&entries, 0.45);
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0].name, "boot");
    }

    #[test]
    fn test_enriched_schema_interact_has_enum() {
        let schema = enriched_schema("interact").unwrap();
        let action = &schema["properties"]["action"];
        assert!(action["enum"].is_array(), "interact.action should have enum");
    }

    #[test]
    fn test_enriched_schema_unknown_returns_none() {
        assert!(enriched_schema("boot").is_none());
        assert!(enriched_schema("compile").is_none());
    }
}
