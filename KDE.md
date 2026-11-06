# iNiR on KDE Plasma (KWin)

This fork runs iNiR's **iRiS** shell on top of **KWin** in a KDE Plasma 6 Wayland session, without Niri and without touching Plasma.
iRiS replaces plasmashell while it runs and gives the normal Plasma desktop back when it stops.

Tested on CachyOS, Plasma / KWin 6.8, Quickshell 0.3, two 4K monitors at 150 %.

## How it works

- **KWin bridge** (`scripts/kwin/`): `kwin-bridge.py` loads `inir-bridge.js` into KWin and turns KWin's state into the
  Niri event stream `NiriService` already reads: outputs, virtual desktops as workspaces, windows (with maximized,
  fullscreen and stacking order), focus, the output under the pointer, and keyboard layouts. Niri actions sent back
  (focus a window or workspace, close, move to a workspace, overview) are translated to KWin calls.
- **Plasma's OSD while plasmashell is away**: volume, microphone and brightness keys are handled by kded/powerdevil,
  which ask `org.kde.plasmashell /org/kde/osdService` to show the level. The bridge answers that service (only when the
  name is free, and hands it back on request), so the levels show on the Island.
- **KDE-safe** (`kde-safe` commits): iNiR never writes Plasma's settings (kdeglobals, kwinrc, shortcuts, Kvantum, ...)
  in a shared Plasma session.
- KWin's global shortcuts are followed, not replaced: e.g. the Dock's launcher opens on KWin's own
  "Application menu on current monitor" (F4 by default).

## Run it

Do **not** run `./setup install` (it installs Niri's dotfiles). Put this repository at `~/.config/quickshell/inir`, then:

```bash
scripts/kwin/inir-kde-on    # stop plasmashell, start iNiR as a transient user unit
scripts/kwin/inir-kde-off   # stop iNiR, start plasmashell again
```

Nothing is installed or enabled at login; logging out also brings Plasma back.

## Options added for KWin

| Option | Default | What it does |
| --- | --- | --- |
| `iris.bar.hideOverMaximized` | `false` | The Island and the pieces on its edge hide while the front window on an output is maximized, which gets their room; the pointer against the edge brings them back, like a menu bar. |
| `iris.notifications.position` | `"center"` | `"right"` / `"left"`: banners in a corner, hung from the frame like the pieces. |
| `iris.bar.externalDashboard` | `[]` | A command that replaces the Island's desktop page with another shell's dashboard (gets the Island's geometry and motion as JSON). |
| `iris.dock.externalLauncher` | `[]` | The same for the Dock's launcher. |
| `iris.dock.hoverPreviews` | `true` | Live window previews on hover (KWin window streams). |

Other fixes: popups and workspace switching follow the output under the pointer, banners stay on the output they
appeared on and never stack over each other, an empty bubble reserves no screen edge, crisp icons under fractional scaling.

## Credits

iNiR is by [snowarch](https://github.com/snowarch/iNiR), GPL-3.0. This fork only adds the KWin adaptation.
