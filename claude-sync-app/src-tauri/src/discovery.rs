// ==========================================================================
// mDNS service discovery and advertisement using the mdns-sd crate.
// Advertises this device as _claude-sync._tcp and browses for peers.
// Uses the same service type and TXT records as the macOS Swift app.
// ==========================================================================

use mdns_sd::{ServiceDaemon, ServiceEvent, ServiceInfo};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::config_scanner;
use crate::device_identity;
use crate::protocol::PeerInfo;

/// The mDNS service type used for discovery. Must match the macOS app.
const SERVICE_TYPE: &str = "_claude-sync._tcp.local.";

/// TCP port used by the sync server listener.
pub const SYNC_PORT: u16 = 52384;

/// How long to wait before considering a peer stale (seconds).
const PEER_STALE_TIMEOUT_SECS: u64 = 30;

/// Manages mDNS discovery and advertisement.
/// Holds the service daemon and the list of discovered peers.
pub struct DiscoveryManager {
    /// The mDNS service daemon (handles both browsing and advertising)
    daemon: ServiceDaemon,
    /// Thread-safe map of discovered peers, keyed by device_id
    peers: Arc<Mutex<HashMap<String, PeerInfo>>>,
    /// This device's unique ID (used to filter self from discovery results)
    device_id: String,
    /// Whether the discovery is currently active
    active: bool,
}

impl DiscoveryManager {
    /// Create a new DiscoveryManager. Does not start discovery yet.
    pub fn new() -> Result<Self, String> {
        let daemon = ServiceDaemon::new()
            .map_err(|e| format!("Failed to create mDNS daemon: {}", e))?;

        let device_id = device_identity::get_or_create_device_id();

        Ok(Self {
            daemon,
            peers: Arc::new(Mutex::new(HashMap::new())),
            device_id,
            active: false,
        })
    }

    /// Start advertising this device and browsing for peers.
    /// This method spawns a background thread for processing mDNS events.
    pub fn start(&mut self) -> Result<(), String> {
        if self.active {
            return Ok(());
        }

        // Register (advertise) this device
        self.advertise()?;

        // Start browsing for peers
        self.browse()?;

        self.active = true;
        Ok(())
    }

    /// Stop discovery and remove our service advertisement.
    pub fn stop(&mut self) -> Result<(), String> {
        if !self.active {
            return Ok(());
        }

        let instance_name = format!("claude-sync-{}", &self.device_id[..8]);
        let _ = self.daemon.unregister(&format!("{}.{}", instance_name, SERVICE_TYPE));
        let _ = self.daemon.shutdown();
        self.active = false;

        Ok(())
    }

    /// Get a snapshot of currently discovered peers.
    pub fn get_peers(&self) -> Vec<PeerInfo> {
        let peers = self.peers.lock().unwrap();
        peers.values().cloned().collect()
    }

    /// Advertise this device on the local network via mDNS.
    /// Registers a service with TXT records matching the macOS app format.
    fn advertise(&self) -> Result<(), String> {
        let hostname = device_identity::get_hostname();
        let platform = device_identity::get_platform();

        // Scan local configs for the advertisement metadata
        let config_files = config_scanner::scan_config_dir();
        let file_count = config_files.len() as u32;
        let fingerprint = config_scanner::compute_fingerprint(&config_files);

        // Build TXT record properties matching the macOS app
        let mut properties = HashMap::new();
        properties.insert("v".to_string(), "1".to_string());
        properties.insert("id".to_string(), self.device_id.clone());
        properties.insert("name".to_string(), hostname.clone());
        properties.insert("configs".to_string(), file_count.to_string());
        properties.insert("fingerprint".to_string(), fingerprint[..16].to_string());
        properties.insert("platform".to_string(), platform);

        let instance_name = format!("claude-sync-{}", &self.device_id[..8]);

        let service_info = ServiceInfo::new(
            SERVICE_TYPE,
            &instance_name,
            &format!("{}.", hostname),
            "",  // Let mdns-sd auto-detect the IP
            SYNC_PORT,
            properties,
        )
        .map_err(|e| format!("Failed to create service info: {}", e))?;

        self.daemon
            .register(service_info)
            .map_err(|e| format!("Failed to register mDNS service: {}", e))?;

        log::info!(
            "Advertising as '{}' on port {} ({} config files)",
            instance_name,
            SYNC_PORT,
            file_count
        );

        Ok(())
    }

    /// Start browsing for _claude-sync._tcp peers on the LAN.
    /// Discovered peers are stored in the shared peers map.
    fn browse(&self) -> Result<(), String> {
        let receiver = self
            .daemon
            .browse(SERVICE_TYPE)
            .map_err(|e| format!("Failed to start browsing: {}", e))?;

        let peers = Arc::clone(&self.peers);
        let my_device_id = self.device_id.clone();

        // Spawn a background thread to process mDNS events.
        // mdns-sd uses flume channels, so recv_timeout returns flume::RecvTimeoutError.
        std::thread::spawn(move || {
            loop {
                match receiver.recv_timeout(Duration::from_secs(PEER_STALE_TIMEOUT_SECS)) {
                    Ok(event) => {
                        handle_mdns_event(&event, &peers, &my_device_id);
                    }
                    Err(flume::RecvTimeoutError::Timeout) => {
                        // Periodic cleanup of stale peers could go here
                        continue;
                    }
                    Err(flume::RecvTimeoutError::Disconnected) => {
                        log::info!("mDNS browser channel disconnected, stopping browse loop");
                        break;
                    }
                }
            }
        });

        log::info!("Browsing for {} services", SERVICE_TYPE);
        Ok(())
    }
}

/// Process a single mDNS service event (resolved, removed, etc.).
fn handle_mdns_event(
    event: &ServiceEvent,
    peers: &Arc<Mutex<HashMap<String, PeerInfo>>>,
    my_device_id: &str,
) {
    match event {
        ServiceEvent::ServiceResolved(info) => {
            // Extract TXT record properties
            let properties = info.get_properties();

            let device_id = properties
                .get_property_val_str("id")
                .unwrap_or_default()
                .to_string();

            // Skip our own advertisement
            if device_id == my_device_id || device_id.is_empty() {
                return;
            }

            let name = properties
                .get_property_val_str("name")
                .unwrap_or_default()
                .to_string();

            let platform = properties
                .get_property_val_str("platform")
                .unwrap_or_default()
                .to_string();

            let file_count: u32 = properties
                .get_property_val_str("configs")
                .unwrap_or_default()
                .parse()
                .unwrap_or(0);

            let fingerprint = properties
                .get_property_val_str("fingerprint")
                .unwrap_or_default()
                .to_string();

            let protocol_version: u32 = properties
                .get_property_val_str("v")
                .unwrap_or_default()
                .parse()
                .unwrap_or(1);

            // Build the address string from the resolved service info
            let addresses = info.get_addresses();
            let port = info.get_port();
            let address = if let Some(addr) = addresses.iter().next() {
                format!("{}:{}", addr, port)
            } else {
                format!("unknown:{}", port)
            };

            let peer = PeerInfo {
                device_id: device_id.clone(),
                name,
                address,
                platform,
                file_count,
                fingerprint,
                protocol_version,
            };

            log::info!("Discovered peer: {} at {}", peer.name, peer.address);

            let mut peers_map = peers.lock().unwrap();
            peers_map.insert(device_id, peer);
        }
        ServiceEvent::ServiceRemoved(_type, fullname) => {
            // Try to find and remove the peer by matching the fullname
            let mut peers_map = peers.lock().unwrap();
            // The fullname contains the instance name which has our device_id prefix
            peers_map.retain(|device_id, _| {
                !fullname.contains(&device_id[..8.min(device_id.len())])
            });
            log::info!("Peer removed: {}", fullname);
        }
        ServiceEvent::SearchStarted(_) => {
            log::debug!("mDNS search started");
        }
        ServiceEvent::SearchStopped(_) => {
            log::debug!("mDNS search stopped");
        }
        _ => {}
    }
}
