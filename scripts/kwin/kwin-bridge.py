#!/usr/bin/python3 -I
"""KWin <-> iNiR bridge.

Owns org.inir.KWinBridge on the session bus, loads inir-bridge.js into KWin
for its own lifetime and translates between the two sides:

- KWin pushes full state snapshots (Push); they leave on stdout as Niri-shaped
  event-stream lines that NiriService.handleNiriEvent already understands, plus
  {"KWinReady": true} once the first snapshot arrived.
- stdin takes Niri IPC requests ({"Action": {...}}), {"op": "sync"} or
  {"op": "setMinimized", "id": <window id>, "value": bool}; they are
  translated to KWin terms and handed to the script through a long-poll (Next).

Run with --selftest to load the script, print one snapshot summary and exit.
"""

import json
import os
import signal
import sys
import time

from gi.repository import Gio, GLib

try:
    from gi.repository import GLibUnix
    signal_add = GLibUnix.signal_add
except ImportError:
    signal_add = GLib.unix_signal_add

BUS_NAME = "org.inir.KWinBridge"
OBJECT_PATH = "/org/inir/KWinBridge"
PLUGIN_NAME = "inir-kwin-bridge"
SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inir-bridge.js")
# KWin's callDBus waits 25 s for a reply; answer an idle Next before that.
POLL_HEARTBEAT_S = 20

INTROSPECTION = """
<node>
  <interface name="org.inir.KWinBridge">
    <method name="Push"><arg name="state" type="s" direction="in"/></method>
    <method name="Next"><arg name="command" type="s" direction="out"/></method>
  </interface>
</node>
"""


def log(*parts):
    print("kwin-bridge:", *parts, file=sys.stderr, flush=True)


class Bridge:
    def __init__(self, selftest=False):
        self.selftest = selftest
        self.loop = GLib.MainLoop()
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.registration = 0
        self.owns_name = False
        self.script_loaded = False
        self.kwin_owner = ""
        self.pending = None
        self.heartbeat = 0
        self.queue = []
        self.stdin_buffer = b""
        self.ready = False
        self.exit_code = 0
        self.closing = False

        # KWin-side identity <-> Niri-side ids.
        self.next_window_id = 1
        self.window_ids = {}
        self.uuid_by_id = {}
        self.focus_stamps = {}
        self.last_active = None
        self.state = None
        self.workspace_targets = {}
        self.last_emitted = {}

    # ---- D-Bus plumbing ---------------------------------------------------

    def call(self, name, path, iface, method, args=None, reply=None):
        return self.conn.call_sync(name, path, iface, method, args,
                                   GLib.VariantType.new(reply) if reply else None,
                                   Gio.DBusCallFlags.NONE, 5000, None)

    def own_name(self):
        result = self.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                           "RequestName", GLib.Variant("(su)", (BUS_NAME, 4)), "(u)").unpack()[0]
        # 1 = primary owner, 4 = already ours.
        if result not in (1, 4):
            log(BUS_NAME, "is already owned by another process")
            return False
        self.owns_name = True
        return True

    def register(self):
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
        self.registration = self.conn.register_object(OBJECT_PATH, node.interfaces[0], self.on_method, None, None)

    def refresh_kwin_owner(self):
        try:
            self.kwin_owner = self.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                        "GetNameOwner", GLib.Variant("(s)", ("org.kde.KWin",)), "(s)").unpack()[0]
        except GLib.Error:
            self.kwin_owner = ""

    def scripting(self, method, args=None, reply=None):
        return self.call("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", method, args, reply)

    def load_script(self):
        if self.scripting("isScriptLoaded", GLib.Variant("(s)", (PLUGIN_NAME,)), "(b)").unpack()[0]:
            self.scripting("unloadScript", GLib.Variant("(s)", (PLUGIN_NAME,)), "(b)")
        script_id = self.scripting("loadScript", GLib.Variant("(ss)", (SCRIPT_PATH, PLUGIN_NAME)), "(i)").unpack()[0]
        if script_id < 0:
            raise RuntimeError("KWin refused to load " + SCRIPT_PATH)
        self.script_loaded = True
        try:
            self.call("org.kde.KWin", "/Scripting/Script%d" % script_id, "org.kde.kwin.Script", "run")
        except GLib.Error:
            self.scripting("start")

    def unload_script(self):
        if not self.script_loaded:
            return
        self.script_loaded = False
        try:
            self.scripting("unloadScript", GLib.Variant("(s)", (PLUGIN_NAME,)), "(b)")
        except GLib.Error as error:
            log("unloadScript failed:", error.message)

    def on_method(self, conn, sender, path, iface, method, params, invocation):
        if sender != self.kwin_owner:
            self.refresh_kwin_owner()
            if sender != self.kwin_owner:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.AccessDenied", "only KWin may call this")
                return
        if method == "Push":
            invocation.return_value(None)
            self.on_push(params.unpack()[0])
        elif method == "Next":
            self.on_next(invocation)
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    # ---- Commands (stdin -> KWin) -------------------------------------------

    def on_next(self, invocation):
        self.release_pending()
        if self.queue:
            invocation.return_value(GLib.Variant("(s)", (self.queue.pop(0),)))
            return
        self.pending = invocation
        self.heartbeat = GLib.timeout_add_seconds(POLL_HEARTBEAT_S, self.on_heartbeat)

    def release_pending(self):
        if self.heartbeat:
            GLib.source_remove(self.heartbeat)
            self.heartbeat = 0
        if self.pending is not None:
            self.pending.return_value(GLib.Variant("(s)", ("",)))
            self.pending = None

    def on_heartbeat(self):
        self.heartbeat = 0
        self.release_pending()
        return GLib.SOURCE_REMOVE

    def enqueue(self, command):
        text = json.dumps(command, separators=(",", ":"))
        if self.pending is not None:
            if self.heartbeat:
                GLib.source_remove(self.heartbeat)
                self.heartbeat = 0
            self.pending.return_value(GLib.Variant("(s)", (text,)))
            self.pending = None
        else:
            self.queue.append(text)

    def on_stdin(self, fd, condition):
        try:
            chunk = os.read(fd, 65536) if condition & GLib.IO_IN else b""
        except OSError:
            chunk = b""
        if not chunk:
            self.quit(0)
            return GLib.SOURCE_REMOVE
        self.stdin_buffer += chunk
        while b"\n" in self.stdin_buffer:
            line, self.stdin_buffer = self.stdin_buffer.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                self.on_request(json.loads(line))
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                log("ignoring bad request", line[:200], error)
        return GLib.SOURCE_CONTINUE

    def on_request(self, request):
        if request.get("op") == "sync":
            # A sync re-emits everything, changed or not.
            self.last_emitted = {}
            self.enqueue({"op": "sync"})
            return
        if request.get("op") == "setMinimized":
            uuid = self.uuid_by_id.get(request.get("id"))
            if uuid:
                self.enqueue({"op": "setMinimized", "uuid": uuid, "value": bool(request.get("value"))})
            return
        action = request.get("Action") or {}
        for name, data in action.items():
            command = self.translate(name, data or {})
            if command is not None:
                self.enqueue(command)
            else:
                log("dropped action", name, "(unsupported or unknown target)")

    def translate(self, name, data):
        if name in ("ToggleOverview", "OpenOverview"):
            return {"op": "overview"}
        if name in ("FocusWindow", "CloseWindow"):
            uuid = self.uuid_by_id.get(data.get("id"))
            if uuid is None:
                return None
            return {"op": "focusWindow" if name == "FocusWindow" else "closeWindow", "uuid": uuid}
        if name == "FocusWorkspace":
            target = self.resolve_workspace(data.get("reference") or {})
            return {"op": "setDesktop", "output": target[0], "desktop": target[1]} if target else None
        if name in ("FocusWorkspaceUp", "FocusWorkspaceDown"):
            desktops = (self.state or {}).get("desktops") or []
            ids = [d["id"] for d in desktops]
            output = self.focused_output()
            screen = next((s for s in (self.state or {}).get("screens") or [] if s["name"] == output), {})
            current = screen.get("current") or (self.state or {}).get("current")
            if current not in ids:
                return None
            index = ids.index(current) + (-1 if name == "FocusWorkspaceUp" else 1)
            return ({"op": "setDesktop", "output": output, "desktop": ids[index]}
                    if 0 <= index < len(ids) else None)
        if name == "MoveWindowToWorkspace":
            # Like Niri, no window_id means the focused window.
            window_id = data.get("window_id")
            uuid = self.uuid_by_id.get(window_id) if window_id is not None else self.last_active
            target = self.resolve_workspace(data.get("reference") or {})
            if uuid is None or not target:
                return None
            return {"op": "moveWindowToDesktop", "uuid": uuid, "output": target[0], "desktop": target[1],
                    "focus": bool(data.get("focus", True))}
        return None

    def resolve_workspace(self, reference):
        if "Id" in reference:
            return self.workspace_targets.get(reference["Id"])
        if "Index" in reference:
            desktops = (self.state or {}).get("desktops") or []
            index = int(reference["Index"]) - 1
            if 0 <= index < len(desktops):
                return (self.focused_output(), desktops[index]["id"])
        if "Name" in reference:
            for desktop in (self.state or {}).get("desktops") or []:
                if desktop["name"] == reference["Name"]:
                    return (self.focused_output(), desktop["id"])
        return None

    # ---- State (KWin -> stdout) --------------------------------------------

    def window_id(self, uuid):
        if uuid not in self.window_ids:
            self.window_ids[uuid] = self.next_window_id
            self.uuid_by_id[self.next_window_id] = uuid
            self.next_window_id += 1
        return self.window_ids[uuid]

    def focused_output(self):
        state = self.state or {}
        screens = [s["name"] for s in state.get("screens") or []]
        active = state.get("active")
        for window in state.get("windows") or []:
            if window["uuid"] == active and window["output"] in screens:
                return window["output"]
        if state.get("activeScreen") in screens:
            return state["activeScreen"]
        return screens[0] if screens else ""

    def on_push(self, payload):
        try:
            state = json.loads(payload)
        except ValueError:
            log("bad snapshot from KWin")
            return
        self.state = state
        if not self.ready:
            self.ready = True
            if self.selftest:
                self.print_selftest()
                self.quit(0)
                return
            self.emit({"KWinReady": True})
        self.translate_state(state)

    def translate_state(self, state):
        screens = state.get("screens") or []
        desktops = state.get("desktops") or []
        windows = state.get("windows") or []
        current_global = state.get("current", "")
        active = state.get("active") or None

        live = {w["uuid"] for w in windows}
        for uuid in [u for u in self.window_ids if u not in live]:
            self.uuid_by_id.pop(self.window_ids.pop(uuid), None)
            self.focus_stamps.pop(uuid, None)
        if active and active != self.last_active:
            self.focus_stamps[active] = time.monotonic_ns()
        self.last_active = active
        for window in windows:
            # Never-focused windows keep their stacking order as a weak recency.
            self.focus_stamps.setdefault(window["uuid"], max(0, window.get("stack", 0)))

        screen_names = [s["name"] for s in screens]
        focused_output = self.focused_output()
        desktop_index = {d["id"]: i for i, d in enumerate(desktops)}
        workspace_ids = {}
        current_by_output = {}
        self.workspace_targets = {}
        for o, screen in enumerate(screens):
            current_by_output[screen["name"]] = screen.get("current") or current_global
            for d, desktop in enumerate(desktops):
                ws_id = (o + 1) * 1000 + (d + 1)
                workspace_ids[(screen["name"], desktop["id"])] = ws_id
                self.workspace_targets[ws_id] = (screen["name"], desktop["id"])

        def workspace_of(window):
            output = window["output"] if window["output"] in screen_names else (screen_names[0] if screen_names else "")
            current = current_by_output.get(output, current_global)
            on = window.get("desktops") or []
            desktop = current if (not on or current in on) else min(on, key=lambda i: desktop_index.get(i, 1 << 30))
            return workspace_ids.get((output, desktop))

        niri_windows = []
        for window in windows:
            uuid = window["uuid"]
            stamp = self.focus_stamps.get(uuid, 0)
            niri_windows.append({
                "id": self.window_id(uuid),
                "title": window.get("caption", ""),
                "app_id": window.get("desktopFileName") or window.get("resourceClass", ""),
                "pid": window.get("pid") or None,
                "workspace_id": workspace_of(window),
                "is_focused": uuid == active,
                "is_floating": False,
                "is_urgent": False,
                "is_minimized": bool(window.get("minimized")),
                "is_fullscreen": bool(window.get("fullScreen")),
                "layout": None,
                "focus_timestamp": {"secs": stamp // 1_000_000_000, "nanos": stamp % 1_000_000_000},
            })
        niri_windows.sort(key=lambda w: w["id"])

        recent = {}
        for window in niri_windows:
            ws_id = window["workspace_id"]
            stamp = self.focus_stamps.get(self.uuid_by_id[window["id"]], 0)
            rank = (not window["is_minimized"], stamp)
            if ws_id is not None and (ws_id not in recent or rank > recent[ws_id][0]):
                recent[ws_id] = (rank, window["id"])

        niri_workspaces = []
        for o, screen in enumerate(screens):
            for d, desktop in enumerate(desktops):
                ws_id = workspace_ids[(screen["name"], desktop["id"])]
                is_active = desktop["id"] == current_by_output[screen["name"]]
                niri_workspaces.append({
                    "id": ws_id,
                    "idx": d + 1,
                    "name": desktop.get("name") or None,
                    "output": screen["name"],
                    "is_active": is_active,
                    "is_focused": is_active and screen["name"] == focused_output,
                    "is_urgent": False,
                    "active_window_id": recent[ws_id][1] if ws_id in recent else None,
                })

        outputs = {}
        for screen in screens:
            outputs[screen["name"]] = {
                "name": screen["name"],
                "make": "Unknown",
                "model": "Unknown",
                "logical": {"x": screen["x"], "y": screen["y"], "width": screen["width"],
                            "height": screen["height"], "scale": screen.get("scale", 1), "transform": "Normal"},
            }

        # Outputs and workspaces first: the window reducer sorts against them.
        self.emit_if_changed("OutputsChanged", {"outputs": outputs})
        self.emit_if_changed("WorkspacesChanged", {"workspaces": niri_workspaces})
        self.emit_if_changed("WindowsChanged", {"windows": niri_windows})

    def emit_if_changed(self, kind, data):
        text = json.dumps({kind: data}, separators=(",", ":"))
        if self.last_emitted.get(kind) == text:
            return
        self.last_emitted[kind] = text
        self.write_line(text)

    def emit(self, event):
        self.write_line(json.dumps(event, separators=(",", ":")))

    def write_line(self, text):
        try:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            self.quit(0)

    def print_selftest(self):
        state = self.state or {}
        windows = state.get("windows") or []
        active = next((w for w in windows if w["uuid"] == state.get("active")), None)
        app = (active.get("desktopFileName") or active.get("resourceClass")) if active else "(none)"
        print("outputs: %d (%s)" % (len(state.get("screens") or []),
                                    ", ".join(s["name"] for s in state.get("screens") or [])))
        print("desktops: %d" % len(state.get("desktops") or []))
        print("windows: %d" % len(windows))
        print("active app_id: %s" % app)
        sys.stdout.flush()

    # ---- Lifecycle ------------------------------------------------------------

    def quit(self, code):
        if self.closing:
            return
        self.closing = True
        self.exit_code = code
        self.loop.quit()

    def cleanup(self):
        self.unload_script()
        self.queue = []
        try:
            self.release_pending()
        except GLib.Error:
            pass
        if self.registration:
            self.conn.unregister_object(self.registration)
            self.registration = 0
        if self.owns_name:
            self.owns_name = False
            try:
                self.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "ReleaseName", GLib.Variant("(s)", (BUS_NAME,)), "(u)")
            except GLib.Error:
                pass
        self.conn.flush_sync(None)

    def run(self):
        if not self.own_name():
            return 1
        try:
            self.register()
            self.refresh_kwin_owner()
            for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal_add(GLib.PRIORITY_HIGH, signum, lambda *_: (self.quit(0), GLib.SOURCE_REMOVE)[1])
            if self.selftest:
                GLib.timeout_add_seconds(5, self.on_selftest_timeout)
            else:
                GLib.io_add_watch(0, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_stdin)
            self.load_script()
            self.loop.run()
        except (GLib.Error, RuntimeError) as error:
            log(getattr(error, "message", str(error)))
            self.exit_code = 1
        finally:
            self.cleanup()
        return self.exit_code

    def on_selftest_timeout(self):
        log("no snapshot from KWin within 5 s")
        self.quit(1)
        return GLib.SOURCE_REMOVE


def main():
    return Bridge(selftest="--selftest" in sys.argv[1:]).run()


if __name__ == "__main__":
    sys.exit(main())
