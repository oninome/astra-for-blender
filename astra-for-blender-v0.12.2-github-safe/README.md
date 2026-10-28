Astra for Blender v0.12.0

- Keeps the working persistent Codex app-server / Blender MCP backend unchanged.
- Replaces the slash-character activity indicator with a Blender-native two-layer loading treatment.
- Main blue BAR keeps `Elapsed: mm:ss` centered.
- Thin blue runner underneath advances continuously and loops to indicate indeterminate work.
- Uses Blender UILayout.progress so it follows the active Blender theme and DPI scaling.

Note: Blender's standard UILayout.progress API does not expose a custom diagonal-stripe fill brush. A true animated striped fill inside the exact panel widget would require a custom GPU overlay, which cannot be robustly anchored to a scrolling Sidebar panel across layouts/themes. This build therefore uses the native Blender UI implementation rather than a fragile screen-coordinate overlay.
