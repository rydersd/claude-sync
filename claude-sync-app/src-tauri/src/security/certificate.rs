// ==========================================================================
// Certificate manager - generates and persists device TLS identity
// On first launch, creates an Ed25519 keypair + self-signed X.509 cert
// (10-year validity). Stored at the platform-appropriate data directory:
//   macOS:   ~/Library/Application Support/claude-sync/
//   Windows: %APPDATA%/claude-sync/
//   Linux:   ~/.local/share/claude-sync/
// ==========================================================================

use rcgen::{CertificateParams, KeyPair};
use ring::digest;
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use tokio_rustls::rustls;

/// File names for the persisted identity within the store directory.
const CERT_PEM_FILENAME: &str = "device-cert.pem";
const KEY_PEM_FILENAME: &str = "device-key.pem";

/// Subject CN for the self-signed certificate.
const CERT_SUBJECT: &str = "claude-sync-device";

/// Manages the device's TLS identity for WAN connections.
/// Generates an Ed25519 keypair + self-signed cert on first launch,
/// then loads from disk on subsequent launches.
pub struct CertificateManager {
    /// Directory where cert and key PEM files are stored
    store_path: PathBuf,
    /// PEM-encoded X.509 certificate
    cert_pem: Option<String>,
    /// PEM-encoded private key
    key_pem: Option<String>,
}

impl CertificateManager {
    /// Create a new CertificateManager using the platform data directory.
    pub fn new() -> Self {
        let store_path = dirs::data_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("claude-sync");
        Self {
            store_path,
            cert_pem: None,
            key_pem: None,
        }
    }

    /// Load existing identity from disk, or generate a new one if none exists.
    /// Must be called before using any TLS or fingerprint methods.
    pub fn get_or_create_identity(&mut self) -> Result<(), Box<dyn std::error::Error>> {
        if self.load_identity()? {
            log::info!("Loaded existing TLS identity from {:?}", self.store_path);
            return Ok(());
        }

        log::info!("No existing TLS identity found, generating new one");
        self.generate_identity()?;
        self.save_identity()?;
        log::info!("Generated and saved new TLS identity to {:?}", self.store_path);
        Ok(())
    }

    /// Get the SHA-256 fingerprint of the certificate's DER encoding.
    /// Returns a colon-separated hex string (e.g., "AB:CD:EF:...").
    pub fn certificate_fingerprint(&self) -> Result<String, Box<dyn std::error::Error>> {
        let cert_der = self.export_cert_der()?;
        let hash = digest::digest(&digest::SHA256, &cert_der);
        let hex: Vec<String> = hash.as_ref().iter().map(|b| format!("{:02X}", b)).collect();
        Ok(hex.join(":"))
    }

    /// Build a rustls ClientConfig for outgoing TLS connections.
    /// Uses the device cert for client authentication and accepts
    /// any server certificate (since peers use self-signed certs).
    pub fn tls_client_config(&self) -> Result<tokio_rustls::TlsConnector, Box<dyn std::error::Error>> {
        let cert_pem = self.cert_pem.as_ref().ok_or("No certificate loaded")?;
        let key_pem = self.key_pem.as_ref().ok_or("No private key loaded")?;

        let certs = rustls_pemfile_parse_certs(cert_pem)?;
        let key = rustls_pemfile_parse_key(key_pem)?;

        // Build a client config that presents our device cert
        // and accepts any server cert (peer self-signed certs).
        let config = rustls::ClientConfig::builder()
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(AcceptAnyCertVerifier))
            .with_client_auth_cert(certs, key)?;

        Ok(tokio_rustls::TlsConnector::from(Arc::new(config)))
    }

    /// Build a rustls ServerConfig for incoming TLS connections.
    /// Presents the device cert and optionally requests client certs.
    pub fn tls_server_config(&self) -> Result<tokio_rustls::TlsAcceptor, Box<dyn std::error::Error>> {
        let cert_pem = self.cert_pem.as_ref().ok_or("No certificate loaded")?;
        let key_pem = self.key_pem.as_ref().ok_or("No private key loaded")?;

        let certs = rustls_pemfile_parse_certs(cert_pem)?;
        let key = rustls_pemfile_parse_key(key_pem)?;

        // Build a server config that presents our device cert
        // and does not require client authentication (pairing happens at app layer).
        let config = rustls::ServerConfig::builder()
            .with_no_client_auth()
            .with_single_cert(certs, key)?;

        Ok(tokio_rustls::TlsAcceptor::from(Arc::new(config)))
    }

    /// Export the certificate as DER bytes (for pairing exchange).
    pub fn export_cert_der(&self) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
        let cert_pem = self.cert_pem.as_ref().ok_or("No certificate loaded")?;
        let certs = rustls_pemfile_parse_certs(cert_pem)?;
        let first = certs.into_iter().next().ok_or("No certificate in PEM")?;
        Ok(first.as_ref().to_vec())
    }

    /// Generate a new Ed25519 keypair and self-signed X.509 certificate
    /// with a 10-year validity period.
    fn generate_identity(&mut self) -> Result<(), Box<dyn std::error::Error>> {
        // Generate Ed25519 keypair
        let key_pair = KeyPair::generate_for(&rcgen::PKCS_ED25519)?;

        // Build certificate parameters with 10-year validity
        let mut params = CertificateParams::new(vec![CERT_SUBJECT.to_string()])?;

        // Set validity: now to 10 years from now
        let now = time::OffsetDateTime::now_utc();
        let ten_years = time::Duration::days(365 * 10);
        params.not_before = now;
        params.not_after = now + ten_years;

        // Self-sign the certificate
        let cert = params.self_signed(&key_pair)?;

        self.cert_pem = Some(cert.pem());
        self.key_pem = Some(key_pair.serialize_pem());

        Ok(())
    }

    /// Try to load an existing identity from disk.
    /// Returns Ok(true) if loaded, Ok(false) if files don't exist.
    fn load_identity(&mut self) -> Result<bool, Box<dyn std::error::Error>> {
        let cert_path = self.store_path.join(CERT_PEM_FILENAME);
        let key_path = self.store_path.join(KEY_PEM_FILENAME);

        if !cert_path.exists() || !key_path.exists() {
            return Ok(false);
        }

        let cert_pem = fs::read_to_string(&cert_path)?;
        let key_pem = fs::read_to_string(&key_path)?;

        // Validate that the PEM data can be parsed
        let certs = rustls_pemfile_parse_certs(&cert_pem)?;
        if certs.is_empty() {
            return Err("Certificate PEM file contains no certificates".into());
        }
        let _key = rustls_pemfile_parse_key(&key_pem)?;

        self.cert_pem = Some(cert_pem);
        self.key_pem = Some(key_pem);

        Ok(true)
    }

    /// Save the current identity to disk.
    fn save_identity(&self) -> Result<(), Box<dyn std::error::Error>> {
        let cert_pem = self.cert_pem.as_ref().ok_or("No certificate to save")?;
        let key_pem = self.key_pem.as_ref().ok_or("No private key to save")?;

        // Ensure the store directory exists
        fs::create_dir_all(&self.store_path)?;

        let cert_path = self.store_path.join(CERT_PEM_FILENAME);
        let key_path = self.store_path.join(KEY_PEM_FILENAME);

        fs::write(&cert_path, cert_pem)?;

        // Write the private key with restricted permissions (Unix)
        fs::write(&key_path, key_pem)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&key_path, fs::Permissions::from_mode(0o600))?;
        }

        Ok(())
    }
}

// -- PEM Parsing Helpers ---------------------------------------------------

/// Parse PEM-encoded certificates into rustls CertificateDer values.
fn rustls_pemfile_parse_certs(
    pem: &str,
) -> Result<Vec<rustls::pki_types::CertificateDer<'static>>, Box<dyn std::error::Error>> {
    let mut reader = std::io::BufReader::new(pem.as_bytes());
    let certs: Vec<_> = rustls_pemfile::certs(&mut reader)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(certs)
}

/// Parse a PEM-encoded private key into a rustls PrivateKeyDer.
fn rustls_pemfile_parse_key(
    pem: &str,
) -> Result<rustls::pki_types::PrivateKeyDer<'static>, Box<dyn std::error::Error>> {
    let mut reader = std::io::BufReader::new(pem.as_bytes());
    let key = rustls_pemfile::private_key(&mut reader)?
        .ok_or("No private key found in PEM")?;
    Ok(key)
}

// -- Custom Certificate Verifier (for self-signed peer certs) ---------------

/// A certificate verifier that accepts any server certificate.
/// We rely on application-layer pairing (fingerprint verification)
/// rather than CA-based trust for peer-to-peer connections.
#[derive(Debug)]
struct AcceptAnyCertVerifier;

impl rustls::client::danger::ServerCertVerifier for AcceptAnyCertVerifier {
    fn verify_server_cert(
        &self,
        _end_entity: &rustls::pki_types::CertificateDer<'_>,
        _intermediates: &[rustls::pki_types::CertificateDer<'_>],
        _server_name: &rustls::pki_types::ServerName<'_>,
        _ocsp_response: &[u8],
        _now: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        // Accept any cert; trust is established through pairing/fingerprint exchange
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        vec![
            rustls::SignatureScheme::ED25519,
            rustls::SignatureScheme::ECDSA_NISTP256_SHA256,
            rustls::SignatureScheme::ECDSA_NISTP384_SHA384,
            rustls::SignatureScheme::RSA_PSS_SHA256,
            rustls::SignatureScheme::RSA_PSS_SHA384,
            rustls::SignatureScheme::RSA_PSS_SHA512,
            rustls::SignatureScheme::RSA_PKCS1_SHA256,
            rustls::SignatureScheme::RSA_PKCS1_SHA384,
            rustls::SignatureScheme::RSA_PKCS1_SHA512,
        ]
    }
}
