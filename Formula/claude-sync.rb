class ClaudeSync < Formula
  include Language::Python::Shebang

  desc "Sync Claude Code configuration between machines via git"
  homepage "https://github.com/rydersd/claude-sync"
  url "https://github.com/rydersd/claude-sync/archive/refs/tags/v1.4.0.tar.gz"
  sha256 "PLACEHOLDER"
  license "MIT"
  head "https://github.com/rydersd/claude-sync.git", branch: "main"

  depends_on "python@3"

  def install
    bin.install "claude-sync.py" => "claude-sync"
    bin.install "claude-sync-mcp.py" => "claude-sync-mcp"
    rewrite_shebang detected_python_shebang, bin/"claude-sync"
    rewrite_shebang detected_python_shebang, bin/"claude-sync-mcp"
  end

  test do
    assert_match "claude-sync", shell_output("#{bin}/claude-sync --help")
  end
end
