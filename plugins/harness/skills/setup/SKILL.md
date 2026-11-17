---
name: setup
description: >
  Set up the default tier of the harness on this account, check it, or take it
  off. Run with no argument to set up (print every file and setting to be
  written, take a yes, apply, make the first ledger write, check the result),
  with "doctor" to check an existing setup, or with "uninstall" to remove what
  setup placed. Use when the person says "set up the harness", "check my
  harness setup" or "uninstall the harness". Run by the person only.
argument-hint: "[doctor | uninstall]"
disable-model-invocation: true
---

# Harness setup

This skill drives one script, `setup.py`, which sits two folders above this
file. The script does every write; this skill shows what it will do, asks, runs
it and reports what it printed. Its verbs are `plan`, `apply`, `ledger-init`,
`doctor` and `remove`.

Read the argument. No argument means **Set up**; `doctor` means **Doctor**;
`uninstall` means **Uninstall**. Any other argument: say which three are
accepted and stop.

Rules that hold in every mode:

- Run each script command as one Bash call, exactly as written below. Home is
  the person's home folder, as an absolute path: `"$HOME"`.
- Show the script's output to the person as it is. Never summarize a plan.
- If the script refuses (one line starting `setup:`), show that line, say
  nothing was changed beyond what it names, and stop. Do not retry, do not edit
  `settings.json` or any file by hand, and do not work around the refusal.
- Never pass `--approve-all`. Never write to the person's rules folder, the
  settings file or the ledger yourself.
- Never ask for, print or store a credential.

## Set up

1. **Who the ledger's writes are for.** Ask the person for the operator name:
   the name the ledger stamps on what they write. Use `claude-code` as the
   harness name unless they give another.

2. **Print the plan.** Nothing is written by this step:

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" plan --home "$HOME" --operator <operator> --harness claude-code
   ```

   Show the whole output. Say what it holds: each `FILE` is a file that will be
   created (the rules default, the override starter, the setup record, the lock
   file and the ledger database); `LEDGER` is the three ledger options that
   Claude Code will store, which is the one change made to the person's
   settings file besides the optional items; `FIRST LEDGER WRITE` is the
   command that creates the database when it is absent; each `OPTIONAL` item is
   a settings change that is only made if the person says yes to that item.
   Nothing the plan lists grants permission to anything.

3. **Take the yes.** First ask one yes or no for the files, the ledger options
   and the first ledger write together. On no, stop: nothing was written.
   Then ask about each `OPTIONAL` item on its own, in the order printed, as a
   separate question showing that item's exact text. A yes to one item is never
   a yes to another, and the answer to the files is never a yes to an item.
   Saying no to every item leaves a complete setup.

4. **Apply.** Pass one `--approve <id>` for each item the person said yes to,
   using the id printed after `OPTIONAL`, and nothing for the others:

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" apply --home "$HOME" --operator <operator> --harness claude-code --approve <id> --approve <id>
   ```

   If the output says a file "already existed" and was kept, tell the person
   that file is theirs and will not be touched by uninstall.

5. **The first ledger write.** This creates the ledger database file when it is
   absent, and does nothing when one is already there:

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" ledger-init --home "$HOME" --operator <operator> --harness claude-code
   ```

   If it says the ledger command is not on PATH, the ledger plugin is not
   loaded in this session yet. Tell the person to run `/reload-plugins`, then
   run this step again. Do not look for the command anywhere else.

6. **Check the result.**

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" doctor --home "$HOME"
   ```

   Report the placed files, the stored options and the database file, one line
   each, from what it printed. Any line starting `FAIL` is a problem: show it
   and what it names. Do not fix it by hand.

7. **End by telling the person to start a new session.** The ledger's options
   reach a session's commands only when a session starts, so the rules default
   and the ledger are in force from the next session, not this one. Say that,
   and that `/harness:setup doctor` checks the setup again at any time.

## Doctor

Run the `doctor` command from step 6 above and report it as there: the placed
files, the stored options, the database file and the tools. It reads and
writes nothing. A `note` line is information, a `FAIL` line is a problem to
show. If the first lines say setup has not run, say to run `/harness:setup`.

## Uninstall

This is step 1 of three. Steps 2 and 3 are shell commands the person runs
themselves, after this one, because no hook runs when a plugin is uninstalled.

1. Run `doctor` as above. From it, show the person what uninstall will do:
   the `files` lines (a file marked unchanged is removed; a file marked changed
   is kept and named) and the `settings` lines (an item marked in place is
   removed; one marked moved, changed or gone is kept or skipped and named).
   Say the ledger database at its path is kept, because it is their data.
2. Ask one yes or no to remove them. On no, stop.
3. Run:

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" remove --home "$HOME"
   ```

   Show the output. Every `kept for a manual check` line names a file the
   person has changed: tell them it was left, and that they can delete it
   themselves. If they ask you to delete the ledger database, show its full
   path first, say it holds all their ledger entries and cannot be recovered,
   and delete only that one file after a second, separate yes.
4. Tell the person the two commands that finish the uninstall, to run in a
   terminal and in this order:

   ```text
   claude plugin uninstall harness@harness-core --prune -y
   claude plugin marketplace remove harness-core
   ```
