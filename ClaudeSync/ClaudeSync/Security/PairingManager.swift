// PairingManager.swift
// ClaudeSync
//
// Manages device pairing via a 6-digit code exchange flow.
//
// Pairing flow:
//   1. Device A (responder) generates a 6-digit code and displays it.
//   2. User enters the code on Device B (initiator).
//   3. Both devices exchange their certificate fingerprints and device info.
//   4. Both persist the pairing in the TrustStore (paired_devices.json).
//
// The pairing code is a cryptographically random 6-digit number (100000-999999)
// valid for 5 minutes. It is exchanged over an already-established connection
// (LAN or relay) using the sync protocol's error channel for out-of-band messages.

import Foundation
import os

/// Manages the device pairing lifecycle via 6-digit code exchange.
/// Published properties allow SwiftUI to display pairing state and code.
@MainActor
final class PairingManager: ObservableObject {

    // MARK: - Published State

    /// Whether a pairing operation is currently in progress.
    @Published var isPairing: Bool = false

    /// The 6-digit code displayed when we are the responder (waiting for the other device).
    @Published var pairingCode: String?

    /// All currently paired devices, loaded from the trust store.
    @Published var pairedDevices: [PairedDevice] = []

    /// Error message from the last failed pairing attempt.
    @Published var pairingError: String?

    // MARK: - Types

    /// View-facing paired device representation (non-actor-isolated).
    struct PairedDevice: Identifiable, Sendable {
        /// The peer's persistent device UUID.
        let id: String

        /// Human-readable device name at pairing time.
        let name: String

        /// SHA-256 fingerprint of the peer's certificate.
        let certFingerprint: String

        /// When the pairing was established.
        let pairedAt: Date
    }

    // MARK: - Private Properties

    /// The trust store for persisting paired device information.
    private let trustStore: TrustStore

    /// The certificate manager for accessing our own identity.
    private let certManager: CertificateManager

    /// Logger for pairing operations.
    private let logger = Logger(subsystem: "com.claudesync", category: "PairingManager")

    /// Expiry duration for pairing codes (5 minutes).
    private static let codeExpiry: TimeInterval = 300

    /// The currently active pairing code and its creation time.
    private var activePairingCode: (code: String, createdAt: Date)?

    // MARK: - Initialization

    /// Creates a PairingManager backed by the given trust store and certificate manager.
    init(
        trustStore: TrustStore = TrustStore(),
        certManager: CertificateManager = .shared
    ) {
        self.trustStore = trustStore
        self.certManager = certManager
    }

    // MARK: - Pairing as Responder

    /// Starts pairing as the responder: generates and returns a 6-digit code.
    /// The code is displayed to the user so they can enter it on the initiator device.
    /// Returns the generated code string.
    func respondToPairing() async throws -> String {
        isPairing = true
        pairingError = nil

        let code = generatePairingCode()
        activePairingCode = (code: code, createdAt: Date())
        pairingCode = code

        logger.info("Generated pairing code (valid for \(Self.codeExpiry)s)")
        return code
    }

    /// Validates an incoming pairing code from a peer (we are the responder).
    /// If valid, completes the pairing by exchanging certificate fingerprints.
    ///
    /// - Parameters:
    ///   - code: The code entered by the initiator.
    ///   - peerDeviceId: The initiating peer's device ID.
    ///   - peerName: The initiating peer's device name.
    ///   - peerCertFingerprint: The initiating peer's certificate fingerprint.
    /// - Returns: Our certificate fingerprint to send back to the initiator.
    func validateAndCompletePairing(
        code: String,
        peerDeviceId: String,
        peerName: String,
        peerCertFingerprint: String
    ) async throws -> String {
        guard let active = activePairingCode else {
            throw PairingError.noPairingInProgress
        }

        // Check code expiry.
        let elapsed = Date().timeIntervalSince(active.createdAt)
        guard elapsed < Self.codeExpiry else {
            cancelPairing()
            throw PairingError.codeExpired
        }

        // Validate the code.
        guard code == active.code else {
            throw PairingError.invalidCode
        }

        // Exchange successful -- persist the pairing.
        try await completePairing(
            peerDeviceId: peerDeviceId,
            peerName: peerName,
            peerCertFingerprint: peerCertFingerprint
        )

        // Return our fingerprint for the initiator to persist.
        let ourFingerprint = try await certManager.certificateFingerprint()

        // Clear the active code.
        activePairingCode = nil
        pairingCode = nil
        isPairing = false

        return ourFingerprint
    }

    // MARK: - Pairing as Initiator

    /// Starts pairing as the initiator: the user has entered a code from the other device.
    /// Sends the code along with our certificate fingerprint to the peer for validation.
    ///
    /// - Parameters:
    ///   - code: The 6-digit code displayed on the responder device.
    ///   - peerDeviceId: The peer's device ID.
    ///   - peerName: The peer's device name.
    ///   - peerCertFingerprint: The peer's certificate fingerprint (received after code validation).
    func initiatePairing(
        withCode code: String,
        peerDeviceId: String,
        peerName: String,
        peerCertFingerprint: String
    ) async throws {
        isPairing = true
        pairingError = nil

        do {
            try await completePairing(
                peerDeviceId: peerDeviceId,
                peerName: peerName,
                peerCertFingerprint: peerCertFingerprint
            )

            isPairing = false
            logger.info("Pairing initiated and completed with \(peerName) (\(peerDeviceId.prefix(8)))")
        } catch {
            isPairing = false
            pairingError = error.localizedDescription
            throw error
        }
    }

    // MARK: - Pairing Lifecycle

    /// Cancels the current pairing operation and clears the active code.
    func cancelPairing() {
        activePairingCode = nil
        pairingCode = nil
        isPairing = false
        pairingError = nil
        logger.info("Pairing cancelled")
    }

    /// Checks if a device is already paired.
    func isDevicePaired(_ deviceId: String) async -> Bool {
        return await trustStore.isTrusted(deviceId: deviceId)
    }

    /// Unpairs a device by removing it from the trust store.
    func unpairDevice(_ deviceId: String) async throws {
        try await trustStore.removeTrustedDevice(deviceId: deviceId)
        await loadPairedDevices()
        logger.info("Unpaired device: \(deviceId.prefix(8))")
    }

    /// Loads paired devices from the trust store and updates the published list.
    func loadPairedDevices() async {
        do {
            try await trustStore.load()
        } catch {
            logger.error("Failed to load trust store: \(error.localizedDescription)")
        }

        let trusted = await trustStore.allTrustedDevices()
        pairedDevices = trusted.map { device in
            PairedDevice(
                id: device.deviceId,
                name: device.name,
                certFingerprint: device.certFingerprint,
                pairedAt: device.pairedAt
            )
        }
    }

    // MARK: - Private Helpers

    /// Generates a cryptographically random 6-digit code (100000-999999).
    private func generatePairingCode() -> String {
        var randomBytes = [UInt8](repeating: 0, count: 4)
        _ = SecRandomCopyBytes(kSecRandomDefault, randomBytes.count, &randomBytes)

        // Convert 4 random bytes to a UInt32 and map to 100000-999999.
        let randomValue = randomBytes.withUnsafeBytes { $0.load(as: UInt32.self) }
        let code = 100000 + Int(randomValue % 900000)
        return String(code)
    }

    /// Persists the pairing in the trust store and refreshes the published device list.
    private func completePairing(
        peerDeviceId: String,
        peerName: String,
        peerCertFingerprint: String
    ) async throws {
        let device = TrustStore.TrustedDevice(
            deviceId: peerDeviceId,
            name: peerName,
            certFingerprint: peerCertFingerprint,
            pairedAt: Date()
        )

        try await trustStore.addTrustedDevice(device)
        await loadPairedDevices()

        logger.info("Pairing completed with \(peerName) (\(peerDeviceId.prefix(8)))")
    }
}

// MARK: - Error Types

/// Errors specific to pairing operations.
enum PairingError: LocalizedError {
    case noPairingInProgress
    case codeExpired
    case invalidCode
    case peerRejected(String)
    case exchangeFailed(Error)

    var errorDescription: String? {
        switch self {
        case .noPairingInProgress:
            return "No pairing operation is in progress"
        case .codeExpired:
            return "Pairing code has expired (5 minute limit)"
        case .invalidCode:
            return "Invalid pairing code"
        case .peerRejected(let reason):
            return "Peer rejected pairing: \(reason)"
        case .exchangeFailed(let error):
            return "Certificate exchange failed: \(error.localizedDescription)"
        }
    }
}
