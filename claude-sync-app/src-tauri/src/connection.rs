// ==========================================================================
// TCP connection management with length-prefixed framing
// All messages are encoded as: [4-byte big-endian length][JSON body]
// This framing format must match the macOS app for interoperability.
// ==========================================================================

use std::io;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

use crate::protocol::SyncMessage;

/// Maximum message size: 50 MB. Prevents OOM from malformed length prefixes.
const MAX_MESSAGE_SIZE: u32 = 50 * 1024 * 1024;

/// A framed TCP connection that sends and receives SyncMessage values
/// with 4-byte big-endian length-prefixed JSON encoding.
pub struct FramedConnection {
    stream: TcpStream,
}

impl FramedConnection {
    /// Create a new FramedConnection wrapping a TCP stream.
    pub fn new(stream: TcpStream) -> Self {
        Self { stream }
    }

    /// Connect to a remote peer at the given address.
    /// Returns a FramedConnection ready for message exchange.
    pub async fn connect(address: &str) -> Result<Self, io::Error> {
        let stream = TcpStream::connect(address).await?;
        Ok(Self { stream })
    }

    /// Send a SyncMessage to the remote peer.
    /// The message is serialized to JSON and prefixed with its length.
    pub async fn send(&mut self, message: &SyncMessage) -> Result<(), ConnectionError> {
        let json_bytes = serde_json::to_vec(message)
            .map_err(|e| ConnectionError::Serialization(e.to_string()))?;

        let len = json_bytes.len() as u32;
        if len > MAX_MESSAGE_SIZE {
            return Err(ConnectionError::MessageTooLarge(len));
        }

        // Write 4-byte big-endian length prefix
        let len_bytes = len.to_be_bytes();
        self.stream
            .write_all(&len_bytes)
            .await
            .map_err(|e| ConnectionError::Io(e.to_string()))?;

        // Write JSON body
        self.stream
            .write_all(&json_bytes)
            .await
            .map_err(|e| ConnectionError::Io(e.to_string()))?;

        self.stream
            .flush()
            .await
            .map_err(|e| ConnectionError::Io(e.to_string()))?;

        Ok(())
    }

    /// Receive a SyncMessage from the remote peer.
    /// Reads the 4-byte length prefix, then the JSON body.
    pub async fn receive(&mut self) -> Result<SyncMessage, ConnectionError> {
        // Read 4-byte big-endian length prefix
        let mut len_buf = [0u8; 4];
        self.stream
            .read_exact(&mut len_buf)
            .await
            .map_err(|e| ConnectionError::Io(e.to_string()))?;

        let len = u32::from_be_bytes(len_buf);

        if len > MAX_MESSAGE_SIZE {
            return Err(ConnectionError::MessageTooLarge(len));
        }

        if len == 0 {
            return Err(ConnectionError::EmptyMessage);
        }

        // Read JSON body
        let mut body_buf = vec![0u8; len as usize];
        self.stream
            .read_exact(&mut body_buf)
            .await
            .map_err(|e| ConnectionError::Io(e.to_string()))?;

        // Deserialize the JSON
        let message: SyncMessage = serde_json::from_slice(&body_buf)
            .map_err(|e| ConnectionError::Deserialization(e.to_string()))?;

        Ok(message)
    }

    /// Shutdown the connection gracefully.
    pub async fn shutdown(&mut self) -> Result<(), io::Error> {
        self.stream.shutdown().await
    }
}

/// Errors that can occur during connection operations.
#[derive(Debug)]
pub enum ConnectionError {
    /// I/O error on the TCP stream
    Io(String),
    /// Failed to serialize a message to JSON
    Serialization(String),
    /// Failed to deserialize a message from JSON
    Deserialization(String),
    /// Message exceeds the maximum allowed size
    MessageTooLarge(u32),
    /// Received a zero-length message
    EmptyMessage,
    /// Protocol error (unexpected message type, version mismatch, etc.)
    Protocol(String),
}

impl std::fmt::Display for ConnectionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConnectionError::Io(msg) => write!(f, "I/O error: {}", msg),
            ConnectionError::Serialization(msg) => write!(f, "Serialization error: {}", msg),
            ConnectionError::Deserialization(msg) => write!(f, "Deserialization error: {}", msg),
            ConnectionError::MessageTooLarge(size) => {
                write!(f, "Message too large: {} bytes (max {})", size, MAX_MESSAGE_SIZE)
            }
            ConnectionError::EmptyMessage => write!(f, "Received empty message"),
            ConnectionError::Protocol(msg) => write!(f, "Protocol error: {}", msg),
        }
    }
}

impl std::error::Error for ConnectionError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_connection_error_display() {
        let err = ConnectionError::Io("connection refused".to_string());
        assert!(err.to_string().contains("connection refused"));

        let err = ConnectionError::MessageTooLarge(999999999);
        assert!(err.to_string().contains("999999999"));

        let err = ConnectionError::Protocol("version mismatch".to_string());
        assert!(err.to_string().contains("version mismatch"));
    }
}
