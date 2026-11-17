#!/bin/bash
# The git guard's commit style gate (design § 2.2 C1, § 12.3 K rows): with
# commit_style on, the first message of a top-level git commit is read; a
# readable first line that is not conventional, or a message the guard cannot
# read, gets the option's verdict. Exemptions are read only in option
# position. The source suite's commit rows are moved here.
# Command strings are inert JSON data and are never executed.
set -u
LC_ALL=C
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE=commit
. "$HERE/fixtures/git-guard/harness.sh"

GG_COMMIT=deny
HEREDOC_OK=$'git commit -m "$(cat <<\'EOF\'\nfeat: x\n\nA body line.\nEOF\n)"'
HEREDOC_BAD=$'git commit -m "$(cat <<\'EOF\'\nnope\n\nA body line.\nEOF\n)"'
HEREDOC_UNQUOTED=$'git commit -m "$(cat <<EOF\nfeat: x\n\nA body line.\nEOF\n)"'
HEREDOC_APOSTROPHE=$'git commit -m "$(cat <<\'EOF\'\nfeat: x\n\nIt doesn\'t balance.\nEOF\n)"'

printf '%s\n' '== K rows, commit_style=deny =='
gg_row "K-1 conventional" silent 'git commit -m "feat: x"'
gg_row "K-2 not conventional" deny:commit_nonconventional 'git commit -m nope'
gg_row "K-3 -am cluster" deny:commit_nonconventional 'git commit -am nope'
gg_row "K-4 -mnope attached" deny:commit_nonconventional 'git commit -mnope'
gg_row "K-5 --message=" deny:commit_nonconventional 'git commit --message=nope'
gg_row "K-6 a later -m is a body paragraph" silent 'git commit -m "feat: x" -m "Body text."'
gg_row "K-7 --amend is exempt" silent 'git commit --amend -m nope'
gg_row "K-8 -F is exempt" silent 'git commit -F msg.txt'
gg_row "K-9 a variable message is unreadable" deny:commit_message_unreadable 'git commit -m "$MSG"'
gg_row "K-10 the quoted-delimiter heredoc subject" silent "$HEREDOC_OK"
gg_row "K-11 a commit inside sh -c is not judged" silent "sh -c 'git commit -m nope'"
gg_row "K-12 scope and bang" silent 'git commit -m "fix(scope)!: x"'
gg_row "K-13 GIT in capitals" deny:commit_nonconventional 'GIT commit -m nope'
GG_COMMIT=ask
gg_row "K-14 commit_style=ask" ask:commit_nonconventional 'git commit -m nope'
GG_COMMIT=deny
gg_row "K-15 --m= prefix" deny:commit_nonconventional 'git commit --m=nope'
gg_row "K-16 --me value prefix" deny:commit_nonconventional 'git commit --me nope'
gg_row "K-17 --mes= prefix" deny:commit_nonconventional 'git commit --mes=nope'
gg_row "K-18 an ANSI-C message is decoded" deny:commit_nonconventional "git commit -m \$'nope'"
gg_row "K-19 an ANSI-C conventional message" silent "git commit -m \$'feat: x'"
gg_row "K-20 the heredoc shape, subject not conventional" deny:commit_nonconventional "$HEREDOC_BAD"
gg_row "K-21 an unquoted heredoc delimiter is unreadable" deny:commit_message_unreadable "$HEREDOC_UNQUOTED"
gg_row "K-22 a substitution message is unreadable" deny:commit_message_unreadable 'git commit -m "$(git log -1 --format=%s)"'
gg_row "K-23 the heredoc shape with an odd apostrophe (the copied scan refuses it)" deny:commit_message_unreadable "$HEREDOC_APOSTROPHE"
GG_COMMIT=off
gg_row "K-24 commit_style=off, a variable message" silent 'git commit -m "$MSG"'
GG_COMMIT=deny
gg_row "K-25 an option-looking value is not an option" deny:commit_nonconventional 'git commit --allow-empty -m "--amend"'
gg_row "K-25b --amend in option position" silent 'git commit --amend -m "x"'
gg_row "K-25c --fixup= is exempt" silent 'git commit --fixup=HEAD -m nope'
gg_row "K-25c -c HEAD is exempt" silent 'git commit -c HEAD -m nope'
gg_row "K-25d -c as the value of -m" deny:commit_nonconventional 'git commit -m "-c" --allow-empty'
gg_row "K-26 an escaped backtick in a double-quoted message" deny:commit_message_unreadable 'git commit -m "fix: handle \`null\` input"'
gg_row "K-26b an escaped dollar in a double-quoted message" deny:commit_message_unreadable 'git commit -m "fix: costs \$5"'
GG_MAIN=off
GG_FORCE=off
gg_row "K-27 a segmenter refusal with a push word: the commit verdict wins" deny:commit_message_unreadable "$HEREDOC_APOSTROPHE && git push origin feature"
GG_MAIN=ask
GG_FORCE=ask
gg_row "K-28 --gpg-sign takes only an attached value" deny:commit_nonconventional 'git commit --gpg-sign -m "wip"'
gg_row "K-29 --no-verify takes no value" deny:commit_nonconventional 'git commit --no-verify -m "wip"'
gg_row "K-30 --untracked-files takes only an attached value" deny:commit_nonconventional 'git commit --untracked-files -m "wip"'
gg_row "K-31 --gpg-sign= with an attached value" deny:commit_nonconventional 'git commit --gpg-sign=KEY -m "wip"'
gg_row "K-32 -S takes only an attached value" deny:commit_nonconventional 'git commit -S -m "wip"'
gg_row "K-33 -u takes only an attached value" deny:commit_nonconventional 'git commit -u -m "wip"'
gg_row "K-34 --author takes the next word" silent 'git commit --author "-m nope" -m "feat: x"'
gg_row "K-35 dashed git-commit" deny:commit_nonconventional 'git-commit -m nope'
gg_row "K-36 a global option before commit" deny:commit_nonconventional 'git -C . commit -m nope'
gg_row "K-37 no message: the editor is not judged" silent 'git commit'
gg_row "K-38 -Fm: F takes the rest" silent 'git commit -Fm'
gg_row "K-39 echo of a commit is data" silent 'echo git commit -m nope'
gg_row "K-40 a commit in a subshell" deny:commit_nonconventional '(git commit -m nope)'
gg_row "K-41 --reuse-message= is exempt" silent 'git commit --reuse-message=HEAD -m nope'
gg_row "K-42 --sq= abbreviation is exempt" silent 'git commit --sq=HEAD -m nope'
gg_row "K-43 an unknown git option before commit is unreadable" deny:commit_message_unreadable 'git --future commit -m nope'

printf '%s\n' '== Commit style at the default (off) =='
GG_COMMIT=off
gg_row "K-D1 a non-conventional commit at the default" silent 'git commit -m nope'
gg_row "K-D2 the case arm row at the default" silent 'case x in x) git commit --amend;; *) git commit -m nope;; esac'
GG_COMMIT=deny

printf '%s\n' '== Rows moved from the source suite (GT:<line> is the source line) =='
GG_COMMIT=deny
gg_row_at "$REPO_FEATURE" $'GT:342 case boundary exposes later bad commit' deny $'case x in x) git commit --amend;; *) git commit -m nope;; esac'
GG_COMMIT=off
gg_row_at "$REPO_FEATURE" $'GT:342 case boundary exposes later bad commit (at the default commit_style=off)' silent $'case x in x) git commit --amend;; *) git commit -m nope;; esac'
GG_COMMIT=deny
gg_row_at "$REPO_FEATURE" $'GT:344 newline exposes later bad commit' deny $'git commit --amend\ngit commit -m nope'
GG_COMMIT=off
gg_row_at "$REPO_FEATURE" $'GT:344 newline exposes later bad commit (at the default commit_style=off)' silent $'git commit --amend\ngit commit -m nope'
GG_COMMIT=deny
gg_row_at "$REPO_FEATURE" $'GT:345 semicolon exposes later bad commit' deny $'git commit --amend; git commit -m nope'
GG_COMMIT=off
gg_row_at "$REPO_FEATURE" $'GT:345 semicolon exposes later bad commit (at the default commit_style=off)' silent $'git commit --amend; git commit -m nope'
GG_COMMIT=deny
gg_row_at "$REPO_FEATURE" $'GT:1363 commit denial before indirection' deny $'git commit -m "bad message" && eval git push -f origin feature'
gg_row_at "$REPO_FEATURE" $'GT:1725 bad commit-message prefix still denied' deny $'git commit -m "nope"'
gg_row_at "$REPO_FEATURE" $'GT:1726 nested sh -c resolves the positional conventional message' silent $'sh -c \'cd "$1" && git commit -m "$2"\' _ . \'chore: probe\''
gg_row_at "$REPO_FEATURE" $'GT:1728 nested bash -c resolves the positional conventional message' silent $'bash -c \'true && git commit -m "$1"\' _ \'chore: probe\''
gg_row_at "$REPO_FEATURE" $'GT:1730 nested bash -c resolves a quoted message containing a colon' silent $'bash -c \'true && git commit -m "$1"\' _ \'fix(parser): keep: quoted colon\''
gg_row_at "$REPO_FEATURE" $'GT:1733 nested sh -c direct multi-line message validates its conventional subject only' silent $'sh -c \'git commit -m "chore: probe\n\nSome body text."\''
gg_row_at "$REPO_FEATURE" $'GT:1735 nested sh -c positional multi-line message validates its conventional subject only' silent $'sh -c \'git commit -m "$1"\' _ \'chore: probe\n\nSome body text.\''
gg_row_at "$REPO_FEATURE" $'GT:1738 nested sh -c multi-line message still denies a nonconventional subject' silent $'sh -c \'git commit -m "$1"\' _ \'nope\n\nbody\''
gg_row_at "$REPO_FEATURE" $'GT:1740 non-nested multi-line legacy control remains allowed' silent $'git commit -m "chore: probe\n\nSome body text."'
gg_row_at "$REPO_FEATURE" $'GT:1742 nested conventional commit cannot hide a nonconventional && sibling' deny $'sh -c \'git commit -m "$1"\' _ \'chore: ok\' && git commit -m nope'
gg_row_at "$REPO_FEATURE" $'GT:1744 nested conventional commit cannot hide a nonconventional ; sibling' deny $'sh -c \'git commit -m "$1"\' _ \'chore: ok\'; git commit -m nope'
gg_row_at "$REPO_FEATURE" $'GT:1747 quoted |& commit path keeps later message' silent $'sh -c \'git commit --only "|&" -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1747 quoted | commit path keeps later message' silent $'sh -c \'git commit --only "|" -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1747 quoted ; commit path keeps later message' silent $'sh -c \'git commit --only ";" -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1747 quoted && commit path keeps later message' silent $'sh -c \'git commit --only "&&" -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1747 quoted || commit path keeps later message' silent $'sh -c \'git commit --only "||" -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1747 quoted & commit path keeps later message' silent $'sh -c \'git commit --only "&" -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1752 escaped |& commit path keeps later message' silent $'sh -c \'git commit --only \\|\\& -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1756 unquoted |& still ends the commit segment' silent $'sh -c \'git commit --only |& -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1761 exempt nested commit does not hide later message (--amend)' silent $'sh -c \'git commit --only "|" --amend; git commit -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1761 exempt nested commit does not hide later message (-F msg)' silent $'sh -c \'git commit --only "|" -F msg; git commit -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1761 exempt nested commit does not hide later message (--fixup=HEAD)' silent $'sh -c \'git commit --only "|" --fixup=HEAD; git commit -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1761 exempt nested commit does not hide later message (--file=msg)' silent $'sh -c \'git commit --only "|" --file=msg; git commit -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1761 exempt nested commit does not hide later message (--squash=HEAD)' silent $'sh -c \'git commit --only "|" --squash=HEAD; git commit -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1767 exempt remainder commit does not hide later message (--amend)' deny $'sh -c \'git commit -m "chore: ok"\' _ x ; git commit --only "|" --amend; git commit -m nope'
gg_row_at "$REPO_FEATURE" $'GT:1767 exempt remainder commit does not hide later message (-F msg)' deny $'sh -c \'git commit -m "chore: ok"\' _ x ; git commit --only "|" -F msg; git commit -m nope'
gg_row_at "$REPO_FEATURE" $'GT:1773 newline after exempt nested commit (--amend)' silent $'sh -c \'git commit --only "|" --amend\ngit commit -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1773 newline after exempt nested commit (-F msg)' silent $'sh -c \'git commit --only "|" -F msg\ngit commit -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1778 newline after exempt remainder commit' deny $'sh -c \'git commit -m "chore: ok"\' _ x ; git commit --only "|" --amend\ngit commit -m nope'
gg_row_at "$REPO_FEATURE" $'GT:1782 literal argv after exempt commit' silent $'sh -c \'git commit --only "|" --amend git commit -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1786 quoted semicolon after exempt commit' silent $'sh -c \'git commit --only "|" --amend ";" git commit -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1790 amend alone remains exempt' silent $'sh -c \'git commit --amend\''
gg_row_at "$REPO_FEATURE" $'GT:1794 message after amend remains exempt' silent $'sh -c \'git commit --amend -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1798 message before amend remains checked' silent $'sh -c \'git commit -m nope --amend\''
gg_row_at "$REPO_FEATURE" $'GT:1803 quoted outer | starts a checked remainder' silent $'sh -c \'git commit -m "chore: ok"\' _ "|" git commit -m nope'
gg_row_at "$REPO_FEATURE" $'GT:1803 quoted outer ; starts a checked remainder' silent $'sh -c \'git commit -m "chore: ok"\' _ ";" git commit -m nope'
gg_row_at "$REPO_FEATURE" $'GT:1808 nested conventional commit cannot hide a nonconventional |& sibling' deny $'sh -c \'git commit -m "$1"\' _ \'chore: ok\' |& git commit -m nope'
gg_row_at "$REPO_FEATURE" $'GT:1812 nested conventional commit cannot hide a nonconventional | sibling' deny $'sh -c \'git commit -m "$1"\' _ \'chore: ok\' | git commit -m nope'
gg_row_at "$REPO_FEATURE" $'GT:1816 nested |& commit scan continues past another command' silent $'sh -c \'git commit -m "chore: ok" |& echo --file; git commit -m nope\''
gg_row_at "$REPO_FEATURE" $'GT:1818 nested conventional commit cannot hide an unattributable && sibling' deny $'sh -c \'git commit -m "$1"\' _ \'chore: ok\' && git commit -m "$MESSAGE"'
gg_row_at "$REPO_FEATURE" $'GT:1822 compound nested sh -c still denies a nonconventional positional message' silent $'sh -c \'true && git commit -m "$1"\' _ \'probe only\''
gg_row_at "$REPO_FEATURE" $'GT:1824 nested sh -c direct commit denies a nonconventional positional message' silent $'sh -c \'git commit -m "$1"\' _ \'probe only\''
gg_row_at "$REPO_FEATURE" $'GT:1826 nested sh -c still denies a nonconventional attached -m message' silent $'sh -c \'true && git commit -m="$1"\' _ \'probe only\''
gg_row_at "$REPO_FEATURE" $'GT:1828 nested sh -c with malformed payload quoting fails closed' silent $'sh -c \'true && git commit -m "$1\' _ \'chore: probe\''
gg_row_at "$REPO_FEATURE" $'GT:1830 nested sh -c with a missing positional message fails closed' silent $'sh -c \'true && git commit -m "$2"\' _ \'chore: probe\''
GG_COMMIT=off
GG_MAIN=ask
GG_FORCE=ask

gg_finish COMMIT
