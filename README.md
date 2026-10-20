# OneClearStep

Resume an interrupted plan without treating an uncertain result as done.

Built for Taskforge on October 19, 2026. This is a working local prototype, not a service that performs tasks or verifies external completion.

## Intended user and barrier

A student returning to a small group project may remember sending a request but not whether it succeeded. A conventional binary checkbox leaves a choice between assuming success and starting over. OneClearStep retains an explicit **uncertain** state, the last context, and dependencies. It suggests one actionable check before a dependent step.

## Run the browser app

Requires Python 3 to serve static files and a modern browser supporting Web Crypto and Web Locks on localhost. No packages, accounts, keys or paid APIs are required.

```sh
python3 -m http.server 8766 --bind 127.0.0.1 --directory web
```

Open <http://127.0.0.1:8766/>. The first visit creates a clearly labeled synthetic example. Read the pending room-booking result, choose **Confirmed done**, and observe **Send invitations** become the next action. Select the booking again and choose **Outcome uncertain**: invitations become blocked again. Selecting a blocked row disables its **Confirmed done** control.

**New plan** creates a plan with a first task. **Add task** allows prerequisites and priority. Edit **Last context** before choosing a state to retain the note. **Export** downloads the browser journal. **Import** accepts a plain plan or validated browser journal and imports its current plan as a new local revision; imported history remains in the source file. Earlier browser history is retained. Clearing site data removes the browser copy, so export a copy when it matters.

The browser limits plans to 100 tasks, 20 prerequisites per task, 500 history events and 2 MiB journal data. Imported plain-plan title and context limits count Unicode code points, matching the Python validator: 160 for a plan title, 240 for a task title and 2000 for context. Plain-plan text accepts tab, LF and CR in both clients. Imported text is preserved in journals and exports; recording context from a browser textarea uses the browser's LF line-ending normalization. Native browser text inputs retain their more conservative UTF-16 input limits; importing or displaying a valid longer Unicode value does not truncate it. Unsupported safe-write capabilities or corrupt storage leave the application read-only. Two cooperating tabs use Web Locks, revision checks and a storage-change warning. These are same-origin browser controls, not a distributed or adversarial lock.

## Run the CLI

Python 3.9+, macOS/Linux. The CLI uses a separate JSONL journal format; browser journals and CLI journals are **not interchangeable**. Both clients accept the same plain plan schema and have a cross-language decision check.

```sh
python3 oneclearstep.py init evidence/synthetic-plan.json my-plan.jsonl
python3 oneclearstep.py next my-plan.jsonl
python3 oneclearstep.py mark my-plan.jsonl booking done --context "User checked the booking" --op-id check-booking-1 --expected-revision 0
python3 oneclearstep.py next my-plan.jsonl --json
```

CLI state updates take an exclusive file lock, check the expected revision, append an operation with a caller-provided ID, and flush/fsync the file. Repeating the same ID and content does not append a second event. Reusing an ID with different content or using a stale revision is rejected. A torn final line or hash mismatch refuses further writes. On an interrupted/erroring write, read the journal before retrying and reuse the original operation ID. Do not manually truncate or repair an uncertain journal. Initial creation refuses an existing path. Direct input/journal symlinks and special files are rejected. Files are opened nonblocking before their type is inspected, so a FIFO without a writer is refused rather than waiting. Plan input is read with a 2 MiB limit before JSON parsing; oversized and excessively nested JSON are refused without creating a journal.

## Validation

```sh
python3 -m unittest -v test_oneclearstep
node test_engine.mjs
```

Sixteen Python tests include real competing processes, stale revisions, duplicate operations, reopened prerequisites, invalid graphs, torn/tampered journals, symlink refusal and Unicode separators inside journal strings. Fifteen JavaScript checks include a known SHA-256 answer, Python/browser decision agreement, hash-chain tamper detection, reset history, dependencies and invalid structures. The Unicode boundary check compares 36 ASCII, Korean, emoji and mixed-script field cases against Python; another check preserves supplementary Unicode context through append and replay. They also compare all 32 C0 control characters in each of the three text fields (96 cases): tab, LF and CR are permitted, while other C0 controls are rejected. A Python plan-import/journal-update test preserves CRLF and CR exactly. Both clients reject unpaired Unicode surrogates before a plan or state event is stored; valid supplementary characters and the replacement character remain accepted. A 33-case comparison covers malformed and valid Unicode in all three text fields, and rejection leaves existing journals unchanged. These checks do not prove equivalence for every possible Unicode or JSON input. JSONL records are separated only at LF; Unicode line/paragraph separators in titles and context are preserved.

Actual browser checks exercised state changes, a second stale tab, new plans, dependency-bearing task creation, download/import, and persistence after reload. A 390 CSS-pixel iframe was checked for narrow layout; this was not a physical-device, screen-reader or accessibility certification test.

## Boundaries

- A recorded `done` or complete plan is a user assertion. No email, booking, payment, upload or task execution is performed.
- This is a local planning aid. It does not provide medical advice, diagnose attention conditions, or prove reduced cognitive load or improved productivity. No real user study or measured time savings is claimed.
- Hashes detect accidental changes against the stored chain. They do not authenticate an author; someone who can alter all records can recompute hashes.
- Browser exports contain task text, context and earlier local plan history. They can contain sensitive information if you enter it. The application has no upload endpoint, telemetry or external API; its CSP blocks application connections. Hosting static assets is distinct from uploading task data.
- CLI file locks coordinate cooperating local writers. Scans are not protected against hostile parent-directory replacement or platform/filesystem failures. File fsync is not a guarantee against every storage failure.
- Automatic synchronization, calendar integration, collaboration across devices and real-world usability evaluation are planned, not implemented.

## Credits and provenance

Codex generated the new source, tests, synthetic examples, documentation and draft copy. Image Gen supplied the visual design reference. Runtime UI controls and behavior are HTML/CSS/JavaScript, not a raster screenshot. No earlier contest project's source was reused. No claim of sole manual authorship or personal learning by the account owner is made.

`web/qa-mobile.html` is a retained local QA helper, excluded from the public source packet. The design reference and operational evidence stay outside the runtime app.

Keyboard check: pressing Enter on a task row moves focus to its labeled action section; the next Tab reaches Last context. Successfully recording a task state also focuses the resulting action section, rather than losing focus when the saving controls are disabled. Opening New plan focuses Plan name, and Escape returns focus to New plan without saving. These paths were exercised in the embedded desktop browser; no screen-reader or accessibility certification is claimed.
