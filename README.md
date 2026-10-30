# FocusDock

A dock for GNOME that helps you stay on what you're doing, tells you when something needs you,
and puts your desk back the way it was after a logout.

- **Focus modes.** Writing, Building, Studying, Social: each with its own apps, allowed and blocked
  sites, and notification rules. A nudge when you drift, and a daily focus recap.
- **Inbox on the dock.** WhatsApp messages and AI agents (Claude Code, Codex, Gemini CLI, and the
  desktop AI apps) tell you when they're done or need you. One click takes you there.
- **Pick up where you left off.** After a logout, your windows come back with their size, place and
  workspace, your terminals with their tabs and Claude Code conversations, your dev servers, your
  database and Chrome's tabs. It always asks first.
- **Workday routine.** On workday mornings it offers to set up your project like last time.
- **Smart places.** After a download or a screen recording, the dock offers to open it or show it,
  and it learns the folders you go back to.
- **Window motion.** Smooth animations when you switch between windows.

## Requirements

| | |
|---|---|
| Desktop | **GNOME 50** on Wayland (Ubuntu 26.04, for example). Other GNOME versions and other desktops such as KDE aren't supported. |
| Needed | Python 3, PyGObject, GTK 4, libadwaita, systemd. The `.deb` and the setup window take care of these. |
| Recommended | **Ptyxis** and **Google Chrome**: terminals and browser tabs come back after a logout with these. |
| Optional | **Claude Code** or an Anthropic API key, for the smart features. Everything else works without AI. |

So far it has only been tested on Ubuntu 26.04.

## Install

### Ubuntu and Debian

1. Download `focusdock_<version>_all.deb` from [Releases](https://github.com/RanggaEghaPermana/focusdock/releases).
2. Double-click it and choose Install, or run `sudo apt install ./focusdock_*_all.deb`.
3. Open **FocusDock Setup** from the app grid and follow the steps.
4. Log out and back in once. The dock is there from then on.

### Other distributions with GNOME 50

```sh
git clone https://github.com/RanggaEghaPermana/focusdock.git
cd focusdock
./setup.sh
```

The setup window copies what it needs, so you can delete the download afterwards.

## What leaves your computer

With AI **off**, nothing does.

With AI **on**, the titles of the windows you use and the notifications held back during a focus mode
are sent to Claude, through your Claude Code login or your API key. Password managers are left out,
and you can add more apps under `llm.exclude_apps` in `~/.config/focusdock/modes.json`.
Using an API key costs money, charged by Anthropic.

Everything else stays on your computer in `~/.config/focusdock` and `~/.local/state/focusdock`.

## What it changes on your computer

- **Hooks in your AI agents**, only if you turn that on during setup: entries in
  `~/.claude/settings.json`, `~/.gemini/settings.json` and `~/.codex/config.toml`. Backups are kept
  as `*.focusdock-bak`, and removing FocusDock takes the entries out again.
- **Resuming Claude Code.** Restoring your desk and the workday routine resume conversations with the
  flags they had. If you run Claude Code with `--dangerously-skip-permissions`, it comes back that way.
- **Ptyxis' saved session** is set aside as `session.gvariant.focusdock-bak` during a restore, so tabs
  don't come back twice.
- **Starting your database without a password** is off unless you ask for it:
  `focusdock routine allow-db` prints a polkit rule for you to install.

## Using it

Click a mode on the dock to switch to it. Everything else is in the dock's menus and cards, and a
few things are also on the command line:

```sh
focusdock doctor        # check that everything is working
focusdock status        # mode, window, inbox
focusdock stats         # today's focus recap
focusdock session       # what would come back after a logout
focusdock routine       # the workday routine
focusdock hooks install # let AI agents report to the dock
```

Modes live in `~/.config/focusdock/modes.json`, and the dock picks up changes right away.

## Remove

Open **FocusDock Setup** and choose **Remove FocusDock**, then uninstall the package if you used the
`.deb`. Your modes and stats stay unless you tick the box to delete them too.

## Development

```sh
./install.sh            # runs right from this clone
./uninstall.sh          # add --purge to delete your modes and stats too
dev/nested.sh           # a throwaway GNOME Shell with its own HOME, for testing
packaging/build-deb.sh  # builds dist/focusdock_<version>_all.deb
```

`extension/` is the GNOME Shell extension, `daemon/` is the background service (`focusd`) and the
`focusdock` command, and `setup/` is the setup window. GNOME only loads extension code when you log
in, so test extension changes in `dev/nested.sh`.

## License

[GPL-3.0-or-later](LICENSE)
