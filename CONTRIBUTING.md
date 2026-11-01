# Contributing to DeskAI

Bug reports and pull requests are welcome. Contributions are accepted under the same license as the project.

## Before you write code

- **A bug:** open an [issue](https://github.com/RanggaEghaPermana/deskai/issues) with your distribution, what happened,
  and the output of `deskai doctor`. Check the output for anything private before posting.
- **A new feature:** open an issue first and wait for a yes. A pull request of more than about 400 lines with no agreed
  issue gets closed, however good the code is, because it can't be reviewed well.
- **A security problem:** don't open an issue. [Report it privately](https://github.com/RanggaEghaPermana/deskai/security/advisories/new).

## What belongs in DeskAI

DeskAI helps you stay on what you're doing, tells you when something needs you, and puts your desk back the way it was.
A feature belongs if it does one of those three. Working on a project counts, so the command bar can start and stop what
a project runs and say what's in its way: servers, containers, ports, room on the disk.

It isn't a system administration tool, a chat with an AI, a launcher for everything, or a plugin system.

## What DeskAI has to stay

A change that breaks one of these doesn't get merged, even if it works.

1. **It works without AI.** AI can make a feature better, but nothing may need it.
2. **It can only do what's on a list.** The command bar and the AI pick from a fixed list of actions, and never run a
   command of their own. Adding to what the AI can pick is a decision about what DeskAI is, so it starts with an issue.
3. **Nothing leaves your computer that the README doesn't list.** Anything new that does goes under "What leaves your
   computer", in the same pull request.
4. **It only does what it was asked.** Anything that can't be undone asks first.
5. **It stays quiet.** A card shows up when there's something to see or decide. It has at most three buttons and goes
   away by itself.
6. **When it fails, it says why in plain words,** never a raw error like `[Errno 2]`.
7. **It checks instead of assuming:** whether a program is installed, where a project is, which GNOME this is.
8. **Removing it puts everything back.** Whatever it changes on a computer goes under "What it changes on your
   computer", and uninstalling undoes it.

## Making the change

- One change per pull request. A new feature goes in a module of its own, as `daemon/compose.py` and `daemon/ports.py` do.
- Match the code around it, and add tests for what you change.
- Anything people read goes in both English and Indonesian: `CARD_STRINGS` in `daemon/brain.py` and `STRINGS` in
  `setup/deskai_setup.py`. If you don't speak Indonesian, say so in the pull request and it'll be checked.
- What differs between GNOME versions goes in `extension/deskai@ranggaeghapermana.github.io/compat.js`.
- Try changes to the extension in `dev/nested.sh`, and say which GNOME version you tested on.
- Every mistake in understanding a request gets a case in `evals/cases.py`.
- You can use AI tools, but read and run everything you send, and keep them out of the commit credits.

## Checking it

```bash
dev/check.sh
```

This runs the tests, scores what the command bar's lists understand, and checks the JavaScript, the shell scripts, the
app metadata and your commit messages. If the dock changed, it also tries it in a throwaway GNOME Shell (`dev/smoke.sh`),
which doesn't touch your session. The rules above that a machine can check are in `tests/test_standards.py`, and it says
what it skipped. To run all of it before every push:

```bash
git config core.hooksPath dev/hooks
```

Passing it doesn't mean a change is right. It only catches what a machine can.

## What happens to your pull request

Every change waits for the maintainer's review. Pull requests are squashed when they're merged, so the title and
description become the commit. Say what changed and why, in plain words.
