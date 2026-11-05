// iNiR KWin bridge: mirrors KWin's windows, virtual desktops and outputs to
// scripts/kwin/kwin-bridge.py over D-Bus and runs the commands it hands back.
// Loaded and unloaded by the bridge itself (plugin name "inir-kwin-bridge");
// nothing is installed into KWin. Plain ES5 on purpose: KWin's engine is QJS.

var SERVICE = "org.inir.KWinBridge";
var PATH = "/org/inir/KWinBridge";
var IFACE = "org.inir.KWinBridge";

// Lost replies (bridge gone, D-Bus timeout) never call back, so in-flight
// calls are also considered over once they are this old.
var PUSH_STALE_MS = 5000;
var POLL_STALE_MS = 30000;

var pushInFlight = false;
var pushAt = 0;
var pushDirty = false;
var pollSeq = 0;
var polling = false;
var pollAt = 0;
var removed = {};
var watched = {};

function uuidOf(w) {
    return String(w.internalId);
}

function tracked(w) {
    return !!w && w.normalWindow && !w.skipTaskbar && !removed[uuidOf(w)];
}

function desktopIds(list) {
    var out = [];
    for (var i = 0; i < (list ? list.length : 0); i++)
        out.push(String(list[i].id));
    return out;
}

function currentDesktopFor(screen) {
    if (typeof workspace.currentDesktopForScreen === "function") {
        var d = workspace.currentDesktopForScreen(screen);
        if (d) return d;
    }
    return workspace.currentDesktop;
}

function snapshot() {
    var desktops = [];
    var all = workspace.desktops;
    for (var i = 0; i < all.length; i++)
        desktops.push({ id: String(all[i].id), name: String(all[i].name || "") });

    var screens = [];
    for (var s = 0; s < workspace.screens.length; s++) {
        var o = workspace.screens[s];
        var g = o.geometry;
        var cur = currentDesktopFor(o);
        screens.push({ name: String(o.name), x: g.x, y: g.y, width: g.width, height: g.height,
            scale: o.devicePixelRatio || 1, current: cur ? String(cur.id) : "" });
    }

    var stack = {};
    var order = workspace.stackingOrder || [];
    for (var k = 0; k < order.length; k++)
        stack[uuidOf(order[k])] = k;

    var windows = [];
    var list = workspace.windowList();
    var live = {};
    for (var j = 0; j < list.length; j++) {
        var w = list[j];
        live[uuidOf(w)] = true;
        if (!tracked(w)) continue;
        watch(w);
        var uuid = uuidOf(w);
        windows.push({
            uuid: uuid,
            caption: String(w.caption || ""),
            resourceClass: String(w.resourceClass || ""),
            desktopFileName: String(w.desktopFileName || ""),
            pid: w.pid || 0,
            desktops: w.onAllDesktops ? [] : desktopIds(w.desktops),
            output: w.output ? String(w.output.name) : "",
            minimized: !!w.minimized,
            fullScreen: !!w.fullScreen,
            stack: stack[uuid] !== undefined ? stack[uuid] : -1
        });
    }
    for (var r in removed)
        if (!live[r]) delete removed[r];

    var active = workspace.activeWindow;
    var activeScreen = workspace.activeScreen;
    return {
        active: tracked(active) ? uuidOf(active) : "",
        activeScreen: activeScreen ? String(activeScreen.name) : "",
        current: workspace.currentDesktop ? String(workspace.currentDesktop.id) : "",
        desktops: desktops,
        screens: screens,
        windows: windows
    };
}

// Coalesces bursts: one Push in flight, the next one carries everything that
// changed meanwhile.
function push(force) {
    pushDirty = true;
    if (pushInFlight && !force && Date.now() - pushAt < PUSH_STALE_MS) return;
    pushDirty = false;
    pushInFlight = true;
    pushAt = Date.now();
    callDBus(SERVICE, PATH, IFACE, "Push", JSON.stringify(snapshot()), function () {
        pushInFlight = false;
        if (pushDirty) push(false);
        ensurePoll();
    });
}

// Exactly one Next is outstanding. A reply to a superseded call still runs
// its command but does not start another poll.
function poll() {
    pollSeq++;
    var mine = pollSeq;
    polling = true;
    pollAt = Date.now();
    callDBus(SERVICE, PATH, IFACE, "Next", function (cmd) {
        if (cmd) handle(String(cmd));
        if (mine !== pollSeq) return;
        polling = false;
        poll();
    });
}

function ensurePoll() {
    if (!polling || Date.now() - pollAt > POLL_STALE_MS) poll();
}

function findWindow(uuid) {
    var list = workspace.windowList();
    for (var i = 0; i < list.length; i++)
        if (uuidOf(list[i]) === uuid) return list[i];
    return null;
}

function findDesktop(id) {
    var all = workspace.desktops;
    for (var i = 0; i < all.length; i++)
        if (String(all[i].id) === id) return all[i];
    return null;
}

function findScreen(name) {
    for (var i = 0; i < workspace.screens.length; i++)
        if (String(workspace.screens[i].name) === name) return workspace.screens[i];
    return null;
}

function onCurrentDesktop(w) {
    if (w.onAllDesktops) return true;
    var cur = currentDesktopFor(w.output);
    return desktopIds(w.desktops).indexOf(cur ? String(cur.id) : "") >= 0;
}

// Plasma can keep a current desktop per output; switch only the one asked for.
function showDesktop(d, screen) {
    if (screen && typeof workspace.setCurrentDesktopForScreen === "function")
        workspace.setCurrentDesktopForScreen(d, screen);
    else
        workspace.currentDesktop = d;
}

function focus(w) {
    if (w.minimized) w.minimized = false;
    if (!onCurrentDesktop(w) && w.desktops.length > 0)
        showDesktop(w.desktops[0], w.output);
    workspace.activeWindow = w;
}

function handle(json) {
    var cmd;
    try {
        cmd = JSON.parse(json);
    } catch (e) {
        print("inir-kwin-bridge: bad command " + json);
        return;
    }
    var w = cmd.uuid ? findWindow(cmd.uuid) : null;
    var d = cmd.desktop ? findDesktop(cmd.desktop) : null;
    var screen = cmd.output ? findScreen(cmd.output) : null;
    switch (cmd.op) {
    case "focusWindow":
        if (w) focus(w);
        break;
    case "closeWindow":
        if (w) w.closeWindow();
        break;
    case "setDesktop":
        if (d) showDesktop(d, screen);
        break;
    case "moveWindowToDesktop":
        if (!w || !d) break;
        if (screen && w.output !== screen) workspace.sendClientToScreen(w, screen);
        w.desktops = [d];
        if (cmd.focus) {
            showDesktop(d, w.output);
            focus(w);
        }
        break;
    case "setMinimized":
        if (w) w.minimized = !!cmd.value;
        break;
    case "overview":
        callDBus("org.kde.kglobalaccel", "/component/kwin", "org.kde.kglobalaccel.Component",
            "invokeShortcut", "Overview");
        break;
    case "sync":
        push(true);
        break;
    }
}

function changed() {
    push(false);
}

function watch(w) {
    var uuid = uuidOf(w);
    if (watched[uuid]) return;
    watched[uuid] = true;
    w.captionChanged.connect(changed);
    w.desktopsChanged.connect(changed);
    w.outputChanged.connect(changed);
    w.minimizedChanged.connect(changed);
    w.fullScreenChanged.connect(changed);
    if (w.skipTaskbarChanged) w.skipTaskbarChanged.connect(changed);
}

workspace.windowAdded.connect(function (w) {
    watch(w);
    changed();
});
workspace.windowRemoved.connect(function (w) {
    var uuid = uuidOf(w);
    removed[uuid] = true;
    delete watched[uuid];
    changed();
});
workspace.windowActivated.connect(changed);
workspace.currentDesktopChanged.connect(changed);
workspace.desktopsChanged.connect(changed);
workspace.screensChanged.connect(changed);
if (workspace.activeScreenChanged) workspace.activeScreenChanged.connect(changed);
if (workspace.desktopLayoutChanged) workspace.desktopLayoutChanged.connect(changed);

var initial = workspace.windowList();
for (var i = 0; i < initial.length; i++)
    watch(initial[i]);
push(true);
poll();
