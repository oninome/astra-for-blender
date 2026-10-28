import threading
import time
import bpy
from bpy.props import IntProperty
from .bridge import (
    get_client, stop_client_turn, close_client, force_close_client,
    codex_version, validate_workspace, BUILD_VERSION,
)

_lock = threading.Lock()
_pending = None
_stop_event = threading.Event()
_stop_requested_at = 0.0
_force_in_progress = False
_run_serial = 0
STOP_GRACE_SECONDS = 4.0


def _ensure_poll_timer():
    """Ensure stop/recovery can finish even after an add-on reload."""
    if not bpy.app.timers.is_registered(_poll_result):
        bpy.app.timers.register(_poll_result, first_interval=0.12)


def prefs(context):
    return context.preferences.addons[__package__].preferences


def _redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _remember_prompt(state, prompt):
    """Keep a small, de-duplicated per-scene prompt history."""
    prompt = (prompt or "").strip()
    if not prompt:
        return
    for index in range(len(state.prompt_history) - 1, -1, -1):
        if state.prompt_history[index].text == prompt:
            state.prompt_history.remove(index)
    item = state.prompt_history.add()
    item.text = prompt
    limit = max(1, int(state.history_limit or 8))
    while len(state.prompt_history) > limit:
        state.prompt_history.remove(0)


def _summarize_approvals(notes):
    notes = [str(note).strip() for note in (notes or []) if str(note).strip()]
    if not notes:
        return ""
    accepted = 0
    blocked = 0
    declined = 0
    other = 0
    for note in notes:
        low = note.lower()
        if "accepted" in low:
            accepted += 1
        elif "blocked" in low:
            blocked += 1
        elif "declined" in low:
            declined += 1
        else:
            other += 1

    parts = []
    if accepted:
        parts.append(f"{accepted} accepted")
    if declined:
        parts.append(f"{declined} declined")
    if blocked:
        parts.append(f"{blocked} blocked")
    if other:
        parts.append(f"{other} other")
    return "MCP approvals: " + "  •  ".join(parts)


def _force_stop_async():
    global _pending, _force_in_progress, _run_serial
    if _force_in_progress:
        return
    _force_in_progress = True
    _run_serial += 1  # Ignore a late result from the abandoned worker.

    def worker():
        global _pending, _force_in_progress
        try:
            force_close_client()
        finally:
            with _lock:
                _pending = {"stopped": True, "message": "", "approvals": []}
            _force_in_progress = False

    threading.Thread(target=worker, daemon=True, name="astra-force-stop").start()


def _poll_result():
    global _pending, _stop_requested_at
    scene = getattr(bpy.context, "scene", None)
    if scene is None or not hasattr(scene, "astra_state"):
        return None
    state = scene.astra_state
    if state.running:
        state.elapsed = max(0.0, time.time() - state.started_at)
        state.progress_phase = (state.progress_phase + 0.055) % 1.0
        state.progress_tick += 1
        if (
            state.status.startswith('Stopping')
            and _stop_requested_at
            and time.time() - _stop_requested_at >= STOP_GRACE_SECONDS
        ):
            state.status = 'Force stopping…'
            _force_stop_async()
        _redraw()
    with _lock:
        result = _pending
        _pending = None
    if result is None:
        return 0.12

    state.running = False
    state.elapsed = max(0.0, time.time() - state.started_at)
    if isinstance(result, Exception):
        state.status = 'Error'
        state.output = str(result)
        state.approval_note = ""
    elif result.get('stopped'):
        state.status = 'Stopped'
        state.output = 'Task stopped by user.'
        state.approval_note = _summarize_approvals(result.get('approvals'))
    else:
        state.status = 'Done'
        state.output = result.get('message') or 'Done.'
        state.approval_note = _summarize_approvals(result.get('approvals'))
    _redraw()
    return None


class ASTRA_OT_run(bpy.types.Operator):
    bl_idname = 'astra.run'
    bl_label = 'Run with Astra'

    def execute(self, context):
        global _pending, _run_serial, _stop_requested_at
        state = context.scene.astra_state
        pref = prefs(context)
        if state.running:
            return {'CANCELLED'}
        prompt = state.prompt.strip()
        if not prompt:
            return {'CANCELLED'}
        ok, workspace, reason = validate_workspace(bpy.path.abspath(pref.workspace))
        if not ok:
            state.status = 'Setup needed'
            state.output = reason
            return {'CANCELLED'}

        _remember_prompt(state, prompt)
        state.running = True
        state.status = 'Working...'
        state.started_at = time.time()
        state.elapsed = 0.0
        state.progress_phase = 0.0
        state.progress_tick = 0
        state.approval_note = "Waiting for Blender MCP…"
        state.output = 'Starting persistent Codex app-server session…'
        _stop_event.clear()
        _stop_requested_at = 0.0
        _run_serial += 1
        my_serial = _run_serial
        args = (pref.codex_command, workspace, pref.model, pref.approve_blender_mcp, pref.extra_args)

        def worker():
            global _pending
            try:
                client = get_client(*args)
                value = client.run_turn(prompt, _stop_event)
            except Exception as exc:
                value = exc
            with _lock:
                if my_serial == _run_serial:
                    _pending = value

        threading.Thread(target=worker, daemon=True, name="astra-run").start()
        if not bpy.app.timers.is_registered(_poll_result):
            bpy.app.timers.register(_poll_result, first_interval=0.12)
        return {'FINISHED'}


class ASTRA_OT_stop(bpy.types.Operator):
    bl_idname = 'astra.stop'
    bl_label = 'Stop'

    def execute(self, context):
        global _stop_requested_at
        state = context.scene.astra_state
        if not state.running:
            return {'CANCELLED'}
        _stop_event.set()
        _stop_requested_at = time.time()
        state.status = 'Stopping…'
        _ensure_poll_timer()
        threading.Thread(target=stop_client_turn, daemon=True, name="astra-stop").start()
        return {'FINISHED'}


class ASTRA_OT_force_stop(bpy.types.Operator):
    bl_idname = 'astra.force_stop'
    bl_label = 'Force Stop'

    def execute(self, context):
        global _stop_requested_at
        state = context.scene.astra_state
        if not state.running:
            return {'CANCELLED'}
        _stop_event.set()
        _stop_requested_at = time.time()
        state.status = 'Force stopping…'
        _ensure_poll_timer()
        _force_stop_async()
        return {'FINISHED'}


class ASTRA_OT_restart_backend(bpy.types.Operator):
    bl_idname = 'astra.restart_backend'
    bl_label = 'Restart Astra Backend'

    def execute(self, context):
        state = context.scene.astra_state
        if state.running:
            if state.status.startswith('Stopping') or state.status.startswith('Force stopping'):
                _stop_event.set()
                state.status = 'Force stopping…'
                _ensure_poll_timer()
                _force_stop_async()
                return {'FINISHED'}
            self.report({'WARNING'}, 'Stop the running task first.')
            return {'CANCELLED'}
        close_client()
        context.scene.astra_state.status = 'Ready'
        context.scene.astra_state.output = 'Persistent Codex app-server session cleared. It will restart on the next prompt.'
        context.scene.astra_state.approval_note = ''
        context.scene.astra_state.elapsed = 0.0
        context.scene.astra_state.progress_tick = 0
        return {'FINISHED'}


class ASTRA_OT_diagnostics(bpy.types.Operator):
    bl_idname = 'astra.diagnostics'
    bl_label = 'Check Codex + Workspace'

    def execute(self, context):
        pref = prefs(context)
        state = context.scene.astra_state
        try:
            exe, workspace, version, rc = codex_version(pref.codex_command, bpy.path.abspath(pref.workspace))
            if rc:
                raise RuntimeError(version or f'codex --version exited {rc}')
            pref.codex_command = exe
            state.status = 'Ready'
            state.output = (
                f'Astra build: {BUILD_VERSION}\n'
                f'Codex: {version}\n'
                f'Executable: {exe}\n'
                f'Workspace: {workspace}\n'
                'Backend: persistent app-server\n'
                'Approval routing: host handles Blender MCP elicitation requests only'
            )
            state.approval_note = ''
            state.elapsed = 0.0
            state.progress_tick = 0
        except Exception as exc:
            state.status = 'Setup needed'
            state.output = str(exc)
        return {'FINISHED'}


class ASTRA_OT_use_history(bpy.types.Operator):
    bl_idname = 'astra.use_history'
    bl_label = 'Use Prompt'

    index: IntProperty(default=-1)

    def execute(self, context):
        state = context.scene.astra_state
        if self.index < 0 or self.index >= len(state.prompt_history):
            return {'CANCELLED'}
        state.prompt = state.prompt_history[self.index].text
        return {'FINISHED'}


class ASTRA_OT_clear_history(bpy.types.Operator):
    bl_idname = 'astra.clear_history'
    bl_label = 'Clear Prompt History'

    def execute(self, context):
        context.scene.astra_state.prompt_history.clear()
        return {'FINISHED'}


classes = (
    ASTRA_OT_run,
    ASTRA_OT_stop,
    ASTRA_OT_force_stop,
    ASTRA_OT_restart_backend,
    ASTRA_OT_diagnostics,
    ASTRA_OT_use_history,
    ASTRA_OT_clear_history,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
