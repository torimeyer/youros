//! MCP transport — stdio JSON-RPC line protocol.
//!
//! Stdio JSON-RPC transport (originated in mish, ostk's experimental predecessor). Each JSON-RPC message is a single
//! line of JSON terminated by `\n`. Reads requests from an async reader
//! and writes responses to an async writer.

use std::fmt;
use std::sync::Arc;

use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncWrite, AsyncWriteExt};
use tokio::sync::Mutex as TokioMutex;

use crate::serve::types::{
    ERR_INVALID_REQUEST, ERR_PARSE_ERROR, JsonRpcError, JsonRpcRequest, JsonRpcResponse,
};

// ----- TransportError -----

#[derive(Debug)]
pub enum TransportError {
    IoError(std::io::Error),
    ParseError(String),
    Eof,
}

impl fmt::Display for TransportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            TransportError::IoError(e) => write!(f, "transport I/O error: {}", e),
            TransportError::ParseError(msg) => write!(f, "transport parse error: {}", msg),
            TransportError::Eof => write!(f, "transport EOF"),
        }
    }
}

impl std::error::Error for TransportError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            TransportError::IoError(e) => Some(e),
            _ => None,
        }
    }
}

impl From<std::io::Error> for TransportError {
    fn from(err: std::io::Error) -> Self {
        TransportError::IoError(err)
    }
}

// ----- StdioTransport -----

pub struct StdioTransport<R, W> {
    reader: R,
    writer: W,
}

impl Default for StdioTransport<tokio::io::BufReader<tokio::io::Stdin>, tokio::io::Stdout> {
    fn default() -> Self {
        StdioTransport {
            reader: tokio::io::BufReader::new(tokio::io::stdin()),
            writer: tokio::io::stdout(),
        }
    }
}

impl StdioTransport<tokio::io::BufReader<tokio::io::Stdin>, tokio::io::Stdout> {
    pub fn new() -> Self {
        Self::default()
    }
}

impl<R, W> StdioTransport<R, W> {
    pub fn with_io(reader: R, writer: W) -> Self {
        StdioTransport { reader, writer }
    }

    pub fn into_parts(self) -> (R, W) {
        (self.reader, self.writer)
    }
}

impl<R, W> StdioTransport<R, W>
where
    R: AsyncBufRead + Unpin,
    W: AsyncWrite + Unpin,
{
    pub async fn read_request(&mut self) -> Result<Option<JsonRpcRequest>, TransportError> {
        loop {
            let mut line = String::new();
            let bytes_read = self.reader.read_line(&mut line).await?;

            if bytes_read == 0 {
                return Ok(None);
            }

            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }

            let raw_value: serde_json::Value = match serde_json::from_str(trimmed) {
                Ok(v) => v,
                Err(e) => {
                    let error_resp = JsonRpcResponse {
                        jsonrpc: "2.0".to_string(),
                        id: serde_json::Value::Null,
                        result: None,
                        error: Some(JsonRpcError {
                            code: ERR_PARSE_ERROR,
                            message: format!("Parse error: {}", e),
                            data: None,
                        }),
                    };
                    self.write_response(error_resp).await?;
                    continue;
                }
            };

            let jsonrpc_field = raw_value.get("jsonrpc");
            let id_value = raw_value
                .get("id")
                .cloned()
                .unwrap_or(serde_json::Value::Null);

            match jsonrpc_field {
                Some(v) if v == "2.0" => {}
                _ => {
                    let error_resp = JsonRpcResponse {
                        jsonrpc: "2.0".to_string(),
                        id: id_value,
                        result: None,
                        error: Some(JsonRpcError {
                            code: ERR_INVALID_REQUEST,
                            message: "Invalid Request: jsonrpc field must be \"2.0\"".to_string(),
                            data: None,
                        }),
                    };
                    self.write_response(error_resp).await?;
                    continue;
                }
            }

            match serde_json::from_value::<JsonRpcRequest>(raw_value) {
                Ok(req) => return Ok(Some(req)),
                Err(e) => {
                    let error_resp = JsonRpcResponse {
                        jsonrpc: "2.0".to_string(),
                        id: id_value,
                        result: None,
                        error: Some(JsonRpcError {
                            code: ERR_INVALID_REQUEST,
                            message: format!("Invalid Request: {}", e),
                            data: None,
                        }),
                    };
                    self.write_response(error_resp).await?;
                    continue;
                }
            }
        }
    }

    pub async fn write_response(
        &mut self,
        response: JsonRpcResponse,
    ) -> Result<(), TransportError> {
        let json = serde_json::to_string(&response).map_err(|e| {
            TransportError::ParseError(format!("Failed to serialize response: {}", e))
        })?;
        self.writer.write_all(json.as_bytes()).await?;
        self.writer.write_all(b"\n").await?;
        self.writer.flush().await?;
        Ok(())
    }
}

// ----- SharedWriter -----

pub struct SharedWriter<W> {
    inner: Arc<TokioMutex<W>>,
}

impl<W> Clone for SharedWriter<W> {
    fn clone(&self) -> Self {
        SharedWriter {
            inner: Arc::clone(&self.inner),
        }
    }
}

impl<W> SharedWriter<W>
where
    W: AsyncWrite + Unpin,
{
    pub fn new(writer: W) -> Self {
        SharedWriter {
            inner: Arc::new(TokioMutex::new(writer)),
        }
    }

    pub async fn write_response(&self, response: JsonRpcResponse) -> Result<(), TransportError> {
        let json = serde_json::to_string(&response).map_err(|e| {
            TransportError::ParseError(format!("Failed to serialize response: {}", e))
        })?;
        let mut w = self.inner.lock().await;
        w.write_all(json.as_bytes()).await?;
        w.write_all(b"\n").await?;
        w.flush().await?;
        Ok(())
    }
}

// ----- TransportReader -----

pub struct TransportReader<R, W> {
    reader: R,
    writer: SharedWriter<W>,
}

impl<R, W> TransportReader<R, W>
where
    R: AsyncBufRead + Unpin,
    W: AsyncWrite + Unpin,
{
    pub async fn read_request(&mut self) -> Result<Option<JsonRpcRequest>, TransportError> {
        loop {
            let mut line = String::new();
            let bytes_read = self.reader.read_line(&mut line).await?;

            if bytes_read == 0 {
                return Ok(None);
            }

            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }

            let raw_value: serde_json::Value = match serde_json::from_str(trimmed) {
                Ok(v) => v,
                Err(e) => {
                    let error_resp = JsonRpcResponse {
                        jsonrpc: "2.0".to_string(),
                        id: serde_json::Value::Null,
                        result: None,
                        error: Some(JsonRpcError {
                            code: ERR_PARSE_ERROR,
                            message: format!("Parse error: {}", e),
                            data: None,
                        }),
                    };
                    self.writer.write_response(error_resp).await?;
                    continue;
                }
            };

            let jsonrpc_field = raw_value.get("jsonrpc");
            let id_value = raw_value
                .get("id")
                .cloned()
                .unwrap_or(serde_json::Value::Null);

            match jsonrpc_field {
                Some(v) if v == "2.0" => {}
                _ => {
                    let error_resp = JsonRpcResponse {
                        jsonrpc: "2.0".to_string(),
                        id: id_value,
                        result: None,
                        error: Some(JsonRpcError {
                            code: ERR_INVALID_REQUEST,
                            message: "Invalid Request: jsonrpc field must be \"2.0\"".to_string(),
                            data: None,
                        }),
                    };
                    self.writer.write_response(error_resp).await?;
                    continue;
                }
            }

            match serde_json::from_value::<JsonRpcRequest>(raw_value) {
                Ok(req) => return Ok(Some(req)),
                Err(e) => {
                    let error_resp = JsonRpcResponse {
                        jsonrpc: "2.0".to_string(),
                        id: id_value,
                        result: None,
                        error: Some(JsonRpcError {
                            code: ERR_INVALID_REQUEST,
                            message: format!("Invalid Request: {}", e),
                            data: None,
                        }),
                    };
                    self.writer.write_response(error_resp).await?;
                    continue;
                }
            }
        }
    }
}

// ----- into_split -----

impl<R, W> StdioTransport<R, W>
where
    W: AsyncWrite + Unpin,
{
    pub fn into_split(self) -> (TransportReader<R, W>, SharedWriter<W>) {
        let shared = SharedWriter::new(self.writer);
        let reader = TransportReader {
            reader: self.reader,
            writer: shared.clone(),
        };
        (reader, shared)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    type TestTransport =
        StdioTransport<tokio::io::BufReader<std::io::Cursor<Vec<u8>>>, Vec<u8>>;

    fn make_transport(input: &str) -> TestTransport {
        let reader = tokio::io::BufReader::new(std::io::Cursor::new(input.as_bytes().to_vec()));
        let writer = Vec::new();
        StdioTransport::with_io(reader, writer)
    }

    fn get_output(transport: TestTransport) -> String {
        let (_reader, writer) = transport.into_parts();
        String::from_utf8(writer).unwrap()
    }

    // ── TransportError display ──

    #[test]
    fn transport_error_display() {
        let io_err = TransportError::IoError(std::io::Error::new(std::io::ErrorKind::BrokenPipe, "broken"));
        assert!(io_err.to_string().contains("I/O error"));
        assert!(io_err.to_string().contains("broken"));

        let parse_err = TransportError::ParseError("bad json".to_string());
        assert!(parse_err.to_string().contains("parse error"));
        assert!(parse_err.to_string().contains("bad json"));

        let eof = TransportError::Eof;
        assert!(eof.to_string().contains("EOF"));
    }

    #[test]
    fn transport_error_source() {
        let io_err = TransportError::IoError(std::io::Error::other("test"));
        assert!(std::error::Error::source(&io_err).is_some());

        let parse_err = TransportError::ParseError("x".to_string());
        assert!(std::error::Error::source(&parse_err).is_none());

        let eof = TransportError::Eof;
        assert!(std::error::Error::source(&eof).is_none());
    }

    #[test]
    fn transport_error_from_io() {
        let io = std::io::Error::new(std::io::ErrorKind::NotFound, "not found");
        let te: TransportError = io.into();
        assert!(matches!(te, TransportError::IoError(_)));
    }

    // ── read_request: valid JSON-RPC ──

    #[tokio::test]
    async fn read_valid_request() {
        let input = r#"{"jsonrpc":"2.0","id":1,"method":"tools/list"}"#.to_owned() + "\n";
        let mut transport = make_transport(&input);
        let req = transport.read_request().await.unwrap().unwrap();
        assert_eq!(req.method, "tools/list");
        assert_eq!(req.id, serde_json::json!(1));
    }

    // ── read_request: EOF returns None ──

    #[tokio::test]
    async fn read_eof_returns_none() {
        let mut transport = make_transport("");
        let req = transport.read_request().await.unwrap();
        assert!(req.is_none());
    }

    // ── read_request: blank lines skipped ──

    #[tokio::test]
    async fn read_skips_blank_lines() {
        let input = "\n\n".to_owned()
            + r#"{"jsonrpc":"2.0","id":5,"method":"ping"}"#
            + "\n";
        let mut transport = make_transport(&input);
        let req = transport.read_request().await.unwrap().unwrap();
        assert_eq!(req.method, "ping");
        assert_eq!(req.id, serde_json::json!(5));
    }

    // ── read_request: invalid JSON writes parse error response ──

    #[tokio::test]
    async fn read_invalid_json_sends_parse_error() {
        // Bad JSON followed by EOF
        let input = "not valid json\n";
        let mut transport = make_transport(input);
        let req = transport.read_request().await.unwrap();
        // After parse error response is written, EOF returns None
        assert!(req.is_none());

        let output = get_output(transport);
        let resp: serde_json::Value = serde_json::from_str(output.trim()).unwrap();
        assert_eq!(resp["error"]["code"], ERR_PARSE_ERROR);
        assert!(resp["error"]["message"].as_str().unwrap().contains("Parse error"));
    }

    // ── read_request: missing jsonrpc field ──

    #[tokio::test]
    async fn read_missing_jsonrpc_field() {
        let input = r#"{"id":1,"method":"test"}"#.to_owned() + "\n";
        let mut transport = make_transport(&input);
        let req = transport.read_request().await.unwrap();
        assert!(req.is_none());

        let output = get_output(transport);
        let resp: serde_json::Value = serde_json::from_str(output.trim()).unwrap();
        assert_eq!(resp["error"]["code"], ERR_INVALID_REQUEST);
        assert!(resp["error"]["message"].as_str().unwrap().contains("jsonrpc"));
    }

    // ── read_request: wrong jsonrpc version ──

    #[tokio::test]
    async fn read_wrong_jsonrpc_version() {
        let input = r#"{"jsonrpc":"1.0","id":2,"method":"test"}"#.to_owned() + "\n";
        let mut transport = make_transport(&input);
        let req = transport.read_request().await.unwrap();
        assert!(req.is_none());

        let output = get_output(transport);
        let resp: serde_json::Value = serde_json::from_str(output.trim()).unwrap();
        assert_eq!(resp["error"]["code"], ERR_INVALID_REQUEST);
    }

    // ── read_request: valid JSON but missing method ──

    #[tokio::test]
    async fn read_missing_method() {
        let input = r#"{"jsonrpc":"2.0","id":3}"#.to_owned() + "\n";
        let mut transport = make_transport(&input);
        let req = transport.read_request().await.unwrap();
        assert!(req.is_none());

        let output = get_output(transport);
        let resp: serde_json::Value = serde_json::from_str(output.trim()).unwrap();
        assert_eq!(resp["error"]["code"], ERR_INVALID_REQUEST);
    }

    // ── write_response ──

    #[tokio::test]
    async fn write_response_formats_json_newline() {
        let mut transport = make_transport("");
        let response = JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(42),
            result: Some(serde_json::json!({"ok": true})),
            error: None,
        };
        transport.write_response(response).await.unwrap();

        let output = get_output(transport);
        assert!(output.ends_with('\n'));
        let parsed: serde_json::Value = serde_json::from_str(output.trim()).unwrap();
        assert_eq!(parsed["id"], 42);
        assert_eq!(parsed["result"]["ok"], true);
        // error should be absent (skip_serializing_if = "Option::is_none")
        assert!(parsed.get("error").is_none());
    }

    // ── into_parts roundtrip ──

    #[test]
    fn into_parts_roundtrip() {
        let transport = make_transport("test data\n");
        let (_, writer) = transport.into_parts();
        // Writer should be empty (nothing written)
        assert!(writer.is_empty());
    }

    // ── into_split produces reader + writer ──

    #[tokio::test]
    async fn into_split_read_and_write() {
        let input = r#"{"jsonrpc":"2.0","id":1,"method":"test"}"#.to_owned() + "\n";
        let transport = make_transport(&input);
        let (mut reader, writer) = transport.into_split();

        let req = reader.read_request().await.unwrap().unwrap();
        assert_eq!(req.method, "test");

        let response = JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id: serde_json::json!(1),
            result: Some(serde_json::json!("ok")),
            error: None,
        };
        writer.write_response(response).await.unwrap();
    }

    // ── SharedWriter clone ──

    #[tokio::test]
    async fn shared_writer_clone_writes_to_same_buffer() {
        let transport = make_transport("");
        let (_reader, writer) = transport.into_split();
        let writer2 = writer.clone();

        writer
            .write_response(JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id: serde_json::json!(1),
                result: Some(serde_json::json!("first")),
                error: None,
            })
            .await
            .unwrap();

        writer2
            .write_response(JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id: serde_json::json!(2),
                result: Some(serde_json::json!("second")),
                error: None,
            })
            .await
            .unwrap();
        // Both writes go to the same underlying buffer — no panic, no error
    }

    // ── TransportReader: parse error on bad JSON ──

    #[tokio::test]
    async fn transport_reader_parse_error() {
        let input = "garbage\n";
        let transport = make_transport(input);
        let (mut reader, _writer) = transport.into_split();

        // After parse error is written, EOF returns None
        let req = reader.read_request().await.unwrap();
        assert!(req.is_none());
    }

    // ── TransportReader: invalid jsonrpc version ──

    #[tokio::test]
    async fn transport_reader_invalid_jsonrpc() {
        let input = r#"{"jsonrpc":"3.0","id":1,"method":"x"}"#.to_owned() + "\n";
        let transport = make_transport(&input);
        let (mut reader, _writer) = transport.into_split();

        let req = reader.read_request().await.unwrap();
        assert!(req.is_none());
    }

    // ── TransportReader: missing method ──

    #[tokio::test]
    async fn transport_reader_missing_method() {
        let input = r#"{"jsonrpc":"2.0","id":1}"#.to_owned() + "\n";
        let transport = make_transport(&input);
        let (mut reader, _writer) = transport.into_split();

        let req = reader.read_request().await.unwrap();
        assert!(req.is_none());
    }
}
