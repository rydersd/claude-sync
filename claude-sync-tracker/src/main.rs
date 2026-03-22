mod protocol;
mod registry;
mod relay;
mod server;

use clap::Parser;

#[derive(Parser)]
#[command(
    name = "claude-sync-tracker",
    about = "Claude Sync tracker server for cross-network peering"
)]
struct Cli {
    /// Port to listen on.
    #[arg(short, long, default_value = "8443")]
    port: u16,

    /// Path to persist registry state as JSON (optional).
    /// If provided, the registry is loaded from this file on startup and saved on shutdown.
    #[arg(long)]
    persist: Option<String>,

    /// Log level (trace, debug, info, warn, error).
    #[arg(long, default_value = "info")]
    log_level: String,
}

#[tokio::main]
async fn main() {
    let cli = Cli::parse();

    env_logger::Builder::from_env(
        env_logger::Env::default().default_filter_or(&cli.log_level),
    )
    .init();

    log::info!(
        "Starting claude-sync-tracker v{} on port {}",
        env!("CARGO_PKG_VERSION"),
        cli.port
    );

    if let Some(ref path) = cli.persist {
        log::info!("Persistence enabled — state file: {}", path);
    }

    let state = server::TrackerState::new(cli.persist.clone());

    // Install a Ctrl-C handler to persist state before exiting.
    let shutdown_state = state.clone();
    tokio::spawn(async move {
        if tokio::signal::ctrl_c().await.is_ok() {
            log::info!("Received shutdown signal — persisting state...");
            server::save_registry_to_disk(&shutdown_state).await;
            std::process::exit(0);
        }
    });

    server::run(state, cli.port).await;
}
