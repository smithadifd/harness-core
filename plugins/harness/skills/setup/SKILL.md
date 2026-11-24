---
name: setup
description: >
  Set up the default tier of the harness on this account, check it, replace
  the rules default after a tag move, or take it off. Run with no argument to
  set up (print every file and setting to be written, take a yes, apply, make
  the first ledger write, check the result), with "doctor" to check an
  existing setup, with "update" to replace the rules default when it is
  unchanged since setup, with "uninstall" to remove what setup placed, or
  with "instance" to make the optional instance folder of plans and
  decisions. Use when the person says "set up the harness", "check my harness
  setup", "update the harness rules", "uninstall the harness" or "make an
  instance folder". Run by the person only.
argument-hint: "[doctor | uninstall | instance | update]"
disable-model-invocation: true
---

# Harness setup

This skill drives one script, `setup.py`, which sits two folders above this
file. The script does every write; this skill shows what it will do, asks, runs
it and reports what it printed. Its verbs are `plan`, `apply`, `ledger-init`,
`doctor`, `update`, `remove`, `instance-plan` and `instance-create`.

Read the argument. No argument means **Set up**; `doctor` means **Doctor**;
`update` means **Update**; `uninstall` means **Uninstall**; `instance` means
**Instance folder**. Any other argument: say which five are accepted and stop.

Rules that hold in every mode:

- Run each script command as one Bash call, exactly as written below. Home is
  the person's home folder, as an absolute path: `"$HOME"`.
- Show the script's output to the person as it is. Never summarize a plan.
  A Bash result is collapsed in the person's terminal and is not read unless
  they expand it, so "show" always means: copy the output into your own reply
  text, in full, as a fenced block. Do this before you ask any question that
  depends on it.
- If the script refuses (one line starting `setup:`), show that line, say
  nothing was changed beyond what it names, and stop. Do not retry, do not edit
  `settings.json` or any file by hand, and do not work around the refusal.
- Never pass `--approve-all`. Never write to the person's rules folder, the
  settings file, the ledger or an instance folder yourself.
- Never ask for, print or store a credential.
- Single-quote every value you put into a command: the operator, the harness,
  each `--approve` id, the ledger database's path and the instance folder's
  path, exactly as the templates below show. The deny ids contain parentheses
  (`deny:Read(.env)`), which the shell reads as syntax, so an unquoted id stops
  the command before the script runs. If a value itself holds a single quote,
  write that quote as `'\''`.

## Set up

1. **Who the ledger's writes are for.** Ask the person for the operator name:
   the name the ledger stamps on what they write. Ask it as a plain question
   in your own reply, such as "What name should the ledger stamp on what you
   write? Type it as your next message.", and stop and wait for their next
   message to be the name. Never ask for it with a picker, option list or
   question dialog: its free-text choice dismisses the question. The harness
   name is `claude-code` unless they give another; use it wherever
   `<harness>` appears. If the person tells you they already keep a ledger
   database at another path, add `--db '<path>'`, with that absolute path (a
   leading `~` replaced with their home folder), to the `plan`, `apply` and
   `ledger-init` commands below, the same path in all three. Never choose or
   suggest a path for them. Setup then lists that path in the plan, stores it
   as the ledger's database option and in the setup record, and uses a
   database already there as it is: it never makes a second one. A later
   ledger-init without `--db` uses that recorded path; `--db` that names a
   different path is refused.

2. **Print the plan.** Nothing is written by this step:

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" plan --home "$HOME" --operator '<operator>' --harness '<harness>'
   ```

   Copy the whole output into your own reply, in full (every `FILE`, the
   `LEDGER` options, the `FIRST LEDGER WRITE` command and each `OPTIONAL`
   item with its exact text), before you ask the yes. Then say what it holds:
   each `FILE` is a file that will be
   created (the rules default, the override starter, the setup record, the lock
   file and the ledger database); `LEDGER` is the three ledger options that
   Claude Code will store, which is the one change made to the person's
   settings file besides the optional items; `FIRST LEDGER WRITE` is the
   command that creates the database when it is absent; each `OPTIONAL` item is
   a settings change that is only made if the person says yes to that item.
   Nothing the plan lists grants permission to anything.

3. **Take the yes.** The plan must already be in your own reply, in full, from
   step 2. First ask one yes or no for the files, the ledger options
   and the first ledger write together. On no, stop: nothing was written.
   Then ask about each `OPTIONAL` item on its own, in the order printed, as a
   separate question showing that item's exact text. A yes to one item is never
   a yes to another, and the answer to the files is never a yes to an item.
   Saying no to every item leaves a complete setup. When you ask about the
   `prompt-suggestions` item, say what it is for: Claude Code can show a grey
   suggested reply already typed into the prompt box, such as a "yes" at a
   decision point, and pressing Enter sends it; the item turns those
   suggestions off (`promptSuggestionEnabled` set to `false`), so an empty
   prompt stays empty. No leaves Claude Code's suggestions as they are. Setup
   adds the key only when it is absent, and uninstall takes it out again.

4. **Apply.** Pass one `--approve <id>` for each item the person said yes to,
   using the id printed after `OPTIONAL`, and nothing for the others:

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" apply --home "$HOME" --operator '<operator>' --harness '<harness>' --approve '<id>' --approve '<id>'
   ```

   If the output says a file "already existed" and was kept, tell the person
   that file is theirs and will not be touched by uninstall.

5. **The first ledger write.** This creates the ledger database file when it is
   absent, and does nothing when one is already there:

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" ledger-init --home "$HOME" --operator '<operator>' --harness '<harness>'
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

7. **Offer an instance folder.** Say in a sentence or two that setup can also
   make an instance folder: a new folder of their choosing, made a local git
   repository, for plans and decisions in markdown, that setup records nothing
   about and uninstall never touches. Ask once whether they want one. On yes,
   do the **Instance folder** steps below, then go on. On no, or when they do
   not answer, do not run it and do not ask again. Never run it without a yes.

8. **End by telling the person to start a new session.** The ledger's options
   reach a session's commands only when a session starts, so the rules default
   and the ledger are in force from the next session, not this one. Say that,
   and that `/harness:setup doctor` checks the setup again at any time, and
   that `/harness:setup instance` makes an instance folder later if they change
   their mind.

## Instance folder

An optional extra, run only when the person asks for it: from the offer at the
end of **Set up**, or as `/harness:setup instance`. It makes a new folder of
the person's choosing a local git repository holding a short README, a
`.gitignore` and a README for each of `roadmaps/`, `decisions/` and
`standards/`. It holds markdown only: no ledger database and no settings
change. The folder is the person's own: setup records nothing about it, and
`doctor` and `uninstall` never touch it.

1. **Ask where.** Ask for the folder as an absolute path to a folder that does
   not exist yet, or exists and is empty. If they write a leading `~`, replace
   it with their home folder. Never choose or suggest a path for them. The
   folder cannot be inside their `.claude` folder.

2. **Print the plan.** Nothing is written by this step:

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" instance-plan --home "$HOME" --path '<path>'
   ```

   Show the whole output: the folder, each file with its text, and the `git`
   command. A refusal names the problem (the folder is not empty, a symlink,
   a file, under `.claude`, or `git` is not on PATH): show it and stop.

3. **Take the yes.** Ask one yes or no to create exactly what the plan shows.
   On no, stop: nothing was written.

4. **Create it.**

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" instance-create --home "$HOME" --path '<path>'
   ```

   Show the output. If it refuses, it has already removed what it made; say so
   and do not retry or create anything by hand.

5. **Tell the person what is theirs.** The folder is a git repository with no
   commit and no remote; they commit with their own git identity when they
   choose (`git add -A`, then `git commit`), and you never do it for them. It
   is not in the setup record, so `/harness:setup doctor` does not list it and
   uninstall leaves it: to remove it they delete the folder themselves.

## Doctor

Run the `doctor` command from step 6 above and report it as there: the placed
files, the stored options, the database file and the tools. It reads and
writes nothing. A `note` line is information, a `FAIL` line is a problem to
show. If the first lines say setup has not run, say to run `/harness:setup`.


## Update

After the plugin is at a newer tag, replace the installed rules default with
the plugin's current file when the installed file is still the bytes setup
recorded. The override file and every settings item are left alone.

1. Run `doctor` as above. A `note files: newer default available` line means
   the installed rules file differs from the plugin's default.
2. Run:

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" update --home "$HOME"
   ```

   Show the output. If it refuses (the file was changed since setup, there is
   no record, the installed file is missing, or its hash cannot be read), show
   that line, say nothing was changed, and stop. Do not overwrite the file by
   hand.

## Uninstall

This is step 1 of three. Steps 2 and 3 are shell commands the person runs
themselves, after this one, because no hook runs when a plugin is uninstalled.

1. Run `doctor` as above. From it, show the person what uninstall will do:
   the `files` lines (a file marked unchanged is removed; a file marked changed
   is kept and named) and the `settings` lines (an item marked in place is
   removed; one marked moved, changed or gone is kept or skipped and named).
   Say the ledger database at its path is kept, because it is their data, and
   that an instance folder, if they made one, is theirs and is not touched.
2. Ask one yes or no to remove them. On no, stop.
3. Run:

   ```text
   python3 "${CLAUDE_SKILL_DIR}/../../setup.py" remove --home "$HOME"
   ```

   Show the output. Report only what it names: the `kept for a manual check`
   lines, the `already gone` lines, the kept ledger database and the removed
   setup record. It does not name the placed files or settings items it took
   out, so do not say it removed them, and do not say it removed the options:
   say that the files and items the earlier `doctor` marked unchanged or in
   place are the ones it removes, and that the person can look in their rules
   folder to confirm. The ledger's stored options stay until the plugin is
   uninstalled. Every `kept for a manual check` line names a file the
   person has changed: tell them it was left, and that they can delete it
   themselves. If they ask you to delete the ledger database, show its full
   path first, say it holds all their ledger entries and cannot be recovered,
   and delete only that one file after a second, separate yes.
4. Tell the person the two commands that finish the uninstall, to run in a
   terminal and in this order (the second one also removes `rails`, if they
   installed it, and leaves the list showing none of the four):

   ```text
   claude plugin uninstall harness@harness-core --prune -y
   claude plugin marketplace remove harness-core
   ```
