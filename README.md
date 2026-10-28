# Astra for Blender

Blender 5.2 add-on connecting Codex (GPT-6 Astra) with Blender via the [MCP for Blender](https://github.com/ahujasid/mcp-for-blender) bridge.

## Status

Latest local build: **v0.12.2 (Stop Recovery Hotfix)**. Source files are being prepared for publication; this repository currently contains documentation only.

## Setup

1. Install Blender 5.2 and Codex CLI, and sign in to Codex.
2. Install Blender MCP with `uvx mcp-for-blender install-addon`.
3. Enable **Interface: MCP for Blender** in Blender and click **Start MCP Server** (default port 9876).
4. Install Astra for Blender from the release ZIP and enable it.
5. Configure the Codex executable path and a dedicated workspace, and use `gpt-6-astra` where available.
6. Leave **Full auto** disabled. Confirm MCP access only for trusted scenes and tools.

> Do not commit personal Blender projects, prompt history, local absolute paths, credentials, or API tokens.

## Current limitations

Astra requires a running Blender MCP add-on. If Codex reports that MCP cannot connect to Blender, check the MCP panel and its socket listener first.

## License

License not yet selected.
