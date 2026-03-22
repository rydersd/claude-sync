// ==========================================================================
// Security module - TLS identity, device pairing, and trust management
// Provides certificate-based device identity for WAN connections,
// 6-digit code pairing workflow, and persistent trust store.
// ==========================================================================

pub mod certificate;
pub mod pairing;
pub mod trust_store;
