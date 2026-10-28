# Astra for Blender

A Blender 5.2 add-on that connects the Codex CLI (GPT-6 Astra, where available) to Blender through [MCP for Blender](https://github.com/ahujasid/mcp-for-blender).

## Version

**v0.12.2 — Stop Recovery Hotfix.** Source code is in [astra_for_blender](astra_for_blender/). This repository is currently private.

## Installation

1. Install Blender 5.2, the Codex CLI, and `uvx`. Sign in to Codex.
2. Run `uvx mcp-for-blender install-addon` once. In Blender, enable **Interface: MCP for Blender** and select **Start MCP Server** (typically port 9876).
3. Download the versioned ZIP from this repository (or a release, once published). Install it via Blender's **Install from Disk**.
4. In Astra preferences, set your own Codex executable path and **your own dedicated workspace directory**. No machine-specific path is supplied by this project.
5. Set the model to `gpt-6-astra` if available to your Codex account. Leave **Full auto** disabled.
6. Open the Astra tab in the 3D View sidebar, click **Check Codex**, and test with a read-only prompt.

## Safety and privacy

The add-on runs Codex locally and can invoke Blender MCP tools capable of editing a scene. The MCP approval bridge can auto-accept certain empty-form requests from the server named `blender`; review the source and use only in trusted environments. Never commit secrets, personal file paths, private .blend files, or prompt history.

## Troubleshooting

If Codex reports **Couldn't connect to Blender**, ensure the Blender MCP add-on is enabled and its server is started. The Codex CLI and Blender MCP are separate dependencies.

## Source layout

- `astra_for_blender/__init__.py` — add-on registration and settings.
- `astra_for_blender/bridge.py` — Codex app-server / MCP bridge.
- `astra_for_blender/operators.py` — Blender actions and task lifecycle.
- `astra_for_blender/ui.py` — sidebar interface.

## License

**Not yet licensed for public redistribution.** A license will be selected before any public release.
