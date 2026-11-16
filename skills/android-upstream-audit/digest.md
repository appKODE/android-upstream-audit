# android-upstream-audit — the Slack digest

Read at step 11 of `SKILL.md`, when the report is written and the digest comes last.

Append the digest to the end of the report, between these exact markers:

```
<!-- slack-digest:start -->
...
<!-- slack-digest:end -->
```

The digest is **in English**, like the rest of the report, written in Slack `mrkdwn` (bold is
`*text*`, links are `<url|text>`). A person copies this block into Slack by hand, so write it
ready to paste.

Structure — up to three blocks of at most five items each, then the still-open block:

```
📋 *Upstream audit · September 2026*
🗓️ Checked: 2 Sep → 9 Sep (7 days)
🔎 Versions: as the build asks for them and as the release build ships them

🚨 *Critical*
• *<subject>* — <consequence> (<N> days)

⚠️ *Requires work*
• *<subject>* — <consequence>

🛠️ *Worth improving*
• *<subject>* — <consequence>

📌 *Still open*
• *<subject>* — <consequence>

🔧 *Audit upkeep:* config.json needs 2 updates (run --init) · feed gap in <feed> (<N> days) — see Coverage

Active <N> (was <M>) · new <N> · closed <N> · checked <N> · declined <N>
Shown <X> of <N> · full report: upstream-audit/<YYYY-MM-DD-HHMM>.md
```

Rules for the digest:

- **Only outstanding work reaches the digest.** A requirement we already satisfy is not a
  critical item however close its date is: it belongs in *Verified good* in the report, and
  nowhere in the digest. The reader scans the emoji; 🚨 must never mean "all fine here".
- **Selection.** The report will usually hold more findings than fit. Take, in this order:
  deadlines that still need work, then the most severe items, then whatever is new or changed
  since the previous report. Stop at five per block; the rest stay in the report only.
- **Every item starts with the finding's `subject` in bold, copied verbatim** from the run
  slice (rule 13) — not paraphrased, not shortened to a category. Then one sentence: the
  consequence, skipping the mechanism. Write "*androidx.biometric* — biometric sign-in rests
  on an abandoned alpha", not "The biometrics library is on an alpha". The same applies to
  the rest of the sentence: when it mentions another library, flag or setting, name it too
  ("without a *KSP* update", not "without updating the code generator").
- Every item under 🚨 ends with the days remaining in parentheses; a finding past its
  deadline ends with `(overdue <N> days)` and leads the block.
- No file paths, no code, no version numbers unless the number *is* the point. Detail lives in
  the report.
- An item that does not fit two lines does not belong in the digest.
- **Omit an empty block entirely.** If all four are empty, write the header, the line
  `Nothing new — no need to send`, the checked block, the trend line and the shown line.
- **The still-open block** is printed by `slice.py`: the unchanged high-severity findings that
  🚨 does not relist. Copy it outside the five-item limit and replace each `<consequence>` by
  the rules above; reuse the wording of the last digest that carried the finding, so the
  same item does not read differently every run. When the script prints `none`, leave the
  block out. It keeps an important finding in front of readers after it stops being news.
- 🚨 relists deadlines that still need work, including ones reported before: the countdown
  changing is news. The other two blocks carry only findings whose `status` is `new` or
  `changed`.
- **The checked block** is printed by `slice.py` (`checked block:`): what this run actually
  saw. Usually one line, `🗓️ Checked: 4 Nov → 9 Nov (5 days)`. When a feed does not reach back
  to the start of the window (a first run asks for 90 days; a blog feed holds only its latest
  posts), it becomes a list: each feed that fell short with the days it did cover, then
  versions, releases and the other feeds, then `_Window asked: …_` in italics — the window is
  what the run asked for, not what it saw. Its last line, `🔎 Versions: …`, says how deep the
  version check went: as the build asks for them only, or also as the release build ships them
  (`resolved.py`). Copy the block right under the title, as printed, on every digest, also one
  with nothing new.
- **The trend line** is printed by `slice.py`. Copy it; do not count by hand.
- **The shown line** is printed by `slice.py` with the active total filled in. Copy it as the
  last line and replace `<X>` with the number of items in all blocks; with nothing new,
  `<X>` is 0. The digest leaves most findings out, so this line tells the reader how many more
  wait in the report.
- **The upkeep line** is printed by `slice.py` too: what this run could not see — `config.json`
  out of date, GitHub asked without `gh`, dependencies not checked, a failed feed, a feed gap or a silent feed, failed sources, unverified quotes, a branch behind
  its base branch. Copy it after the three
  blocks, outside their five-item limit. When the script prints `none`, leave it out.

### Self-check before finishing

Re-read the digest item by item and fix it until each one passes:

1. It starts with a bold `subject` that matches a finding's `subject` in the run slice exactly.
2. Every other library, flag or setting it mentions is named, not described.
3. A 🚨 item is outstanding work, ends with the days in parentheses, and is not a "manual
   check" item (rule 14).
4. It fits two lines and contains no file path or code.

Then check that `<X>` in the shown line equals the number of items, and that the line under
the title is the checked block `slice.py` printed.

Record in *Coverage* that the self-check ran and how many items it corrected.
