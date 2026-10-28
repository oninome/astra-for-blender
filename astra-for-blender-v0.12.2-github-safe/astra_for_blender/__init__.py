bl_info = {
    "name": "Astra for Blender",
    "author": "Gianluca Magurno + OpenAI",
    "version": (0, 12, 2),
    "blender": (5, 2, 0),
    "location": "View3D > Sidebar > Astra",
    "description": "Persistent Codex app-server bridge for GPT-6 Astra and Blender MCP",
    "category": "3D View",
}

import bpy
import importlib
import sys
from bpy.props import (
    StringProperty,
    BoolProperty,
    PointerProperty,
    FloatProperty,
    CollectionProperty,
    IntProperty,
)

# Blender can keep add-on submodules alive when a ZIP is updated in place.
# Reload the actual cached package modules, even when this __init__.py is
# executed in a fresh module namespace. Reload bridge first because operators
# imports functions from it.
for _name in ("bridge", "operators", "ui"):
    _full = f"{__package__}.{_name}"
    if _full in sys.modules:
        importlib.reload(sys.modules[_full])

from . import bridge, operators, ui


class AstraPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    codex_command: StringProperty(
        name="Codex command",
        default="codex",
        description="Auto-detects the official Windows Codex install; full path is also supported",
    )
    workspace: StringProperty(name="Workspace", default="", subtype='DIR_PATH')
    model: StringProperty(name="Model", default="gpt-6-astra", description="Leave blank to use Codex default")
    approve_blender_mcp: BoolProperty(
        name="Allow Blender MCP approval requests",
        default=True,
        description=(
            "Automatically accepts only empty-form approval requests coming from the MCP server named 'blender'. "
            "Other MCP servers and requests requiring user input are not auto-approved."
        ),
    )
    full_auto: BoolProperty(
        name="Full auto",
        default=False,
        description="Legacy setting; not used by the v0.12 app-server approval bridge",
    )
    extra_args: StringProperty(name="Extra arguments", default="", description="Advanced Codex app-server CLI arguments")

    def draw(self, context):
        layout = self.layout
        layout.label(text="Astra for Blender v0.12.2", icon='INFO')
        layout.prop(self, "codex_command")
        layout.prop(self, "workspace")
        layout.prop(self, "model")
        layout.prop(self, "approve_blender_mcp")
        layout.prop(self, "full_auto")
        if self.full_auto:
            box = layout.box()
            box.alert = True
            box.label(text="Full auto is not used by the v0.12 backend.", icon='ERROR')
        layout.prop(self, "extra_args")


class AstraHistoryItem(bpy.types.PropertyGroup):
    text: StringProperty(name="Prompt", default="")


class AstraState(bpy.types.PropertyGroup):
    prompt: StringProperty(
        name="Prompt",
        default="Inspect the current Blender scene and describe it. Do not modify anything.",
    )
    output: StringProperty(name="Output", default="Ready")
    status: StringProperty(name="Status", default="Idle")
    running: BoolProperty(default=False)
    started_at: FloatProperty(default=0.0)
    elapsed: FloatProperty(default=0.0)
    progress_phase: FloatProperty(default=0.0, min=0.0, max=1.0)
    progress_tick: IntProperty(default=0, min=0)
    approval_note: StringProperty(name="Approval", default="")
    show_history: BoolProperty(name="Recent prompts", default=False)
    prompt_history: CollectionProperty(type=AstraHistoryItem)
    history_limit: IntProperty(default=8, min=1, max=20)


classes = (AstraPreferences, AstraHistoryItem, AstraState)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    operators.register()
    ui.register()
    bpy.types.Scene.astra_state = PointerProperty(type=AstraState)

    # A running Codex process cannot survive an add-on reload / Blender restart.
    # Clear only transient runtime flags; prompt history is deliberately untouched.
    for scene in bpy.data.scenes:
        state = scene.astra_state
        if state.running:
            state.running = False
            state.status = 'Stopped'
            state.approval_note = ''


def unregister():
    if hasattr(bpy.types.Scene, "astra_state"):
        del bpy.types.Scene.astra_state
    ui.unregister()
    operators.unregister()
    bridge.close_client()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
