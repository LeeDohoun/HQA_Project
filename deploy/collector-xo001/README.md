# XO001 separate deployment

Use the complete coordinator commands, schemas, timing rules and qualification
requirements in
[`implementation_notes.md`](../../research/experiments/XO001_open_executability/implementation_notes.md).

Code: `/opt/hqa-xo001`; data: `/var/lib/hqa-xo001`.
Systemd alone loads `/etc/hqa/xo001.env`; application code never reads it.
Installation is offline from the exact wheelhouse lock and leaves timers OFF.
Only XO001 units are installed. No shared heavy lock or collector modification.

REST quotation support, dates/session/venue, individual print fields, official
PAPER limits and order inquiry semantics still need actual November shakedown.
Unsupported fields never get synthetic replacements. No valid main-key
activation by November 30 means `not_started`; failed order qualification leaves
primary measurement active with diagnostics OFF. Flags are installed manually
by the coordinator after reviewing the sealed decision, never by the recorder.
