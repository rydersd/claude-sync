// CertificateManager.swift
// ClaudeSync
//
// Manages the device TLS identity for WAN connections.
// Generates an ECDSA P-256 keypair and a self-signed X.509 certificate on
// first launch, storing them in the macOS Keychain. The certificate fingerprint
// (SHA-256 of the DER-encoded cert) is used as the trust anchor during pairing.
//
// Architecture decision: P-256 via SecKey instead of Ed25519 because
// SecIdentity + NWProtocolTLS.Options require Keychain-backed keys, and
// macOS Keychain only supports RSA/ECDSA for certificate operations.
// The public API (fingerprint, export DER) is key-type agnostic so a
// future migration to Ed25519 would not change callers.

import Foundation
import Security
import CryptoKit
import os

/// Manages device TLS identity for WAN connections.
/// Generates a P-256 keypair + self-signed X.509 cert on first launch.
/// Stores the identity in the macOS Keychain under a unique tag.
actor CertificateManager {

    // MARK: - Singleton

    static let shared = CertificateManager()

    // MARK: - Properties

    /// Logger for certificate operations.
    private let logger = Logger(subsystem: "com.claudesync", category: "CertificateManager")

    /// Directory for fallback file-based cert storage.
    private let storePath: URL

    /// Tag used to identify the private key in the Keychain.
    private static let keychainTag = "com.claudesync.device-identity"

    /// Label used for the Keychain identity.
    private static let keychainLabel = "ClaudeSync Device Identity"

    /// Cached identity to avoid repeated Keychain lookups.
    private var cachedIdentity: SecIdentity?

    /// Cached certificate DER data.
    private var cachedCertDER: Data?

    // MARK: - Initialization

    private init() {
        let appSupport = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first!
        storePath = appSupport.appendingPathComponent("claude-sync")
    }

    // MARK: - Public API

    /// Load existing identity or generate a new one.
    /// Returns a SecIdentity suitable for use with NWProtocolTLS.Options.
    func getOrCreateIdentity() async throws -> SecIdentity {
        // Return cached identity if available.
        if let cached = cachedIdentity {
            return cached
        }

        // Try to load from Keychain.
        if let existing = try loadIdentityFromKeychain() {
            cachedIdentity = existing
            return existing
        }

        // Generate a new identity.
        let identity = try generateAndStoreIdentity()
        cachedIdentity = identity
        return identity
    }

    /// Get the certificate fingerprint (SHA-256 of the DER-encoded certificate).
    /// Returns a lowercase hex string (64 characters).
    func certificateFingerprint() async throws -> String {
        let derData = try await exportCertificateDER()
        let digest = SHA256.hash(data: derData)
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    /// Export the certificate as DER data (for sharing during pairing).
    func exportCertificateDER() async throws -> Data {
        if let cached = cachedCertDER {
            return cached
        }

        let identity = try await getOrCreateIdentity()

        var certRef: SecCertificate?
        let status = SecIdentityCopyCertificate(identity, &certRef)
        guard status == errSecSuccess, let cert = certRef else {
            throw CertificateError.failedToExportCertificate(status)
        }

        let derData = SecCertificateCopyData(cert) as Data
        cachedCertDER = derData
        return derData
    }

    // MARK: - Keychain Operations

    /// Loads an existing identity from the Keychain.
    private func loadIdentityFromKeychain() throws -> SecIdentity? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassIdentity,
            kSecAttrLabel as String: Self.keychainLabel,
            kSecReturnRef as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]

        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)

        if status == errSecItemNotFound {
            return nil
        }

        guard status == errSecSuccess else {
            logger.warning("Keychain lookup failed: \(status)")
            return nil
        }

        // result is a SecIdentity
        let identity = result as! SecIdentity
        logger.info("Loaded existing TLS identity from Keychain")
        return identity
    }

    /// Generates a new P-256 keypair, creates a self-signed X.509 certificate,
    /// and stores the identity in the Keychain.
    private func generateAndStoreIdentity() throws -> SecIdentity {
        // Step 1: Generate a P-256 private key in the Keychain.
        let keyTag = Self.keychainTag.data(using: .utf8)!

        // Remove any existing key with this tag to avoid duplicates.
        let deleteQuery: [String: Any] = [
            kSecClass as String: kSecClassKey,
            kSecAttrApplicationTag as String: keyTag,
        ]
        SecItemDelete(deleteQuery as CFDictionary)

        let keyAttributes: [String: Any] = [
            kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeySizeInBits as String: 256,
            kSecAttrApplicationTag as String: keyTag,
            kSecAttrIsPermanent as String: true,
            kSecAttrLabel as String: Self.keychainLabel + " Key",
            kSecPrivateKeyAttrs as String: [
                kSecAttrIsPermanent as String: true,
                kSecAttrApplicationTag as String: keyTag,
            ],
        ]

        var error: Unmanaged<CFError>?
        guard let privateKey = SecKeyCreateRandomKey(keyAttributes as CFDictionary, &error) else {
            let cfError = error?.takeRetainedValue()
            throw CertificateError.keyGenerationFailed(cfError as Error?)
        }

        guard let publicKey = SecKeyCopyPublicKey(privateKey) else {
            throw CertificateError.keyGenerationFailed(nil)
        }

        // Step 2: Build a self-signed X.509 certificate (DER-encoded).
        let certDER = try buildSelfSignedCertificate(
            publicKey: publicKey,
            privateKey: privateKey,
            commonName: "ClaudeSync-\(DeviceIdentity.deviceId.prefix(8))",
            validityYears: 10
        )

        // Step 3: Import the certificate into the Keychain.
        let certRef = SecCertificateCreateWithData(nil, certDER as CFData)
        guard let certificate = certRef else {
            throw CertificateError.invalidCertificateData
        }

        // Add the certificate to the Keychain.
        let certAddQuery: [String: Any] = [
            kSecClass as String: kSecClassCertificate,
            kSecValueRef as String: certificate,
            kSecAttrLabel as String: Self.keychainLabel,
        ]
        let certAddStatus = SecItemAdd(certAddQuery as CFDictionary, nil)
        guard certAddStatus == errSecSuccess || certAddStatus == errSecDuplicateItem else {
            throw CertificateError.keychainStoreFailed(certAddStatus)
        }

        // Step 4: Retrieve the identity (private key + certificate pair).
        // The Keychain associates them by the public key hash automatically.
        let identityQuery: [String: Any] = [
            kSecClass as String: kSecClassIdentity,
            kSecAttrLabel as String: Self.keychainLabel,
            kSecReturnRef as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]

        var identityResult: CFTypeRef?
        let identityStatus = SecItemCopyMatching(identityQuery as CFDictionary, &identityResult)

        guard identityStatus == errSecSuccess, let identity = identityResult else {
            // Fallback: try matching by key tag instead of label.
            let fallbackQuery: [String: Any] = [
                kSecClass as String: kSecClassIdentity,
                kSecAttrApplicationTag as String: keyTag,
                kSecReturnRef as String: true,
                kSecMatchLimit as String: kSecMatchLimitOne,
            ]
            var fallbackResult: CFTypeRef?
            let fallbackStatus = SecItemCopyMatching(fallbackQuery as CFDictionary, &fallbackResult)

            guard fallbackStatus == errSecSuccess, let fbIdentity = fallbackResult else {
                throw CertificateError.identityNotFoundAfterCreation(fallbackStatus)
            }

            cachedCertDER = certDER
            logger.info("Generated and stored new TLS identity (fallback lookup)")
            return fbIdentity as! SecIdentity
        }

        cachedCertDER = certDER
        logger.info("Generated and stored new TLS identity in Keychain")
        return identity as! SecIdentity
    }

    // MARK: - X.509 Certificate Builder

    /// Builds a self-signed X.509 v3 certificate in DER format.
    /// Uses manual ASN.1/DER encoding to construct the TBSCertificate,
    /// then signs it with the private key using ECDSA-SHA256.
    private func buildSelfSignedCertificate(
        publicKey: SecKey,
        privateKey: SecKey,
        commonName: String,
        validityYears: Int
    ) throws -> Data {
        // Extract the public key raw data (X9.63 format for EC keys).
        guard let pubKeyData = SecKeyCopyExternalRepresentation(publicKey, nil) as Data? else {
            throw CertificateError.failedToExportPublicKey
        }

        let now = Date()
        let notBefore = now
        let notAfter = Calendar.current.date(byAdding: .year, value: validityYears, to: now)!

        // Build the TBSCertificate structure.
        let tbsCert = buildTBSCertificate(
            serialNumber: generateSerialNumber(),
            commonName: commonName,
            notBefore: notBefore,
            notAfter: notAfter,
            publicKeyData: pubKeyData
        )

        // Sign the TBSCertificate.
        let tbsData = Data(tbsCert)
        let signatureAlgorithmOID = DER.ecdsaWithSHA256OID

        guard SecKeyIsAlgorithmSupported(privateKey, .sign, .ecdsaSignatureMessageX962SHA256) else {
            throw CertificateError.unsupportedSigningAlgorithm
        }

        var signError: Unmanaged<CFError>?
        guard let signatureData = SecKeyCreateSignature(
            privateKey,
            .ecdsaSignatureMessageX962SHA256,
            tbsData as CFData,
            &signError
        ) as Data? else {
            throw CertificateError.signingFailed(signError?.takeRetainedValue())
        }

        // Assemble the full X.509 Certificate structure:
        // SEQUENCE { tbsCertificate, signatureAlgorithm, signatureValue }
        var certBody = Data()
        certBody.append(contentsOf: tbsCert)
        certBody.append(contentsOf: DER.sequence(signatureAlgorithmOID))
        certBody.append(contentsOf: DER.bitString(signatureData))

        return Data(DER.sequence(Array(certBody)))
    }

    /// Builds the TBSCertificate DER structure.
    private func buildTBSCertificate(
        serialNumber: [UInt8],
        commonName: String,
        notBefore: Date,
        notAfter: Date,
        publicKeyData: Data
    ) -> [UInt8] {
        var body: [UInt8] = []

        // Version: v3 (explicit tag [0] with INTEGER 2)
        let versionInt = DER.integer([0x02]) // v3
        let versionExplicit = DER.contextTag(0, constructed: true, content: versionInt)
        body.append(contentsOf: versionExplicit)

        // Serial number
        body.append(contentsOf: DER.integerTag(serialNumber))

        // Signature algorithm: ecdsaWithSHA256
        body.append(contentsOf: DER.sequence(DER.ecdsaWithSHA256OID))

        // Issuer: CN=commonName (self-signed, so issuer == subject)
        let issuerRDN = buildDistinguishedName(commonName: commonName)
        body.append(contentsOf: DER.sequence(issuerRDN))

        // Validity
        let validity = buildValidity(notBefore: notBefore, notAfter: notAfter)
        body.append(contentsOf: DER.sequence(validity))

        // Subject: CN=commonName
        body.append(contentsOf: DER.sequence(issuerRDN))

        // SubjectPublicKeyInfo for EC P-256
        let spki = buildSubjectPublicKeyInfo(publicKeyData: publicKeyData)
        body.append(contentsOf: DER.sequence(spki))

        return DER.sequence(body)
    }

    /// Builds a distinguished name with a single CN attribute.
    private func buildDistinguishedName(commonName: String) -> [UInt8] {
        // SET { SEQUENCE { OID(CN), UTF8String(commonName) } }
        let cnOID: [UInt8] = [0x06, 0x03, 0x55, 0x04, 0x03] // 2.5.4.3
        let cnValue = DER.utf8String(commonName)
        let attrSeq = DER.sequence(cnOID + cnValue)
        let attrSet = DER.set(attrSeq)
        return attrSet
    }

    /// Builds the Validity structure with UTCTime dates.
    private func buildValidity(notBefore: Date, notAfter: Date) -> [UInt8] {
        return DER.utcTime(notBefore) + DER.utcTime(notAfter)
    }

    /// Builds the SubjectPublicKeyInfo for an EC P-256 key.
    private func buildSubjectPublicKeyInfo(publicKeyData: Data) -> [UInt8] {
        // AlgorithmIdentifier: ecPublicKey with namedCurve prime256v1
        let ecPublicKeyOID: [UInt8] = [0x06, 0x07, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x02, 0x01] // 1.2.840.10045.2.1
        let prime256v1OID: [UInt8] = [0x06, 0x08, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07] // 1.2.840.10045.3.1.7
        let algorithmId = DER.sequence(ecPublicKeyOID + prime256v1OID)

        // SubjectPublicKey as BIT STRING (the X9.63 encoded EC point)
        let publicKeyBits = DER.bitString(Data(publicKeyData))

        return Array(algorithmId) + publicKeyBits
    }

    /// Generates a random 16-byte serial number for the certificate.
    private func generateSerialNumber() -> [UInt8] {
        var bytes = [UInt8](repeating: 0, count: 16)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        // Ensure the first byte's high bit is 0 so it's a positive INTEGER.
        bytes[0] &= 0x7F
        // Ensure it's not zero.
        if bytes.allSatisfy({ $0 == 0 }) {
            bytes[0] = 0x01
        }
        return bytes
    }
}

// MARK: - DER Encoding Helpers

/// Lightweight DER/ASN.1 encoding utilities for constructing X.509 certificates.
/// Not a general-purpose ASN.1 library -- only supports the subset needed
/// for self-signed certificates with EC keys.
private enum DER {

    /// OID for ecdsaWithSHA256 (1.2.840.10045.4.3.2)
    static let ecdsaWithSHA256OID: [UInt8] = [
        0x06, 0x08, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x04, 0x03, 0x02,
    ]

    /// Encodes a SEQUENCE (tag 0x30).
    static func sequence(_ content: [UInt8]) -> [UInt8] {
        return [0x30] + lengthBytes(content.count) + content
    }

    /// Encodes a SET (tag 0x31).
    static func set(_ content: [UInt8]) -> [UInt8] {
        return [0x31] + lengthBytes(content.count) + content
    }

    /// Encodes an INTEGER (tag 0x02) from raw bytes.
    static func integerTag(_ bytes: [UInt8]) -> [UInt8] {
        return [0x02] + lengthBytes(bytes.count) + bytes
    }

    /// Encodes a small INTEGER (tag 0x02) from a single-byte value.
    static func integer(_ value: [UInt8]) -> [UInt8] {
        return [0x02] + lengthBytes(value.count) + value
    }

    /// Encodes a BIT STRING (tag 0x03). Prepends a 0x00 unused-bits byte.
    static func bitString(_ data: Data) -> [UInt8] {
        let content = [UInt8(0x00)] + Array(data)
        return [0x03] + lengthBytes(content.count) + content
    }

    /// Encodes a UTF8String (tag 0x0C).
    static func utf8String(_ string: String) -> [UInt8] {
        let bytes = Array(string.utf8)
        return [0x0C] + lengthBytes(bytes.count) + bytes
    }

    /// Encodes a UTCTime (tag 0x17) from a Date.
    /// Format: YYMMDDHHMMSSZ
    static func utcTime(_ date: Date) -> [UInt8] {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyMMddHHmmss"
        formatter.timeZone = TimeZone(identifier: "UTC")
        let timeString = formatter.string(from: date) + "Z"
        let bytes = Array(timeString.utf8)
        return [0x17] + lengthBytes(bytes.count) + bytes
    }

    /// Encodes a context-specific tag (e.g., [0] EXPLICIT).
    static func contextTag(_ tag: UInt8, constructed: Bool, content: [UInt8]) -> [UInt8] {
        let classBits: UInt8 = 0x80 // context-specific
        let constructedBit: UInt8 = constructed ? 0x20 : 0x00
        let tagByte = classBits | constructedBit | (tag & 0x1F)
        return [tagByte] + lengthBytes(content.count) + content
    }

    /// Encodes the DER length field.
    /// Short form for lengths 0-127, long form otherwise.
    static func lengthBytes(_ length: Int) -> [UInt8] {
        if length < 0x80 {
            return [UInt8(length)]
        } else if length < 0x100 {
            return [0x81, UInt8(length)]
        } else if length < 0x10000 {
            return [0x82, UInt8(length >> 8), UInt8(length & 0xFF)]
        } else if length < 0x1000000 {
            return [0x83, UInt8(length >> 16), UInt8((length >> 8) & 0xFF), UInt8(length & 0xFF)]
        } else {
            return [
                0x84,
                UInt8(length >> 24),
                UInt8((length >> 16) & 0xFF),
                UInt8((length >> 8) & 0xFF),
                UInt8(length & 0xFF),
            ]
        }
    }
}

// MARK: - Error Types

/// Errors specific to certificate management operations.
enum CertificateError: LocalizedError {
    case keyGenerationFailed(Error?)
    case failedToExportPublicKey
    case failedToExportCertificate(OSStatus)
    case invalidCertificateData
    case keychainStoreFailed(OSStatus)
    case identityNotFoundAfterCreation(OSStatus)
    case unsupportedSigningAlgorithm
    case signingFailed(Error?)

    var errorDescription: String? {
        switch self {
        case .keyGenerationFailed(let error):
            return "Failed to generate keypair: \(error?.localizedDescription ?? "unknown")"
        case .failedToExportPublicKey:
            return "Failed to export public key data"
        case .failedToExportCertificate(let status):
            return "Failed to export certificate: OSStatus \(status)"
        case .invalidCertificateData:
            return "Generated certificate data is invalid"
        case .keychainStoreFailed(let status):
            return "Failed to store in Keychain: OSStatus \(status)"
        case .identityNotFoundAfterCreation(let status):
            return "Identity not found after creation: OSStatus \(status)"
        case .unsupportedSigningAlgorithm:
            return "ECDSA-SHA256 signing not supported by this key"
        case .signingFailed(let error):
            return "Certificate signing failed: \(error?.localizedDescription ?? "unknown")"
        }
    }
}
