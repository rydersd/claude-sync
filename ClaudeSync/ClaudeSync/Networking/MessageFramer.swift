// MessageFramer.swift
// ClaudeSync
//
// NWProtocolFramer implementation for length-prefixed JSON messages over TCP.
// Each message on the wire is:
//   [4 bytes: big-endian UInt32 payload length] [N bytes: JSON payload]
//
// This handles TCP stream reassembly so higher layers receive complete
// JSON messages without worrying about partial reads or message boundaries.

import Foundation
import Network

/// Unique label identifying this framing protocol within Network.framework.
/// Used when creating NWProtocolFramer.Options.
let claudeSyncFramerLabel = "ClaudeSyncFramer"

/// The framing protocol definition, used to create NWProtocolFramer.Options.
class ClaudeSyncProtocol: NWProtocolFramerImplementation {
    /// Protocol label for identification.
    static let label = claudeSyncFramerLabel

    /// The 4-byte header that precedes every message on the wire.
    static let headerLength = 4

    /// Maximum allowed message size (16 MB) to prevent memory exhaustion.
    static let maxMessageSize: UInt32 = 16 * 1024 * 1024

    /// Definition of this protocol for use with NWParameters.
    static let definition = NWProtocolFramer.Definition(implementation: ClaudeSyncProtocol.self)

    required init(framer: NWProtocolFramer.Instance) {
        // No state needed at initialization.
    }

    /// Called when the framer starts. Signals readiness immediately since
    /// this is a simple framing protocol with no handshake.
    func start(framer: NWProtocolFramer.Instance) -> NWProtocolFramer.StartResult {
        return .ready
    }

    /// Called when the connection is being torn down.
    func stop(framer: NWProtocolFramer.Instance) -> Bool {
        return true
    }

    /// No additional wakeup logic needed.
    func wakeup(framer: NWProtocolFramer.Instance) {
        // Nothing to do.
    }

    /// Called when the framer is being cleaned up.
    func cleanup(framer: NWProtocolFramer.Instance) {
        // No cleanup needed.
    }

    /// Handles outgoing messages. Prepends the 4-byte length header
    /// to the message data before passing it to the wire.
    func handleOutput(
        framer: NWProtocolFramer.Instance,
        message: NWProtocolFramer.Message,
        messageLength: Int,
        isComplete: Bool
    ) {
        // Write the 4-byte big-endian length header.
        var length = UInt32(messageLength).bigEndian
        let headerData = withUnsafeBytes(of: &length) { Data($0) }

        // Write header first, then let the framer write the actual message content.
        framer.writeOutput(data: headerData)

        do {
            try framer.writeOutputNoCopy(length: messageLength)
        } catch {
            // If writeOutputNoCopy fails, the connection will be torn down
            // by Network.framework. No recovery action needed here.
        }
    }

    /// Handles incoming data. Parses the 4-byte length header, then
    /// waits for the full payload before delivering the message.
    func handleInput(framer: NWProtocolFramer.Instance) -> Int {
        // Loop to handle multiple messages that may have arrived in a single TCP segment.
        while true {
            // Attempt to parse the 4-byte header to learn the payload length.
            var parsedHeader = false
            var payloadLength: UInt32 = 0

            let headerParseResult = framer.parseInput(
                minimumIncompleteLength: Self.headerLength,
                maximumLength: Self.headerLength
            ) { buffer, isComplete in
                guard let buffer = buffer, buffer.count >= Self.headerLength else {
                    return 0
                }

                // Read 4 bytes as big-endian UInt32.
                let rawLength = buffer.loadUnaligned(as: UInt32.self)
                payloadLength = UInt32(bigEndian: rawLength)
                parsedHeader = true
                return Self.headerLength
            }

            // If we could not parse a complete header, tell the framer
            // how many more bytes we need before trying again.
            guard headerParseResult, parsedHeader else {
                return Self.headerLength
            }

            // Validate payload length to prevent memory attacks.
            guard payloadLength > 0, payloadLength <= Self.maxMessageSize else {
                // Invalid length -- this is a protocol error. Deliver an empty
                // message so the connection handler can detect the problem.
                let message = NWProtocolFramer.Message(definition: Self.definition)
                _ = framer.deliverInputNoCopy(length: 0, message: message, isComplete: true)
                return 0
            }

            // Now parse the payload itself.
            let totalExpected = Int(payloadLength)
            let message = NWProtocolFramer.Message(definition: Self.definition)

            // deliverInputNoCopy will consume exactly `totalExpected` bytes
            // from the input buffer and deliver them as a complete message.
            guard framer.deliverInputNoCopy(
                length: totalExpected,
                message: message,
                isComplete: true
            ) else {
                // Not enough data yet; tell the framer we need more.
                return Self.headerLength + totalExpected
            }

            // Successfully delivered one message; loop to check for more.
        }
    }
}

// MARK: - NWProtocolFramer.Message convenience

extension NWProtocolFramer.Message {
    /// Convenience initializer for creating a message with the ClaudeSync framing protocol.
    convenience init(claudeSyncMessage: SyncMessage) {
        self.init(definition: ClaudeSyncProtocol.definition)
    }
}

// MARK: - NWParameters extension

extension NWParameters {
    /// Creates NWParameters configured with TCP and the ClaudeSync framing protocol.
    /// This is the standard parameter set for all ClaudeSync connections.
    static var claudeSync: NWParameters {
        let tcpOptions = NWProtocolTCP.Options()
        // Enable keepalive to detect dead peers promptly.
        tcpOptions.enableKeepalive = true
        tcpOptions.keepaliveIdle = 30
        tcpOptions.keepaliveInterval = 10
        tcpOptions.keepaliveCount = 3
        // Disable Nagle's algorithm for lower latency on small messages.
        tcpOptions.noDelay = true

        let params = NWParameters(tls: nil, tcp: tcpOptions)

        // Insert the framing protocol on top of TCP.
        let framerOptions = NWProtocolFramer.Options(definition: ClaudeSyncProtocol.definition)
        params.defaultProtocolStack.applicationProtocols.insert(framerOptions, at: 0)

        return params
    }
}
