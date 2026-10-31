# DeskAI

An AI-aware dock for GNOME. It helps you stay on what you're doing, tells you when something needs
you, and puts your desk back the way it was after a logout. The AI part is optional.

![The dock with the Writing mode on, and a card offering to show a file that was just downloaded](docs/dock.png)

![The command bar above the dock, asking what you want done](docs/command-bar.png)

- **Focus modes.** Writing, Building, Studying, Social: each with its own apps, allowed and blocked
  sites, and notification rules. A nudge when you drift, and a daily focus recap.
- **Inbox on the dock.** WhatsApp messages and AI agents (Claude Code, Codex, Gemini CLI, and the
  desktop AI apps) tell you when they're done or need you. One click takes you there.
- **Command bar.** Press `Super+/` or click the sparkle and type what you want: "set up my project",
  "restart the server", "close my project", "how long did I focus today?". It can only do what's on a
  fixed list, never run commands. Plain requests, in English or Indonesian, are done instantly with no AI:
  switching mode, setting up or closing a project, servers, opening things, your focus time, a mode's lists,
  which projects have work not committed or pushed.
- **Servers on the dock.** The dev servers and database in your projects get a status dot, including the
  ones you started yourself in a terminal. If one DeskAI started stops, you see why, and one click restarts
  it or opens its log. Closing a project asks before stopping anything you started yourself, and tells you
  if it has files not committed or commits not pushed.
- **Docker in the command bar.** Start, stop or restart a Compose project by name, along with the containers it
  needs from other projects, and see which images you have. Running containers get a status dot too.
- **Who has which port.** Ask who has a port, what's free and what's in the way. A Docker project whose port is
  taken says who has it, instead of failing halfway through starting.
- **Room on your computer.** A card when memory or a disk runs low. Ask about your RAM or storage to see what's
  using it, and ask to clear it to see which caches can go. The card's button or "turn off warnings" stops the
  warnings.
- **Music on the dock.** While something plays, in a music app or a browser tab, the dock shows what it
  is with previous, pause and next. Click the song to go to the player. It leaves when the player closes,
  or after half an hour on pause.
- **Who gets through.** The first time someone new is held during a focus mode, the dock shows you the
  message and asks once: keep holding, or always show them. Notifications that are plainly urgent, like a
  deadline or an error, come through anyway.
- **Pick up where you left off.** After a logout, your windows come back with their size, place and
  workspace, your terminals with their tabs and Claude Code conversations, your dev servers, your
  database and Chrome's tabs. It always asks first.
- **Workday routine.** On workday mornings it offers to set up your project like last time.
- **Smart places.** After a download or a screen recording, the dock offers to open it or show it,
  and it learns the folders you go back to. A suggestion leaves the dock after a minute, and clicking Files
  on the dock then takes you there: your latest screenshot, download or recording, or the folder you closed
  Files on.
- **Window motion.** Smooth animations when you switch between windows.
- **Smooth login.** The login animation plays once your desktop and extensions have loaded, so it doesn't
  stutter.
- **Break reminders.** A small card after two hours without a pause. One click turns it off, and
  `breaks.after_minutes` in `~/.config/deskai/modes.json` sets how long.

## Requirements

| | |
|---|---|
| Desktop | **GNOME 50 or 51** on Wayland (Ubuntu 26.04, for example). Other GNOME versions and other desktops such as KDE aren't supported. |
| Needed | Python 3, PyGObject, GTK 4, libadwaita, systemd. The `.deb` and the setup window take care of these. |
| Recommended | **Ptyxis** and **Google Chrome**: terminals and browser tabs come back after a logout with these. |
| Optional | **Claude Code**, an Anthropic API key or **[Ollama](https://ollama.com)** for the smart features. Everything else works without AI. |

So far it has only been tested on Ubuntu 26.04.

## Install

### Ubuntu and Debian

1. Download `deskai_<version>_all.deb` from [Releases](https://github.com/RanggaEghaPermana/deskai/releases).
2. Double-click it and choose Install, or run `sudo apt install ./deskai_*_all.deb`.
3. Open **DeskAI Setup** from the app grid and follow the steps.
4. Log out and back in once. The dock is there from then on.

### Other distributions with GNOME 50 or 51

```sh
git clone https://github.com/RanggaEghaPermana/deskai.git
cd deskai
./setup.sh
```

The setup window copies what it needs, so you can delete the download afterwards.

## What leaves your computer

With AI **off**, or with **Ollama**, nothing does: the model runs on your computer.

With **Claude**, these are sent to it, through your Claude Code login or your API key:

- the titles of the windows you use, and the notifications held back during a focus mode (password
  managers are left out, and you can add more apps under `llm.exclude_apps` in `~/.config/deskai/modes.json`);
- when you use the command bar: what you typed, your modes, the names of your projects, servers and
  installed apps, and today's focus time. No file contents or full paths.

Using an API key costs money, charged by Anthropic.

## Choosing the AI

DeskAI Setup offers Claude Code's login, an API key, Ollama, or no AI. With Claude Code's login, if Claude
stops working (signed out, plan ended, limit reached) and Ollama has the model, DeskAI carries on with it
and tries Claude again every hour.

Ollama is free and private. It only powers the command bar, loading the model when you type and letting it go a
couple of minutes later: small local models judge real windows and notifications badly (see `evals/real.py`), so
with Ollama those are decided by the lists and what DeskAI has learned from you, and for notifications that aren't
plainly urgent, your answer about the sender. It also can't rewrite your modes, though allowing or blocking something
works without it. The default model, `qwen3:1.7b`, downloads 1.4 GB
and needs about 2 GB of free memory while thinking. On a laptop CPU it answers in about 4 seconds, or 10 to 25 when
it has to load first. Pick another with `llm.ollama_model` in `~/.config/deskai/modes.json`.

Everything else stays on your computer in `~/.config/deskai` and `~/.local/state/deskai`.

## How it learns

The AI gets the hard cases only. For each window, DeskAI goes through, in order: your own word on it, your
allow and block lists, a built-in list of what's plain (docs for a coding mode, shops and games anywhere), a small
classifier trained on your corrections, and only then Claude, if you use it. With Ollama, what's still unclear
counts as neutral, so nothing interrupts you on a guess.

To correct it, press `Super+/` and click **Work** or **Distraction**, or right-click the active mode on the dock,
about the window in front of you. Choosing **Allow** or moving to another mode on a nudge counts too. A few times a
day, at a pause, it also asks about a window you spent a while on that it couldn't place. All of it stays in
`~/.local/state/deskai/learned.json`, and the classifier retrains itself right away, with no GPU.

What you say about senders is in `~/.local/state/deskai/senders.json`: their names, never their messages.
`deskai senders` lists them, and `deskai senders --forget NAME` takes your word back.

The command bar shows what you last asked for, and `Up` and `Down` go through it. Your last 30 requests are in
`~/.local/state/deskai/commands.json`, readable only by you, and `Delete` forgets the one you're on.

Your focus time per day is in `~/.local/state/deskai/stats/`, with the titles of the windows that distracted you.
Each day's file is deleted after 90 days.

## What it changes on your computer

- **Hooks in your AI agents**, only if you turn that on during setup: entries in
  `~/.claude/settings.json`, `~/.gemini/settings.json` and `~/.codex/config.toml`. Backups are kept
  as `*.deskai-bak`, and removing DeskAI takes the entries out again.
- **Resuming Claude Code.** Restoring your desk and the workday routine resume conversations with the
  flags they had. If you run Claude Code with `--dangerously-skip-permissions`, it comes back that way.
- **Ptyxis' saved session** is set aside as `session.gvariant.deskai-bak` during a restore, so tabs
  don't come back twice.
- **Clearing caches** only happens when you press the button on the card. It empties the caches of npm, pip, yarn
  and Composer and your thumbnails, and prunes Docker's build cache: all things their tools fill again.
- **Starting and stopping system services** from the command bar goes through GNOME's password prompt.
- **Starting your database without a password** is off unless you ask for it:
  `deskai routine allow-db` prints a polkit rule for you to install.
- **A second extension, DeskAI Login**, holds back GNOME's login animation until your extensions have
  loaded, so the desktop can appear a moment later. Turn it off in the Extensions app if you'd rather see
  it sooner. On GNOME 51 it stays out of the way, since sessions start in the overview there.
- **Other docks go off** the first time DeskAI's dock appears: Ubuntu Dock, Dash to Dock, Dash to Panel and the like.
  They're only turned off, not uninstalled, and removing DeskAI turns them back on. To keep one, turn it on again in
  the Extensions app, and DeskAI leaves it alone. If the dock can't load, on a newer GNOME say, they come back by
  themselves. Docks that are separate apps, such as Plank, aren't touched.
- **The color before the desktop**, on Ubuntu: `com.ubuntu.login-screen background-color` is set to black, unless
  you already chose one, so there's no gray flash at login. Removing DeskAI puts it back.

## Using it

Click a mode on the dock to switch to it. Click the app you're in to minimize its windows on this workspace,
click it again to bring them back, and right-click it to pick one window. Point at an open app to see its windows,
minimized ones too, and click one to go to it. Everything else is in the dock's menus
and cards, and a few things are also on the command line:

```sh
deskai doctor        # check that everything is working
deskai status        # mode, window, inbox
deskai stats         # today's focus recap
deskai senders       # who always gets through, and who stays held
deskai session       # what would come back after a logout
deskai routine       # the workday routine
deskai hooks install # let AI agents report to the dock
```

Modes live in `~/.config/deskai/modes.json`, and the dock picks up changes right away.

## Remove

Open **DeskAI Setup** and choose **Remove DeskAI**, then uninstall the package if you used the
`.deb`. Your modes and stats stay unless you tick the box to delete them too. Uninstalling the package without that
step leaves your other docks off: `gnome-extensions enable ubuntu-dock@ubuntu.com` turns Ubuntu Dock back on.

## Development

```sh
./install.sh            # runs right from this clone
./uninstall.sh          # add --purge to delete your modes and stats too
dev/nested.sh           # a throwaway GNOME Shell with its own HOME, for testing
packaging/build-deb.sh  # builds dist/deskai_<version>_all.deb
python3 -m unittest discover tests
python3 evals/run.py    # scores the AI pipeline on set cases (needs Ollama)
```

`extension/` holds the GNOME Shell extensions, `daemon/` is the background service (`deskaid`) and the
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
