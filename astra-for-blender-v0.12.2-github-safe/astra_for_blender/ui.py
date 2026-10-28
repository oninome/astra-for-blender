import re
import bpy
from .bridge import BUILD_VERSION


def fmt_time(seconds):
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def _clean_markdown(text):
    """Light display cleanup only; the stored/model output remains unchanged."""
    text = text or ""
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = text.replace("`", "")
    return text


def wrapped_lines(text, width=86, max_lines=36):
    """Word-wrap Blender labels while preserving simple bullets and paragraphs."""
    text = _clean_markdown(text)
    out = []
    truncated = False
    previous_blank = False

    for raw in text.splitlines() or [""]:
        raw = raw.rstrip()
        if not raw.strip():
            if not previous_blank and out:
                out.append("")
            previous_blank = True
            if len(out) >= max_lines:
                truncated = True
                break
            continue
        previous_blank = False

        stripped = raw.strip()
        prefix = ""
        continuation = ""
        if stripped.startswith("- ") or stripped.startswith("* "):
            stripped = stripped[2:].strip()
            prefix = "• "
            continuation = "  "

        words = stripped.split()
        if not words:
            continue
        line = prefix + words[0]
        for word in words[1:]:
            if len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                out.append(line)
                if len(out) >= max_lines:
                    truncated = True
                    break
                line = continuation + word
        if truncated:
            break
        out.append(line)
        if len(out) >= max_lines:
            truncated = True
            break

    if truncated:
        if out:
            out[-1] = out[-1].rstrip() + " …"
        else:
            out = ["…"]
    return out


def compact_prompt(text, limit=72):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def activity_bar_text(elapsed, running):
    """Centered label for the native Blender progress bar."""
    if running:
        return f"Elapsed: {fmt_time(elapsed)}"
    return "Ready" if elapsed <= 0 else f"Last: {fmt_time(elapsed)}"


class ASTRA_PT_panel(bpy.types.Panel):
    bl_label = "Astra Assistant"
    bl_idname = "ASTRA_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Astra'

    def draw(self, context):
        layout = self.layout
        state = context.scene.astra_state
        pref = context.preferences.addons[__package__].preferences

        header = layout.row()
        header.label(text=f"GPT-6 Astra via Codex + Blender MCP  •  v{BUILD_VERSION}", icon='LIGHT')

        # Connectivity first: users can verify Codex before composing a task.
        check = layout.row(align=True)
        check.operator("astra.diagnostics", text="Check Codex", icon='CHECKMARK')

        prompt_box = layout.box()
        prompt_box.label(text="Prompt")
        prompt_input = prompt_box.column(align=True)
        prompt_input.scale_y = 1.35
        prompt_input.prop(state, "prompt", text="")

        prompt_preview = prompt_box.box()
        prompt_preview.label(text="Prompt preview", icon='ALIGN_JUSTIFY')
        for line in wrapped_lines(state.prompt, width=84, max_lines=8):
            if line:
                prompt_preview.label(text=line)
            else:
                prompt_preview.separator(factor=0.3)

        # Main task controls stay together on one line.
        row = layout.row(align=True)
        run = row.row(align=True)
        run.enabled = not state.running
        run.operator("astra.run", text="Run with Astra", icon='PLAY')
        stop = row.row(align=True)
        stop.enabled = state.running and not state.status.startswith('Stopping') and not state.status.startswith('Force stopping')
        stop.operator("astra.stop", text="Stop", icon='CANCEL')
        if state.running and (state.status.startswith('Stopping') or state.status.startswith('Force stopping')):
            force = row.row(align=True)
            force.enabled = not state.status.startswith('Force stopping')
            force.operator("astra.force_stop", text="Force Stop", icon='CANCEL')
        restart = row.row(align=True)
        restart.enabled = (not state.running) or state.status.startswith('Stopping') or state.status.startswith('Force stopping')
        restart.operator("astra.restart_backend", text="Restart Backend", icon='FILE_REFRESH')

        # Native Blender indeterminate loading treatment.  The main BAR carries
        # the centered elapsed label; the thin runner below continuously advances
        # left-to-right and loops, so it communicates activity without pretending
        # to know task completion.  This stays inside Blender's UI/theme system.
        activity = layout.column(align=True)
        activity.scale_y = 1.15
        activity.progress(
            factor=1.0 if state.running else 0.0,
            type='BAR',
            text=activity_bar_text(state.elapsed, state.running),
        )
        runner = activity.row(align=True)
        runner.scale_y = 0.32
        runner.progress(
            factor=state.progress_phase if state.running else 0.0,
            type='BAR',
            text="",
        )

        # Prompt history is intentionally small and stored with the .blend scene.
        hist_head = layout.row(align=True)
        hist_head.prop(
            state,
            "show_history",
            text=f"Recent prompts ({len(state.prompt_history)})",
            icon='TRIA_DOWN' if state.show_history else 'TRIA_RIGHT',
            emboss=False,
        )
        if state.show_history:
            hist_box = layout.box()
            if not state.prompt_history:
                hist_box.label(text="No prompt history yet.", icon='INFO')
            else:
                start = max(0, len(state.prompt_history) - 5)
                for index in range(len(state.prompt_history) - 1, start - 1, -1):
                    row = hist_box.row(align=True)
                    op = row.operator("astra.use_history", text="", icon='LOOP_BACK')
                    op.index = index
                    row.label(text=compact_prompt(state.prompt_history[index].text))
                hist_box.operator("astra.clear_history", text="Clear history", icon='TRASH')

        box = layout.box()
        box.label(text=f"Status: {state.status}")
        if state.running:
            box.label(text="Working", icon='TIME')
        elif state.elapsed > 0:
            if state.status == 'Done':
                box.label(text=f"Completed in {fmt_time(state.elapsed)}", icon='CHECKMARK')
            elif state.status == 'Stopped':
                box.label(text=f"Stopped after {fmt_time(state.elapsed)}", icon='CANCEL')
            elif state.status == 'Error':
                box.label(text=f"Failed after {fmt_time(state.elapsed)}", icon='ERROR')
            else:
                box.label(text=f"Elapsed {fmt_time(state.elapsed)}", icon='TIME')

        if state.approval_note:
            approval = box.box()
            approval.label(text=state.approval_note, icon='LOCKED')

        result = box.box()
        result.label(text="Result", icon='TEXT')
        for line in wrapped_lines(state.output, width=88, max_lines=36):
            if line:
                result.label(text=line)
            else:
                result.separator(factor=0.35)

        note = layout.box()
        if pref.approve_blender_mcp:
            note.label(text="Blender MCP: empty-form tool confirmations are auto-approved", icon='LOCKED')
        else:
            note.label(text="Blender MCP: automatic approvals disabled", icon='UNLOCKED')
        note.label(text="Other approval types remain blocked")
        layout.label(text=f"Astra build {BUILD_VERSION}  •  Settings: Preferences > Add-ons > Astra for Blender")


classes = (ASTRA_PT_panel,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
