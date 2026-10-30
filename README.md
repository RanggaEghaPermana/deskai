# DeskAI

An AI-aware dock for GNOME. It helps you stay on what you're doing, tells you when something needs
you, and puts your desk back the way it was after a logout. The AI part is optional.

- **Focus modes.** Writing, Building, Studying, Social: each with its own apps, allowed and blocked
  sites, and notification rules. A nudge when you drift, and a daily focus recap.
- **Inbox on the dock.** WhatsApp messages and AI agents (Claude Code, Codex, Gemini CLI, and the
  desktop AI apps) tell you when they're done or need you. One click takes you there.
- **Command bar.** Press `Super+/` or click the sparkle and type what you want: "set up my project",
  "restart the server", "switch to Writing", "how long did I focus today?". It can only do what's on
  a fixed list, never run commands. A mode's name and restarting a server work without AI too.
- **Servers on the dock.** The dev servers and database DeskAI started get a status dot. If one stops,
  you see why, and one click restarts it or opens its log.
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

1. Download `deskai_<version>_all.deb` from [Releases](https://github.com/RanggaEghaPermana/deskai/releases).
2. Double-click it and choose Install, or run `sudo apt install ./deskai_*_all.deb`.
3. Open **DeskAI Setup** from the app grid and follow the steps.
4. Log out and back in once. The dock is there from then on.

### Other distributions with GNOME 50

```sh
git clone https://github.com/RanggaEghaPermana/deskai.git
cd deskai
./setup.sh
```

The setup window copies what it needs, so you can delete the download afterwards.

## What leaves your computer

With AI **off**, nothing does.

With AI **on**, these are sent to Claude, through your Claude Code login or your API key:

- the titles of the windows you use, and the notifications held back during a focus mode (password
  managers are left out, and you can add more apps under `llm.exclude_apps` in `~/.config/deskai/modes.json`);
- when you use the command bar: what you typed, your modes, the names of your projects, servers and
  installed apps, and today's focus time. No file contents or full paths.

Using an API key costs money, charged by Anthropic.

Everything else stays on your computer in `~/.config/deskai` and `~/.local/state/deskai`.

## What it changes on your computer

- **Hooks in your AI agents**, only if you turn that on during setup: entries in
  `~/.claude/settings.json`, `~/.gemini/settings.json` and `~/.codex/config.toml`. Backups are kept
  as `*.deskai-bak`, and removing DeskAI takes the entries out again.
- **Resuming Claude Code.** Restoring your desk and the workday routine resume conversations with the
  flags they had. If you run Claude Code with `--dangerously-skip-permissions`, it comes back that way.
- **Ptyxis' saved session** is set aside as `session.gvariant.deskai-bak` during a restore, so tabs
  don't come back twice.
- **Starting your database without a password** is off unless you ask for it:
  `deskai routine allow-db` prints a polkit rule for you to install.

## Using it

Click a mode on the dock to switch to it. Everything else is in the dock's menus and cards, and a
few things are also on the command line:

```sh
deskai doctor        # check that everything is working
deskai status        # mode, window, inbox
deskai stats         # today's focus recap
deskai session       # what would come back after a logout
deskai routine       # the workday routine
deskai hooks install # let AI agents report to the dock
```

Modes live in `~/.config/deskai/modes.json`, and the dock picks up changes right away.

## Remove

Open **DeskAI Setup** and choose **Remove DeskAI**, then uninstall the package if you used the
`.deb`. Your modes and stats stay unless you tick the box to delete them too.

## Development

```sh
./install.sh            # runs right from this clone
./uninstall.sh          # add --purge to delete your modes and stats too
dev/nested.sh           # a throwaway GNOME Shell with its own HOME, for testing
packaging/build-deb.sh  # builds dist/deskai_<version>_all.deb
python3 -m unittest discover tests
```

`extension/` is the GNOME Shell extension, `daemon/` is the background service (`deskaid`) and the
`deskai` command, and `setup/` is the setup window. GNOME only loads extension code when you log
in, so test extension changes in `dev/nested.sh`.

## Reporting bugs

Open an [issue](https://github.com/RanggaEghaPermana/deskai/issues) with your distribution, what
happened, and the output of `deskai doctor`. Check the output for anything private before posting.

## Contributing

Pull requests are welcome. Contributions are accepted under the same license as the project. Please
test changes in `dev/nested.sh`, and say which GNOME version you tested on.

## Terms and license

DeskAI is free software, licensed under [GPL-3.0-or-later](LICENSE).

- You can use it for anything, including commercial work, at no cost.
- You can study it, change it and share it.
- If you distribute it or a changed version, you have to keep this license and provide the source code.
- It comes **with no warranty**. You use it at your own risk.

This is an early release. DeskAI uses parts of GNOME Shell that can change, so a new GNOME version
may break it until it's updated. It isn't affiliated with GNOME, Anthropic, Google or Meta; their names
and products belong to their owners.
