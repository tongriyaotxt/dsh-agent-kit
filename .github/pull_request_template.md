## What this changes

<!-- One or two sentences. If it doesn't affect "cases pass" or "mutations caught", say why it matters anyway. -->

## How I know it works

<!-- Paste the command and its output. Not a description of what it should do. -->

```
$ python -m pytest tests/ -q
$ python tests/mutate.py
```

## Checks I ran

- [ ] `python -m pytest tests/ -q` (all green)
- [ ] `python tests/mutate.py` (6/6 caught — if my change touches verify/scaffold, this must still hold)
- [ ] `python tests/test_conformance_negative.py` (if I touched the protocol/schema)
- [ ] `cd host && go test ./...` (if I touched the host)
- [ ] Docs updated, and any number I wrote matches what the code actually does

## Notes for the reviewer

<!--
Things worth flagging:
  · anything measured that surprised you
  · anything you deliberately did NOT do, and why
  · if you changed a documented number, where did the new one come from
-->
