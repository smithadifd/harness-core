# Security

## Reporting a vulnerability

Please report a suspected vulnerability privately, through this repository's
"Report a vulnerability" form under the Security tab, and not in a public
issue or pull request. Include what is affected, how to reproduce it, and the
impact you see. You will get an acknowledgement, and the fix is coordinated
with you before anything is disclosed.

## Scope

Every file here is vendored, byte for byte, into consuming repositories. A fix
lands here first and reaches each consumer as a pin move, so a report against
a vendored file in a consumer belongs here.

`skills/_lib/ledger/` is vendored from the public ledger repository; a
vulnerability in it is best reported there, and reaches this repository as a
ledger pin move.

## What the core must never hold

No credential, token, private key, personal name, machine name, home path or
private repository name. A change that adds one is refused by review and by
the checks named in `VENDORING.md`.
