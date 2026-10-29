---
name: evolve
description: Evolve this project's skills and instructions for HARNESS from the sessions run in it. Use when the user types /evolve, /evolve diff, /evolve amend, /evolve apply, /evolve discard, /evolve ignore or /evolve status, or asks to improve the project's agent configuration from past sessions.
---

# Evolve

This project's HARNESS configuration (its skills and instructions) is evolved by thearc. A **round** reads the
HARNESS sessions run in the project since the last one, reflects on each, and curates the configuration from the
reflections into a **proposal**: a new version, not yet installed. The user reviews it, amends it, and applies it.
All the work is done by the `thearc evolve` command; your part is to run it and present what it says.

## Commands

| The user types | You run |
|---|---|
| `/evolve` | `thearc evolve --json --open` |
| `/evolve diff` | `thearc evolve diff` |
| `/evolve amend` | `thearc evolve amend -m "<why, in the user's words>"` |
| `/evolve apply` | `thearc evolve apply --json` (`--push` to push the workspace) |
| `/evolve discard` | `thearc evolve discard` |
| `/evolve ignore <pattern>` | `thearc evolve ignore <pattern>` (`--remove` to stop ignoring) |
| `/evolve status` | `thearc evolve status` |

Run them in the project's root folder. If `thearc` is missing, or `thearc --version` differs from the `thearc` version
in this skill's frontmatter, say so before running anything.

## A round

`thearc evolve` takes minutes: reflections and curation run in their own agent sessions. Run it in the
background and wait for it to finish; do not start a second one.

Its first line (on stderr) is `Progress: file://…/.thearc/cache/progress.html`: a page that shows the round live —
which step it is on, each session's reflection as it finishes, and the round's log — and `--open` opens it in the
browser. Give the user that link straight away, before waiting.

When it finishes, show the user:

1. the new version and what it changes: for each changed section, the reason given and the ratings behind it;
2. the diff (`thearc evolve diff`), or its most important parts when it is long;
3. the report's path, for the whole picture, and the round's progress page (`progress` in the JSON).

If it reports that there is nothing to do yet (too few sessions), say how many sessions it found and stop.

## Amendments

While a proposal is open, its files are in `.thearc/workspace/`. When the user asks for a change to the
proposal, edit those files, not the installed ones: installed files only change on apply. After each change the
user accepts, commit it with `thearc evolve amend -m "<why>"`. Keep the `<!-- res_… -->` markers
on headings: they carry each section's history.

## Applying

`/evolve apply` releases the proposal and installs it. Tell the user that the changes take effect in their **next**
session, since a harness reads skills when a session starts. Run `thearc evolve apply --push --json` when the user asks
to push too: it pushes the workspace to its remote. Either way the user commits the installed files and
`.thearc/workspace` (a git submodule) together.

## The recipe

How a round works is `evolve.py` next to this file, with its prompts in `prompts/`. This skill is not part of the
configuration that evolves: edit the recipe and prompts here when the user asks, and the next round uses them.

## What you do not do

- Do not review the sessions or edit the configuration yourself during a round; the round does that.
