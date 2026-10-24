# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the package is `synaptixs-spine`
(import/CLI stay `orchestrator`).

## Unreleased

### Fixed

- **A change `openspec draft` wrote now reads back as the criteria it was drafted from ([SSPN-240](https://fibonacci-solutions.atlassian.net/browse/SSPN-240), B103).** `openspec_writer` says its files round-trip; they did not. Reading a drafted change through `openspec://` returned the draft's summary paragraph as a criterion, the writer's placeholder scenario ("The behavior described in the proposal holds.") when the source stated nothing, and every other criterion wrapped as `Scenario: <label> — THEN <text>`, and the spec writer copies what it is given into the *stated* criteria, the only ones that bind the review. On the two benchmark tickets that exposed it, a source that stated no criteria came back stating two, and one that stated two sentences came back as three criteria, two of them reworded. A change is now read as drafted for as long as its proposal still opens with the draft banner: the requirement text is a summary and not a criterion, a plain scenario comes back as the sentence it was written from, and the placeholder is not handed to a run (`openspec check` still calls it out). A change a person wrote, or whose owner deleted the banner, is read exactly as before; the banner now says that deleting it is how an owner takes the change over. Proposed criteria in `tasks.md` are still not read back: they are guesses, not a contract. No model call in any of it. Known limit, unchanged: a Given/When/Then criterion that starts with a lead clause before its first keyword has always lost that clause on the way back.

## 3.63.0 — 2026-10-23

### Changed

- **The context-budget gate weighs the files the design will propose, not an estimate of them (SSPN-231).** Codegen is
  shown the design's files, and in the autorun path the design is deterministic, so `_stage_validity` and `build_plan`
  now ask the same function the design stage uses (`design.gate_file_set`: paths the ticket names first, then stated
  modules, else the landing reading with weak rows dropped, at most five files). B89's estimate (`design_file_set`, the
  files of the first eight landing rows) counted files the fix does not need and never counted a path the ticket names
  in the run, so the plan and the run could disagree. The estimate stays as the fallback when the design proposes
  nothing. On 40 fresh benchmark runs the estimate parked five tickets at 1.5x-2.6x the budget; for four of them the
  design's own files were small or fit alone. Counts on used instances, not a rate; a model-written design (not used in
  the autorun path) would make this an estimate again. Plan and measurements: SSPN-231.
- **The context-budget refusal line moves from 1.5x to 2.0x the budget (SSPN-231).** A design between 1.5x and 2.0x
  (300,000-400,000 bytes) now proceeds with the existing "files will be excerpted" warning instead of being parked before
  any code is written. The 1.5x rested on one observed case and was never re-tested when the budget went from 40,000 to
  200,000 bytes (SSPN-159). Replayed on 97 recorded runs that had passed the gate, with the design's own files weighed,
  three designs were 1.7x, 1.85x and 1.9x the budget, and two of the three resolved when they ran; 2.0x is the first line
  that refuses none of the runs known to have resolved. It is not a tested point: the largest resolved design was
  382,303 bytes (1.91x) and above it there is no outcome data. Counts on used instances, not a rate; no improvement is
  claimed.
- **Dependency updates (Dependabot, #622-#624).** pydantic-settings 2.16.0, fastapi 0.150.0, langgraph 1.2.16, langgraph-checkpoint-postgres 3.2.0, sqlglot 30.23.0, pypdf 6.21.1 and ruff 0.17.1; the GitHub Actions setup-java 6.1.0, codeql-action 4.39.0 and pypa/gh-action-pypi-publish 1.14.3 (the action that publishes to PyPI); and Pillow 12.3.0 to 13.0.0, a major version that only the optional `media` extra (image OCR) uses and that CI's default sync does not install: its 25 media tests pass under 13.0.0, and OCR on a real image with a Tesseract binary was not exercised.

## 3.62.0 — 2026-10-22

### Changed

- **A test the run wrote that imports a library the environment lacks is withdrawn on the first red run, not after the refines.**
  When every failing test is a file the run wrote, the output says `No module named 'X'` for an `X` the repository does not itself
  contain, and each such file imports one of those modules (django-14349: `import pytest` under Django's unittest runner, which cost
  three refines in P6 and P5), the run's test is withdrawn at once and the suite re-run once, with the existing rescue's label (unproven
  by its own tests) and no model call spent on an import no edit can add. A missing repository module, a test that does not import
  the named module, and any failing test the run did not write keep their refines exactly as before. `SDLC_OWN_TEST_RESCUE=0` turns it off.

- **A change whose only red tests are the ones the run wrote is released, labelled unproven, instead of failing ([SSPN-227](https://fibonacci-solutions.atlassian.net/browse/SSPN-227)).**
  Where the refine loop would stop (a stall, an exhausted allowance, a refine that edits nothing), if every failing
  test is one a test file this run wrote defines and a test the run did not write ran too, the run's test files are
  withdrawn and the suite re-run once. Green: the run goes on to its remaining checks with the coverage gate waived and
  named, and the proof bundle, the autorun journey line, the PR body and the log all say the change is unproven by its
  own tests; a live PR opens as a **draft**. Still red: the files are restored byte for byte and the run fails exactly as
  before. The official grader (REPLAY1, SSPN-226) found that the model's first patch resolved in 14 of 23 runs lost on
  their own tests, and (SSPN-227 P1) that the loop's final tree resolves in 9 of 14 such runs. Failing test names are read
  from the runner's output (unittest `FAIL:`/`ERROR:` lines, pytest's summary), because `SDLC_TEST_COMMAND` runners return
  none. `SDLC_OWN_TEST_RESCUE=0` restores the old stop. Not a promise: it releases wrong patches too (5 of those 14).
  "A test the run did not write ran" counts the tests the run *added* against `HEAD`, not every test in a file it edited, and a
  name the runner could not import (`unittest.loader._FailedTest`) is matched to the test file of that name, never to a
  same-named method elsewhere (both found by running the classifier over the 14 stored runs: 0 of 14 were rescuable before).
  **No longer required (D10):** a test command that runs only the files the run wrote never runs an existing test, which refused
  11 of those 14 runs. The rescue now goes ahead when tests ran, a re-run with nothing left to run is not counted red, and the
  label says "no test supports this change at all" (proof bundle, journey line, draft PR body). `SDLC_OWN_TEST_RESCUE=strict`
  restores the old requirement.
  The adapter reports the files it wrote as absolute paths; the rescue now reads them as repository paths (found live in the first
  real runs: a rescue with 18 existing tests behind it was labelled "no pre-existing test ran", and a pytest failure could never be
  matched to a file the run wrote), and its log line states how many tests ran and how many the run added.
- **A test the run wrote that imports a library the runner lacks is named to refine (test-trust B, SSPN-228).** When the failure says
  `No module named 'X'` and a test file this session *created* imports `X` (django-14349 wrote `import pytest` for a unittest-based
  runner), the refine prompt gets one deterministic paragraph: that import is yours, no production change can add the library, you may
  edit those files to remove it using what the project's own tests import, keeping every test and every assert. A module the
  repository itself contains, a file the run only edited, and a pytest assertion failure open nothing.
- **A repair turn for a test the run wrote (test-trust C, adapter half, SSPN-228).** `LLMCodegenAdapter.repair_own_tests` gives the model one
  move refine is denied: rewrite files this session *created*, with the weakened-test guard stood aside for those files only (a new
  `only_paths` argument on `apply_files` refuses every other file in the reply). A pre-existing test is never offered and costs no call.
  The loop now calls it (C2, below).
- **The repair turn runs before the rescue, and what it fixes is labelled "not proof" (test-trust C2, SSPN-228).** At the two places the loop
  gives up (a stall or spent allowance; a refine that edits nothing), if every failing test is one a test file this run wrote defines, the
  adapter's `repair_own_tests` is offered once per run, through `getattr` (a codegen without it is skipped; no Protocol change). If it
  rewrote a file the suite is re-run; green means the change goes on carrying `own_tests_repaired` on the run result, the proof bundle
  ("Withdrawn work" check: *rewritten by the model, proves less than a test that passed untouched*), the autorun journey line
  (`own tests repaired (not proof)`), the PR body and a **draft** PR when live. A repair that changes nothing, raises (a spent
  budget excepted) or leaves the suite red falls through to the rescue exactly as before; a rewritten test the rescue then withdraws is
  reported as withdrawn only. `SDLC_OWN_TEST_REPAIR=0` skips the repair turn.
- **A person can be asked when the automatic repair fails (test-trust P4a, SSPN-228).** `run_feature(ask=…)`, shaped like `gate`, is
  called once, when every failing test is one the run wrote and the repair turn did not make the suite pass. The question names the
  tests, a deterministic cause class (`own_test_cause`: `import_error`, `missing_app`, `wrong_expectation`, `other`) and a suggested fix.
  Answers: `suggest` / `explain` (one more guided repair turn; the person's words are quoted into the prompt, the scope fence does not
  move), `delete` (withdraw the tests, even where the automatic rescue would refuse), `release` (the rescue), `stop` (fail the run).
  `sdlc autorun --ask-minutes N` (or `SDLC_ASK_OWN_TESTS_MINUTES`; default 0 = never ask) raises an `own_tests` approval and waits that
  long, keeping the run's heartbeat; answer with `orchestrator sdlc runs approve <id> --choice … [--note …]` (new `Approval.choice`).
  No answer in the window decides the approval rejected in the system's name and the labelled rescue applies as before. The decision
  (`<choice> by <who>`) is on the run result, proof bundle, journey line and PR body.
- **Every answer is kept, and `sdlc runs interventions` reports them per cause (test-trust P4b, SSPN-228).** One JSON line per question
  (cause, evidence, files, choice, the person's words, who, seconds waited; a window that closed unanswered is a record too) and one
  outcome line per run (passed or not), in `_interventions.jsonl` beside the run state. The report counts answers per diagnosed cause
  and how many of those runs passed, quotes what people said when they explained, and lists a choice given at least three times for one
  cause with no disagreement as a **candidate rule for a person to review; nothing applies it**.

### Fixed

- **After a test run that executed nothing, the refine prompt now says where this repository keeps its tests ([SSPN-223](https://fibonacci-solutions.atlassian.net/browse/SSPN-223)).**
  The layout block tells the model to put tests under `tests/`. Modern sympy (1.6 to 1.11) runs only test files inside its
  `sympy/` package, so in P6 six runs wrote root `tests/test_*.py`, had the runner execute zero tests, and retried five or
  six times without moving the file. The refine prompt now carries a block naming the directories the repository already
  uses for the touched modules (a test file the PKG says reaches the code, then a `tests/` beside the module, then a
  `test_<module>` name match), and only when the failure output says the run executed nothing (`the runner executed 0
  tests`, pytest's `no tests ran` / `collected 0 items`, unittest's `Ran 0 tests`). A run whose tests execute never sees it,
  and `author_tests` is unchanged. Python only. `PKGCodegenGrounder.covering_test_files` is an optional method reached with
  `getattr`; the `CodegenGrounder` Protocol is not extended. Measured free on P6: the block would appear in 50 refine prompts
  across 15 runs, 4 of which resolved anyway; an always-on version would have changed 39 of the 55 runs that already
  executed their tests, so it was not built. Not covered: Django's two zero-test runs were test style (plain pytest classes
  under a unittest runner), not placement. Not measured: that a corrected location resolves any of these issues.

### Changed

- **A repository that declares Python 3.6 is now told that `subprocess.run(text=...)` and `capture_output=...` need 3.7 ([SSPN-153](https://fibonacci-solutions.atlassian.net/browse/SSPN-153), B70).**
  The TARGET PYTHON paragraph in the codegen prompt listed grammar and some stdlib names a declared floor lacks, but no
  stdlib keyword argument. In one SWE-bench run (`django__django-13658`, repository on 3.6) the model's test called
  `subprocess.run(text=True)`, which 3.6 rejects when the line runs, so no syntax or import check could see it. The table
  has one more row, listed for a floor below 3.7 and not at 3.7, with the older spelling (`universal_newlines=True`,
  `stdout=subprocess.PIPE, stderr=subprocess.PIPE`). This is prompt text only: nothing reads the written files against it,
  and a 3.7+ repository's prompt is unchanged. The row is from the Python documentation ("changed in 3.7"), not run on a
  3.6 or 3.7 interpreter (none older than 3.8 was available). Not measured: that it changes what the model writes. The
  surviving `text=True` in that run was most likely B72's doing (a refine reply read as "nothing to change"), and 0 of 101
  P6 run logs show a stdlib-newer-than-floor failure, so a live check needs a re-run of `django-13658` (paid).


## 3.61.0 — 2026-10-19

### Fixed

- **The ticket's words and the draft note in the PR body can no longer mention a stranger, render HTML or load an image ([SSPN-220](https://fibonacci-solutions.atlassian.net/browse/SSPN-220), B97).**
  The PR description put the ticket's summary and every acceptance criterion into the body unchanged, and `sdlc autorun`'s
  draft note did the same with the review's stop reason, each unresolved finding's path and message, and the gate-override
  note. The intake prompt tells the model to carry identifiers over verbatim, so decorator and annotation names reach the
  summary: a lookup of `@dataclass`, `@staticmethod`, `@Override`, `@Test`, `@Autowired`, `@Component` and `@Deprecated`
  shows each is an existing GitHub account (`@property` is not), and a bare one links to it. Raw HTML, images and `org/repo#N`
  render too. The ticket text now keeps its Markdown (code spans, fenced blocks, lists, emphasis, links, a local `#N`) and
  breaks only what acts on someone else, and only outside code: a mention gets a zero-width space after the `@`, `<` becomes
  `&lt;`, `![` becomes `!\[`, and a cross-repository `org/repo#N` is broken. What is not surely code (an unclosed backtick,
  an escaped one, a span across a line break, an indented fence) counts as text, so a doubt escapes too much rather than too
  little; a fenced block left open is closed so it cannot swallow the proof bundle below it. The draft note shows review and
  operator text as typed, as the proof bundle already does, and withdrawn file names use a code span a backtick in the name
  cannot close. Plain text produces the same body as before. Not covered: the PR title and the commit message (no mention
  or HTML rendering is known there; not tested), and a real post to observe the notification itself.

- **Text the run copied into the proof bundle can no longer act as Markdown, HTML, a mention or a link in the PR ([SSPN-219](https://fibonacci-solutions.atlassian.net/browse/SSPN-219), B96).**
  The "Spine proof bundle" section put criteria, judge evidence, review-finding messages, file and failure names, risk
  reasons and model names into the PR body with only whitespace flattened and `|` escaped. GitHub's render-only
  `POST /markdown`, given synthetic text (nothing posted), stripped scripts, iframes, styles, `javascript:` links and event
  attributes and kept the tables intact, but rendered the data's own HTML and Markdown (69 broken `<img>` tags and a remote
  image, which is a viewer-side beacon, bold and strike-through) and linked `#N` to issues; a real `@user` would have been
  linked and, once the body was posted, notified. The PR copy now shows such text as the characters it is: `<`, `>` and `&` as
  entities, Markdown punctuation backslash-escaped, `@`, `#`, `://` and `www.` broken with a zero-width space, and bidi,
  zero-width and control characters dropped. Names stay in code spans, whose fence is now longer than any backtick in the
  name (a backtick in a file or test name used to close the span early), and a pipe in a name is shown as typed. The cell
  limits count the characters written and never cut an escape, so the section is no longer than before. `proof-bundle.json` is
  unchanged: it keeps the text as recorded, and its hashes do not move. Re-rendered on the same endpoint with the same synthetic bundle plus a real public username: the old code
  produced 69 images and 139 links, 21 of them to that user and 21 to issues; the new code produces none of either, keeps
  every section and all 28 table rows at three cells. The ticket text, which the PR body carried unescaped, is B97 below.

- **A name the repository cannot own no longer parks a bug ticket ([SSPN-218](https://fibonacci-solutions.atlassian.net/browse/SSPN-218)).**
  The criteria binder counted an *unresolved* name as a claim about the code, so `None`, `os.getcwd`,
  `types.JSONObject` or `build/gettext/x.pot` in a criterion parked the run as `CRITERIA_WRONG`. Such a name is a
  Python builtin, has a standard-library root, or is a path to a file that is neither source nor documentation,
  so it cannot be a false premise about this repository: it is now `no-claim`, with the reason saying why.
  Nothing new is refused: only an unresolved name is asked, a repository symbol named like a builtin still binds,
  and a name that looks like repository code (`ghost_helper`, `django/db/nope.py`) still parks. The classifier moved
  from `orchestrator.intake.quality` to `orchestrator.pkg.repro_material.classify_name`, so the intake scorer and the
  gate share one definition (the scorer's development-half figures are unchanged). Replayed free over the stored
  runs, four of the seven issues IR17 left parked on a name stop parking; flipped is not resolved, and no resolving
  rate is claimed.

- **The blast-radius measurement now sees a file that was moved or added in a new directory ([SSPN-212](https://fibonacci-solutions.atlassian.net/browse/SSPN-212), B95).** `escalation.blast_radius` (the escalation
  check's risk signal, and the proof bundle's) found the changed files by reading `git status --porcelain` one line at a time. A new file
  inside a brand-new directory showed up as `?? newpkg/` and was skipped, and a rename showed up as `R  old -> new`, read as the single
  path `old -> new`, which matched nothing: moving a module reported "0 dependents" however many files imported it. Measured on a real
  repository: a rename with its callers updated reported `(0, "")` before and `(2, "py:core.helper")` now. It now reads
  `--porcelain -z --untracked-files=all` (`changed_python_files`): NUL separated and never quoted, so names with spaces, quotes or
  non-ASCII letters come back exactly, a rename counts under its new name, a deletion counts for nothing, and `None` (could not tell) is
  not the empty set (nothing Python changed). Replaying the 35 patches P6 released, none of their radii changed.

### Added

- **A deterministic score for an intake output ([SSPN-204](https://fibonacci-solutions.atlassian.net/browse/SSPN-204), P0 of the intake-quality plan).**
  No product behaviour changes. `orchestrator.intake.quality` scores a ticket's intake on five properties without a model:
  *faithful* (a stated criterion is an exact span of the report), *typed* (no code or output in the contract), *well-formed*
  (a sentence, not a fragment or a question), *linked* (a derived criterion's `[from: "…"]` tag is a real quote) and *grounded* (a
  name the graph does not hold is classified as builtin, standard library, the reporter's own or a path). `scripts/intake_quality.py`
  splits a directory of stored intake outputs into a seeded development half and a sealed half and scores the development half; the
  sealed half is refused unless `--unseal` is given, because it is meant to be read once. `specs.verify_source_quotes` now shares
  its quote check with the scorer (`quality.quote_in_source`); its behaviour is unchanged. **Baseline on the 49-issue development
  half of the 98 stored P6 intake outputs (old prompt):** 96.8% of stated criteria are exact spans of the report, 33.1% are code,
  51.6% are well-formed sentences (14.5% fragments), none of the 235 derived criteria carries a source tag, and 46.2% of the
  names the graph did not hold are classified. A measure of intake, not a resolving rate.

- **Every live PR now carries a proof bundle: what the run checked, and what it did not ([SSPN-205](https://fibonacci-solutions.atlassian.net/browse/SSPN-205), with SSPN-206 to 211 and 213).** A Spine PR used to carry the ticket and its
  criteria; what the run had established lived in log lines and a worklog. Now a "Spine proof bundle" section follows the ticket text
  (before any review note) and, for a run that keeps artifacts (`sdlc autorun`), `proof-bundle.json` is written beside the run's other artifacts. It lists six checks, each *checked*,
  *not checked* or *not applicable*: the acceptance judge (its verdict, and each criterion's status and evidence), the tests (what
  failed before the change, by name), the project's required-behaviour manifest, work the run withdrew, risk (the blast radius and
  the escalation policy's reasons), and the automated review (clean after N rounds, with any advisory nits it left listed for a human, or not clean with the
  blockers and warnings it left, which is what makes a PR a draft; *not checked* when a supervised run opened a PR with no review, *not applicable* on a path with no review loop); then the files changed, the cost and models (an API list-price estimate, not an invoice), a
  **Not recorded** list, and three SHA-256 hashes (the requirement, the final diff, the record itself; `verify_bundle` recomputes the
  last, which catches an accidental edit but is not a signature). Nothing is blank and nothing is "verified": an unreadable baseline is
  not "clean", an unmeasured blast radius is not 0, and not knowing whether review-loop fixes followed the judge is not "none". The
  section is rendered by code from facts the run recorded — no model call — and the same facts give the same bytes. A bundle that cannot
  be built is one line (`Spine proof bundle unavailable: <error>`) and the PR still opens; `SDLC_PROOF_BUNDLE=0` returns the PR body to
  exactly what it was. The blast radius is measured in a child process killed after 120 s (`SDLC_PROOF_BUNDLE_RISK_TIMEOUT` changes the
  limit), recorded as *not computed* on timeout. Autorun's PR note now says `Automated review: clean after N round(s)` when a review ran
  clean (only while the bundle is on; `SDLC_PROOF_BUNDLE=0` leaves the note as it was), and nothing when none ran. `ReviewResult.criteria` keeps every criterion's status (it kept only the doubts before).
  **Measured, a replay with no model call:** the 35 patches P6 released rendered 35 bundles with no check missing; the median risk
  measurement took 13.9 s and the worst 64.4 s (sympy, 1,180 callers), 0.2–1 s more through the child process; the diff hash equals the
  benchmark harness's patch hash on 33 of 35 (the other 2 are the runs where the harness dropped an edited existing test file);
  and the escalation policy's default threshold (10 cross-file callers) was exceeded by 30 of the 35, so the Risk row states the number
  and the reasons and passes **no verdict** until the threshold is calibrated against outcomes. The judge approved all 35, which is
  what releasing them required, so its verdict does not separate the 9 that did not resolve.
  Every string the bundle copies from the run (the ticket, the judge's evidence, file and test names, reasons) is passed through the
  attempt record's redaction first, so the value of any credential-named environment variable never reaches the PR or the file.
  **Not shipped here:** per-criterion rows exist only for runs made after this release; the test pass/fail counts are not recorded
  (the bundle says so); the Temporal workflow's `sdlc_open_pr` does not carry a bundle.

## 3.60.1 — 2026-10-18

### Fixed

- **Intake no longer writes the reporter's code as an acceptance criterion, and a bug report gets criteria that say what should happen ([SSPN-202](https://fibonacci-solutions.atlassian.net/browse/SSPN-202), B94).**
  In P6 the validity gate parked 17 of 100 sampled issues on a criterion naming a symbol the graph does not hold. In 8 of the 17 the
  "criterion" was a line of the report's code, an interpreter session or a traceback: intake's prompt said to copy stated criteria
  VERBATIM, a bug report states none, and the model copied what looked like requirements. Now (1) **the extraction prompt** says what a
  bug report's criteria are (the reporter's own "should" sentences, verbatim) and sends code, commands, sessions, tracebacks and output
  to a new `reproduction` field; (2) **the spec-writer prompt** proposes Given/When/Then criteria from the report's own words for a bug
  report, after the verbatim ones, each ending `[from: "<quote>"]`; (3) the service **checks every such quote against the report**
  (`specs.verify_source_quotes`) and relabels one that is not there `[from: paraphrase, not a quote of the report]`; (4) `Intent` and
  `FeatureSpec` move any whole-code criterion the model still slips in from `acceptance_criteria` to `reproduction`, deterministically;
  (5) the binder treats a criterion that is only code or output as an example (it never parks a run) and is given the whole ticket, so a
  name the ticket *defines* (an assignment, even behind `>>>`; a `def` or `class`) is the reporter's in every criterion. Codegen,
  design and retrieval still read the reproduction (codegen as "Reproduction from the report (examples, not requirements)"); the
  acceptance judge and the binder read the requirements only. `reproduction` is absent from every serialisation when empty, so a spec
  without one is byte-identical and existing caches and approvals are untouched until a ticket is re-extracted.
  **Measured, a diagnostic on used instances:** intake replayed with `claude-opus-5` on 25 P6 issues ($2.43): the 6 intake-caused parks
  ended with 0 junk criteria and a non-empty `reproduction`, and **11 of the 17 name-parks no longer park** (6 still do: real
  requirements naming something outside the repository); no prose criterion was lost on 8 comparison issues. **The pre-registered
  quote check failed**: only 53% of the model's `[from: …]` tags were exact quotes (25% matched nothing), which is why step 3 exists. Side
  effect: a bug report with no stated expectation now has an empty `acceptance_criteria`, and the acceptance judge skips when there are
  none. **Not confirmed in a full live run yet.** Not a resolving rate. One B93 test changed because its premise did: a lone code line
  (`result = ghost_helper(x)`) is an example, while "the report calls ghost_helper" in prose still parks.

## 3.60.0 — 2026-10-18

### Fixed

- **The validity gate no longer parks a bug over the reporter's own example ([SSPN-199](https://fibonacci-solutions.atlassian.net/browse/SSPN-199), B93).**
  Intake copies lines of an issue into the stated acceptance criteria, and on a bug report some are the reporter's
  reproduction: a code line, an assignment, an error line, a URL, a SQL schema. The names in them (`custom_coord`,
  `first_filter`, `other_id`) belong to the example, but the binder treated each as a claim the graph could not hold, and on
  a bug that parks the run as `CRITERIA_WRONG`. Measured over every stored run it was 9 criterion-level parks in 7
  instances, about 15% of those run, and the first two instances of the aborted first attempt of the P6 run. Now a name is
  the reporter's, and no claim, only when the criterion's own text shows it: defined there (an assignment target, a
  `def`/`class` name and its parameters), quoted in an error line, part of a URL, or inside a SQL statement. A name that is only
  used still parks, a repository symbol quoted in an error line still binds, and the criterion stays stated (`criteria.md`
  says why it no longer binds). One shared definition now serves the gate and the acceptance judge's transcript rule (B92):
  `pkg/repro_material.py`, which also learns a bare error line. Free replay of the 13 stored unbound rows: 12 become
  "no claim", the one that stays is a test id. **Live (P6, pin `bc2a6af0`):** 17 of the 100 sampled issues still parked on an unbound criterion — names from the reporter's example in prose, symbols the fix is asked to create, standard-library and built-in names — so this removes the shapes it covers and does not close the problem. Deterministic, no model. Not a resolving rate.
- **The acceptance judge no longer fails a run over a transcript it cannot reproduce, and reads a criterion that states the defect in the ticket's light ([SSPN-197](https://fibonacci-solutions.atlassian.net/browse/SSPN-197), B92).**
  Intake copies lines of an issue into the stated acceptance criteria, and on a bug report some are not requirements. In the
  pre-spend gate run two of five problems were lost to it: on `pydata__xarray-4966` the criterion was a REPL session fetching a
  remote URL, the judge answered "uncertain" three times and the run ended with no patch; on `django__django-14404` it was the
  sentence describing the bug ("returns `path_info` instead of `path`"), the judge rejected the correct first fix as going "the
  opposite direction" and the revise step put the bug back (graded unresolved). Now (1) an `uncertain` verdict on a criterion
  that is a copied REPL or shell session or traceback (its lines open with `>>>`, `In [n]:`, `Out[n]:`, `$ ` or `Traceback`) is
  reported, and still counts for the escalation policy, but is no longer a blocker; `unmet` on a transcript, and `uncertain` on any
  other criterion, block exactly as before. **On by default; `SDLC_JUDGE_TRANSCRIPT_SOFT=0` restores the old blocking.** (2) The
  judge is shown the ticket's own summary and problem and told that a criterion may describe the defect, in which case the
  requirement is its correction. The revise step can still reverse a good change for some other reason; that is not addressed.
  **Live (P6):** the transcript path never fired in 100 runs (0 soft-passes), the judge returned `REQUEST_CHANGES` once, and no run ended on the judge's verdict, so neither change is shown to matter or to hurt there. The tests use a scripted judge, so the prompt change is checked for presence, not for how a model reads it. Not a resolving rate.
- **The validity gate weighs the files codegen will be shown, not every file the ticket lands in ([SSPN-185](https://fibonacci-solutions.atlassian.net/browse/SSPN-185), B89; tracked in [SSPN-196](https://fibonacci-solutions.atlassian.net/browse/SSPN-196)).**
  The context-budget check summed the *landing set* — the files of the first ten non-weak rows, one row per file since #575, so
  up to ten files — against codegen's 200 KB budget and refused at 1.5x. Codegen is not shown those ten; it is shown the
  design's one to six files (median two over 27 recorded runs). In the SWE-bench small run `django__django-14140` was parked on
  that check (10 files, 328,788 bytes, 1.64x) although the same problem had reached codegen in pilot v3 with a two-file
  design. The gate now weighs a **proxy for the design's files**: the files of the first eight non-weak rows, at most five
  (`landings.design_file_set`, the cut `design._landing_state` already makes), passed to `assess` as the new optional
  `context_files` by `autorun`'s validity stage and by `build_plan`, so the plan and both modes of the run still give one
  verdict. A file the ticket names is always measured; with no rows to estimate from, or for any other caller of `assess`, the
  gate weighs the landing exactly as before. The 200 KB budget and the 1.5x ratio are unchanged, and the finding says the files
  are the set the design is expected to propose. Replayed on the saved rows of the five small-run problems, `django-14140`
  goes from 1.64x (parked) to 1.08x (proceeds, with the excerpting warning) and the other four keep their verdicts. It is a
  proxy: the real design proposed six files on `django-15104`, where the estimate is four. **Live:** `django-14140` was not parked at this gate in the pre-spend gate run (P5), but in P6, 12 of the 100 sampled issues were still parked on the context budget. On the 40 recorded tickets B88 replayed
  about 7 would still be refused as too big (a reconstruction on a large-file corpus). Not a resolving rate: that problem
  reached codegen in pilot v3 and still ended unresolved.
- **A change no test exercises is withdrawn instead of failing the whole run ([SSPN-195](https://fibonacci-solutions.atlassian.net/browse/SSPN-195), Part B of B91 / [SSPN-193](https://fibonacci-solutions.atlassian.net/browse/SSPN-193)).**
  When the two cover attempts have not closed a coverage gap, the run used to end `FAILED … not opening a PR` and discard
  everything. In the SWE-bench small run sympy-11618 ended there over unrequested edits to `line.py` and `line3d.py` that no
  test touched, while the tested fix in `point.py` — graded afterwards, as a diagnostic on a reconstructed patch — resolved.
  Now the run takes the untested production files out (whole files, stashed), re-runs the suite and the type check once, and
  keeps the rest only if both pass and at least one tested production file remains; otherwise the files come back and the run
  fails as before. It tries once, and never when more than four production files are testable, since the probe only looks at
  four. Disclosed three ways, plus the PR body: a `[coverage] withdrawn: <files>` line, the new
  `FeatureRunResult.coverage_withdrawn_production` field (separate from `coverage_withdrawn`, which names tests), and the
  `implement` line of the journey. **On by default; `SDLC_COVERAGE_WITHDRAW=0` restores the old failure.** The patch that ships
  is smaller than the model wrote and what the withdrawn files would have done is not proven. **Live (P6):** it fired 3 times in 100 runs (`django-16612`, `pytest-5631`, `scikit-learn-13328`); each run kept the rest, and all three patches were graded resolved — three observations, not evidence that the withdrawal helps. The tests use a real git repository and a fake suite. Not a resolving rate.
- **The coverage gate no longer fails a run over a file whose change is only comments or docstrings ([SSPN-194](https://fibonacci-solutions.atlassian.net/browse/SSPN-194), Part A of B91 / [SSPN-193](https://fibonacci-solutions.atlassian.net/browse/SSPN-193)).**
  After the model's tests pass, the gate reverts each changed production file on its own and asks whether the suite goes red; a file
  whose revert leaves it green is "not exercised", and after two cover attempts the run ended FAILED. A change that adds only a
  comment can never redden a suite, so it failed by construction: in the SWE-bench small run django-14404 ended there because the
  model added two comment lines to `request.py`, while the real fix, in `sites.py`, was tested and passed the probe. Now a Python
  file whose syntax tree, docstrings dropped, equals the committed one is left out of the probe and reported as
  `excluded (not testable): <file> (comment-only)` — comments, blank lines, formatting and docstring wording are not behaviour. Only
  Python can be compared exactly: a new file, a file that does not parse on either side, and every other language are probed as
  before. No switch. Replayed free over the 15 stored coverage stops of the SWE-bench pilots and small run, every flagged file was
  comment-only in 3 of them and some in 2 more; the other 8 are real edits no test touches, which this does **not** change
  (sympy-11618 still stops: its extra edits in `line.py` and `line3d.py` are real). Not a resolving rate.

- **A yes to "build anyway?" is now read on `sdlc autorun --resume` ([SSPN-55](https://fibonacci-solutions.atlassian.net/browse/SSPN-55), B12).** Before, a validity or design park
  asked "build anyway?", the answer was saved, and the resumed run re-ran intake, met the same verdict and parked again: external
  testing of 3.52.0 hit it after a size refusal. Now the approval carries the kind of park and a digest of the exact refusal (the
  verdict and every finding's check, detail and evidence), and a resume that meets that same refusal builds past it. The yes
  covers only what it was given: a refusal that changed since (a different spec, a file that moved the evidence) parks again and the
  reason says the earlier yes was for a different refusal; the gate's later checks still run, so a second refusal needs its own yes;
  and a `DUPLICATE` is never overridden. The override (refusal, who, date, digest, note) is written to `validity.md` or
  `design-references.md`, the run record and the pull request description. `sdlc runs approve` takes `--by` (default
  `git config user.name`) and now refuses when there is no name to record, instead of writing `cli`.
  **Upgrade note:** approvals saved before this release carry no digest and never count as an override, so a run parked on 3.59.0
  or earlier parks once more on resume under the new rule.
- **Two parks of one run inside the same second overwrote each other's approval file.** Approval ids were `<run>-<unix second>`;
  the second replaced the first, and with it a human's earlier yes. A repeated id now takes a numeric suffix.
- **The refine loop stops when two edits in a row leave the failing run unchanged ([SSPN-164](https://fibonacci-solutions.atlassian.net/browse/SSPN-164), B80).**
  Before, it stopped early only when refine edited no files, so a model-written test that asked for something the ticket never stated
  could cost the whole allowance (five refines per check): the product rewritten, round after round, with the same assertion failing.
  Now each failing test run is reduced to a signature: the output the runner returned, with only the parts that are never the same twice
  removed (a test summary's duration, an object's address after `at`, the unique part of a temporary directory), plus the failing test
  ids where the runner names them (pytest and .NET do; `SDLC_TEST_COMMAND`, Maven, Node and Go do not). A third identical failing run
  takes the exit an exhausted allowance takes, so the one-time withdrawal of a cover-written failing test (#406) still happens, sooner:
  up to three refines sooner at the default allowance of five. If nothing is withdrawn, or the suite is still red afterwards, the run
  ends `VERDICT: FAILED` (exit 1), as an exhausted allowance does, with its work left in the run's worktree (nothing is committed before
  a run passes); if the withdrawal makes the suite pass, the run goes on and its pull request lists the withdrawn test, as before.
  A run that timed out, and a run whose output reached the runner's 4,000-character limit, are never counted: a timeout reads the same
  whatever the edits did, and a clipped output may hide an earlier failure that was fixed. The message names the failing test; when it
  is one this run wrote, it says so and lists the spec's stated and proposed criteria, and it does not say whether any criterion covers
  the test. Only the failing-tests allowance is watched, not the type-error or required-behavior ones, and only `run_feature` (the CLI,
  the MCP tools and autorun): the Temporal workflow's own loop is unchanged. `SDLC_REFINE_PROGRESS=0` restores the old behaviour.
  **Behaviour change:** a refine whose edit leaves the failure exactly as it was no longer earns another run after the second repeat
  (the test that said "an editing refine uses the whole budget" now says it for a failure that changes).
  Measured on the 116 recorded test outputs of this repository's SWE-bench pilot runs: the rule fires in 2 of 18 multi-run sequences
  and in neither did a later recorded run pass. It would fire in 2 more if clipped outputs were compared; 39 of the 116 recorded
  outputs (a third) had been clipped to the runner's 4,000-character limit, and they are deliberately not compared. Not measured:
  Maven, Go and Node output (none recorded), where the rule stays inert unless the output repeats exactly. Not reproduced live.

### Changed

- **Behaviour change: `investigate` ranks landing sites on the ticket's title, and the body only breaks ties ([SSPN-192](https://fibonacci-solutions.atlassian.net/browse/SSPN-192)).**
  Before, the retriever was handed the title and the body as one flat set of words, so the title had no extra weight and a long body
  drowned it. Now the `investigate` command (single- and multi-repo) and the `investigate` MCP tool order hits by the score the title's
  words earn; hits with an equal score are ordered by the full text's score; hits that match the body only follow the title's. A ticket
  with no body, or a title with no usable words, is ranked exactly as before. `design`, `build`, autorun and every other caller keep the
  flat query, because a file named only in the body must still be proposed there (two NSS-1231 design tests pin that). Turn it off with
  `INVESTIGATE_TITLE_RANK=0`. The API change is an optional `title` on `GroundedRetriever.scored_symbols` and `rank_on_title` (default
  `False`) on `build_investigation`.
  **Measured, pre-registered, independent:** on 250 random SWE-bench issues outside the Verified set, a fixing file is in the top three
  for 54.4% of issues against 45.6% before (+22 of 250; paired one-sided exact sign test p = 0.013) and in the top ten for 70.0%
  against 68.8%. The 38-label localization floor (12/18/22/27) holds exactly, and the 40 further labels are unchanged (both sets are
  title-only, so they cannot move). On the sealed half of Verified, which is **not blind** for this change, the gain is larger (top-3 143
  against 101). **Not shown:** that it beats a plain keyword search (level at top-3, 0.544 against 0.536; below at top-10, 0.700
  against 0.728; no test was pre-registered), or that the first line alone beats the flat query (it did not on fresh issues, p = 0.34).
  Details and limits in `BENCHMARK.md`.

### Added

- **The SWE-bench localization harness is in the repository ([SSPN-191](https://fibonacci-solutions.atlassian.net/browse/SSPN-191)).**
  `scripts/swebench_localization/` re-scores the 500-issue localization figure published in `BENCHMARK.md` (no model call, no Docker, no
  spend): `loc_harness.py` checks each issue out at its pre-fix commit from local bare mirrors and scores `investigate` against the files the
  fixing patch changed, with chance and a BM25 text-search baseline; `analyze.py` prints the pre-registered tables; `clone_mirrors.sh`,
  `check_commits.py` and `selftest.py` support it. No product behaviour changes and nothing under `src/`. The paths (Spine checkout,
  mirrors) are now arguments, the memory reading moved into the worker so it no longer needs `/usr/bin/time`, and the pin check is the
  optional `--expect-sha`; the scoring is unchanged and was checked rank-for-rank against the original run on a seeded subset (see
  `BENCHMARK.md`). A full 500-instance re-run through the moved harness was **not** done in this change.

### Documentation

- **`BENCHMARK.md` now reports an independent localization check on 500 SWE-bench Verified issues, and it does not flatter Spine.**
  No product behaviour changes. On Spine `69ada66a`, run once, untuned and pre-registered, a fixing file is in the top ten for **0.668**
  of the 500 (95% interval 0.626–0.710; chance 0.017), but a plain BM25 text search with no graph scores **0.702**: the difference is not
  significant (paired p = 0.21 at top-10, 0.14 at top-3) and favours text search at every cut-off. Giving `investigate` the whole issue
  text does worse than the first line alone at top-3 (0.428 against 0.552, p = 8e-6). Python only, 46% Django, and the harness and
  results are kept outside the repository (the section gives their digests), so this is the one figure on the page that cannot be re-run
  from a checkout. `README.md` carries one sentence saying so.
- **`BENCHMARK.md` now reports the first end-to-end number: 26 of 100 ([SSPN-200](https://fibonacci-solutions.atlassian.net/browse/SSPN-200)).**
  No product behaviour changes. On a random sample of 100 SWE-bench Verified issues that were never run through the generation pipeline
  during development, Spine `bc2a6af0` with `claude-opus-5` resolved **26 of 100** (Wilson 95% interval 18.4–35.4%) under the official
  grader, counting parked and failed runs as not resolved; mean cost $0.93 per issue ($92.75 for the run). Of the 74 that did not
  resolve, 65 never released a patch (29 were parked at the validity gate, 32 stopped in implement, 4 hit a setup or time failure); of
  the 35 patches released, 26 resolved. One run per instance, Python only, **not a Verified score, not a leaderboard figure, and not
  compared with any other system.** The section also discloses the aborted first attempt and the one pipeline change made after its first
  two instances parked (B93, #600), and that the runner, books and pre-registration are kept outside the repository, so this figure cannot
  be re-run from a checkout. `README.md` carries one sentence; `docs/specs/STATE-OF-SPINE.md` no longer says no SWE-bench number exists.

## 3.59.0 — 2026-10-15

### Fixed

- **Upgrade note: a plan approved on 3.58.0 reads as stale once — re-plan and re-approve.** The plan's twelve sections are
  hashed, and several fixes below change what they say for the same spec (the ranking and the landing set reach `Areas`,
  the blast-radius diagram, containment and coverage). Measured: a Bug and a Story spec rendered against one tree by 3.58.0
  and by this release give different plan digests (`1c7fa8c2…` → `7451a3cd…`, `abffda4b…` → `655ecc4e…`). Nothing else about an
  approval changes.
- **The run budget counts a model whose price Spine does not know (SSPN-180 / B87).** A call on a model the pinned price table
  does not list recorded $0, so `SDLC_RUN_BUDGET_USD` never tripped on it: a run could spend without limit while the budget read
  $0. Measured on the pinned table: the GPT-6 models make the price lookup raise (B81 marked those "not priced", and the budget
  ignored the mark), and the **Claude 5.5 family** (`claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`)
  makes it return 0.0 *without raising*, so nothing marked them at all. Now a zero is "free" only if the table lists the model
  (a local model listed at zero stays free), and the budget charges every call with no known price an **estimate** at
  `SDLC_UNPRICED_USD_PER_MTOK` (`input,output` dollars per million tokens; default `5,25`, Claude Opus 5's price, in the top tenth of the
  table's chat models by price), from the reported tokens or, if the provider reported none, from characters (about four a token). The
  ledger is unchanged (`$0`, "not priced"); the budget keeps the estimate apart, the exhausted-budget message says how much of the
  spend was estimated and for which models, and a disabled cap (`0`) still tracks it and never refuses. `SDLC_MODEL_PRICES` (a JSON
  object, or the path of one) gives a model its exact price through litellm, so nothing is estimated for it. Both variables fail at
  startup, naming the variable, when malformed (a malformed value is also refused by every entry point that reads the cap, with
  the cap's exit 2). **A local model the table does not list** (for example `ollama/qwen3:30b`) is not free to this rule: it is
  estimated unless you give it a price of `0` in `SDLC_MODEL_PRICES` (a model listed at zero, tag ignored, stays free). The
  estimate is blunt by design: it overcounts a cheap model and undercounts the
  priciest tenth. **Not measured:** a full client call against a live provider for the Claude 5.5 names — the probe called the
  same cost function on a synthetic response.
- **A `bin/` directory is skipped only where it is .NET build output (B63).** `bin` sat in `DEFAULT_IGNORE_DIRS` with the
  comment ".NET build output", so `pkg extract`, doc ingestion and C include admission skipped every `bin/` in every
  language, silently: on SWE-bench Verified base commits sympy's `bin/*.py` (12 files) and django's `django-admin.py` and
  `compress.py` produced no nodes, so their symbols were absent from `blast_radius`, `investigate` and `localize`. A
  `bin/` is now skipped only when a `.csproj`, `.fsproj`, `.vbproj`, `.sln` or `.slnx` file sits beside it
  (`is_dotnet_output_dir`); compiled output and published static assets (`wwwroot/*.js`, which would enter the graph as
  phantom JavaScript) stay out, a Python or shell `bin/` is walked. Only the three `pkg` walkers the ticket measured use the
  new set (`PKG_IGNORE_DIRS`); `DEFAULT_IGNORE_DIRS` is unchanged for the other ten files that read it (codegen layout,
  language detection, the agent's file listing), which were not reported and are not measured. `pkg extract` now names the
  directories that can hold real source and that it did not enter, e.g. `(ignored directories: build (2), vendor (1))`;
  `node_modules` and `__pycache__` are not listed. No `bin/` exists in Spine's own tree, so its graph is unchanged. The
  other ignored names the ticket mentions (`build`, `dist`, `target`, `vendor`, `obj`) are not changed: each needs
  measuring on the pinned repositories first.

- **A cross-repo Python import of a re-export lands on the symbol, not the provider's package (N18).** Inside one
  repository `from shared import to_cents` resolves to `shared.money.to_cents` when `shared/__init__.py` re-binds it,
  but across a declared `package` join the consumer's placeholder matched no provider symbol and fell back to the
  longest provider module prefix, `shared` — so two imports collapsed into one edge to a module and both calls landed
  on it. The provider's re-export tables were built while its files were read and thrown away, and a warm cache never
  re-reads them, so they now travel with the join: `PythonExtractor` keeps them, `load_or_extract_repos` saves them in
  a `.exports.json` sidecar beside the cached facts (a missing or unreadable one means extracting again, never a
  guess) and passes them to `link_joins`, whose package join resolves the name through them with the same resolver the
  single-repository pass uses (aliases, chains, star imports, a member of a re-exported class). A name the tables
  cannot pin down (two imports that disagree, a rebinding) keeps the old package fallback, so the change only
  sharpens an edge. New corpus case `multirepo/package_reexport_join`; the other six multirepo cases are
  byte-identical.
- **A Python function's own name is no longer read as the file's import or `def` of the same name (B54).** The call
  pass looked the leading name of `Store.make()` or `Store()` up in the file's imports and module-level defs and never
  asked whether the calling function binds that name itself — as a parameter (`def f(Store)`), an assignment
  (`Store = Rocket`, `whisper = _load_whisper()`), a loop or `with` target, a nested `def`/`class`, or an import of a
  different target — so the call was emitted to the module-level name, and the receiver pass then landed the invented
  `Store.make` on the ancestor that declares `make`. Such a call now produces no edge (refuse, never re-point), and a
  nested function inherits the locals of the functions around it. A local import of the *same* target keeps its edge.
  Edges only disappear: an old-vs-new run over Spine's `src` and `tests`, the Python corpus, pydantic, httpx and litellm
  removed 291 `CALLS` edges and added none, and each removed edge's caller binds the name in an enclosing function.
  New corpus refusals in `corpus/python/instance_calls`; no known gap closed.
- **A Kotlin constructor call to a nested class lands on the nested class (N17).** `Inner()` written inside `Outer`,
  inside a class nested in `Outer`, or two levels down is `Outer.Inner` by Kotlin's scoping, but the call pass built the
  id from the package and the simple name alone — `java:<package>.Inner`, which exists nowhere — so the edge pointed at
  an external placeholder of that name. The call now looks the name up through the caller's enclosing classes first
  (`_FileContext.nested_type`), so a nested class also shadows a top-level class of the same name inside its outer
  class. Anything else keeps its old id: the enclosing class's own constructor, a top-level class, a type alias, and
  the qualified `Outer.Inner()`. Old-vs-new over the 21 existing Kotlin corpus cases, the 385 Gradle accessor sources and a local
  Android project moved 2 `CALLS` edges (both genuinely nested classes) and changed nothing else. New corpus case
  `kotlin/nested_class_calls`.
- **`explain_symbol` says how many it dropped (B59).** It cut `called_by`, `called_through_interface`, `calls` and
  `instantiated_via_type` to 15 and `contains` to 25 with no total, so a symbol with 40 callers read as having 15
  (CLAUDE.md invariant 7: bound honestly). Each list now has its total beside it — `called_by_count`,
  `called_through_interface_count`, `calls_count`, `contains_count`, and `instantiated_via_type_count` for a
  constructor — the way `blast_radius` already reports `caller_count`. New output keys only; the caps are unchanged.
  On Spine's own source 73 symbols have more than 15 callers (the most, `typer.echo`, 339).
- **The doc radius walks every owner of a symbol, and an ORM entity owns its columns (B41, items 1, 2, 3 and 5).**
  `blast_radius` and `explain_symbol` list the docs naming a symbol, its class or its module. (1) The owner walk kept
  one parent per child, so a C function declared in `cart.h` and defined in `cart.c` listed the docs naming only one of
  the two files — a doc naming `src/cart.c` was missed entirely; it now walks every parent
  (`FactStore.all_parents_index`; `parents_index` is unchanged, it has seven other callers). (2) A column under an ORM
  entity (`py:entity:users.id`) got none of the entity's docs, because the walk accepted only a type or a module; an
  entity now counts as a class. (3) `_in_repo_store` / `_in_repos_store` take their callback by `Callable[..., dict]`
  and pick a 2-, 3- or 4-argument call by flag, so mypy could not catch a mismatch; they are now overloaded on the
  flags, with no change in behaviour. (5) Text in an HTML page that spelled the `<pre>` placeholder (NUL-delimited) was
  rewritten as a code fence; NUL is now dropped from page text. Item 4 (`_NOT_DOCS_RE` missing `dev-requirements.txt`
  and dropping a prose `Requirements.txt`) is not changed: it needs a content heuristic, and Spine's own tree has no
  such file. The C# partial-class case the ticket names does not reproduce: both files sit under the same namespace
  module, so nothing was lost there.
- **The PR review no longer calls an unchanged route "stale" (N19).** `GroundingVerifier.stale_findings` re-extracts
  each changed file but never runs the front-end's `finalize`, and a route (`py:endpoint:GET /health`) or an ORM entity
  is created there. So every route and entity in a touched file was reported "Knowledge graph is stale: ... the current
  source no longer defines it" about source that had not changed, as a WARNING on each PR review touching one. Running
  `finalize` on a single file is not the fix: a route's id carries the prefix set where its blueprint is registered, in
  another file (`GET /v2/api/items` exists only because `app.py` registers the blueprint under `/v2`), so a walk over the
  changed files alone yields `GET /api/items` and a new false finding. Those facts are now not judged: listed in the
  new `GroundingVerifier.unjudged_freshness` (replaced on every call) rather than reported, as `skipped_freshness`
  already does for a suffix no front-end claims. A deleted or unparseable file is still judged stale in full, and a
  symbol removed from a file that also holds a route is still flagged. The ticket's second claim, per-walk state growing
  across runs, did not reproduce (three passes on one verifier, sizes flat).
- **`is_test_node` no longer classes production names as tests (B85).** After the path check it fell back to a name
  match with no word boundary, so `latest`, `contest`, `attest`, `testify`, `AgentSpec`, `ToolSpec`, `TestRunner` and
  `_run_tests` counted as test code, in coverage and regression-gap decisions and in the build document's importer
  list. The fallback now accepts only a `test_` function and a CamelCase `...Test` / `...Tests` class (C# keeps its
  test classes in a `Foo.Tests/` project, which the path rule does not match). On Spine's own graph (13,609
  function/type symbols): 49 of the 53 symbols classed as tests by name alone are now production, none moved the other
  way, and the 4 left (`test_author`, `test_command`, `test_files`, `test_functions`) are `test_`-prefixed names. Not
  shown: the effect on a rendered build document or regression-gap report, and no .NET or Java repository was measured.

- **`investigate` ranks the fixing file higher and shows ten files, not ten symbols (SSPN-176; the retrieval half of SSPN-168 / B84).**
  Three changes. (1) `build_investigation` lists the ranked symbols of up to ten distinct *files*, at most three symbols each and
  thirty in all (ten symbols named six or seven files), and says how many matches it did not list — an exact count below 200
  matches, "at least" beyond; an explicit `max_symbols` keeps the old meaning, so `design` (8) is unchanged. `score_label` now ranks
  by distinct file, as its description always said. (2) A *weak* match (its words also name symbols in other files; flagged since
  81029ecc but only for hits that survived the cut, and ranking nothing) now has its score halved for every match before the cut, so
  a stem such as `handl` no longer lets every `handle` outscore the one symbol a ticket named. (3) Each ticket word found in a hit's
  file path and not already matched by its name adds 0.5, at most two, to a hit that already matches by name. Settings were chosen
  from pre-registered grids on 30 labels held out from the original 38; two other candidates (less aggressive word-ending
  stemming, which breaks the CB-686 fixture; rare-word weighting, which loses five hits from the top ten) were tried and dropped.
  Net on the 38 benchmark labels, top-1/3/5/10: ranking view 9/16/20/27 → **12/18/22/27**; as shown 9/16/20/23 → 12/18/22/27 (four
  of the top-10 labels are display, not ranking); nothing that hit now misses. On 40 further labels from seven other projects
  (30 chose the settings, 10 sealed and read once), top-3 18 → 23; over all 78 the top-3 gain is 34 → 41 (p = 0.065 — consistent in
  direction, **not significant at 0.05**). `scoreboard.json`'s localization block is re-recorded at the file view: 9/14/17/23 (symbol
  view) → 12/18/22/27, so the gated top-1 floor rises 9 → 12.
  **What else moves.** `pkg_relevant_symbols`, `pkg_api_surface` and the codegen grounder use the same ranking: their top-8 differs
  on 62 of 78 benchmark titles (the first item on 23). Callers that take the default `max_symbols` (`autorun`, `build`, `change`,
  the plugin, `evidence`, `builddoc`, `spec_context`) now receive up to ten files — briefs are about 37% longer (median 3,433 →
  4,709 characters). The effect on delivery quality is **not measured**. (This entry first said the validity gate's context-size
  check would see the larger file set; it did, and the next entry reverses it.)
- **The validity gate, blast radius, churn and the `in_evidence` badge measure the files of the first ten non-weak rows, not the ten
  files the brief lists (SSPN-181 / B88).** The change above widened the "files this ticket lands in" from six or seven to ten for
  every consumer that took the default list. Replayed on 40 recorded real autorun tickets (SWE-bench pilot runs from the
  maintainers' machine; a corpus of large files, so a ceiling for big repos and not a Spine-wide rate), **6 flipped from PROCEED to
  TOO_BIG** (13 → 19 refused). The first idea, dropping weak rows, fixes none of the six — weak rows occur in 2 of the 40 — and graph
  mode, which already dropped them, was affected too (the same six flip, one other ticket flips back: 13 → 18). One definition now
  serves every consumer, `landings.landing_set`: the files of the first ten non-weak rows, first-seen order. It is used by
  `evidence.landing_files` (graph mode: gate, blast radius, churn, `bind_criteria`), the `autorun` fallback, `cli/build.py` and
  `builddoc.build_plan` (so `sdlc_plan` and `autorun` cannot disagree about one ticket; a path the ticket names still leads). The
  brief still shows ten files. On the same 40 tickets, on one graph, it refuses 13, as before the change; it differs from the
  pre-#575 verdict on two (scikit-learn-25747 → TOO_BIG, sphinx-8621 → PROCEED), which come from the new ranking, not from this fix.
  **One behaviour change to know:** in the `autorun` fallback an all-weak ticket now has an empty landing set, as it already did in
  graph mode, `design` and `builddoc` — a Bug is refused `UNLOCALIZED` and a Story proceeds with the "names no file" finding; before,
  the weak files counted as located and it proceeded silently. **Not measured:** whether codegen succeeds on any of them. The gate
  also counts up to ten files where a design proposes one to six (median 2, 27 recorded runs); whether it should measure the
  design's files instead is SSPN-185 / B89, not changed here.
- **The CLI says "cannot reach the API" in one line instead of a 245-line traceback (B36).** `orchestrator template list`
  and `contract list` against a closed port ended in a Rich traceback and exit 1: `_check` handled an HTTP *status* of 400
  or more, and a refused connection or a timeout never produced one. The CLI's HTTP client now turns a connect failure,
  a timeout and any other transport error into one line on stderr and exit 1 — the URL it tried, `orchestrator up` for a
  refused connection, `ORCHESTRATOR_API_TIMEOUT_SECONDS` for a timeout — and a 401/403 adds a line pointing at
  `ORCHESTRATOR_API_KEY`. The happy path is unchanged. Reproduced before the fix; the timeout and 401 paths are tested
  against a local server and a patched `send`, not a live API.
- **`SDLC_MAX_CRITERIA` raises the validity gate's acceptance-criteria cap (B82).** `TOO_BIG` is a hard stop at 12 stated
  criteria and the only way past it was editing the source. The cap now reads `SDLC_MAX_CRITERIA` (a positive integer;
  anything else is ignored with a logged warning and 12 applies), and the refusal's evidence names the variable. An
  explicit `max_criteria=` argument still wins, and the gate's accuracy eval is pinned to 12 so it cannot drift with an
  environment. No flag and no split hint: on 262 saved specs the largest has 12 criteria and none exceeds the cap, so the
  reporter's case is not reproduced locally.
- **`MCPRegistry.call` now refuses a write tool, so intake's MCP source can no longer reach one (B43).**
  `MCPToolHandler` has always refused a tool that is not declared read-only (`readOnlyHint: true`) unless its server is
  `write_enabled`; `MCPRegistry.call` checked only the allow-list, and intake's `MCPSourceAdapter`, `db.py`, the docs
  pull and `orchestrator mcp call` all call the registry directly. A misconfigured `MCP_SOURCE_*` tool name could reach
  `jira_create_issue` (which mcp-atlassian reports as `read_only=None`) with no gate; reproduced with a fake client, the
  registry reached the server. The rule now lives in the registry: a tool whose hint is not `True` is refused unless its
  server is `write_enabled`, before the server is contacted, and an unannotated tool counts as unknown, not safe. The hint
  comes from discovery when it has run; otherwise the first call to a tool costs one `list_tools` session and the answer
  is cached (skipped for a `write_enabled` server). If that lookup fails the call is refused. The one deliberate exception
  is a new `vouched=True` argument for a caller that has already cleared the tool: the docs pull (its tools are vetted
  against `readOnlyHint` or the source's `trust_read_only`) and database introspection (a constant SELECT Spine wrote, sent
  through a query tool most Postgres servers never annotate). Not measured against a live server: the real per-tool flags
  of the Atlassian server were not re-read.

- **A token count or a price the provider did not give is no longer recorded as a real zero (B81).**
  A reply with `usage` absent, null or all-zero was recorded as 0 tokens, indistinguishable from a real zero, so a run
  record, a Jira worklog or a cost figure built from it was wrong without anyone knowing. The cause is not where the
  ticket put it: litellm itself pads a missing or null `usage` with `Usage(0, 0, 0)` (and `usage` is set either way),
  so `_extract` never sees an absence; what is reliable is that a real call always has at least one prompt token.
  A reply with none is now marked `CompletionResult.usage_reported = False`, said once per model
  (`llm.usage_missing`), counted per stage in the token ledger, and shown beside the figure: the run journal
  (`0 tokens (usage not reported on 3 of 3 calls)`), the Jira worklog (a **Caveat** line, and the claim that every count
  is the provider's own is dropped), the HTML report and the worklog log line. The same goes for the price:
  `completion_cost` raising was swallowed to `$0.0`, so every GPT-6 call (the bundled price table lists none) recorded
  $0 even with usage present; it is now `cost_known = False`, said once per model (`llm.cost_unknown`) and shown as
  `not priced on K of N calls`. A genuine zero, and a price of exactly zero (a local model), stay as they were. Both
  fields default to `True`, so the 69 places that build a `CompletionResult` are unchanged, and a clean ledger renders
  byte-identically to the previous release. Not fixed: a run budget still charges an unpriced call as $0, so
  `SDLC_RUN_BUDGET_USD` cannot trip on it (filed as B87, SSPN-180, a design choice). The tester's zero totals were not
  reproduced; 383 local run records hold no call with 0/0 tokens.

- **A C# static call inside a class that derives from ASP.NET's controller bases is no longer refused (B26).**
  `Helper.Run()` inside `class Home : ControllerBase` got no edge, because an external base class might declare a
  member called `Helper` (C# looks a simple name up in the enclosing class, inherited members included, before it
  looks for a type; `File.Read()` in a controller binds the method group `ControllerBase.File`, not `System.IO.File`),
  and the pass cannot see inside the framework. On a .NET service that refused 91 true static calls. A committed table,
  `typed_receivers.EXTERNAL_MEMBER_NAMES`, now lists every member name `ControllerBase` (65) and `Controller` (76)
  declare, inherited members included, read from the accessible metadata of the ASP.NET Core 8.0.28 reference pack and
  the 10.0.9 runtime (the two agree) plus `System.Object`; a static call whose first name is absent from it is the type.
  Keyed by the fully qualified id, so a bare `ControllerBase` counts only where a `using Microsoft.AspNetCore.Mvc`
  makes it Microsoft's. A base the table does not list still refuses, and so does a type named like a member the base
  does declare (`User`, `File`, `Url`, `ViewBag`, `Equals`). On the service: **81 calls gained, none lost** (all 81 sit
  under `ControllerBase`, on two in-repo static helper classes; the 10 others sit under `ClientBase` and one unlisted
  base and remain refused); Spine's own graph and a 698-file Java repository are unchanged. `ClientBase<T>` is not in
  the table: its assembly is not on the machine, and an entry is read from metadata, not recalled.

- **The localization gate now runs: a drop in top-1 or top-10 on the pinned corpus fails a weekly and a path-filtered pull-request
  workflow (B84).**
  The gate was recorded on 2026-09-01 as `not_measured` and compares only when both sides are measured, while ordinary CI is
  offline by contract, so it could not fire and top-10 fell 27 → 23 unseen. `scoreboard.json` now holds the measured floor
  (top-1 9/38, top-3 14, top-5 17, top-10 23; 0 empty; gold digest `0a6ecf8c7bd22fd8`) and `localization-gate.yml` runs
  `pkg accuracy --check --pinned-corpus` with CI's extras. Not a required check yet — it needs the network. Only the localization
  block of the scoreboard changed; the other baselines were not re-recorded.

- **Java enums keep their methods and fields, a field and a method of one name are two nodes, an external base stops
  dangling, and an overload is contained once (B29, B25, B27, N9).**
  Measured on mysql-connector-j (698 files) and a .NET service (561 files).
  *Enums (B29):* a Java enum's methods, constructors, fields and nested types sit in `enum_body_declarations`, which
  the member walk never entered: 81 enums held 327 methods and 328 fields the graph did not know, and 524 calls and
  creations inside them. They are extracted now; enum constants (769, three with bodies) are still not.
  *Same name (B25):* `private int length; int length()` minted one id and the first declared won, so a class that
  declared the field first lost its method node and a call such as `z.length()` was refused. The field takes
  `java:a.Lazy.length#field` **only when a method of that name exists**; no other id changes (38 of the repository's
  fields), and 266 more calls resolve. Two old edges move: `ConnectionImpl.isClosed` reached the interface's
  `Session.isClosed` because `NativeSession`'s field hid its method; it now reaches `NativeSession.isClosed`, which is
  what the compiler binds.
  *External bases (B27):* 270 `IMPLEMENTS` edges pointed at nodes that did not exist. A qualified name as written
  (`com.google.protobuf.MessageOrBuilder`) now gets an external `Type` node; 27 simple names that fell to the
  same-package guess for an implicit `java.lang` type (`java:com.mysql.cj.jdbc.Comparable`) are repointed to the name
  as read (`java:Comparable`), as C# already did. `pkg verify` finds 0 dangling edges; the step runs after calls are
  settled, so no call can change.
  *Contained once (N9):* each overload emitted another `CONTAINS`, so one member was listed up to 104 times by
  `children_of`. One per (type, member) declaration now, in both languages: 2,483 duplicate rows leave the Java
  graph and 114 the C# one; the 91 that remain in the C# service all span two files (a partial class, or one source file built by two projects), which a per-declaration rule cannot see.
  Old against new: no node lost, no `CONTAINS` pair lost, no call lost except the two moved above; Spine's own graph is
  unchanged. The Java `CALLS` recall on the corpus rises from 0.9231 to 0.9487 (the `field_method_same_name` gap in
  `typed_receivers_refusals` closes) and the committed baseline is re-recorded.

- **The published localization figure was out of date: 27/38 → 23/38 top-10, 12/38 → 9/38 top-1 (docs only, no behaviour change).**
  `BENCHMARK.md`, the README and three internal records quoted Spine 3.29.0's score. Re-measured on 2026-10-13 on the same 38
  labels, pinned trees and scorer, 3.57.0 scores 23/38 and 9/38; two changes account for it (3.35.0's C++ header parse, which
  removed two hits that came from locals mis-parsed as fields, and 3.38.0's stemmer, which won no label). The pages now say so,
  and say that the ratchet gate described there had never compared two measured values. The retrieval fix and the gate are
  tracked separately.

- **The documentation walker and language detection no longer read files behind a symlink the code walker refuses (N7,
  N8).**
  `RepoCodeExtractor` admits a symlinked file only when its target is somewhere the walk would go: inside the root, and
  not inside an ignored, dot-prefixed or nested-checkout directory. `read_doc_pages` and `language_file_counts` walk the
  same tree with the same filters but had no such rule. A `docs/guide.md` linking into `node_modules` brought vendored
  prose in as a first-party `Doc`; a link leaving the repository was read too, and its text can reach a codegen
  prompt when its page names a symbol; and three JavaScript links into `node_modules` beside one real `.py` made
  `--language auto` pick JavaScript for a Python repository whose graph then held no JavaScript. A dangling link was
  counted as a source file. All three walkers now share one rule, `pkg.extractor.symlink_admitted`. Directory links were
  never followed (`os.walk` does not descend them) and are pinned by a test. Measured against the previous code on a
  synthetic repository: 6 inadmissible docs leave, JavaScript 3 to 0, the code walker is unchanged; Spine itself, its
  `corpus/` and 8 real checkouts are identical (none holds a symlinked doc or source file), and no walk gained a file.

- **A refine whose every edit is refused now ends the loop instead of crashing the run, and the guard that refuses test
  edits recognises more ways to switch a test off (B50, B48).**
  When every edit refine proposed was refused (a weakened test, or a file nothing names), the refusal was filed under
  "the model submitted no files": two corrective retries, advice that said "it needs at least one file", and then,
  with the retries spent, an error that left `refine` (declared to tolerate an empty answer) and crashed the run past
  the code that releases the ticket. A refusal is now its own kind: one corrective retry that says what was refused
  and why, and an empty change with that reason as its summary when it is spent, so the loop reaches its normal FAILED
  verdict. Implement and author_tests, which do not allow an empty answer, still raise. The guard itself
  (`_weakens_test`) refused only a lost `assert` or test; a test can be disabled without either. It now also refuses,
  in a test that exists in both versions: a changed decorator (`skip`, `xfail`, `skipif`, a `parametrize` case) or
  `pytestmark`; an added `pytest.skip`/`xfail`/`importorskip` call or early `return`; an assert put under `if False:` or
  a `try` that swallows it; `pytest.raises` removed, widened to `Exception` or stripped of its `match`; a second
  `def test_x` shadowing the first; a value an assert compares against changed; and a removed `self.assert*`. A new
  test may use `importorskip`, and renaming an exception inside `pytest.raises` is not widening it. Measured on 118
  real test-file edits from lint, format and type commits in this repository's history: all 29 the old guard refused
  are still refused, 3 more are (a changed `parametrize` case and two changed test inputs, edits to what a test
  checks rather than lint repairs). Not covered: a value changed in a fixture or helper the test calls.
  Not shown: either guard against a live model's refine pass.

- **`openspec://` no longer hands a REMOVED requirement to codegen as something to build (B56).**
  Every `### Requirement:` in a change's delta specs became an acceptance criterion, whatever `## ADDED / MODIFIED /
  REMOVED` section it sat under, so the requirements a change exists to *delete* were written into the contract the
  generated tests are checked against. On a real upstream change (`merge-init-experimental`, 47 criteria) four of
  them were removals such as "File Generation: **Reason**: AGENTS.md and project.md are no longer generated".
  A requirement under `## REMOVED` is now never an acceptance criterion; the change's scope says what it removes
  instead, one line per requirement (`Removes: File Generation — AGENTS.md and project.md are no longer generated`,
  using the `**Reason**:` line OpenSpec writes, or the bare name when there is none), so the plan and the reviewer
  still see it. ADDED and MODIFIED (which carries the full replacement) are unchanged, as is a spec with no
  recognised section header. The same read serves `sdlc feature --source`, `ingest`, the backlog, autorun, the studio
  preview and the requirements check. Measured on the 104 changes of a real OpenSpec checkout: 102 read exactly as
  before, criteria and scope; the 2 that differ are the 2 that contain a REMOVED requirement, 6 criteria leave and
  none are added. `RENAMED` is not parsed (none of 185 real spec files uses it, and its entries are not requirement
  headings, so it never produced a criterion); a test pins that.
- **The run cap now covers intake, and the MCP and remediate entry points, which ran without one (B32, B33).**
  `SDLC_RUN_BUDGET_USD` (and `--max-cost`) bounded the build and the review but not the stage that runs first: intake
  built its own model client, outside the cap and outside the run's ledger, so a run could spend on intake with the
  cap untouched and the run record reporting $0.00 for it. Intake is the only planning stage that can spend (research
  runs tools that reach no model, and design is called without one, a test now pins that), and it now calls the model
  through the run's own client in both `sdlc autorun` and `sdlc feature`: its spend counts toward the cap, a cap
  spent during intake parks an autorun run (exit 4, as in the build and the review), and the ledger has an `intake`
  row. **Ledger and run-record totals for a run that does intake go up; that is the missing spend now being counted,
  not new spend.** A cached intake still costs nothing. Separately, three entry points ran with no cap at all: the MCP
  `sdlc_feature` tool, the MCP remediate tool and `sdlc remediate`. They read the same variable now (a malformed value
  refuses before any spend, an exhausted cap comes back as an error result), with one budget per call. For remediate
  the cap is **per task**: a batch of N tasks can spend up to N times it.
  Not shown: what intake costs in practice, or that a live run is stopped by the cap; the tests drive fake clients, and
  the figures in the ticket's comment (108 s and 320 s "of model calls" booked at $0.00) are elapsed time, part of
  which is graph extraction, so they do not say how much of it was model spend.

- **The build document no longer calls a change "contained" because its importers have `test` somewhere in their
  name (B78).**
  "Nothing in the product depends on what changes" is printed when every module that imports the change is a test,
  and the rule that decided that matched `test` as a substring: `testimonials.views`, `app.latest_tests_runner`,
  `app.system_test.cli` and `app.testing.utils` were all read as tests, and so were Spine's own production modules
  `sdlc.testenv`, `sdlc.testpaths` and `sdlc.testrunner`. A change visible to production code was reported as
  invisible to it. A module is now a test only by a whole segment of its name: `tests`, `spec`, `conftest`,
  `__tests__` and the like, a PascalCase `...Tests` suffix, or a file name that is `test_*` or `*_test(s)`; a
  package merely called `system_test` or `test_data` is read as product, and `testing` no longer counts by itself.
  Where the name is doubtful it is read as product, because the opposite mistake only inflates a count the caveat
  beneath already hedges. Measured against the old rule on 9,072 module names from installed packages: 72 go from
  test to not-test, none the other way. 57 of those 72 are `testing` packages (`sqlalchemy.testing`,
  `alembic.testing`), shipped test-support code that now counts as product; the rest are mostly genuinely
  production (`temporalio.api.testservice`) or private test helpers (`anyio._testing`). The same defect class in
  `coverage.is_test_node` (symbol names such as `ToolSpec` and `latest`) is tracked separately as B85.

- **GPT-6 models (`gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-6.1-sol`, bare or `openai/`-prefixed) can now be
  called (B45).**
  Every call to one failed, in three ways that surfaced one after another. litellm refused `reasoning_effort` for
  them before the request left the process; sent without it, the request carried `max_tokens`, which OpenAI
  refuses for these models; and under the price table Spine pins, litellm did not know the bare name's provider at
  all ("LLM Provider NOT provided" - that table lists no GPT-6 model). They are now called through litellm's
  Responses bridge (`openai/responses/<model>`), which names the provider, carries the token limit as
  `max_output_tokens` and the level as `reasoning.effort`; the level is named explicitly (GPT-6 is chosen by name
  because the pinned table cannot say) and passed through `allowed_openai_params`. Checked against a local server
  that records the request, with the real litellm in a process set up as the CLI is: it POSTs `/v1/responses` with
  `max_output_tokens` and `reasoning.effort`. Exactly 4 of the 4,083 names in litellm's table change route; every
  other name, including 47 that merely contain `gpt-6` (`azure/gpt-6-astra`, `bedrock/...`), gets the request it
  always got. `azure/gpt-6-*` is not covered: sending it to OpenAI's route would call a different provider.
  Not shown: that OpenAI accepts this request and returns usable tool calls for these models. The harness shim this
  replaces got both at reasoning `high` on `gpt-6-astra`; this exact request has not been sent to the API.

- **A provider-prefixed model name (`openai/gpt-5.1`, `anthropic/claude-opus-4-5`) now gets the reasoning level you
  set, instead of silently running at the model's own default (B79).**
  The check that decides whether to send `ORCHESTRATOR_REASONING_EFFORT` looked the name up in litellm's price table
  by exact key, and that table holds bare names, so `openai/gpt-5.1` missed and was treated as a model with no
  reasoning. Nothing said so. A name the table does not hold is now put to `litellm.supports_reasoning`, which
  resolves the prefix; a name the table does hold answers exactly as before (checked on all 4,083 table entries: none
  changed, and of 1,300 prefixed names tried, 93 went from "no" to "yes" and none the other way). An unknown model
  still gets no parameter. New: when you set `ORCHESTRATOR_REASONING_EFFORT` yourself and it is not sent to a model,
  one warning per model says so (`llm.reasoning_unsupported`); the unset `high` default stays silent.
  Not shown: that a provider accepts the level on a prefixed name it now receives. Where the bare name was already
  sent the parameter it is unchanged; `openai/gpt-6-*` now meets the same GPT-6 rejection the bare name already did
  (B45, a separate fix).

- **Codegen's file list is now requested as native structured output on models that can constrain their output, to
  stop it coming back as a garbled string (B77).**
  On `claude-opus-5` the forced `submit_files` tool call returned `files` as a string, not an array, in about a quarter
  of calls: the original responses to one captured run were malformed 3 times in 11, and the same captured request
  replayed unchanged, straight to the API with no Spine, litellm or proxy involved, 3 times in 12. The string is not
  valid JSON (37 of 37 in the third benchmark pilot, a fresh draw of 20 instances), so it cannot be repaired. 12 of the
  14 instances that ran produced at least one; the B72 retry recovered 9 of those 12, and the other 3 ended with no
  patch. A tool can now opt in with
  `ToolSpec.native_schema`; `LiteLLMClient` then sends its schema as a `json_schema` response format, with **no tools and
  no `reasoning_effort`**, when litellm's table says the model supports native structured output (and see the next
  entry: sending no thinking parameter at all turned out to leave thinking on). The effort part is measured, not a
  preference: the same request with `thinking` and `effort` sent explicitly used about 14 times the output tokens, took about
  17 times as long, cost about 1.6 times as much, and truncated one call in ten at `max_tokens`. Codegen's own call is
  unchanged and the text reply goes through the same parse and the same B72 refusal. Any other model, a tool that did not
  opt in, an explicit caller schema, or `ORCHESTRATOR_NATIVE_STRUCTURED_OUTPUT=0` gets the request Spine has always sent,
  and a provider that rejects the native request gets that request instead, and once the forced tool works where the
  native request did not, the model is not asked again in that process. A rejected `temperature` is left to the
  existing temperature retry and does not count as a refusal.
  Not shown: that the API accepts this exact variant, and its malformed rate. The first live call is the acceptance test.

- **The native structured-output request now switches the model's thinking off explicitly, on Anthropic reasoning
  models (B83).** The request above sent no thinking parameter, on the assumption that this keeps thinking off. It does
  not. On `claude-opus-5` the first live run of one instance returned 7,935, 7,566 and 5,157 output tokens per codegen
  reply, against a median of about 2,008 under the forced tool (which prevents thinking), and every response opened with
  an empty `thinking` block. Replaying two of those requests straight to the API with `thinking: {"type": "disabled"}`
  was accepted next to the format and came back with 1,800 to 2,554 tokens and no thinking block (three calls); the
  unchanged request, replayed once, thought again (4,551 tokens). `LiteLLMClient` now adds that parameter to the native
  request when litellm lists the model under the `anthropic` provider and as a reasoning model (22 table entries), and
  to nothing else: for 31 of the 125 native-capable entries (Bedrock, Friendli, Vertex-Anthropic) litellm raises
  `UnsupportedParamsError` only because of this parameter, so those models keep the request they had. A model that
  rejects it gets the forced tool through the existing fallback, which drops the parameter with the response format.
  Not shown: the malformed rate with thinking off (three replies cannot give one); that the whole path through Spine
  reaches the API this way (the replay went straight to it; litellm's request build was checked offline); patch quality;
  and any model but `claude-opus-5` (the other 21 entries in the set take the same API parameter but were not run, and
  an older Claude model that does not think by default would simply see a no-op). Codegen's default path on these models
  no longer sends `ORCHESTRATOR_REASONING_EFFORT` at all; `ORCHESTRATOR_NATIVE_STRUCTURED_OUTPUT=0` brings it back.

- **The SWE-bench test wrapper no longer reports a run that executed zero tests as green (B74).** In the second
  benchmark pilot two instances ended at the coverage stop after in-loop test runs that had executed nothing
  were reported green (django-13516: all three runs, so refine never ran; sympy-23950: runs 2 to 4, after one
  real failure and one refine pass): Django printed `Ran 0 tests` and exited 0 (the model's tests were plain classes, which
  Django's unittest runner does not collect), and sympy's `bin/test` printed `tests finished: 0 passed` and exited
  0. pytest returns 5 for "no tests collected"; these two runners do not, so the refine loop never ran and the
  model was never shown that its tests were not being run. `orchestrator.evals.swebench.exec` now reports exit
  status **5** when a Django or sympy run exits 0 and the runner's own final summary line says it executed zero
  tests, and appends one line saying so. It is refuse-only: it can turn a false green into a failure and never the reverse;
  a non-zero exit, a run that executed tests (including all-skipped), pytest-kind repositories and an empty output
  are left exactly as they were. Harness only; no product behaviour changes. **Not claimed:** that the two
  instances now produce patches (best case on the 20 pilot instances is 12 → 14), why sympy collected zero
  (unverified), that the sympy "skipped" wording matches a real run (synthetic), or that the first pilot's
  runs also executed zero tests (it kept no test output).
- **A reply whose `files` is not an array is no longer read as "nothing to change" (B72).** The attempt record
  added in 3.58.0 showed what ended a benchmark run at `[refine] [] - <a fix in words>`: the model's refine
  reply had `files` as a JSON *string* (a list written out as text, cut off mid-file, holding the fix its own
  summary described) where the other stages of the same run sent arrays. `_apply` treated any non-list like an
  empty list, so a refine pass that had produced its fix was read as a legitimate no-op and the loop stopped,
  and a stage that must send files told the model it had "submitted NO FILES", which was untrue. A `files` that
  is **present and not a list** (a string, `null`, an object, a number) is now a malformed reply: it gets its
  own failure kind, `shape`, with two corrections like an empty submission, and advice that says what was
  found (type and length, never the content), quotes the summary the model gave (the retry is a fresh draw and
  cannot see its last reply), and says `files` must be an array of objects. The string is never parsed, even
  where it would parse. An absent `files` key and `[]` are unchanged. **If the corrections run out, the stage
  ends as it always did:** a stage that must send files (`implement`, `author_tests` on testable source)
  raises `CodegenError`, as before; a stage that allows an empty reply (`refine`, `revise`, `author_tests` on
  a change with nothing to test) returns an empty change if the summary claims no change, and raises the same
  `CodegenError` if it claims one. So a malformed reply costs up to **two extra model calls** and never ends a
  run in a worse place than before; a first version of this change raised in the empty-allowed case, which
  skipped the caller's cost record and ticket release, and was corrected before release. The log gains
  `sdlc.codegen.files_not_a_list type=… len=…` on each malformed reply, `sdlc.codegen.shape_retry` on each
  correction and `sdlc.codegen.shape_exhausted` when a malformed reply is let through as a no-op; the attempt
  record shows each as `rejected`. **Not claimed:** how often the model sends this shape (one recorded reply was
  read), that its retry returns an array, or that this is the cause of B68 (`no 'files' list` in
  `author_tests`) — that message comes from the same line, which is why it is suspected, but it is not shown.

## 3.58.0 — 2026-10-12

### Added

- **Every codegen model call and every test run of a run is now kept (B72).** In 9 of 29 benchmark runs a
  refine pass ended with a summary that described a fix and no files, the loop stopped at "no file changes —
  not fixable by editing code", and nothing written down could say why: the raw reply, the files the write step
  dropped and the failing test output were kept nowhere. This is the instrument for that, **not a fix**: no
  value a stage returns and no decision the loop makes changes. When a run is given a directory — `sdlc
  autorun` hands it the run's artifacts directory — each model call of `implement`, `author_tests` (the
  coverage pass included), `refine` and `revise`, retries included, writes `attempts/<stage>-<n>.json`: the
  raw reply (capped at 32 KiB, and marked when cut), the summary, the outcome (`applied`, `empty`,
  `unparseable`, `rejected`, `error`), every file that landed or was dropped with a one-word reason (`edit_failed`, `syntax`,
  `missing_target`, `too_large`, `placeholder`, `skipped_existing_file`, `refused_unnamed_edit`,
  `out_of_scope`, `weakened_test`, `shadows_module`, `not_an_object`, `no_path`, `no_content_or_edits`) and the hash of each named file before and after. Each `[run_tests #n]`
  output is kept as `run-tests-<n>.txt`, the last 64 KiB when it is longer, and one `[record]` line at the
  start of the run names the directory. **On by default; `SDLC_ATTEMPT_RECORD=0` (or `false`, `off`, `no`) turns it off.** Every write
  is best-effort — a record that cannot be written, or encoded, is one log line, never an error. Each
  attempt file also holds what the model said beside its answer and why it stopped (`meta`: model, token
  counts, finish reason), what the stage was asked to act on (`context`: refine's failure text, which
  includes a type-check, coverage or required-behaviour failure, and revise's blockers), and a model
  error that ends the call. A resumed run (`--resume` keeps the run id and so the directory) continues the
  numbering and never replaces a file, and with recording off nothing is traced or hashed. **A raw reply
  or a test's output can echo a credential**, so the value of every environment variable whose name says
  KEY, TOKEN, SECRET, PASSWORD or CREDENTIAL (eight characters or more) is replaced by `[redacted]` before
  anything is written, in its JSON-escaped form too, longest value first, and never a value that is an
  existing path — it cannot catch a secret in a changed form such as base64 or inside a URL — and the reusable workflow
  `spine-sdlc.yml` **leaves `attempts/` and `run-tests-*` out of the artifact it uploads**, because that
  job holds `ANTHROPIC_API_KEY` and `GITHUB_TOKEN` and an uploaded artifact outlives the run. Limits: the
  Temporal worker and the experimental agentic loop write no attempt record; the test runs inside the
  review-fix loops and the pre-change baseline run (taken once before the loop) are not kept; a failure
  longer than 16 KiB is recorded by its last 16 KiB and says so (`context_truncated`); and an exception
  raised while building a prompt, before any model call, has nothing to record. ([SSPN-155](https://fibonacci-solutions.atlassian.net/browse/SSPN-155))

- **`SDLC_TEST_COMMAND` runs a project's tests through a command you supply.** The pipeline ran
  generated tests only as `python -m pytest` from an interpreter it builds itself, so a project
  that needs another interpreter (an old Django on Python 3.5) or another runner (`tests/runtests.py`)
  could not be built at all. Set it to a JSON array — an argv, run without a shell — typically a
  `docker exec` into an image that already has the project's environment. One element may be exactly
  `{files}`; it expands in place to the Python test files this run has added or modified **since it
  began**, read from git against the commit the run started from, so a review fix made after the
  build was committed still finds the build's tests. **An empty selection fails with exit code 5
  and never falls back to the whole suite**, and a command without the token runs as given. While
  it is set the host builds no venv, skips its in-loop `mypy` pass (the host interpreter is not the
  project's), takes no whole-suite baseline and does not try to auto-install a missing module into
  the host; a malformed value stops `sdlc autorun` and `sdlc feature` with exit code 2 before any
  model is called. It applies wherever the feature runner runs — `sdlc feature`, `sdlc autorun` and
  the MCP tools that call it — for Python projects; the Temporal worker and the experimental
  agentic tools build their runner directly and ignore it. Unset, nothing changes. If the project
  is installed into the command's environment, put the worktree first on `PYTHONPATH` there — an
  installed copy otherwise shadows the files Spine edited. The command is run as you and is read
  from the environment or `.env` like the other `SDLC_` settings; the run log names only its
  program, since an argument can carry a credential. ([SSPN-137](https://fibonacci-solutions.atlassian.net/browse/SSPN-137),
  [spec](docs/specs/sandboxed-test-execution.md))

- **A SWE-bench Verified harness: run Spine's pipeline on benchmark instances and keep honest books.**
  `orchestrator.evals.swebench` plus `scripts/swebench_prep.py` and `scripts/swebench_run.py` (standard
  library only; the official `swebench` grader stays separate and pinned). **There is no benchmark
  number yet** — this is the instrument, exercised against the real images of the twelve Verified
  repositories (the in-loop test command ran on each; see the runbook for what did not pass) and on a
  handful of real runs, not a result. What it does: it builds each instance's
  generation repository from the pinned image as one fresh commit (no history, remote or future object);
  refuses an instance file that carries a gold field (the patch, the test patch, the grading tests, the
  issue's comment thread) and hands generation a type that has no field to hold one; runs the pipeline
  as its own process group under a wall-clock limit and a spend cap, with only an allow-listed
  environment (the model key is the only thing read from a `.env`); runs the project's tests in a
  throwaway offline container from the instance image with only the run's changes laid over it (a
  mounted clean checkout was measured to fail on a compiled repository); and files every instance in
  exactly one of six buckets — resolved, unresolved, parked, generator-error, budget-stop, grader-error
  — refusing a summary over a ledger with a missing or ungraded row (`scripts/swebench_report.py` settles
  the books from the official grader's report). The generator starts from each instance's base commit,
  not the image's `HEAD` (SWE-bench's setup commit is not empty), exported by a checkout rather than
  `git archive` and compared tree-for-tree with the image before it is used; the first version of that
  check refused six of twelve repositories and was fixed. The total cap is a projection over recorded
  spend, not a tripwire, and a `generator-error` is re-run only with `--retry-generator-errors`. A run
  that ends without a change is `unresolved`; a crash is a `generator-error`. Limits: a C or Cython edit is not rebuilt in the test
  container, and a test that already fails in a file a run modifies stays red there. How to run it:
  [`docs/evals/swebench-harness.md`](docs/evals/swebench-harness.md).
  ([SSPN-138](https://fibonacci-solutions.atlassian.net/browse/SSPN-138))

### Fixed

- **A C# run judges only what it caused, and stops before spending anything when it cannot
  (B73, [SSPN-156](https://fibonacci-solutions.atlassian.net/browse/SSPN-156)).** NSS-1243 ended
  `FAILED after 6 test run(s)` with the feature unwritten: after the model fixed its own first error,
  five attempts went on three unrelated `.razor` files. Two causes, both in code, neither shown by a
  raw log yet (**live confirmation is owed** — the run's output was not kept, and the build needs a
  private NuGet feed). (1) `DotnetTestRunner` took no part in the pre-change baseline, so failures
  the repository already had looked like the run's own; and refine may edit any file a failure
  names. It now names the tests that failed, a baseline is taken, and the failing tests it recorded
  are left out of the text refine reads. (2) `dotnet_errors` read only `path(line,col): error`, so a
  restore failure (`NU1301`), an MSBuild task failure and a bare `error CS…` line matched nothing and
  refine was handed the last 4,000 characters — warnings, on a project with 138 of them. Those
  errors are read now, warning lines no longer fill the tail, failing tests lead when no error
  explains the failure, and a failure that still explains nothing says `UNPARSED FAILURE … warnings
  are not the cause` instead of inviting an edit to silence them. **What you will see:** a C# run
  whose repository does not compile before any change, or whose machine cannot build it (an
  unreachable or unauthorised NuGet feed, no matching SDK), now ends `VERDICT: BLOCKED` (exit code
  2) before generating anything and hands the ticket back; a baseline that times out stops nothing.
  Mid-run, an unreachable feed, a missing SDK or the time limit end the run; a package the change
  itself added that does not resolve, or a project file asking for an SDK the machine lacks, still goes
  to refine. A run whose test host crashed, or whose own summary disagrees with the failures named,
  is **unreadable** — it names none, so "every failure predates the change" can never pass a run whose
  tests did not finish; an `error CS…` inside a failing test's own block is that test's message
  text, not a compiler error, while an MSBuild error line anywhere else is one — even when another
  project's tests ran and printed a summary, as they do when one project of a solution fails to
  compile. Because recognising a build error is a guessing game that three review rounds each
  defeated with a real input, the one verdict that must be right does not rely on it: while
  error-like text (`error CS…:`, a code-less `error :`) appears anywhere in the output, a run is never
  judged "only the old failures" and refine is shown the whole output. **Cost:** a repository whose old
  tests print such text never gets that shortcut and is judged as it was before the baseline existed
  (the baseline is still recorded). `SDLC_TEST_BASELINE=0` skips the baseline, the BLOCKED check and
  the mid-run stops; the digest changes (errors without a position, no warnings in the tail,
  `UNPARSED FAILURE`) apply either way. **Unchanged:** every pytest baseline behaviour, and
  Maven/Node/Go/Rust runners, which have the same gap and are not covered here. **Verified on:** real
  xunit 2.7 output on .NET 10 (captured as fixtures) and a real `dotnet` for the compile-error,
  unreachable-feed and nothing-to-build cases. **Not verified:** NUnit and MSTest — a test is
  identified by the name vstest's console logger prints, and those adapters may print the method name
  alone, in which case an old failure could hide a new one with the same short name. Measured on four
  synthetic outputs: one hid its cause from refine before (an `MSB` error with 138 warnings after it),
  none does now. A maintainer review of the first version found a false green (a crashed host), a
  false BLOCKED (message text that looks like a compiler error) and an incomplete strip; all three
  are fixed and pinned on the real output; a second review then found a regression in the first fix
  (a multi-project solution), now fixed, and a third round's real input (a custom MSBuild
  `<Error>` task) by the refuse-only rule above. 58 deliberate breakages of the change were all
  caught (two survived a first sweep until tests were added for them).

- **Codegen's tidy-up no longer rewrites compatible code into syntax the repository's Python cannot run
  (B69).** After writing, codegen runs `ruff check --fix` and `ruff format` on the files the run
  created (B64). Where the repository configures no ruff, ruff applied its own default rules and
  default target, and it reads a declared minimum only from `pyproject.toml`, and only for 3.7 and up
  — so a floor declared in `setup.cfg`, `setup.py` or classifiers (every Django instance in the
  SWE-bench pilot) was invisible to it. A model that had written `typing.Dict`, `Optional[str]` and a
  nested `with` for a Python 3.6 repository got `dict[...]`, `str | None` and a parenthesised
  multi-item `with` back. Measured on Django 3.0's `tests/` (1,720 files, valid on 3.6) through the
  real function: ruff's default introduced 36 parenthesised `with` statements; with the floor given
  to ruff it introduced none. Files in a repository that configures no ruff now get
  `--target-version` from the declared minimum Python (the same reader as the TARGET PYTHON
  paragraph), clamped to ruff's range of py37 to py315. **Unchanged:** which files are tidied (B64),
  ruff's rule set, repositories that configure ruff (their config governs and no flag is passed),
  and repositories that declare nothing. A pass that changes files now writes one log line per group (with and without the
  repository's ruff config) naming the files, relative to the repository, and the target, never their
  content, so a rewrite is visible in `autorun.log`.
  **Limits:** ruff's lowest target is py37, so a 3.6 floor is approximated (checked at syntax level
  on the Django corpus, not executed on 3.6); a repository at 3.9 still gets ruff's parenthesised
  `with`, which Python 3.9's parser accepts although it is documented as 3.10; ruff still
  reformats whole files it is allowed to touch, as B64 left it. Whether this was why the SWE-bench
  Django instances failed is not shown until they are re-run with their files kept.
  ([SSPN-152](https://fibonacci-solutions.atlassian.net/browse/SSPN-152))

- **Codegen is told the oldest Python the repository declares (B65).** It wrote tests and code for the
  interpreter it runs on and never asked which one the project targets: in a 20-instance SWE-bench
  pilot, 5 instances failed because the model wrote `str | None`, `list[str]` or a parenthesised
  multi-item `with` for repositories whose tests run on Python 3.6 or 3.8, the tests could not be
  imported, and refine spent its passes on syntax. For a **Python** target the conventions block that
  implement, author-tests, refine and revise already receive now carries one deterministic **TARGET
  PYTHON** paragraph naming the declared minimum and the syntax and standard-library features newer
  than it. The minimum is read, never executed, from `pyproject.toml` `requires-python`, then
  `setup.cfg` `python_requires`, then a *literal* `python_requires="…"` in `setup.py` (tokenized, so a
  comment or docstring is not a declaration, and classifiers are read only from a `classifiers = [...]`
  list), then the lowest `Programming Language :: Python :: X.Y`
  classifier (last, because classifiers go stale). A specifier built at run time
  (`'>={}.{}'.format(...)`, a concatenation, a name) is not a literal and falls through; an unreadable
  or malformed file is skipped. A repository that declares nothing, or declares `>=3.12`, gets exactly
  the block it got before. **Not changed:** the `episteme/conventions.md` page that `understand` writes
  (the paragraph is an instruction to a model, not an observation) and the conventions block of every
  other language's toolchain. **Limits:** it is guidance to the model, not a check — whether the model
  obeys is measured separately, and a deterministic check of the written files is the next step only if
  it does not. It targets the *declared* floor, so a repository whose test environment is older than its
  declaration is not covered, and a stale low classifier makes the paragraph stricter than needed. The
  feature table is keyed to each feature's documented release: `with (a as x, b as y):` is listed under
  3.10 although 3.9's parser accepts it. Poetry's `python = "^3.8"` dependency is not read. The table
  covers Python 3 features from 3.6 up: a declared 2.x or 3.5 is told about those, and nothing about
  `print`, `unicode` or `yield from` idioms. ([SSPN-146](https://fibonacci-solutions.atlassian.net/browse/SSPN-146))

- **Codegen no longer reformats files it did not create in a repository that has no ruff configuration
  (B64).** After writing, codegen ran `ruff check --fix` and `ruff format` over every file the model
  touched, existing ones included. With no ruff config ruff applies its own default rule set, so a
  one-line edit came back as a whole-file rewrite — measured on the first real SWE-bench attempt as a
  53,109-character patch over 8 files for a change of a few lines, which also made every benchmark
  patch about the formatter. A file is now tidied only if this run created it, or if the repository
  configures ruff (the nearest `ruff.toml`, `.ruff.toml` or a `pyproject.toml` with a `[tool.ruff]`
  table, up to the repository root; a bare `pyproject.toml` is not a config). A caller that cannot say
  what the run created gets no reformatting. **Behaviour change:** in a repository without ruff
  configuration, edits to existing files are no longer auto-reformatted; new files still are (and so is a
  blank file the model fills, such as the scaffold's empty `__init__.py`), and a repository that configures
  ruff (Spine's own does) is unchanged. **The cost, not measured:** a line the model adds to an existing file
  in a different style is no longer silently corrected, so where preflight runs `ruff format --check` it can
  now cost a refine round. The `write_files` tool of the agentic
  loop now remembers which files it created, so a later repair pass still tidies them. That record is
  the adapter's run-wide one, so refine's lint allowance for "test files this session created" (B47) now
  also covers files the agentic loop created, which it did not before.
  ([SSPN-145](https://fibonacci-solutions.atlassian.net/browse/SSPN-145), B64)

## 3.57.1 — 2026-10-08

### Fixed

- **OpenSpec drafts preserve stated requirements context.** Intent extraction now asks for
  and carries an explicitly stated problem, users, observable outcome, and non-goals into
  the generated proposal. It does not invent missing fields. The source title from a
  structured response is also retained. Refresh an existing cached draft with
  `orchestrator openspec draft --refresh` to re-extract its source.
  ([spec](docs/specs/intake-backlog-progress.md))
- **Feature coverage and acceptance review see the actual change.** The feature runner ignores
  `.benchmark-tmp` test scratch when identifying application code, while the acceptance judge
  receives a bounded tracked diff before source excerpts. This prevents temporary test files
  from counting as feature coverage and lets the judge inspect changed lines directly.

## 3.57.0 — 2026-10-08

### Added

- **`blast_radius` and `explain_symbol` say how many calls they could not trace.** The graph binds
  a call only through a receiver's declared type (on purpose: a wrong edge is worse than a missing
  one), so a call through an untyped receiver left no trace and the tools could not say so. For a
  Python function they now return `unresolved_calls` — the count of attribute calls with the same
  name whose receiver the graph could not type (as call **sites**, in function bodies — module-level
  and class-body code is not scanned), a bounded list of them (caller, `file:line`, the receiver's
  source text), and how many functions in that repository share the name — and the markdown gains a
  "Possible untraced callers" line. It is a **hint, never a caller**: it is not part of
  `caller_count` or of any edge, because the same name is also a `str.find` somewhere else. On this
  repository `FactStore.find` has 35 untraced calls and 2 are the store; the receiver text beside
  each is what tells them apart. The list is `null` with a note — never `0` — for a language that
  does not record refusals (every front-end except Python's) or when it cannot be loaded; `0` means
  "tracked, and none were recorded in function bodies". Limits: only calls to a function the call's own repository
  declares; with more than 25 sites the first 25 by file path are listed and the count stays exact.
  The records live beside the graph cache in a `.unbound.json` sidecar (about 2% of its size); the
  graph itself, `pkg verify`, `understand` and the accuracy gate are unchanged. The first run after
  upgrading re-extracts each cached repository, as any change under `pkg/` does.
  ([SSPN-133](https://fibonacci-solutions.atlassian.net/browse/SSPN-133), B62)

### Fixed

- **`blast_radius`, `explain_symbol` and `docs_for` no longer hide matches without saying so.**
  A name shared by many symbols was cut to its first five with no total, so the one you asked about
  could be missing and the answer still read as complete — on this repository `summary` has 28
  matches and `FactStore.summary` was never shown. Each tool now details the first **7** (it was 5)
  and, when it resolves a symbol, reports `match_count` and `truncated` (a "Showing 7 of N matches"
  line in the markdown); a qualified name (`FactStore.summary`) or a full node id selects one
  symbol. A plain name resolves exactly as before. A name with a `.` or `:` now also matches ids
  that *end* with it — on this repository two dotted names gain results and none lose any — and a
  repository with no docs still answers `docs_for` before resolving the symbol, so that answer
  carries neither key. ([SSPN-112](https://fibonacci-solutions.atlassian.net/browse/SSPN-112), B52)
- **`blast_radius`'s description said what it does not do.** It claimed a "cross-layer set a change
  ripples into (CALLS + IMPORTS + REFERENCES)"; it reports `touches` — every node one edge away, in
  either direction, of any kind — and the transitive walk (4 hops) runs only with `repos=`. The
  tool description, `AGENT_GUIDE.md` and the `understand-codebase` skill now say so.
  ([SSPN-84](https://fibonacci-solutions.atlassian.net/browse/SSPN-84), B40)
- **`blast_radius`'s description now says it lists only the callers the graph can type.** The graph
  binds a call only through a receiver's declared type (on purpose: a wrong edge is worse than a
  missing one), so a call whose receiver the source does not declare — in Python, an `Any` or
  unannotated parameter, a name bound twice, a closure, a chain — is not a caller, and the tool did
  not say so. The description (and `explain_symbol`'s, the guide and the skill) now say
  `caller_count` is a floor, and the markdown `Called by (N, at least …)` line says so too (B62).
  On this repository 11 functions took `store: Any` and hid 23 of the 137 call sites on a
  store-named receiver we could recognise; they are typed now (a `TYPE_CHECKING` import, no runtime
  change), so 100 → 123 of those sites are in the graph and `FactStore.touches` went from 0 callers
  to 2. The other 14 (11 ambiguous bindings, 3 closures) are not reshaped. That is Spine's own code:
  another repository's untyped receivers are unchanged. Reporting how many calls could not be bound
  is tracked separately ([SSPN-133](https://fibonacci-solutions.atlassian.net/browse/SSPN-133), B62;
  closures: [SSPN-132](https://fibonacci-solutions.atlassian.net/browse/SSPN-132), B61).
  ([SSPN-131](https://fibonacci-solutions.atlassian.net/browse/SSPN-131), B60)

## 3.56.0 — 2026-10-06

Spine now checks requirements, not only builds from them. Whoever drafts a requirement — a
person, Claude in a chat, a Confluence page — Spine runs a deterministic clarity gate over it,
says which of its criteria already name code that exists, and records the answers people give
with the channel it actually observed. **No new model call anywhere in this.**

### Added

- **`orchestrator openspec check <change> [PATH|--repos]`.** The strict clarity gate: a stated
  problem, named users, an observable outcome, and every open question answered or deferred to
  a named owner. With a repository it also lists the criteria that already name code that
  exists (evidence, not a verdict) and those naming code the graph cannot find; with none it
  says `ungrounded` rather than reporting nothing. Exit 1 when the gate fails. Works on any
  OpenSpec change, however it was drafted. ([spec](docs/specs/mcp-plugin-surface.md))
- **`orchestrator openspec answer <change>`** (`--question`/`--answer`, `--defer-to`, or
  `--answers FILE`). Writes the answer under its question in `proposal.md` as a nested bullet
  with the channel Spine observed (`cli` / `answers-file` → `user`). The edit is surgical — a
  change a person wrote keeps its prose — and all-or-nothing across a batch.
- **`orchestrator openspec draft --idea "<one sentence>"`.** A skeleton change, no model call:
  the why-fields (problem, users, outcome, non-goals) are its open questions, in fixed wording;
  answering one fills the field it names.
- **MCP tools `requirements_check` and `requirements_answer`.** Over MCP every answer is
  recorded as `relayed`, never `user`: Spine cannot see who typed it, so it never claims a person.
- **Why-fields on `Intent` and `FeatureSpec`** (`problem`, `users`, `outcome`, `non_goals`,
  `idea_id`, `resolutions`), and an OpenSpec layout for them (`### Problem` / `### Users` /
  `### Outcome` under `## Why`, `### Non-goals` under `## What Changes`, `idea:` under the title,
  answers nested under their question). Every new field is **absent when empty**: an input with
  no why produces a byte-identical cache and build-document digest, so no existing approval moves.
- **Gap-rule check kinds `pattern` and `question_state`**, and a strict rule set —
  `examples/gap_rules/requirements_gaps.yaml`.

### Changed

- **A cache written with why-fields cannot be read by an older Spine.** `Intent` and
  `FeatureSpec` are strict, so an older version reading an entry that carries `problem`,
  `users`, `outcome`, `non_goals`, `idea_id` or `resolutions` treats it as a miss and
  re-extracts — which makes an approval granted on the old spec stale. Only caches that *have*
  a why-field carry the new keys; everything else reads and writes exactly as before. Same
  shape as the 3.45 `variants` note.
- **A legacy `proposal.md` that already has `### Problem`, `### Users`, `### Outcome` or
  `### Non-goals` is now read as the why-fields**, not as part of the description or scope
  (a bare `## Why` or `## What Changes`, and any other subsection, reads exactly as before).
- **`ingest` now shows up to four extra *warning* findings** for an intent that states no why
  (`problem_stated`, `users_named`, `outcome_stated`, `non_goal_named`; plus
  `outcome_observable` and `criteria_testable` when there is something to judge). They never
  gate: what `ingest` blocks on is unchanged. `examples/gap_rules/intent_gaps.yaml` mirrors the
  new default set.
- **The spec writer no longer resolves open questions silently.** An answered question is
  passed as a stated fact; an unanswered one is listed in `technical_notes` as a labelled
  *Unanswered assumption*, replacing "resolve in technical_notes". Only a **re-extracted** spec
  differs (`--refresh`); cached specs and their approvals do not move.

### Fixed

- **A malformed structured response no longer ends codegen outright.** A client that parses a
  forced tool call before returning (the Codex app adapter, or a native client raising
  `StructuredOutputError`) raised before the parse retry could see any text, so one bad response
  stopped a run during test authoring. It now gets the same bounded corrective retry as text that
  fails to parse, then a `CodegenError`; transport, timeout and budget errors still raise at once.
  (SSPN-127, B57)

## 3.55.0 — 2026-10-06

Extensive Rust support broadens the Core Rust release with measured async, procedural
macro, `no_std`, and Cargo feature boundaries on supported hosts.

### Added

- **Extensive Rust validation.** Three hand-labelled corpus cases cover Tokio-style
  async calls, procedural-macro source boundaries, and `no_std` with `core`/`alloc`
  imports and conflicting `cfg` branches. All 14 Rust cases retain 1.00 precision
  on every emitted fact kind; the three known cross-module/workspace `CALLS` misses
  remain labelled. Pinned real-repository runs cover mini-redis, async-trait,
  embedded-hal, and Synaptreesitter, including one non-default Cargo feature and
  a clean-checkout async codegen patch. The extractor now normalizes external
  `core`/`std` trait aliases, and the verifier follows Rust semantic module
  containment. Before governed codegen, the runner detects a missing cross
  compilation target, linker, or custom JSON target and reports an actionable
  refusal. Macro expansion, type-aware dispatch, full feature matrices, and
  embedded firmware execution remain outside this release.
  ([PR #516](https://github.com/synaptixs/spine/pull/516),
  [validation](docs/evals/rust-extensive-validation.md))

## 3.54.0 — 2026-10-06

Required-behavior gates close the ONTM-4 gap where a generated test could pass without
exercising a feature's real default wiring, and Core Rust support (comprehension +
Cargo-aware codegen/build/test) is signed off end to end.

### Added

- **Required-behavior gates.** A project opts in by committing `.spine/required-behavior.yaml`:
  checks that must pass before `orchestrator sdlc feature` reports a run verified, distinct
  from the existing lint/type-quality preflight and from the model's own generated tests.
  ONTM-4 showed generated tests passing while the feature under test never exercised its own
  default wiring; `RequiredBehaviorRunner` (`sdlc/required_behavior.py`) closes that gap with
  checks the model does not author and cannot edit away, re-verified after every refinement as
  a fourth independently-budgeted check in `run_feature`'s loop. `PKGCodegenGrounder` grounds
  the model in a correctly-wired sibling implementation (via the existing `IMPLEMENTS` graph —
  no new PKG fact type) when a requirement names a resolvable entry point. Worklogs gain a
  `Deterministic (s)` column (the check is a subprocess run, not a model call — zero tokens
  does not mean it did not run) and an `Outcome` line distinguishing an initial candidate from
  a first completed autonomous workflow that needed internal repairs.
  ([SSPN-116](https://fibonacci-solutions.atlassian.net/browse/SSPN-116))

- **Rust language support (Core).** A new `[rust]` comprehension and Cargo-aware codegen
  front-end, closed end-to-end across three follow-on PRs that resolved every gate opened
  along the way. Comprehension indexes Cargo package/target topology and builds a semantic
  module tree, giving `.rs` declarations target-scoped IDs and recording exact imports,
  explicit trait implementations, fields, references, and precision-safe local calls; the
  Rust corpus holds 1.00 precision on every emitted node/edge kind, with three cross-module/
  workspace `CALLS` gaps declared rather than silently dropped. Codegen adds
  `TOOLCHAINS["rust"]`: a Cargo-aware scaffold, build-before-test with changed-package and
  reverse-dependent selection, workspace escalation for root-manifest/lockfile/toolchain
  edits, and Rust-specific implement/test/refine prompts — validated with live
  `claude-opus-5` runs against a pinned real-world crate, including deliberate compile/test-red
  cases and clean-checkout replays. The installed `[rust]` parser's four-line upstream grammar
  error, open at both merges, is now closed by pinning the published
  `tree-sitter-rust-orchard` 0.16.8 grammar, which parses the pinned validation repository's
  109/109 files cleanly. Core Rust support (comprehension + Cargo-aware codegen/build/test) is
  signed off for supported host environments; the `[rust]` extra needs a C compiler since
  Orchard is source-only. Macro-generated declarations, `include!` expansion, receiver type
  inference, and ambiguous `cfg` variants remain out of scope, as do multi-target/embedded
  execution and rust-analyzer enrichment.

## 3.53.0 — 2026-10-04

Python's call graph now reaches through a typed variable, and a locked `pyjwt` closes ten
published advisories.

### Fixed

- **The locked `pyjwt` moves from 2.13.0 to 2.15.1.** Ten advisories published against 2.13.0
  (fixed in 2.14.0) made the dependency audit fail every pull request. Only the `pyjwt` entry
  in `uv.lock` changes; `pyproject.toml` already allows it (`>=2.9`).
  ([SSPN-115](https://fibonacci-solutions.atlassian.net/browse/SSPN-115))
- **Python methods called through a typed variable now have callers in the graph.** The Python
  front-end dropped every call through a parameter, a local or a stored attribute, so
  `blast_radius` answered "0 callers" for most methods — `FactStore.impact_of`, `edges_of_kind`,
  `docs_for` and `summary` and `LLMCodegenAdapter.refine`/`author_tests` among them. A call now
  lands on the method its declared type means: a parameter annotation (`Optional[X]` and
  `X | None` included), a local assigned once from a constructor, a `self.attr` typed by a class
  annotation, a constructor or a typed parameter, and an inherited `self.m()`. It refuses rather
  than guesses — a name bound to two types, a union of two classes, a type this repository does
  not declare, a class with two bases or an external base — and a new corpus case with decoys
  holds precision at 1.00. On this repository: +2,077 `CALLS` edges (779 from non-test code); a
  20-edge hand sample was 20/20 correct. `Class.inherited()` on a class name now lands on the
  ancestor that declares it; the 25 edges that pointed at an invented `Class.member` (22 of them
  pydantic's `model_validate`) are dropped instead. **Build documents render new caller counts, so
  a plan approved before the upgrade reads as stale once** — re-plan and re-approve.
  ([SSPN-48](https://fibonacci-solutions.atlassian.net/browse/SSPN-48))
- **The Python accuracy corpus now pins the receiver refusals the review added, and two wrong
  edges are gone.** `corpus/python/instance_calls` had filed two true calls
  (`via_reassigned`, `via_mixed_field` → `Rocket.get`) as refusals, so the published Python
  `CALLS` recall of 0.889 was really 0.857 on the same cases; both are labelled as gaps now.
  Most refusal rules added in review were pinned only by unit tests — 16 of 24 mutants of them
  passed `pkg accuracy --check`; a decoy module and the `super()`/`cls` gaps take that to 2 of 24
  (both unreachable from a corpus shape). Recall on the enlarged set reads 0.85 (63/74). Two
  shapes landed on an inherited method they should not have: `self = other; self.m()` and a
  `@staticmethod` whose first parameter is `self` — `self` is now the instance only as the first
  parameter of a method that is not a static or class method and is never rebound.
  ([SSPN-113](https://fibonacci-solutions.atlassian.net/browse/SSPN-113))
- **Concurrent features on one SDLC worker no longer switch each other's codegen guards off.**
  The worker shares one codegen adapter across features running in parallel, and refine stored
  its edit allowlist on it — as single-shot implement did its edit scope — before awaiting the
  model, clearing it afterwards. A feature that finished first cleared the other's value: its
  refine then applied with the pre-existing-file guard (NSS-1243) and the weakened-test guard
  off, while an implement running alongside a refine got refine-only guards. Both values now
  ride down as arguments on every attempt, retries included; a test runs each interleaving, and
  another fails if any adapter method stores state on the adapter again. `sdlc feature` and
  `autorun` (one adapter per run) were never affected.
  ([SSPN-95](https://fibonacci-solutions.atlassian.net/browse/SSPN-95))
- **The codegen benchmark's `NEW-DRIFTMD-1` ticket can be passed.** Its held-out test built a
  stand-in for the doc-drift finding (a `str` kind, no `message`) where the ticket requires the
  real `DocDriftFinding`, so correct code failed on `kind.value` — every run of every tool on
  every model failed the ticket. The test now builds real findings and checks the rendered text,
  so a Markdown-escaped `missing\_symbol` counts. Re-graded on the kept runs: 7/7 for Spine + PKG,
  and 6/7 for spec-kit, whose one failure never wrote the function. A new guard runs the suite
  against a reference solution, and against a wrong one that must fail.
  ([SSPN-97](https://fibonacci-solutions.atlassian.net/browse/SSPN-97))

## 3.52.0 — 2026-09-27

Docs join the blast radius — the repository's own, Confluence and Jira pages, and any RAG system
over MCP — and codegen stays inside the ticket it was given.

### Added

- **`blast_radius` and `explain_symbol` list the docs that describe a symbol.** Each match now
  carries `docs` — the repository's own doc sections naming the symbol, its class or its module
  (`via`), each with `file:line` — plus `doc_count` and `related_doc_count` (pages naming its
  callers or the code it touches, counted rather than listed). A public class with no callers in
  its own repository read as safe to change; on a real SDK repository, one such class is described
  by 12 doc sections. Docs never enter `touches`, so code-impact counts are unchanged. With
  `repos=`, a document binds only to symbols in the repository it lives in, and only the matched
  repository's docs are read. If linking docs fails, the code answer still returns, with
  `docs_unavailable` saying why. ([SSPN-79](https://fibonacci-solutions.atlassian.net/browse/SSPN-79))
- **`${VAR}` in `mcp.json` `headers` values and `url` is filled from the environment.** A
  remote MCP server's bearer token no longer has to sit in plaintext in the file:
  `"Authorization": "Bearer ${INTERNAL_MCP_TOKEN}"` reads the token at load time (the example
  OPERATIONS.md already showed, which until now was sent literally). An unset variable is an
  error naming the variable and the server, never an empty header; `$${VAR}` is a literal
  `${VAR}`, and `$VAR` without braces is left alone. `command`, `args`, `env` and `allow` are
  not expanded. The expanded value never leaves: writes keep the placeholder, the Connections
  page shows it, and error text from a failing server has the value swapped back to `${VAR}`.
  Adding a server from the Connections page now tests the expanded url rather than the text as
  typed. ([SSPN-81](https://fibonacci-solutions.atlassian.net/browse/SSPN-81))

- **`orchestrator mcp ingest-docs` — Confluence and Jira docs join the blast radius.** Declare a
  repository's external doc sources under a new `docs:` block in `.spine/repos.yaml`
  (`confluence: {roots, max_depth, max_docs}` or `jira: {jql, max_issues}`, through an onboarded
  MCP server) and the command pulls them into a cache outside the checkout. `blast_radius`,
  `explain_symbol` and `docs_for` then bind the cached pages at read time with the same binder as
  the repository's own docs — a rename drops a stale mention without a re-pull — listing them with
  `origin: "mcp:<server>"`, `source`, `title` and `url`; `docs_for` adds `external` per match and
  external coverage and drift apart from the repository's numbers. Every answer that read them
  carries `external_docs` — each source's status, age and `stale` flag (over 7 days); a failed
  pull keeps the last good data and says from when. A section identical to a repository doc is
  listed once, under it, with `also_in`. The pull calls only allow-listed tools the server declares
  read-only and refuses any other by name; Jira reads never touch view history. `understand` and
  `state` never read pulled docs. ([SSPN-80](https://fibonacci-solutions.atlassian.net/browse/SSPN-80))
- **Any RAG system's MCP server can be a docs source — a `rag:` kind under `docs:`.** Onboard
  the server (chroma-mcp, mcp-server-qdrant, Bedrock Knowledge Bases, Ragie, a `search`/`fetch`
  pair) and declare `rag: {collection, top_k, max_queries, max_chunks, trust_read_only}` — every
  key optional; `tool` / `query_arg` pin what detection picks. `mcp ingest-docs` reads the tools'
  input schemas: with a list tool it walks the corpus (`N of M`), otherwise it asks one query per
  module and class, most-called first (`queried N of M symbols (cap reached)`). Two tools fitting
  one role are refused naming both, nothing called. Chunks are listed in `blast_radius`,
  `explain_symbol` and `docs_for` only where the binder finds one symbol they name; per symbol
  asked about, `external_retrieved_count` / `external_unverified_count` and "10 retrieved, 2 name
  the symbol" count the rest. A chunk from one of the repo's own doc files collapses into it as
  `also_in`. `external_docs` standings add `strategy` and `pull_bound`; drift is reported only for
  a walked corpus. ([SSPN-82](https://fibonacci-solutions.atlassian.net/browse/SSPN-82))
- **`trust_read_only` — vouching for an unannotated RAG tool.** The docs pull still calls only
  allow-listed tools that declare `readOnlyHint: true`; a `rag` source may also name tools in
  `trust_read_only`, because neither chroma-mcp nor mcp-server-qdrant annotates any tool. The
  allow list is never widened, and Confluence/Jira sources keep the strict rule.
  ([SSPN-82](https://fibonacci-solutions.atlassian.net/browse/SSPN-82))
- **`MCPToolResult` keeps each text block (`blocks`) and the structured content (`structured`).**
  `text` is unchanged; a server that answers one block per chunk no longer loses its separators.

### Fixed

- **Refine can now fix lint and type errors in the tests it wrote, and may not drop their asserts.**
  A generated test file that failed preflight stayed failed: refine is told not to modify tests,
  and ruff has no autofix for the common cases. On develop, preflight passed on 25 of 30 benchmark
  runs, and every failure was in a test file the run had written. Two changes. A module docstring
  written after `from __future__ import annotations` (ruff E402 on every import after it) is now
  moved to the top before ruff runs; the move is checked on the AST and changes no code. And when
  a ruff, mypy or `ruff format --check` line names a Python test file this run created, refine is
  told it may fix those findings in that file. Nothing is unlocked while the output shows a failing
  pytest run, and a test that existed before the run is never unlocked, even one the run edited.
  A refine edit to a test this run wrote that removes or changes an existing `assert` statement,
  or removes a test function, is refused and reported (in the retry, when the pass is retried);
  new asserts are allowed (the usual mypy `union-attr` fix adds `assert x is not None`). Other ways
  of weakening a test — a skip marker, a changed expected value — are not caught yet
  ([SSPN-94](https://fibonacci-solutions.atlassian.net/browse/SSPN-94)). Measured on the same 30
  runs: preflight 27/30, with no failure left in a test file (the three remaining are an
  implementation file's mypy error and two failing test runs). Python only; other languages and
  the agentic loop keep today's rule. ([SSPN-93](https://fibonacci-solutions.atlassian.net/browse/SSPN-93))
- **Codegen no longer edits files the ticket is not about.** On OpenAI models a run edited 6-9
  unrelated files, each named by Spine's own design. A module a ticket names by its dotted name
  (`orchestrator.codereview.verifiers`, "the orchestrator.pkg docs module") now resolves through the
  graph (Python modules only) and the design lists it ahead of word matches. The design heading is
  now **Files to edit**, and codegen takes files to change only from it: the blast radius and the
  new **Files to read (reference — do not modify)** list are shown as reference, never as files to
  change. When a design says the ticket creates code, its named modules and word matches go to the
  read list; today only the codegen benchmark says so — a spec from intake carries no create/edit
  kind, so there a named module stays a file to edit. When the ticket stated its file paths or
  creates code, an edit to any other existing file that changes no code (comments and docstrings
  only) is discarded and the model is told so up front; a real code change elsewhere is applied and
  reported in the change summary. The agentic loop, `author_tests` and refine are not guarded.
  `files_to_touch` keeps its name and now holds edit targets only; `files_to_read` is new.
  ([SSPN-89](https://fibonacci-solutions.atlassian.net/browse/SSPN-89))
- **A `#` comment inside a code block no longer splits a doc into a phantom section.**
  Section splitting in markdown and HTML now skips fenced code (```` ``` ```` / `~~~`, CommonMark
  rules: a line like ```` ```x``` ```` is inline code, not a fence; Word and Excel text has no
  fences), and an HTML `<pre>` block is
  read as a fenced sample with its lines kept — flattened to one line, a `#`-led sample became a
  single heading with a 476-character id. Measured before the fix: 68 of this repository's 2,257
  markdown headings, and 14% of one field repository's, were code comments. The code stays in its
  real section, so its mentions still bind. ([SSPN-83](https://fibonacci-solutions.atlassian.net/browse/SSPN-83))
- **`requirements*.txt` and `constraints*.txt` are no longer ingested as docs**, and doc section
  ids are capped at 100 characters, numeric suffix included (ordinary headings keep their full
  GitHub anchor).
- The `docs_for` "no docs" note lists every format the reader accepts, not four of them.

### Changed — upgrade notes

- **A plan whose ticket names a module can read as changed after upgrading.** The design now
  resolves a module a ticket names (by dotted name) through the graph and lists it first, so that
  plan's design files, provenance and confidence band render differently — measured: the same spec
  on the same commit renders a different plan under 3.51.1 and 3.52.0. `require_approved_plan`
  then refuses the old approval once ("the plan … has changed since … approved it"): re-run
  `orchestrator sdlc plan --spec <file>`, read it, and `orchestrator sdlc approve <INTENT>`.
- **A literal `${NAME}` in an `mcp.json` `headers` value or `url` is now expanded.** If `NAME` is
  unset, loading that server fails with an error naming it. Set the variable, or write `$${NAME}`
  to keep the text literal.

## 3.51.1 — 2026-09-26

A patch release: one sandbox fix.

### Fixed

- **`run_python_analysis` reports its time limit reliably on the local sandbox.** The sandbox
  enforces the limit twice, as wall-clock time and as `RLIMIT_CPU`, and a busy loop reaches
  both at once. When the CPU limit won, about one run in six, the tool either raised
  `ProcessLookupError` or returned exit `-24` with an empty stderr. It now returns exit `124`
  and `timeout after Ns` either way, adding `of CPU time` when the CPU limit stopped it. A
  `SIGKILL` before the limit (the OOM killer) is still reported as itself.

## 3.51.0 — 2026-09-26

`--layout auto` never scaffolds a second project beside code it cannot place — in any language.

### Fixed

- **`--layout auto` stops in every language but SQL, not only Python.** 3.50.0 stopped a Python
  run from scaffolding a new package beside `.py` files it could not place. C#, TypeScript,
  Java, Kotlin, Go, C and C++ still scaffolded a `.sln`, `package.json`, `pom.xml`,
  `build.gradle.kts`, `go.mod` or `CMakeLists.txt` beside loose source that build would never
  compile. `sdlc feature` now stops with exit 2 and names the build file it looked for (no
  `.csproj`, no `go.mod`, …). A language's whole suffix family counts (C# `.cs .razor .cshtml`,
  TypeScript `.ts .tsx`, Kotlin `.kt` but not a `.kts` build script, C `.c .h`, C++ sources and
  headers). One file is enough; a sample under `docs/`, `doc/`, `examples/` or `samples/` is
  not. SQL still scaffolds: its scaffold is a `migrations/README.md` and a `.gitignore`.
- **A TypeScript, Go, C or C++ project below the root is followed, not refused.** With no
  root build file, these look further down (`frontend/package.json`, `svc/go.mod`,
  `native/CMakeLists.txt`, skipping vendored trees and nested checkouts) and take the project
  holding the files the design names, else the one with the most source, as C#, Java and Kotlin
  already did. Dependencies install and tests run in that directory: `npm install`/`npm test`,
  `cmake`/`ctest` or `meson`, and the Go baseline run no longer start at a root with no build
  file.
- **`sdlc plan` says the Build will stop before anyone approves it.** A `layout_would_stop`
  Validity finding, from the same resolver and count the Build uses, plus a penalty-only
  "Build layout" row in §12. It is reported, never a refusal: the plan does not know which
  `--layout` the Build will get.

### Changed — upgrade notes

- **A Build that used to scaffold beside existing code now exits 2.** This happens only when
  all three hold: `--layout auto` (the default), a language other than SQL, and source in that
  language with no build file the resolver recognises. Pass `--layout existing` (with
  `--package-name`) to build into the code that is there, or `--layout new` to scaffold anyway.
  `--layout new` is the only override; there is no environment variable.
- **Plans for such repositories render a new finding and §12 row**, so an approval made before
  the upgrade reads as stale for them; approve the plan again.

## 3.50.0 — 2026-09-26

A fixes release driven by field reports from a .NET/Blazor and a Python/AWS SAM pilot: every
Build failed before codegen, C# builds wrote namespaces that could not parse, Python builds were
failed by tests that predated the change, and plans rated an invented spec "high".

### Fixed

- **A damaged workspace base is rebuilt, not reused — and bases live out of `/tmp`.** Every
  `sdlc feature` run failed with `git worktree add … fatal: not a git repository`: the shared base
  under `/tmp/sdlc-workspaces` had lost `.git/HEAD` to macOS's age-based `/tmp` sweep, and `.git`
  existing was the whole check. A base is now reused only when git can read its `HEAD` commit
  (via `--git-dir`, so a repository enclosing the root is never mistaken for it); there is one
  base per repository and branch under `<root>/_bases/`, so switching repositories no longer
  re-clones and orphans the other's worktrees; an advisory lock serialises separate processes; a
  failed refresh now prints git's reason. See OPERATIONS.md, "Build workspaces".
- **C# code declares the project's real namespace.** For an existing .NET project the guidance
  told the model the `.csproj` file stem (`acme-order-portal`) *was* the namespace. It is
  now read from `<RootNamespace>`, else what the project's files declare, else `<AssemblyName>`,
  else the stem made legal; namespaces follow folders, `@namespace` is never added to a `.razor`
  file, and the `[layout]` line prints the namespace with its evidence.
- **An illegal `namespace`/`using` line is refused before it is written** — `.cs`, `.razor`, and
  any non-directive line in `_Imports.razor` — and costs one corrective retry, not a `dotnet test`.
- **Refine sees the compiler's first error.** `dotnet test` output was cut to its last 4,000
  characters, which on a project with many warnings kept the cascade and dropped the cause. Errors
  are now lifted from the whole output, de-duplicated and put first; refine is shown the
  pre-existing files a failure names, and may change a pre-existing file only when the failure,
  the spec or the design names it (dependency manifests and project files always).
- **A Python run is judged by the failures it caused.** Before codegen the suite runs once on
  the untouched worktree; a later run whose every failure was already failing passes (reported,
  not counted), and refine is told which failures are not its to fix. pytest now runs with
  `--continue-on-collection-errors`, so one unimportable test file no longer stops the suite.
  Without a baseline, a failure made only of old test files missing a dependency stops the run
  with that diagnosis instead of refining.
- **Python layout follows an existing repository.** An AWS SAM repository is recognised from
  `template.yaml` (the `CodeUri` function the design names, else the one with the most Python),
  each function's `requirements.txt` and the runtime-provided `boto3` are installed for its tests,
  and a repository of top-level modules is followed rather than given a new `src/<pkg>/`.
- **The plan reports an invented spec.** The spec writer is told the repository's languages and
  the symbols the ticket's words match, and to flag an ambiguous term instead of choosing a
  meaning. The validity line reports — without refusing — a named file that is absent and in
  another language than the run's, and a ticket whose criteria were all proposed; §7 lists every
  named-but-absent file.
- **§12 no longer rates a keyword guess "high".** "Named paths" is n/a when the files came from
  the graph; the band is capped at medium when the files are the plan's own keyword retrieval and
  set to low when no files are proposed. The heuristic test strategy no longer ends on a colon.
- **An edited `file://` ticket is re-extracted.** A cache hit never re-read the source; a local
  file is now fingerprinted and a changed one re-extracts, and says so.
- **A build from a local checkout names the uncommitted work it leaves out** (`[workspace]
  WARNING: N uncommitted file(s) … are not in this build`).

### Changed — upgrade notes

- **Workspaces default to `~/.cache/orchestrator/sdlc-workspaces`** (was `/tmp/sdlc-workspaces`).
  `SDLC_WORKSPACE_ROOT` still wins and now expands `~`. The old `/tmp` base is ignored — delete it.
- **`--layout auto` on Python stops (exit 2)** rather than scaffold a new package beside `.py`
  files it cannot place; pass `--layout new` to scaffold anyway. Other languages are unchanged.
- **A Python build runs the suite once more, before codegen.** `SDLC_TEST_BASELINE=0` skips it.
- **Plans render differently (§7, §12, Validity), so approvals made before the upgrade read as
  stale once.** Re-plan and re-approve.
- **Specs cached before the upgrade keep their old extraction** and say so; run
  `sdlc plan --refresh` once to extract with the repository context.

## 3.49.0 — 2026-09-25

### Added

- **`sdlc plan --refresh` re-extracts the spec — the only way to refresh a `--follow-links` one.**
  A ticket planned with `--follow-links` is cached as its own entry, and nothing could re-extract
  it: `ingest --refresh` rewrote the flag-off entry and left the linked-pages spec as it was, so a
  page linked after extraction never reached the spec (the header named it, and nothing acted on
  it). `sdlc plan --refresh --follow-links` re-extracts that entry; `sdlc plan --refresh` alone the
  ticket-only one — never both. It re-extracts the intake spec, not the PKG, and is refused with
  `--spec` (exit 2). `ingest`, `openspec draft` and `sdlc feature` keep their flag-off `--refresh`;
  `sdlc autorun` gets none, because it re-checks the approval right after intake and a refresh
  there could only park it.
  - A refresh that changes an approved spec prints a `WARNING:` naming who approved it — before
    anything else can exit, since the cache is already rewritten. For the intent it renders, it
    then says whether the approval still holds, by the gate's own comparison: when the re-rendered
    plan differs from the approved one, `sdlc autorun` parks (exit 6) until `sdlc approve` again;
    a change only in fields the plan does not render (`nfrs`, `estimate`, …) leaves it holding,
    and it says so. Any other approved intent whose spec changed is named with the command that
    re-plans it before it is re-approved — its plan on disk was rendered from the old spec, so
    approving that would approve an unread document. One line adds that the intake cache is
    shared by every checkout, and that an approval made from the plan with the other flag is not
    moved. It compares the spec before and after, so a refresh that returns the same spec, or a
    change in the code, says nothing. An approved intent the re-extraction renamed or dropped is
    named with the ids now available (and, when a flag-off refresh prunes it, the progress and PR
    recorded for it); a pinned `--intent` that vanished keeps exit 3 and says why. Exit codes are
    unchanged.
  - The cache-hit hint `(--refresh to re-extract)` — printed by `sdlc autorun` too, which has no
    `--refresh` — now names `sdlc plan --source <uri> --refresh` there (with `--follow-links` for
    that entry); `ingest`, `openspec draft` and `sdlc feature` keep their own `--refresh`. The
    `**Linked pages:**` clause for a page linked since extraction ends with
    `sdlc plan --refresh --follow-links`.

### Fixed

- **`sdlc complete` renders the ledger that holds the merged intent.** When the PR came from a
  `--follow-links` plan whose intent the ticket-only plan names differently, `BACKLOG.md` was
  re-rendered from the ticket-only plan — `0 / 1 done`, without the intent whose PR had just
  merged. It now renders the plan that holds it (the ticket-only one when both do, as before).
- **Upgrade note: Spine 3.44 and older delete the `--follow-links` cache entry.** Entries planned
  with `--follow-links` (3.45+) sit beside the ticket-only one in the same intake-cache file. A
  3.44-or-older run on that file drops them: planning a ticket only ever analysed with the flag
  treats it as a miss (no `--refresh` needed) and rewrites the file without them — and without the
  PR recorded for their intent — and so does a `--refresh` of a ticket that has both; its `sdlc
  complete` renders an empty ledger. `sdlc plan --refresh --follow-links` re-extracts what was
  lost, at the price of a new spec. Documented rather than guarded: a cache version bump would
  make 3.44 re-extract every ticket, not fewer (see `intake/cache.py`). The byte-pinned intake
  goldens now also cover the attachments 3.44 never downloaded, rendered by v3.44.0 itself.
- **Past 20 attachments, the spec is derived from the same text 3.44.0 gave it.** Since 3.45.0
  the extractor's bounded view of a ticket's attachments is derived from the full read, which stops
  at 20 files — so on a ticket with more than 20 readable attachments a later file the bounded view
  still had room for was never downloaded, and was named `(bound of 20 reached)` instead of read or
  named as 3.44.0 did. It took a budget cut that happened to end on whitespace, or a small file
  after many long ones; measured on seeded random tickets, about 1 in 100 with 21–30 attachments,
  none with 20 or fewer. The full read now keeps fetching past 20 files while the bounded view can
  still take one, and stops once it cannot, so the extractor reads 3.44.0's bytes again — proved
  by a new golden rendered by 3.44.0 itself — over REST and MCP alike. The full view §8 checks
  criteria against carries every file downloaded, so it can hold more than 20 — and when the
  bounded view ends a few dozen characters short of its budget (too few to carry a cut file), it
  never closes, so every readable attachment on the ticket is downloaded and lands in the full
  view (and `source.txt`). 3.44.0 downloaded those files too, and one cannot be dropped from the
  full view without changing the reason the extractor's view names it with; capping it is
  SSPN-77. A spec already cached from such a ticket keeps its text (the cache is keyed by the
  ticket, not its body): nothing approved re-parks, and only a fresh extraction reads the
  corrected view.

- **`--follow-links` says what the spec was derived from, not only what it fetched.** The intent
  extractor reads at most 60,000 characters, fills them with the ticket first, and linked pages
  come last — so `sdlc plan`'s header could say "5 read" while the spec was derived from one of
  those pages, cut, and nothing said so. The `**Linked pages:**` line now counts the pages that
  reached the spec in full, names the ones cut or left out, and names any page linked since the
  cached spec was extracted (`followed — 5 read; 1 cut (…), 4 did not fit the 60,000-char budget
  (…)`); with a hand-written `--spec` it says the pages reached `source.txt` only. The counts come
  from the documents the intake cache already holds, through the same function that builds the
  extractor's prompt, so they cannot disagree with what the model was given. A page the spec was
  derived from that no longer reads is named as unreadable, and one no longer linked as such —
  never dropped from the count.
  - A structured source (`openspec://`) is parsed verbatim, with no model and no budget, so it is
    never reported as cut.
  - A ticket whose own text the budget cut gets an `**Extraction:**` header line, with or without
    `--follow-links`; `sdlc plan` and `sdlc autorun` print a `WARNING:` whenever anything did
    not fit. Exit codes are unchanged.
  - `sdlc autorun --follow-links` prints `[intake] linked pages: …` — which linked pages the
    cached spec was extracted from, whole, cut or left out — and `investigate --follow-links` puts
    `**Linked pages:** …` in its brief. Both said nothing before.
  - Three kinds of link used to vanish without a word: a `/pages/edit-v2/<id>` URL now resolves
    to its page; a draft link (`resumedraft.action?draftId=…`) and a Confluence page on another
    site pasted into the ticket (`/wiki/spaces/<KEY>/pages/<id>` or `viewpage.action?pageId=`) are
    named, not read — once per page.

  What the model reads is unchanged byte for byte, and both new lines are header, outside the
  approval digest, so no approved plan goes stale.

## 3.48.0 — 2026-09-24

### Fixed

- **`sdlc autorun --live` opens the pull request after its own review, with the fixes in it.** The
  build used to open the PR, and the review that followed fixed what it found in the worktree,
  uncommitted, so the PR never carried the fixes. Now the build commits locally, the review runs,
  its fixes are committed as `<KEY>: review fixes`, and then the PR opens. A review that does not
  finish clean — findings left unresolved, a fix that broke the tests, a crash — opens a **draft**
  PR saying why, leaves the ticket In Progress, marks the run failed and exits 1; it used to end
  `done`, exit 0. Edits that broke the tests or came from a crashed review are discarded, so the
  PR carries the tested build. With `--review`, the review's own fixes are shown and asked about
  before they are committed; declining discards them.
- **`SDLC_RUN_BUDGET_USD` is the per-run cap it is documented to be.** Only the Temporal worker
  applied it. `sdlc feature` and `sdlc autorun` now apply it too, read from the environment or
  `.env`: $25 by default, `0` disables, and `autorun --max-cost` overrides it. A malformed value
  (or `nan`, or a negative) refuses the run with exit 2. The MCP `sdlc_feature` and remediate
  tools and `sdlc remediate` do not apply it yet. autorun's cap now covers the review's fixes as well as the
  build, and a resumed run continues from what it had already spent instead of starting from $0.

  **Upgrade note:** `sdlc feature` and `sdlc autorun` without `--max-cost` now stop a run that
  spends more than $25 (autorun parks it; `sdlc feature` exits 4). Set `SDLC_RUN_BUDGET_USD` to raise
  it, or to `0` for the old uncapped behaviour; a budget park names the `--resume … --max-cost`
  that continues it. Scripts that treated autorun's exit 0 as success
  now see exit 1 for a run whose review left findings unresolved.

- **The Java/C# type lookup no longer skips a closer binding.** Every Java and C# edge that names
  a type — a typed receiver's member, a static call, a creation, a base type, a DI binding —
  resolves it through one lookup. That lookup could not see some bindings closer than the one it
  found, and grounded the name on a farther in-repo type of the same name:
  - a Java local class hides a same-named type from its declaration on, so `new Order()` there gets
    no edge instead of landing on the package's `Order`;
  - inside an anonymous class body, the base's member types come first (`Base.Helper`);
  - a qualified name resolves its head first, so `Response.Status`, where the nested `Response`
    extends `BaseResponse`, is `BaseResponse.Status`. In C# a head that names a nearer namespace is
    that namespace;
  - a member type that an external supertype declares hides an in-repo namesake. `class MyMap
    extends AbstractMap` means `AbstractMap.SimpleEntry`, and `Panel : Control` means
    `Control.ControlCollection`. This comes from a small committed table of published API facts,
    because refusing every name under any external base would have dropped 19–49% of true calls;
  - C# type parameters of a local function, or of the generic method a DI registration sits in, hide
    an in-repo class of that name.

  On four field repositories (one Java, three .NET), and on commons-collections, gson, ShareX and
  Newtonsoft.Json, it moves no edge. On guava it moves 16, and all 16 are now right: members that a
  nearer class hides (`ForwardingSortedMap.StandardKeySet` over `ForwardingMap`'s), and member types
  inside nested anonymous classes. Extraction speed is unchanged. Java `CALLS` recall on the corpus
  is 0.92 and C# 0.89; the two new cases add their right readings as edges.
  - A Java `private` member type is not inherited, but it still hides a deeper one. `java.lang.*` is
    imported implicitly. A C# alias to a namespace (`using M = App.Model;`) heads `M.Order`.

## 3.47.0 — 2026-09-24

### Added

- **Java and C# calls through a typed receiver.** `_service.Do()` through a field, `repo.Load(id)`
  through a parameter, `local.Count()` on a typed local or `var x = new T()` — and, in C#, static
  calls on an in-repo type (`Helper.Format(x)`) — now land on the member the declared type means.
  Both front-ends used to drop them: on a .NET service 2,427 such calls were resolvable from the
  source and 0 were emitted, now all 2,427 (775 CALLS in the whole graph, now 5,456); on
  mysql-connector-j 334 of 14,984, now 12,962 (CALLS 20,235 → 31,423). Resolution follows the
  compiler's lookup order — enclosing and inherited member types, the namespace (with a `using`
  inside a namespace block at that block's level, a `global using` for its own project only) or
  package, `using`/imports — and the nearest in-repo type that declares the member, base classes
  before interfaces; an interface-typed receiver lands on the interface's member. A local binds
  only inside its block (and, in Java, from its declaration), a type parameter is never an
  in-repo type, and a name the language binds explicitly — a Java single-type import, a C#
  alias — ends the lookup even when its type is external. A receiver whose type is not written
  (a lambda parameter, `var x = Call()`, a query variable), a lookup the compiler would find
  ambiguous, or a static-looking name inside a class with an external base class (a controller's
  `User` is `ControllerBase`'s property, not the in-repo `User`) is refused, never guessed
  (corpus `*/typed_receivers_refusals`).
- **Java and C# constructor calls are `CALLS` to the created type.** `new Order(7)`, a qualified,
  nested or generic creation, a C# object initializer `new Order { Id = 3 }`, a Java anonymous
  class `new Base() { … }`, and a C# `Order o = new()` whose declaration writes the type now land
  on `Order` — the corpus rule for instantiation, which Python and Kotlin already followed. The
  created type is resolved by the typed-receiver lookup, with the same refusals; arrays,
  `Foo::new`, constructor chaining (`super(…)`, `: base(…)`) and a `new()` whose type would be
  inferred (`return new();`) are not read. So a class's `blast_radius` lists who creates it: on
  mysql-connector-j `com.mysql.cj.result.Field` went from 0 callers to 264, on a .NET service a
  view model from 0 to 279. A constructor node, which no edge targets, reports those creators as
  `instantiated_via_type` rather than "0 callers". `CALLS` recall on the corpus: Java 0.91 (30 of
  33), C# 0.89 (32 of 36); every remaining miss is a type the source does not write at the site —
  an inferred receiver, or `return new();`.
- **ASP.NET Core DI registrations become `PROVIDES`.** `services.AddScoped<IMailer, SmtpMailer>()`
  (and `Transient`/`Singleton`/`TryAdd*`) records `SmtpMailer PROVIDES IMailer`, the edge Kotlin's
  Hilt bindings introduced — only when `SmtpMailer` really implements `IMailer`. A factory
  registration is read only when its lambda builds exactly one type,
  `AddScoped<IAudit>(sp => new DbAudit())`; a factory that computes its result is not.
- **`blast_radius` reaches an implementation's callers through its interface** — in every language
  that emits `IMPLEMENTS`, not only Java and C#. Every consumer of a DI-bound service is handed the
  interface, so nothing calls the implementation by name — its blast radius was empty. `blast_radius` now also reports `interface_callers` (and
  `explain_symbol` `called_through_interface`): the callers of each member this one implements, at
  every level up through `IMPLEMENTS` and `PROVIDES`, each with the `via` member. They are counted
  apart from direct callers, because they *may* reach this implementation, not must. On the .NET
  service: a logging implementation's method went from 0 callers to 0 direct + 304 through
  its interface's member.

### Fixed

- **C# base types reached through `using`.** `class Store : IStore` with `IStore` in a sibling
  namespace was placed in the class's own namespace and then made external — 46 of 77 "external"
  bases on the .NET service were in-repo interfaces. They now resolve through the same lookup.
- **Java `interface X extends Y` had no `IMPLEMENTS` edge** (an interface's `extends` list was not
  read): +126 on mysql-connector-j, which is also what lets a member an interface inherits be found.
- **Java static calls land only on a member that exists.** `Type.method()` was emitted per file
  whether or not the repository declared it; it is now settled once every file is known, and a
  capitalized name that is a declared field (`DEFAULT_INSTANCE.toBuilder()`) is read as the
  field. Dangling edges from `pkg verify` on mysql-connector-j: 4,894 → 270 (the rest are
  external protobuf bases).

  **Upgrade note:** Java and C# graphs gain many `CALLS`, `IMPLEMENTS` and (C#) `PROVIDES` edges,
  so an `understand --check` in CI diffs once — regenerate `episteme/` with
  `orchestrator understand .`. The new `blast_radius` / `explain_symbol` fields are additive; a
  symbol with no interface member reports exactly what it did before. Java and C# classes also
  gain callers — the code that creates them.

## 3.46.0 — 2026-09-24

### Fixed

- **A one-line Kotlin body lost every declaration after it (#396).** `interface Iface { fun
  f() }` and `class B { fun f() {} }` are a `tree-sitter-kotlin` 1.1.0 parse ambiguity, and the
  `ERROR` it produced dropped the rest of the file. Reported as a missing same-file
  `IMPLEMENTS`, which it caused, but it lost every node and edge after the body. The Kotlin
  front-end now moves such a body's closing brace onto its own line and re-parses. A split is
  kept only when the parser reads that byte as a brace and the `ERROR` shrinks, only brace
  pairs overlapping an `ERROR` are tried, one file spends at most 64 re-parses, and the result
  is used only when it parses cleanly. Every line number, including those of facts emitted in
  `finalize` (Compose routes, Ktor routes, Retrofit calls), points at the file on disk. A file
  that parses cleanly takes no part of this path. See
  [kotlin-support-roadmap.md](docs/specs/kotlin-support-roadmap.md).
- **Two Kotlin recall gaps from #397.** A Room `@Insert`/`@Upsert`/`@Delete` whose parameter
  type is reachable only through `import app.data.*` now gets its `WRITES` edge. The lookup
  follows Kotlin's order: an explicit import, then the same package (which hides a star import
  even when it declares a plain class), then star imports, where two candidates are an
  ambiguity and emit nothing. Overloaded write methods are settled one method at a time, and a
  qualified parameter type resolves as written. An extension declared in another file of the
  caller's own package now resolves with no import, as Kotlin does. It resolves only when the
  receiver in scope is compatible and no supertype the repository cannot see (or `Any`) could
  declare a member of that name, because a member beats an extension.
- **Kotlin: a bare call inside `with(x) { }` no longer lands on the enclosing class (#453).**
  `with(x) { f() }`, `x.apply { }`, `x.run { }` and `buildString`/`buildList`/`buildSet`/`buildMap`
  put the receiver's members ahead of the enclosing class's, and the front-end ignored that:
  `apply("com.android.application")` inside `with(pluginManager) { }` was recorded as a Gradle
  convention plugin's own `apply(target: Project)` calling itself — 17 invented self-loops on
  the Android validation app. Inside those blocks an enclosing-class member reading is now
  **refused**, and never redirected to the receiver, so the change can only remove an edge:
  measured on seven Kotlin code bases, every edge it emits was emitted before, and every edge it
  removes is a bare call inside such a block. Top-level and imported calls are unchanged.
  **What it costs:** a *true* call to the enclosing class from inside one of these blocks is
  dropped too — React Native's `ReactAndroid` loses 20, detox 4, `@react-native/gradle-plugin` 5;
  ktor-samples, KaMPKit and spring-petclinic-kotlin are unchanged. Kotlin `CALLS` recall reads
  0.93 → 0.85: the new corpus case labels the true targets the rule gives up as known gaps.
  Resolving such a call *to* the receiver's member is the follow-up in #459.
  See [kotlin-support-roadmap.md](docs/specs/kotlin-support-roadmap.md) §3.2.
- **A call through a Python re-export lands on the symbol that defines it.** `from app import
  Store; Store()` put the edge on an external placeholder, `py:app.Store`, instead of
  `py:app.store.Store` — so `blast_radius`, `explain_symbol`, `investigate` and grounding
  under-counted the callers of every re-exported symbol and called it third-party. On Spine's own
  graph that was 1,271 `CALLS` (5.5%) on 141 phantom twins: `FactStore` showed 57 call sites of
  167. Resolution follows Python's binding rules — a package `__init__` or any module, renames
  (`as`), chains through sub-packages, `import *` (a literal `__all__`, else public names),
  `if TYPE_CHECKING:`, and members of a re-exported class. An import through a re-export now
  names the defining symbol too, as a direct import always did. A binding that differs by
  environment (`try`/`except ImportError`) or comes from a module `__getattr__` is never guessed.
  **Upgrade note:** Python graphs change wherever a repository re-exports — more callers, and
  `IMPORTS` that name a symbol instead of its package. Two consumers of the import graph move
  with it: `pkg export`'s SQLite `imports` table (module → module only) loses the rows that now
  name a symbol (110 on Spine's own graph), and the dependency lists on `understand`'s module
  pages shift from the package to the defining module (104 pairs out, 162 in on Spine; import
  cycles unchanged). An `understand --check` in CI therefore diffs once — regenerate `episteme/`
  with `orchestrator understand .`. `import a.b` now binds `a` (the package) for call
  resolution, as Python does; it used to bind `a.b`.

### Added

- **`pkg verify` warns on a `phantom-symbol`**: an external node that carries calls while a
  first-party symbol of the same name lives under the same path — an unresolved re-export or
  alias, whose callers are missing from the real symbol. It is the check that would have caught
  the fix above (141 on the pre-fix graph, with `pkg verify` otherwise OK). A warning, never an
  error; what it still finds is a binding no front-end can decide.

## 3.45.0 — 2026-09-24

### Added

- **A plan's criteria are checked against the whole ticket.** §8 of the build document checks
  each acceptance criterion against the ticket's own words, and those words were whatever
  survived the intent extractor's caps (5 attachments, 8,000 characters each, 20,000 in total).
  `sdlc plan --source` now reads the ticket fresh at every plan, **with every attachment read in
  full** (up to 20 files, the 1 MB download cap still per file), and saves that as the text the
  plan and its approval are checked against. The AI that derives a spec still reads exactly the
  bounded summary it did before — pinned byte for byte by golden files — so no cached spec is
  re-extracted and no approved run is re-parked; the intake cache is unchanged too.
- **`--follow-links`** on `sdlc plan`, `sdlc autorun` and `investigate` also reads the
  **Confluence pages a ticket links to** — Jira remote links first, then page URLs in its
  description and comments (page-id URLs and `/x/` tiny links, decoded offline and kept only when
  re-encoding reproduces the link; a `/display/…` title URL is named, not guessed). Direct links
  only, at most 5, the rest named with why. The pages reach both the criteria check and the spec,
  and the build document's header says what was read. A Confluence-typed remote link to *another*
  site is named, not read — a page id means a different page on a different site. Off by default;
  without Confluence access
  (an MCP server exposing `confluence_get_page`, or `CONFLUENCE_*` credentials) it **refuses**
  rather than planning from less than was asked for. A spec derived with linked pages is its own
  intake-cache entry, beside the flag-off one in the same file.
- **A Jira ticket carries every part over MCP that it does over REST.** Through `mcp-jira` it used
  to arrive as the description only (said so since 3.44.0); it now carries comments, issue links
  and attachment text, rendered by the same code — attachment bytes via mcp-atlassian's
  `jira_download_attachments` (`MCP_JIRA_ATTACHMENTS_TOOL` to rename), extracted by the same
  reader, so attachment text is identical. Formatted prose is not byte-identical: REST flattens
  ADF to text, mcp-atlassian returns Markdown. What the server cannot supply is named with why —
  a missing download tool, two attachments with one name (it cannot tell revisions apart), or a
  server that rejects the fields request, which is then read as the description only. Any other
  server error is an error, not a quietly thinner ticket.

### Fixed

- **A ticket that cannot be read is an error, not a traceback.** A missing file, an HTTP error or
  an MCP failure was a Python traceback in `sdlc plan`, `investigate` and `autorun`'s intake; it
  is now `ERROR:` and exit 2 — only for those named source failures, so a bug in Spine's own code
  still shows its traceback. A source that returns no text (no documents, or blank ones) is a
  warning instead of a plan quietly checked against nothing.

### Removed

- **`sdlc plan --out` and `sdlc approve --out`**, deprecated in 3.44.0. Plans live in
  `<repo>/.spine/plans`, the only place `sdlc autorun` reads approvals from. `sdlc autorun --out`
  (run artifacts) is a different option and is unchanged.

## 3.44.0 — 2026-09-24

### Fixed

- **`sdlc autorun --help` no longer denies the gates it has.** It said the command did not
  judge whether a ticket is worth doing, enforce a budget, survive a crash, or loop on review
  findings — written for the walking skeleton and never revised as the validity gate, the run
  supervisor (`--resume`, `--max-cost`) and the review loop landed. The help now says what stops
  a run, where each decision is made (`sdlc approve` for the plan gate, `sdlc runs approvals`
  for a park), and what a resume keeps — and names the gaps that remain rather than implying
  they are closed: no spend cap unless `--max-cost` is passed (this path does not read
  `SDLC_RUN_BUDGET_USD`); a resume re-runs every stage, so approving a validity or design park
  parks again rather than building; and the review pass leaves its fixes uncommitted — under
  `--live`, after the PR is already open. The module docstring and
  `docs/specs/autonomous-run-agent.md`'s status line, which the help points to, were stale the
  same way.
- **An approved plan is the plan that builds.** Four ways `sdlc plan` → `sdlc approve` →
  `sdlc autorun` refused, or could not be run for, a plan nobody had changed:
  - **Planning no longer invalidates its own approval.** `sdlc plan` writes `.spine/plans/` into
    the repository. Unless `.spine/` was ignored, the tree then read as dirty, the plan gate
    re-derived a `<sha>-dirty` document and refused the approval it had just been given. That is
    the exact sequence `.github/workflows/spine-sdlc.yml`'s build job runs. Every later command
    also stopped trusting the knowledge-graph cache. `.spine/plans/` is no longer counted as an
    uncommitted change anywhere, whether you commit it or not; `.spine/repos.yaml` and
    `.spine/workflows/` still are. **One-time action:** an approval given while the tree read as
    dirty (its plan's header says `Derived at: <sha>-dirty`) is now refused once — re-run
    `sdlc plan` and `sdlc approve`.
  - **`sdlc plan --spec X.json --source <uri>` no longer crashes** (`UnboundLocalError`). The
    spec stays the requirements and the ticket is only read, with no model call, for §8 to check
    the hand-written criteria against. A spec file and a `jira://` key that name different
    tickets are warned about by `sdlc plan` and `sdlc autorun` alike.
  - **A Bug that lands nowhere keeps its approval.** The gate re-derived the plan without the
    issue type, so §12's validity row read `PROCEED` where the approved one read `UNLOCALIZED`.
    The approval now records the issue type, and the document's header names it (or says
    `untyped`).
  - **A Jira ticket read through an MCP server says it is the description only.** The REST path
    also carries links, comments and attachments; the MCP path silently did not.

### Deprecated

- **`sdlc plan --out` and `sdlc approve --out`** now warn and will be removed in 3.45. A plan or
  approval written outside `<repo>/.spine/plans` is one `sdlc autorun` never reads, so it could
  never be built. `sdlc autorun --out` (run artifacts) is a different option and is unaffected.

## 3.43.1 — 2026-09-23

### Fixed

- **Python: a `src/` that is a package no longer breaks every import.** The src-layout rule
  stripped a leading `src.` from every module id, even when `src/` held an `__init__.py` and the
  code imported through it (`from src.services.x import …`). Ids and imports then named the same
  modules two ways and nothing joined: a field report on 3.42.0 had `pkg verify` fail with 33 of
  34 modules imported by nothing, 99% of import edges external, and `src/__init__.py` named
  `py:<root>`. `src/` is now stripped only when it is a sys.path root (no `__init__.py`); a
  package `src/` keeps its name, which is the one Python resolves. Graphs of repos with a
  package `src/` change ids from `py:X` to `py:src.X`; the standard src layout is untouched. New
  corpus case `python/src_package`.

## 3.43.0 — 2026-09-23

### Added

- **JavaScript — the 13th front-end, and the 12th language.** `.js`, `.jsx`, `.mjs` and `.cjs`
  used to reach the walker with no front-end and produce nothing, with no complaint: this
  repository's own operator UI is 22 files and 1,460 lines of JavaScript, and none of it was in
  its graph. `express` and `react-boilerplate` extracted to zero nodes while the profiler counted
  141 and 222 JavaScript files in them. `JavaScriptExtractor` rides the `typescript` extra — no
  new install — and subclasses the TypeScript front-end, whose TypeScript-only rules are clean
  no-ops on JavaScript. Ids share TypeScript's `ts:` namespace, so a `.ts` file importing a
  `.js` one resolves across the pair; nodes are tagged `javascript`. What it adds:

  - **CommonJS.** `require()` in every shape the front-end can bind precisely — whole module,
    destructured, `.member` — and the exports a CommonJS module is actually made of:
    `exports.f =`, `module.exports = {…}`, and the aliased object (`var app = exports =
    module.exports = {}`) that spells 43 of express's 49 exported functions.
  - **JSX in `.js`**, parsed with the TSX grammar. The plain TypeScript grammar does not reject
    JSX, it mis-parses it — 90 of react-boilerplate's 222 files in error, against 0.
  - **Express routes the CommonJS way**: a router bound through `var app = module.exports =
    express()` (18 of express's 28 example apps, which had no endpoints at all) and a handler
    named as another file's export, `app.get('/', site.index)`. On express: 13 endpoints and 0
    `EXPOSES` became 31 and 7.
  - **A Sequelize data layer** — `Entity`, `Field` and foreign-key-direction `REFERENCES`, read
    the way Sequelize's own example app writes it: `define` inside a function, associations in a
    file that never imports `sequelize`. Only an attribute map that uses a Sequelize type counts
    — `customElements.define` and `ajv.define` take a string and an object too. `modelName` and
    `tableName` are read, and `link_data_layer` folds an entity onto the `.sql` table of the same
    name: the declared `tableName`, or the model name with a plain `s` (`user` → `users`; an
    irregular plural such as `people` needs `tableName` to match). A column type counts anywhere
    in its chain — `INTEGER.UNSIGNED`, `STRING(120)` — so a join model keyed only by unsigned
    integers is a model, while `Op.is` and `Sequelize.NOW` are not. An imported model is the
    *binding* it was exported as, read through the same three tiers as calls:
    `module.exports = { Booking }` over `define('gig', …)` makes `const { Booking }` the `gig`
    model, as do `db.Booking = define(…)` on the exported object, `{ Booking: define(…) }`,
    `exports.a = exports.b = define(…)` and `export const Booking = define(…)`. A name a readable
    or names-known module does not export is nothing, and so is a binding declared inside a
    function or a name written onto an object `module.exports` later replaced; an opaque module
    is still matched by the model's name.
  - **Calls bound to what a module actually exports**, as far as its own file says. Each
    CommonJS module is read into one of three tiers, by what was *read* of it. **Readable**:
    every reference to its exports object is a recognised form (an allowlist — a member write, a
    string-key write, a read, the `exports = module.exports = …` chain, `Object.assign` or
    `defineProperty` with literal names; the finder follows `exports`, `module.exports`, their
    aliases, top-level `this` and bare `module`, and any spelling it cannot follow makes the
    module opaque) and `module.exports` is set to something read in full,
    so the export map is exact. **Names-known**: every reference is recognised, but a write sits
    under a branch or in a function, a merge adds members, the value is `Object.freeze({…})`, or
    a UMD branch assigns `module.exports` — so the map is every name the file may export.
    **Opaque**: some reference is not recognised (`const e = exports`, `mixin(exports, …)`,
    `exports[k] =`), and calls into it are resolved by name. A call reaches only a name a
    readable or names-known module writes to its exports: `module.exports = { run: helper }`
    makes `m.run()` — and `app.get('/', m.run)`, and `new m.run().go()` — reach `helper`, and
    `Object.freeze({ f })` beside a private `secret` does not hand `m.secret()` to a name lookup.
    `module.exports = Base` fills a *default* slot, not a member: `const B = require('./base');
    class C extends B` reaches `Base` under any local name, and a destructured `{ Base }` —
    `undefined` at run time — reaches nothing. Reassigned at the top level, the last
    `module.exports` wins; members written onto the object it replaced are never exported. A
    call never lands on a module node, from JavaScript or TypeScript: `db.config()` beside a
    sibling `db.config.ts` is the `config` export of `db`, or nothing. TypeScript files calling into CommonJS get the same routing,
    whichever file the walk reaches first. The module a target names is the longest prefix that
    *is* a module, so `user.model.ts` beside `user.js` keeps its edges, TypeScript's included.
  - **An existence check** on everything it resolves by name: a `CALLS`, `IMPLEMENTS`,
    `EXPOSES` or `REFERENCES` edge from a JavaScript file to a node nothing declares is dropped
    rather than left dangling. Zero dangling edges on all four validation repositories.
  - **Compiled output is skipped**: a `foo.js` beside its `foo.ts` is `tsc`'s build, not source.

  Twenty corpus cases, each labelled before its first run: precision **1.00 on every node and
  edge kind**, `CALLS` recall 0.97 (97 of 100), and the three misses are the three gaps declared
  in advance — a renamed destructuring, an inherited `this.method()`, and a bare `new X()` with no
  member call. `pkg verify` and `--oracle parity` now count JavaScript routes and entities; the
  route count takes only named-handler registrations, the ones that can produce the `EXPOSES`
  edge it checks, so an inline `function (req, res) {…}` is not reported as a miss. The entity
  count reads a model class's whole `init({…})` object, `super.init` included, in linear time.
  Codegen is not part of this: `--language javascript` is refused with the list of supported
  languages, as before. Prisma is not either — its schema is its own `.prisma` language, and it
  has a track of its own.

### Fixed

- **Whole-repository passes ran in the order the walk first met each language**, so which
  front-end's `finalize` saw the other's edges depended on which file sorted first. They now run
  in the order the front-ends are registered. Measured: no language's corpus result and none of
  the four validation repositories changed; the order was simply not guaranteed.
- **A `var` inside a class `static {}` block shadowed an import across the whole enclosing
  function — in TypeScript too.** The block is its own `var` scope; hoisted past it, a true call
  after a class expression carrying one was dropped.
- **`sdlc plan` wrote a different build document for the same commit depending on whether
  GitHub was reachable.** Section 11's cost table is priced from LiteLLM's model map, and by
  default LiteLLM does not read the map it ships: every import fetches the current one from
  GitHub, and falls back to the bundled copy only when the fetch fails. CI planned one commit
  twice, one fetch failed, and `sdlc_shapes.py` caught two documents differing only in §11 —
  "of 2,289 priced models" against "of 1,716", with different nearest-price rows (4,175
  models fetched, 2,982 bundled). `orchestrator.core.llm.catalog` now defaults
  `LITELLM_LOCAL_MODEL_COST_MAP` to `True` before LiteLLM is imported, so prices are the
  installed version's, as the catalog already claimed, and an offline machine no longer
  waits on a fetch at every import. Set `LITELLM_LOCAL_MODEL_COST_MAP=False` for today's
  prices, at the cost of that determinism.
- **`import './mod.js'` minted a module no file declares — in TypeScript too.** ESM requires the
  extension, and specifier resolution stripped only `.ts`/`.tsx`, so it produced `ts:mod.js`
  with `external=False`, a first-party-looking phantom, plus a `CALLS` target nothing could
  rescue. It now strips every suffix the TypeScript namespace carries.
- **A member call on a name the function had rebound resolved through the file's namespace — in
  TypeScript too.** `function save(user) { user.save() }` beside `import * as user from './user'`
  (or `const user = require('./user')`) drew `CALLS` to the module's export, which the parameter
  need not have. The rule that already refused a rebound *bare* name now covers the receiver, and
  every binding is tracked over the byte range JavaScript scopes it to — a callback's parameter
  over the callback, `catch (e)` over its clause, `let`/`const` to the end of their block, `var`
  over the function it is hoisted to (a callback's own `var` over that callback, not the function
  the callback sits in), and a nested `function` over its block. So a one-line
  `const x = f(); x.g()` is covered, and a call *after* a callback that shadowed the name is no
  longer refused: the rule used to bind the name to the end of the enclosing function, which
  dropped true calls in TypeScript as well.
- **`import … from '..'` or `'../index'` minted a module no file declares** (`ts:.`), and a call
  through it targeted `ts:f` where the root module's members are `ts:<root>.f`. Both now name the
  root module; a specifier that escapes the scanned tree yields no first-party id.
- **The capability matrix under-reported a front-end that subclasses another**, and its runtime
  cross-check silently skipped any front-end without a hand-written fixture — which is how
  **Gradle had never been cross-checked** at all. The matrix now follows direct inheritance
  (changing no existing row), and a test requires a fixture, or a stated reason, for every
  front-end in both directions. Gradle's is recorded as a gap, not excused.

- **The documented local-gate sync command omitted `--extra clang`, so following the docs
  made the accuracy gate fail.** CONTRIBUTING.md and SETUP.md both presented an eleven-extra
  `uv sync` as "the extras set CI syncs"; CI's own `Install project` step installs a twelfth.
  Because `uv sync` replaces the environment rather than adding to it, running the documented
  command *uninstalls* `libclang` — and `pkg accuracy --check` then reports
  `cpp/edges/CALLS matched edges — was 5, now 3` with two unexplained misses, which reads
  exactly like a C++ regression on `develop` rather than a missing optional dependency. Both
  documents now carry the twelfth extra and say what its absence costs. Same failure class as
  the `mypy` phantom `unused-ignore` errors already documented beside it: an absent optional
  dependency does not error, it silently scores lower.

## 3.42.0 — 2026-09-21

### Fixed

- **Three Kotlin defects that the previous round fixed only half of.** Each was reported
  against a shape that now works and left a second shape that did not, so the issues stayed
  open with a passing test beside them.

  **An inherited member overridden in between was refused as ambiguous** (#391). `Impl : Mid :
  Base` declares `ping` twice on the way up, and the walk collected hits across the whole
  hierarchy into one set — so "exactly one match" refused the commonest inheritance shape in
  the language. An override always sits strictly nearer than the thing it overrides, which is
  what Kotlin resolves to. The walk now goes level by level and the first level with a single
  answer wins; two declarations at the *same* distance are still a real ambiguity and are
  still refused.

  **A destructured lambda parameter did not shadow a sibling member** (#392). `m.forEach {
  (name, key) -> key() }` hangs a `multi_variable_declaration` off the lambda's parameter list
  rather than off a `for_statement`, so `key` was never bound and `key()` resolved to a member
  the lambda never calls. Fixed in the extractor *and* in the D9 invention oracle, which
  shared the blind spot and was reporting the fabrication clean.

  **A nested generic wrapper opened the door to its element** (#393). The unwrap allowlist was
  tested on the outermost name and the peel that followed ran to the bottom, so
  `Provider<Set<OkHttpClient>>` reduced to `OkHttpClient` — an ordinary Dagger shape whose key
  is `Set<OkHttpClient>`, and nothing injecting a plain client is satisfied by it. Unwrapping
  now happens one level at a time, re-asking the question at each.

  Wrappers are also recognised by **resolved id** rather than by name, which the bare name
  could never do: `kotlin.Lazy` needs no import line at all — it is a Kotlin default import,
  the `by lazy` delegate — and a repository is free to declare its own `class Provider<T>`.
  Both were being read as Dagger's. The same change recovers a binding that was being
  dropped: a fully-qualified `dagger.Lazy<T>` failed the old name check for the opposite
  reason, since its written name is not the bare one.

- **The accuracy gate failed a build for writing down a known limitation.** `expected` counts
  every labelled edge, and a `known_gaps` entry still counts as a miss — deliberately, so a low
  number stays honest. But the gate compared the recall *ratio*, so labelling a gap lowered it
  and reported a regression with nothing about the extractor having moved. The only remedy was
  regenerating the committed baseline, which accepts **everything** that moved rather than the
  one thing intended. The cheapest way to keep CI green was to not label your gaps, which is
  the opposite of what the corpus is for.

  Corpus recall is now gated on *unexplained* misses — `expected - matched - known_gaps` — and
  on `matched` never falling. Two conditions, because either alone has a hole: the first catches
  a new miss nobody accounted for, including the case a ratio cannot see at all (8/10 and 12/15
  are both 0.80 while the misses go from two to three); the second catches an edge that stopped
  resolving even when a gap labelled in the same commit would otherwise pay for it. **The
  published score is unchanged** — precision and recall are still `matched/emitted` and
  `matched/expected`, and a known gap still counts as a miss. A drop where every new miss is
  explained is now reported on the trend channel rather than failing, because the number did
  move and a reader comparing releases deserves the reason.

  Precision stays a plain ratio: no annotation makes a fabrication acceptable, so there is
  nothing to net off.

- **Four `known_gaps` entries asserted limitations that no longer existed.** Validation asked
  only whether a gap named an edge in `edges`, which a *closed* gap still satisfies. Three in
  `typescript/instance_calls` and one in `cpp/instance_calls` named edges the front-ends emit
  today. Because the gate subtracts gaps per language and per kind rather than per case, the
  three dead TypeScript entries were paying for two genuine unexplained misses in
  `receiver_shapes` — credit the gate had not earned, hiding real misses. A gap naming an edge
  the extractor emits now fails the case to load, the same way a broken `refusal` does.
  Removing them moved no score: those edges are labelled *and* matched.

- **A Kotlin extension call resolved onto an id nothing declares.** The check that was added
  to stop a fabrication became one. It asked whether an extension's declared receiver *name*
  equalled the call site's and treated "no" as grounds to refuse — but Kotlin's rule is a
  subtype-compatible receiver, so `fun NavController.navigateToSearch()` called on a
  `NavHostController`, which is the standard Compose navigation pattern, answered "no". The
  refusal did not drop the edge; it discarded the `import` the file had actually written and
  minted `androidx.navigation.NavHostController.navigateToSearch` in its place. Four such
  edges on the Android validation app and three on KaMPKit, all with no provenance, and
  `pkg verify` called both graphs clean because a placeholder never dangles. The `CALLS`
  count did not move either, because the edges were redirected rather than lost.

  The question is now three-valued: compatible when the receiver ids intersect, when the
  extension takes a type parameter, or when `IMPLEMENTS` runs from the receiver up to a
  declared receiver; incompatible only when the repository declares *both* types and no such
  path exists; unknown otherwise — and unknown accepts, because an external receiver has no
  supertype list to walk and a name mismatch against it proves nothing. Comparing resolved
  ids rather than bare names also stops `app.data.Topic` and `app.legacy.Topic` being read as
  one receiver, which closes [#390](https://github.com/synaptixs/spine/issues/390).

  The same comparison had refused two other ordinary shapes: `fun Int.toDp()` and
  `fun Float.toDp()` share one id, and a single-receiver slot kept whichever file was parsed
  last — so which call resolved depended on filesystem order — while `fun <T> T.x()` stored
  the literal `T`, which equals no real receiver.

- **A warm cache and a cold cache disagreed about the same commit.** The repository-wide
  Kotlin extension table was never cleared, though the four other accumulators on the same
  class are all cleared in `finalize`. `pkg extract --repos` and `investigate --repos` reuse
  one extractor across every declared repository, so the first repository's extensions
  verified the second's imports; and because a cache hit returns before `finalize` runs,
  whether a repository happened to be cached changed what the *next* one emitted.

- **A Ktor mount and a Compose route constant stopped resolving in the default package.**
  "Same package" was compared as a string, and a file that declares no `package` has none —
  its module falls back to the repo-relative path, so two such files read as two different
  packages. Kotlin resolves these with no import at all and both sources compile, so this was
  a real edge lost. The declared package is now read from the parse tree.

### Changed

- **`scoreboard.json` is version 2** and every corpus edge entry carries `known_gaps` alongside
  `expected`/`emitted`/`matched`. The version field is now *read* — it was written, exported and
  checked nowhere — because an older baseline silently lacking the key would quietly stop gating
  every kind it touched. Regenerate with `orchestrator pkg accuracy --scoreboard`; the check
  refuses an older baseline rather than guessing, and only for boards that carry a corpus
  section, so the evals' comprehension-only boards are unaffected.

## 3.41.1 — 2026-09-21

### Fixed

- **A drafted change's `Grounding` type collided with the code-review one.** Two types were
  named `Grounding`; `intake.pkg_evidence`'s is now `DraftGrounding`. Internal only — the
  rendered `## Grounding` section, `ungrounded()` and every other name are unchanged. Found by
  running `blast_radius`, which returned two matches where every `grep` returned one
  undifferentiated pile.

- **Three integration tests that no one could see failing.** Without Postgres the whole
  directory errors out on the connection, so these sat red behind it. `/console` has required a
  web session since 2026-06-26 and redirects to `/login` with 303; the test still asserted 200
  with a comment saying no auth was needed. And Alembic's `fileConfig(...)` — whose
  `disable_existing_loggers=True` default is the trap — was disabling every logger created
  before it, from a **session-scoped** fixture, so `tests/plugin/test_audit.py` found an empty
  `caplog` and failed only when run after that directory. `migrations/env.py` now carries
  Alembic's own `configure_logger` guard, defaulting to `True`, so `alembic upgrade` is
  unchanged for operators.

  A third failure needed no fix: it mocks its LLM and was failing on the object store, which
  `docker-compose.dev.yml` provides and which simply was not running.

## 3.41.0 — 2026-09-20

### Added

- **`openspec draft` can read the repository.** Its whole signature was
  `--source/--out/--refresh/--overwrite`, so a drafted change could only restate the ticket
  more formally — and `tasks.md` took the spec and read nothing from it, emitting the same two
  checkboxes for every change ever drafted. It now takes a repo path (or `--repos` for
  several, grouped by repository key), and the proposal carries a `## Grounding` section:
  where the change lands with `file:line`, and each stated criterion bound against the graph.
  `tasks.md` becomes one checkbox per criterion, with `proposed_criteria` in their own
  labelled group and a bound criterion flagged **verify first** — never *done*, because the
  binder found code the criterion *names*, which is evidence and not a verdict.

  **What it does not change: the prose.** The requirements and scenarios are still written
  from the source document alone. Grounding puts the code's facts *beside* them, fenced off
  and cited, so a reader can tell the two apart — a line with no `file:line` has been checked
  by nothing. Evidence reaching the model is a separate question, deliberately not answered
  here.

  **Absence is stated, in four distinguishable ways.** Grounded; grounded but the graph came
  back **empty** (a language with no front-end yields zero nodes and looks exactly like a
  repository with nothing to find — read as *"we looked and found nothing"*, never *"this
  ticket touches nothing"*); grounded against an **uncommitted** tree, recorded in the file
  rather than on stderr, because a drafted change is committed and read months later while a
  warning scrolls away; and **ungrounded**, which says so on its own face. No task ever cites
  a file: an instruction is derived, and a citation on one would lend it authority it has not
  earned.

  **Two of these changes land with or without a repository**, and the distinction is worth
  stating because an earlier draft of this entry got it wrong: `proposal.md` always gains the
  banner line and the `## Grounding` section naming its mode, and `tasks.md` is always one
  checkbox per criterion. A repository adds the *facts*; it is not what turns the task list
  on. `specs/<cap>/spec.md` — the contract codegen hits — is unchanged in every mode.

### Changed

- **One renderer for the landing block, one loader for a merged graph.** The landing bullet
  had drifted into two spellings — `investigate`'s brief and `evidence`'s tool output, which
  the codegen agent reads — and `--repos` into four, differing in error prefix, return shape
  and whether they warned about an uncommitted tree. Both are now single implementations, with
  the real differences as parameters. Neither surface's bytes changed, and tests pin them.

## 3.40.0 — 2026-09-18

### Added

- **Briefs quote the code they located.** `investigate` and `root-cause` rendered the graph's
  *index* — symbol names, `file:line`, caller counts — and stopped, so a reader opened the
  files the brief had already found. Measured before this change: **zero** code blocks in
  either. `investigate` now renders the source under the landing bullet it belongs to, for the
  strongest few, saying how many it did not quote; `rca` quotes its fault site, so "ranked by
  evidence, not asserted" means the evidence is on the page.

  **Weak landings are never quoted, and never carry a coverage note.** On a real ticket the
  first implementation spent two of three excerpts on DTOs the brief itself labelled *"weak:
  only `auct`, which other files use too"*, and printed "no test reaches this" in bold on all
  ten rows, including model fields nobody would test. A signal that fires on every row has
  stopped being one; both now appear only where the retriever is confident.

- **Each landing says whether a test reaches it.** *"no test reaches this"* is the line a
  reviewer acts on. `covered` is three-valued: a language with no call graph renders **nothing**
  rather than an accusation, because a front-end that emits no `CALLS` edges has not proven an
  absence of tests.

- `excerpt.source_at(root, "file:line")` — the module's first public entry point. Returns
  `None` for every failure and raises for none: a deleted path, a line past the end of a file
  that shrank, a binary blob, a permission error. A line past the end is **refused, not
  clamped** — clamping quotes the wrong code confidently.

### Fixed

- A root-cause report told the reader to re-run tests "over the regression surface **below**"
  when the shared section vocabulary puts it above. A document that points the wrong way.


- **A merged multi-repo brief carries project knowledge again.** `investigate --repos` passed
  `root=None` and omitted "Relevant project knowledge" entirely — `episteme/` belongs to one
  repository and a merged brief has no single owner for it, so rather than fill the section
  from an arbitrary repo it said nothing. Correct in isolation, and the consequence was that
  the mode a cross-cutting ticket needs was the only mode with no project knowledge at all:
  on a real two-repo pair with both knowledge bases committed, the brief still reported the
  section absent. It now reads **each repository the ticket actually lands in**, headed by its
  repo key — a fact the brief already had, so a four-repo declaration does not put a second
  service's domain model in front of a reader whose ticket never touches it. The single-repo
  budget is **split across those repos, never multiplied**, and a declared repository whose
  bank is missing is **named rather than skipped**, because silence there reads as "that
  repository has nothing to say" when it means nobody ran `understand` there.

## 3.39.0 — 2026-09-19

A field report from a pilot engagement (NSS-1239) and the follow-ups a maintainer review
found around it. **NSS-1239:** `sdlc feature` scaffolded a WebApp ticket into `ApiClient` and
failed after six test runs — the target project was whichever `.csproj` sorted first, and the
domain type it needed is unreachable from there by construction. Two CI guards that were
skipped rather than passed are closed in the same release.

### Fixed


- **`sdlc feature` builds in the project the ticket names.** In a repository holding several
  projects of the same language the target was whichever one sorted first, so NSS-1239 — whose
  plan named five files under `WebApp/` — scaffolded into `ApiClient`, could not resolve
  `Product`, spent every refine on `using` directives and ended `FAILED after 6 test run(s)`. The
  project is now chosen in three steps: the one holding the files the design names (the deepest
  project owns its own files, since nested projects are the normal .NET shape), else the one with
  the most source **in the language being resolved**, counted the way the extractor walks — a
  generated `obj/` tree and a vendored `wwwroot/lib/` of jQuery cannot vote — else the name,
  which now only breaks a tie the first two could not. The `[layout]` line says
  which rule fired: `src=WebApp (project chosen: holds 5 of 5 file(s) the design names)`.
  **`--package-name` now retargets, not just renames:** naming a project settles the choice
  outright, which is the lever a `sdlc feature` run had no way to offer when the inference was
  wrong, and the test project it pairs with is that project's own rather than the first suite
  in the repository. Vendored and sample trees (`third_party/`, `samples/`, …) are never
  candidates in any language — unless the ticket names a file inside one, since an SDK repo's
  own `examples/` module is first-party to whoever filed the ticket about it. Greenfield
  scaffolding is unchanged.

- **Java multi-module repositories resolve at all.** `root/src/main/java` is the single-module
  shape; a Maven or Gradle build keeps each module's tree under `<module>/src/main/java` at
  any depth, so `include(":services:worker")` counts. The lookup previously matched nothing
  and the layout fell through to a package name absent from the repository; the module, and
  the package inside it, now follow the ticket's files. Kotlin was already module-aware but chose by package name and stopped at "name a module"
  when several qualified; the ticket's files settle it, and that honest refusal remains when they
  say nothing.


- **The `episteme/` guard cannot be skipped by aiming at `main`.** CI exempts the
  `develop → main` promotion PR, whose `episteme/` diff is the regeneration bot's own work —
  but the exemption keyed on the *base* alone, so any PR into `main` from any branch got a
  free pass. A contributor's feature branch targeting `main` carried `episteme/README.md`
  through with every check green, because the guard was skipped rather than passed. It now
  keys on both refs: only `develop → main` is a promotion, and only a promotion is exempt.

- **Kotlin scope functions are refused by name *and* shape, and an imported extension outranks a
  receiver-member guess.** `m.let { }` on an imported `Modifier` minted
  `java:androidx.compose.ui.Modifier.let`, a member `Modifier` does not declare, because
  `finalize`'s "does the repository declare this?" check has no answer for a third-party receiver
  (#389). Refusing on the member name alone then cost true edges — `run`, `apply` and `use` are
  genuine members of `Runnable`, Gradle's `Project` and others, and a three-call probe fell from
  three `CALLS` to one — so the refusal now also requires the call to *pass a function*, which a
  scope function does and `r.run()` does not. `with` left the set: Kotlin's `with(x) { }` is
  top-level and never reached the check, so listing it could only ever drop a real member such as
  `java.time.LocalDate.with`. Separately, `m.padding(8)` now lands on
  `androidx.compose.foundation.layout.padding` — the extension the file imports, which is a name
  the source wrote — instead of the invented `Modifier.padding`. Kotlin corpus precision stays
  1.00 on every kind with `CALLS` recall 0.94, invention 0; new `scope_functions` corpus case and
  five tests in `tests/pkg/test_kotlin_fabrication.py`.

## 3.38.0 — 2026-09-18

Two field reports from a React Native engagement (CB-686, CB-760), plus what the NSS-1231 build
document had scored itself, all diagnosed to defects and each reproduced as a fixture. **CB-686:**
`sdlc feature --language auto` scaffolded a *Python* package into a React Native app, because
one build script under `ios/Pods` was the only `.py` the walk reached — and the walk reached
`ios/Pods` at all, so boost, glog and every React header were the repository's own symbols, with
`node_modules/` coming back in one header at a time through the symlinks CocoaPods leaves under
`Pods/Headers/Public`. **CB-760:** the run ended `VERDICT: FAILED` after nine test runs, five of
them refines editing the module to satisfy a test the run's own cover stage had written, and the
coverage probe had reverted `.gitignore` and `pyproject.toml` to ask whether the suite noticed.

### Fixed

- **Vendored trees stay out of the graph.** `Pods` joins `DEFAULT_IGNORE_DIRS` (the CocoaPods
  checkout, like `node_modules` and `vendor`). A symlinked file keeps its own path — provenance
  and module name alike — and is admitted only when its target stays inside the root and outside
  an ignored, dot or nested-checkout directory; before, `resolve()` followed the link and relabelled
  an ignored tree's header as a first-party file. CB-686's checkout: 0 nodes under `ios/Pods/` or
  `node_modules/`, the in-tree symlink still present. Blazor is detected as its own framework.

- **`--language auto` is what most of the source is.** One `toolchains.resolve_language(root,
  requested, store=None)` for the CLI, autorun and the runner: graph node counts per language when
  a store is in hand, else `profile.language_file_counts`; the language with the most source among
  those a toolchain exists for wins, ties keeping the old order. One stray `.py` is not a vote.
  CB-686's shape: `auto → typescript`. An empty repository still gets Python (`sdlc_shapes.py`).

- **The coverage probe asks only what a test could answer.** Both the whole-change proof and the
  per-file probe run over files with a testable suffix and a body (`.kt` and `.cshtml` join that
  set — Kotlin is a full codegen toolchain whose files the probe had been calling "not source",
  switching the coverage gate off for the whole run; `.kts` stays out with `pyproject.toml` and
  `.csproj`, because in a Kotlin repository it is `build.gradle.kts`, and probing a build script
  means stashing it and recording the build's collapse as proof that a test reaches it), and say
  what they excluded:
  `[coverage] excluded (not testable): .gitignore (not source), pyproject.toml (not source),
  __init__.py (empty)`. A scaffold file is never reported as "not exercised".

- **A cover-authored test the refine budget cannot satisfy is withdrawn, not chased.** When the
  tests budget dies on a failure the output attributes to a test the run's own cover stage wrote,
  that test is withdrawn (checked out from HEAD, or removed when new), the suite runs once more,
  and a green, type-clean result passes with `FeatureRunResult.coverage_withdrawn` naming it. The
  log says `[cover] withdrawn: … coverage not proven`, the ticket's journey carries `coverage
  withdrawn: …` on the outcome line, and the PR body says which test and why — so the run reads
  exactly as proven as it is. Only a file the cover stage itself *created* can be withdrawn, and
  only when a failing line names its full path: the spec's tests and the run's `author_tests`
  tests are never touched, and a red test elsewhere is still fatal.

- **The build document stops grading its own homework.** §12 scored "the brief agrees with the
  design" on NSS-1231 — "4 of 4" — while a heuristic design had taken its files *from* the brief's
  retrieval. The design now records `files_origin`; when it is `landing`, §4 says the files are the
  brief's own reading and §12 scores the row n/a, out of the denominator. Row 08's `stated` is
  earned, not trusted: a filed criterion is `stated` only when it matches a **whole line** of
  the ticket's text as intake read it — description, comments, attachments — bullet stripped,
  whitespace- and case-insensitively; otherwise `derived · model`, and the block counts how
  many. Containment would certify a narrowed rewrite ("deletion is cancellable" against a
  ticket saying "only for admins"), which is the failure the check exists to catch. The
  ticket text is persisted beside the plan, because the approval gate proves an approval by
  re-deriving the document and must see the same input. A hand-written `--spec` file has no
  ticket text: the block says so.

- **Retrieval reads a ticket's inflections.** CB-686 asked for "account deletion"; the screen is
  `DeleteAccountScreen`, and `deletion` matched nothing. `_tokens` now strips `-ies`→`-y`, plural
  `s`, one of `-ion`/`-ing`/`-ed`, then a trailing `e` — never below four characters — on the
  query and the names alike, so `deletion`/`deleted`/`delete` all read `delet` while `string`,
  `type` and `Reader`/`read` stay as they are. The NSS-1231 fixture ranks exactly as before.

- **Attachments are bounded together, and the body says so.** Five attachments at the per-file
  cut were 40,000 characters, which with ten comments passed the prompt cut and trimmed the
  *criteria* off the tail. All attachments together stay under 20,000; the one that crosses the
  line is cut with the reason, the rest are named and cost no request, and the `Attachments read`
  header states the budget used.

- Smaller: a bare filename a ticket names is resolved from one basename index per call rather
  than one tree walk per name; the test-example search reads any front-end suffix, not only
  `src/*.py`; a partial class declared twice in one file — every Razor component — emits one
  `CONTAINS`, not two; `test_builddoc` asserts the measured C# recall rather than a pinned `0.80`.

## 3.37.0 — 2026-09-17

Two field reports from a C#/.NET engagement, both diagnosed to defects rather than misuse and
both reproduced as fixtures. **NSS-1231:** `sdlc plan` proposed five files for an OAuth2 change;
one was right. The ticket had named the right file in its first sentence, and the spec writer's
paraphrase deleted it before retrieval ran — 40 identifiers destroyed, 14 generic words invented,
three of which produced three database models as "files to touch". **NSS-1209:** three of five
proposed files were flagged "absent from the knowledge graph", including the one that was right,
and the five `.razor` components the work landed in could not be proposed at all.

### Added

- **Blazor components enter the graph** (`.razor`, read by the C# front-end — no new grammar or
  extra). `pkg.razor` rewrites a component into line-aligned C#: `@using`/`@namespace`/`@inject`
  become the statements they stand for on the same line, markup becomes blank lines, and
  `@code`/`@functions` open a `partial class` named after the file. Every symbol carries its true
  `.razor` line. A component's module is its `@namespace` or, like an unnamespaced `.cs`, its
  path. Inline `@expression` references in markup are not read — a guessed reference is worse
  than a missing one. Corpus case `corpus/csharp/razor`: precision 1.00, recall 1.00 on every kind
  from the first extraction; C# `CALLS` recall 0.75 → 0.80 on the scoreboard.

- **Jira attachments are read, not only named.** On a single-issue fetch, each attachment the doc
  readers claim (markdown, text, PDF, HTML, docx, xlsx) is downloaded and read through
  `pkg.doc_source` — the same code `understand` uses. At most five, none over 2 MB, each cut at
  8,000 characters with the cut stated. Images stay named-only: reading one needs OCR, which lives
  on the opt-in `media extract` seam. A JQL scan never fetches them.

- **Retrieval carries its evidence, and the brief says when it is weak.** A landing site now
  records its score and the query words it shares, and is **weak** when every one of those
  words also names symbols in other files — `client` across four files; `api` + `client`. The
  investigation brief marks each weak hit with the words it rests on and says when every hit is
  weak. The heuristic design drops weak hits, so an all-weak ticket falls through to *"no files
  are proposed — locate the change before building"* instead of five confident wrong paths.

### Fixed

- **A file in a shared namespace was reported "absent from the knowledge graph".** C#, Java, PHP
  and Go key one `Module` node per namespace, package or directory, with the provenance of
  whichever file was walked first; the design's reference check matched paths against `Module`
  provenance only, so one file per namespace resolved and every sibling was called a possible
  hallucination — two sections after its own symbols had been printed with line numbers. The
  check now resolves a path against any grounded node's file. A module spanning several files
  says so in the blast-radius line, and its importers are summed once in the build document,
  not once per file.

- **The spec writer dropped the identifiers the ticket named.** `FeatureSpec` now carries the
  intent's `description` and `scope` — the fields the extractor keeps identifiers verbatim in —
  and a deterministic backstop appends whatever the source named and the summary dropped to
  `technical_notes`, labelled as carried, bounded at 24. The prompt already asked for this and a
  real model ignored it; a rule a model can ignore is not a rule.

- **The design read only the paraphrase.** Retrieval, the overview fallback and the stated-path
  reader now read every identifier-bearing spec field — title, summary, description, scope,
  technical notes, criteria. On the NSS-1231 shape the named file ranks first.

- **Naming a file in the ticket only worked for Python.** The path regex behind
  `design._stated_paths` and `codegen._paths_from` matched `src/…*.py` and `tests/…*.py`, so a
  .NET ticket writing `EBSOrderApiClient.cs` could not override a keyword guess. One shared
  `sdlc.source_paths` accepts every front-end suffix, either separator, and a bare basename
  resolved to its one location (two locations is a guess and is dropped). `_claims_a_change`
  inherits it: "Rewrote orchestrator/api_errors.py …" — the summary its docstring was written
  for — is now a claim.

- **Build document §1 labelled a model's paraphrase as a quote.** The section carried
  `stated — the ticket body, quoted` over the spec writer's summary. It now shows the intent's
  description when intake carried one, labelled `derived · model`, or the summary with a label
  saying the ticket's own words were not carried. [`build-document.md`](docs/specs/build-document.md)
  row 01 moves with it.

- **A Story that lands nowhere on a grounded graph said "nothing contradicts the code".** True
  and useless. It still proceeds — a feature can be about code that does not exist yet — but
  now carries a `localization` finding saying the design proposes nothing to build from and what
  to name. Bugs keep `UNLOCALIZED`.

## 3.36.0 — 2026-09-17

### Added

- **Kotlin comprehension and call graph — the 11th PKG front-end** (`[kotlin]` extra, P1+P2 of
  [the Kotlin roadmap](docs/specs/kotlin-support-roadmap.md)). Reads `.kt` into the universal
  facts: classes and objects in every flavour, companions folded onto the class they belong to,
  extension and top-level functions, constructor properties, enum entries, imports and
  inheritance. The call graph leans on the one thing Kotlin gives you for free — a declared type
  on every property and parameter — so `dao.getTopics()` resolves *exactly*, through the import
  map, with no inference.

  **What it refuses to guess:** a call on an unannotated `val`, anything reached through `it` in
  a lambda, a chained receiver, a callable reference, and a same-package function declared in
  another file. A local binding silences a bare call of the same name, because a Kotlin local
  genuinely can shadow one — unlike Java, where the two live in separate namespaces. Measured on
  a 263-file Android app: 2,221 `CALLS` edges, **0 fabricated**; corpus precision **1.00** on
  every node and edge kind, `CALLS` recall 0.94. (The first measurement of this said 2,169 edges
  and 0 fabricated. Maintainer review found that 72 of them *were* fabricated and invisible —
  see Fixed, below.)

  Kotlin ids share Java's `java:` namespace, so a mixed `.kt`/`.java` module — the normal Android
  layout — is **one** graph, with inheritance and calls crossing the language boundary. `.kts`
  Gradle scripts are read as build markers, not parsed as source. Routes, Room entities and
  codegen are later phases of the same roadmap, shipped further down this list.

  Install with `pip install 'synaptixs-spine[kotlin]'`, or `[languages]` for every front-end.

- **Kotlin reads the Android data layer, and an Android app can now be a cross-repo
  consumer** (P3 of the same roadmap). Room `@Entity` classes become `Entity` nodes named
  by their **table**, so `data_layer_link` reconciles them against a real `.sql` schema and
  the two describe one thing instead of two. Their columns come from the constructor
  properties, honouring `@ColumnInfo(name = …)`. `REFERENCES` come from `ForeignKey(...)`
  inside `@Entity` and from `@Relation`/`Junction` on a view class. DAO `READS`/`WRITES`
  are obtained by **parsing** each `@Query` with sqlglot — so a `@Query` holding
  `DELETE FROM topics` is a write, which no amount of reading the annotation's name would
  tell you — and are skipped entirely, never regexed, when the `sql` extra is absent.

  Retrofit is read as the **client** it is. `@GET("topics")` produces **no `Endpoint`**:
  an Android app calls routes, it does not serve them, and claiming otherwise would make
  the app look like a provider. The calls become `CONSUMES` candidates on the same
  side-channel the Python client scanner uses, so `pkg joins` can match them against
  another repository's endpoints — making a mobile app the first consumer in the
  multi-repo graph. A computed path yields nothing; a `baseUrl(...)` that is not a
  literal leaves the call path-only.

  Measured on a 263-file Android app: 6 entities, 12 `READS`, 11 `WRITES`, 4 `REFERENCES`,
  0 dangling edges, and 4 cross-repo join candidates.

- **A twelfth edge kind, `PROVIDES`, and Compose navigation as routes** (P4 of the same
  roadmap). `PROVIDES` records which implementation a Hilt/Dagger module actually wires
  behind an interface. `IMPLEMENTS` cannot say that — it is already true of every
  implementation, and the validation app has three for one interface, two of them test
  fakes — so the question "what breaks if I change this repository" had no answer before.
  `blast_radius` follows the new edge **outbound**, which is the only edge here that it
  does, because dependency injection means nothing calls an implementation by name: its
  dependents are reachable only through the interface it provides. Measured: the blast
  radius of a repository implementation goes from empty to the use-case, view model and
  worker that use it. Two providers for one type both keep their edge — qualifiers are
  recorded, never resolved, so the answer is "two providers", not a guess.

  Compose navigation becomes `Endpoint`s with the pseudo-verb `NAV`: `composable(...)`
  declares a route, `EXPOSES` names the single screen it shows, and `navigate(...)`
  `CONSUMES` it. `NAV` is not an HTTP verb, so in-app routes never join to real ones.
  Routes are read the way they are actually written — a constant imported from another
  module, or a literal with a constant interpolated into it — and a declaration and its
  call site pair even when they spell the parameter differently. A computed route yields
  nothing, and a lambda showing two screens gets the route but no `EXPOSES`.

- **Gradle `.kts` build scripts are read as a module graph** (P5 of the same roadmap), by a
  reader of their own rather than by the Kotlin front-end — a build script's "functions" are
  configuration, and parsing them as source would add a phantom component per module.
  `settings.gradle.kts` declares what the modules are; each script's
  `implementation(project(":core:model"))` declares what depends on what.

  This is what an Android app's architecture actually is, and nothing else in the repository
  states it. `state` now reports real components — `core/data`, `core/database`,
  `feature/foryou`, `sync/work` — where before every type in the app landed in a single area,
  because reverse-DNS package names share their first two segments. Its dependency arrows are
  the build's own edges (`app → core/data`, `sync/work → core/testing`).

  Version-catalog coordinates (`libs.foo`) produce nothing — they are third-party artifacts,
  not modules — and `project(someVariable)` produces nothing either. One honest limit:
  dependencies a module inherits from a `build-logic` convention plugin live in Kotlin
  source, not in the script, so the module list is complete while the edge set is a lower
  bound.

- **Ktor and Spring MVC routes — a Kotlin service is now a provider, and the Java front-end
  finally reads Spring** (P6 of the same roadmap). Until now every route a Kotlin program
  could show was one it *called* (Retrofit) or navigated to in-process (Compose). Ktor
  `routing { route("/api") { get("/topics") { … } } }` and Spring MVC controllers now become
  ordinary `Endpoint`s in the shared `java:endpoint:` namespace, so `pkg joins` can pair an
  Android app's Retrofit call with the Kotlin service that answers it.

  Spring is read by `pkg/jvm_routes.py`, which **both** JVM front-ends call. That is the
  point: the Java front-end has read JAX-RS since it shipped and had never read Spring — the
  framework most Java services actually use — so adding it for Kotlin alone would have left
  the same blind spot in the older front-end. Java now reads `@GetMapping`, `@PostMapping`
  and a verbed `@RequestMapping` on `@Controller`/`@RestController` classes, with the
  class-level prefix joined on, including `method = {RequestMethod.PUT, RequestMethod.PATCH}`
  naming two verbs at once.

  **What it refuses to guess.** The class stereotype is required, because Spring Cloud
  OpenFeign puts the *same* annotations on an interface to declare a client — reading those
  as endpoints would turn every consumer into a provider. A verb-less `@RequestMapping`
  yields nothing: `ANY` is a verb no client sends and the source never wrote. In Ktor, a
  `route(…)` group whose path is not a literal silences every route inside it, a verb call
  with a computed path yields nothing, and a `fun Route.orders()` route module that nothing
  in the repository mounts yields nothing — its path is genuinely unknown, and rooting it at
  `/` would be a guess that happens to be right in the common case. A handler written as an
  inline lambda gets an `Endpoint` and no `EXPOSES`; `get("/x", ::handler)` gets both.

  Ktor's difficulty is that `get` is one of the most common method names in Kotlin, so a
  route is read only when the callee is a bare identifier *and* it sits in a route context.
  Measured on the Ktor sample corpus (151 files): **67 endpoints**, every one of them
  anchored to a line that really is a route call. On spring-petclinic-kotlin: **18 endpoints,
  18 `EXPOSES`** — all 18 mapping annotations in the repository, none missed, none invented.

  One fix came out of it that helps every Kotlin framework reading. tree-sitter-kotlin 1.1.0
  parses a top-level annotation with parenthesised arguments as a standalone expression and
  strips the decorated declaration's modifiers, so `@AndroidEntryPoint class MainActivity`
  arrives with no annotations at all. 32 files across the validation repositories hit it.
  `annotations_of` now recovers them, which is what Room, Hilt, Retrofit and Spring all read.

- **Kotlin Multiplatform: source sets, and `expect`/`actual` as a contract** (P7 of the same
  roadmap). A multiplatform module declares one API in `commonMain` and implements it once per
  target, so `expect class Clock` and its two `actual class Clock` declarations share a
  package-qualified name — and therefore, under the package rule every other Kotlin declaration
  follows, one id. The graph de-duplicates by id, so two of the three used to vanish silently,
  taking their provenance with them.

  An `actual` declaration's id now carries the source set that declares it
  (`java:app.Clock@iosMain`), and the `expect` keeps the plain id. That way round is the point:
  common code is written against the contract, so a call from `commonMain` resolves to the
  thing the source actually names rather than to one platform's copy picked by walk order.
  `IMPLEMENTS` runs actual → expect — an `actual` fulfils a contract exactly as a class fulfils
  an interface, and the graph already had an edge for that.

  **What it refuses to guess.** The suffix keys off the `actual` keyword, never the directory:
  a plain helper in `androidMain` keeps its plain id, because renaming declarations that never
  collided would break every call into them. An `actual` whose `expect` is not in the scanned
  tree gets no edge — the contract may be real, but this run has not seen it. And a member of
  an `actual` class inherits the suffix through its parent without carrying the keyword, so it
  gets no contract edge of its own: Kotlin does not require `actual` on every member, and a
  platform class may add members the `expect` never declared.

  `state` now splits a module with a `commonMain` directory into its source sets, because in a
  multiplatform module that is the component boundary. Measured on KaMPKit: **2 components
  before, 7 after** — `shared/commonMain`, `shared/androidMain`, `shared/iosMain` and their
  test sets, beside `app`. Ordinary Android modules are deliberately left whole, and the
  validation app's 28 components are unchanged.

- **Kotlin/JVM codegen, and a Gradle test runner the Java front-end has been missing**
  (P8 of the same roadmap). `sdlc feature --language kotlin` works: it scaffolds a
  `kotlin("jvm")` Gradle project or writes into an existing one, and runs the tests through
  `./gradlew test`. Until now `--language kotlin` exited 2.

  **The runner closes a gap that was never Kotlin's.** `layout.py` has detected Gradle since
  the Java track shipped, but `MavenTestRunner` was the only JVM runner — so codegen on *any*
  Gradle project, Java included, could not run its tests. `GradleTestRunner` fixes both at
  once. It prefers a committed `./gradlew` over a `gradle` on PATH, because the wrapper pins
  the version the project was written against and downloads it on first use, and it runs the
  tests of the Gradle **modules the change touched** rather than the whole build — the same
  reasoning that made the Go runner module-aware. A repo with neither a wrapper nor Gradle
  fails with that message rather than reporting a suite that never compiled.

  **What the toolchain check does not look for is `kotlinc`.** The Kotlin compiler arrives as
  a Gradle plugin pinned in the build script, so a machine with a JDK and a wrapper can build
  Kotlin without a Kotlin install anywhere on it. Gradle itself is checked per-repository
  rather than on PATH, since a committed wrapper is what most real Kotlin projects ship.

  **Generated tests follow the repository, not the language default.** A Kotlin project may
  use `kotlin.test` or JUnit 5, and the two are not interchangeable: the Spring validation
  repo declares `junit-jupiter` and no `kotlin("test")`, so a generated `import kotlin.test.Test`
  does not compile there. The layout now reads which library the build actually declares and
  the prompt follows it — measured at `junit5` on spring-petclinic-kotlin, `kotlin.test` on the
  Android app, neither on KaMPKit.

  Preflight runs `./gradlew check`, but only when ktlint or detekt is configured: without a
  linter `check` is `test` again, and a preflight that fails for the same reason as the tests
  reads as two independent problems. Otherwise it reports *skipped*.

  Proven against real Gradle 8.13 / Kotlin 2.0.21, not a stub: a greenfield scaffold compiles
  and tests **green**, then **red** when an assertion is broken; and a class placed into
  spring-petclinic-kotlin's existing package runs **3 tests, 0 failures**, re-run
  independently with `--rerun-tasks`.

- **Android codegen: the right module, and the unit-test task that actually exists**
  (P9 of the same roadmap). `sdlc feature --language kotlin` now works on an Android
  repository, which differs from a plain Kotlin/JVM one in two ways that both decide whether
  the generated code compiles at all.

  **There are no sources at the root.** A real Android app is dozens of Gradle modules; the
  validation app is 27 and its root holds only build scripts. Placement is therefore keyed on
  the target package: the module that already contains it wins, otherwise the module whose own
  package is the longest prefix of it, so a brand-new `…core.data.pricing` lands inside
  `core/data` — under that module's own `src/main/java`, because Android keeps Kotlin in the
  Java source root and a second root would need a build-script change to compile. When nothing
  matches, the run **stops** and lists the candidate modules instead of guessing. A wrong
  module is not a crash: it is a file that compiles, a test that passes, and a change nobody
  finds.

  **`testDebugUnitTest` is not guaranteed to exist.** It is the documented Android unit-test
  task, and on the validation app Gradle rejects it as *ambiguous* — every library module
  inherits the `demo`/`prod` flavour dimension from a convention plugin, so the real tasks are
  `testDemoDebugUnitTest` and `testProdDebugUnitTest`. Flavour names are computed in Kotlin
  inside a separate included build and cannot be read off a build script, so Spine does not
  predict them: it asks for the documented task and, when Gradle rejects it, takes the
  replacement from Gradle's own list of candidates and remembers it for the rest of the run.
  A plain Kotlin/JVM module in the same build still uses `test` — the task follows the module,
  not the repository.

  A missing Android SDK is reported **before** generation as one sentence naming `ANDROID_HOME`,
  rather than as an Android Gradle Plugin failure minutes into a build, and it says that no
  emulator is needed — because instrumented tests are never run. The prompt rules them out
  explicitly (no `androidTest/`, no Espresso, no `AndroidJUnit4`) and asks for an explicit
  `TODO: UI test` where UI verification is genuinely required, so the gap is stated rather than
  hidden.

  Two detection bugs came out of this, and both reach past Android. The test-library probe read
  only `build.gradle{,.kts}`, and the validation app declares `kotlin("test")` exactly once —
  inside `build-logic/`. No build script in the repository names a test library at all, so the
  probe returned "unknown" and was correct only by luck. And the answer is not a property of the
  repository in the first place: `core/data` gets `kotlin("test")` from a convention plugin while
  `core/model`, a plain `id("kotlin")` library in the same build, declares no test dependency at
  all. It is now resolved per module — from what that module's own tests already import, else
  what its build script declares, else what its convention plugins declare for it — and when the
  answer is genuinely "nothing", the prompt says so and names the dependency to add rather than
  emitting an import that cannot resolve.

  Proven against real Gradle 8.1 / AGP 8.1.0-beta01 / JDK 17: a repository function and its
  test placed into the Android app's existing `core/data` package run **4 tests, 0 failures**,
  verified independently with `--rerun-tasks` (197 tasks, nothing from cache); breaking an
  assertion turns it **red** with the failing test named. A second feature placed into
  `core/model` — a plain Kotlin/JVM module in the same repo — runs as `:core:model:test` in the
  same invocation that runs `core/data` as `:core:data:testDemoDebugUnitTest`, which is the
  mixed-build case the per-module rules exist for.

- Every brief states **what it did not establish**. `investigate` and `root-cause` now close
  with a `Not verified` section derived from the run itself — that retrieval was lexical
  rather than semantic, how many matches were elided, that no `episteme/` was read, that
  hypotheses were ranked from static evidence and never reproduced. It is conditional on
  real state, not a disclaimer: a brief with nothing to declare says so instead.


- **The roadmap-currency gate now reads the half of a roadmap nobody was checking, and runs in
  CI.** Every language-track roadmap carries a living phase table whose rule is that Status,
  Started, Finished and Evidence move in the same commit as the work. The rule names the
  *table*, so the table is what stayed maintained: this repository's Kotlin roadmap carried
  *"P0–P5 done · P6–P11 not started"* at the top of the file while P6, P7 and P8 sat finished and
  evidenced twelve lines below it — for three phases, with the gate green throughout.

  Two checks close it. A roadmap's own `**Status:**` phase claim is compared against its table's
  DONE rows, in both directions, and against `SPEC-INDEX.md`'s claim about the same spec. Both
  read **structured** claims on both sides — a set of phase ids, not prose — which is what
  separates them from the generalised spec-status gate that was measured at 33% precision and
  withdrawn: this fires only where both sides use the `P0–P9 done` shorthand, so a status word
  appearing in ordinary prose is never read. It caught a live disagreement on its first run.

  A third defect surfaced while adding them, and it is the more interesting one: the pattern
  reading the status line was anchored to a single line ending in a period. Every roadmap here
  wraps that line, so it matched **nothing at all** — and the checks built on it had been
  counted as passing on every green run. A check that is silently reading nothing is worse than
  a check that is missing, because the missing one is visible in the list.

- **Reverse-DNS namespaces group into real components instead of one.** An *area* is meant to be
  a component, derived from a module's first two namespace segments. Under a reverse-DNS
  convention those two segments name the organisation, so the Android validation app put **258
  of its 273 first-party types into a single area called `com.google`** — an architecture
  diagram with one box in it. The prefix every module shares carries no information, so it is
  dropped before grouping: the same app now renders **39 areas** — `core.data`, `core.database`,
  `feature.topic` — and Java and C# repositories under the same convention benefit identically.

  Two things kept it honest. The rule as specified — the prefix shared by *every* first-party
  module — does not survive the repository it was specified against: one module declaring
  `package androidx.test.uiautomator` takes the common prefix of all 71 down to nothing. It is
  coverage-based instead, with the threshold placed in a measured gap (the vendor prefix covers
  94.4% of modules; the next segment falls to 57.7%). And it must not fire where namespaces are
  already shallow — on this repository the deepest majority prefix is `orchestrator` at 48.1%,
  under the threshold, so grouping is measurably unchanged and `orchestrator.pkg` stays the area.

- **Removed `Toolchain.conventions_skill_id`, which ten toolchain rows filled in and nothing
  read.** A skill reaches codegen through the catalog planner's capability selector, an entirely
  separate mechanism — which is how a conventions skill could be named on a toolchain while the
  skill itself did not exist, with every test green. The invariant it appeared to guard is real
  and now sits on the live path: a registered `<language>-conventions` capability must resolve to
  a defined skill. Not every language has one, and none is required — SQL ships without.

- The build document's blast-radius paragraph stated four things it could not back, all
  of them visible on any non-Python repository.
  - The caveat cited an issue key from Spine's own tracker, in documents describing other
    people's repositories. The sentence already explains the limitation; the key only told
    a reader to look up something they cannot reach. Two `--help` examples leaked the same
    convention and now use `PROJ-123`.
  - Measured `CALLS` recall was read from `--language`, a *codegen* flag that
    `orchestrator sdlc plan` defaulted to the literal `python` — so a C# repository was told
    Python's 0.73 against a graph Python had not touched. The blast radius now carries the
    front-ends that actually built it, scores each of them, and names an unmeasured
    front-end without a figure rather than dropping it.
  - Containment printed a module count and then listed eight names, so a reader who counted
    got a different number. Both branches now state what they elided, and say "at least N"
    when the names were already capped upstream.
  - Test detection was four Python-shaped clauses applied to language-neutral prose, so
    `UnitTests/…Tests.cs` counted as product code and inflated the containment figure. The
    rule is now segment-based across Python, .NET, Java and JS/TS conventions, and strictly
    additive — nothing that was a test before stops being one.
- `orchestrator sdlc plan --language` defaults to `auto` (was the literal `python`) and
  refuses an unsupported value, matching `sdlc feature`. It selects the codegen prompt, the
  layout and the test environment, so the old default gave non-Python repositories
  Python scaffolding without saying so.

### Changed

- The briefs share one section vocabulary (`sdlc/brief.py`). Five modules had each grown
  their own and drifted to two spellings of "where it lands" and three of "next step";
  `evidence.py` and `builddoc.py` re-emitted the others' labels by hand, so a rename in one
  silently desynchronised a document nobody edited. Sections are now split by **provenance**:
  a deterministic surface cannot render a verdict, a recommendation or an options table —
  `investigate` and `rca` must stay re-derivable from the graph, and the argument on top of
  them belongs to `design`. **One user-visible rename:** `## Suggested next step` is now
  `## Next step`.
- A root-cause report whose fix approach was written by a model now says so per section
  (`derived · model` vs `derived · deterministic`) and types the document accordingly,
  rather than presenting model text inside a deterministic report.
- `scripts/brief-sections.py --check` runs in CI: a brief that renders a section the
  vocabulary does not own, or renders them out of order, fails the build.

### Fixed

- **Kotlin: eleven fabrication paths, found in maintainer review** (§11 of
  [the Kotlin roadmap](docs/specs/kotlin-support-roadmap.md)). One defect repeated: a reader that
  could not answer a question returned a *plausible* answer instead of none, and the answer was
  minted as an `external` placeholder — so nothing dangled, `pkg verify` reported clean, and the
  invention oracle saw nothing. `s.uppercase()` became a call to a `String` class inside the
  caller's own package; `topic.let { }` became a member of `Topic`; `route("/api/${cfg.version}")`
  became the path `/api/`; `@Provides fun x(): Set<OkHttpClient>` provided a `Set` class in the DI
  module's package; `@Entity(tableName = TOPICS)` claimed the class name as the table.

  A target that was *assumed* rather than read is now checked in `finalize` against what the
  repository actually declares, and dropped when nothing does. Measured on the validation app:
  **72 fabricated edges over 36 invented ids → 0**, and 46 invented placeholder nodes gone. The
  graph went *up*, not down — the same check that drops a guess recovers the true edge the guess
  displaced, 126 of them here (`CALLS` 2,167 → 2,221) — and corpus `CALLS` recall is **0.92 →
  0.94** with precision still 1.00.

  **Narrowed, not closed.** The check applies to targets the repository declares; a second
  review round found the same rule applied one step short for imported and undeclared types.
  Tracked in [#397](https://github.com/synaptixs/spine/issues/397).

- **The invention gate covered one language.** `score_invention` had only ever been pointed at
  this repository, which is pure Python, so its per-language map held one row and every other
  front-end's zero meant "never examined". It now also runs over the corpus fixtures — committed,
  offline, one per language — and every front-end has a row gated at zero.

- **`state` and `understand` drew different architectures for the same commit.** They voted for
  the shared namespace prefix over different module sets (tests included, tests excluded), and a
  prefix is decided by a majority of the voters. Measured on this repository: 103 area labels one
  way, 386 the other. One definition now serves all three callers. Two defects behind it: area
  `coupling` was computed without the prefix, so on a reverse-DNS repository the whole "System
  architecture" section rendered empty; and `tested_areas` was structurally `0` for **any** Gradle
  repository, so a fully tested one reported "no automated tests detected".

- **A Spring `@Value` placeholder was read as a literal path in the Java front-end** —
  `@GetMapping("${api.base}/topics")` produced that endpoint verbatim. The rule was stated in
  `jvm_routes`'s docstring and enforced only at the node type. Now shared by both JVM front-ends.

- **Java codegen on a Gradle project still ran `mvn test`.** `GradleTestRunner` shipped with only
  Kotlin wired to it, against a build with no `pom.xml`; the Java toolchain row now selects its
  runner from the build tool the layout already detected.

- **Codegen safety**: an emulator task (`connectedDebugAndroidTest`) was selectable by the
  variant-task preference, repo-controlled Gradle output could reach `argv` as a flag, and
  brownfield placement could invent a package for a repository that already had one.

- The build document's blast-radius paragraph stated four things it could not back, all
  of them visible on any non-Python repository.
  - The caveat cited an issue key from Spine's own tracker, in documents describing other
    people's repositories. The sentence already explains the limitation; the key only told
    a reader to look up something they cannot reach. Two `--help` examples leaked the same
    convention and now use `PROJ-123`.
  - Measured `CALLS` recall was read from `--language`, a *codegen* flag that
    `orchestrator sdlc plan` defaulted to the literal `python` — so a C# repository was told
    Python's 0.73 against a graph Python had not touched. The blast radius now carries the
    front-ends that actually built it, scores each of them, and names an unmeasured
    front-end without a figure rather than dropping it.
  - Containment printed a module count and then listed eight names, so a reader who counted
    got a different number. Both branches now state what they elided, and say "at least N"
    when the names were already capped upstream.
  - Test detection was four Python-shaped clauses applied to language-neutral prose, so
    `UnitTests/…Tests.cs` counted as product code and inflated the containment figure. The
    rule is now segment-based across Python, .NET, Java and JS/TS conventions, and strictly
    additive — nothing that was a test before stops being one.
- `orchestrator sdlc plan --language` defaults to `auto` (was the literal `python`) and
  refuses an unsupported value, matching `sdlc feature`. It selects the codegen prompt, the
  layout and the test environment, so the old default gave non-Python repositories
  Python scaffolding without saying so.

## 3.35.0 — 2026-09-16

### Added

- `orchestrator pkg export --format cypher` — a sixth projection, loading the graph
  into Neo4j, Memgraph or any openCypher store for the traversal questions the flat
  projections cannot answer: transitive closure, cycles, shortest path. Relationships
  carry `file`/`line` in their MERGE key deliberately, because one `CALLS` fact is one
  call site while a graph database identifies a relationship by `(start, type, end)`
  alone — the idiomatic form drops 4,054 of 45,058 edges (9.00%) on this repository.
  The emitted row count reconciles exactly against `--format json`'s edge count. See
  the [projection table](docs/specs/knowledge-graph-architecture.md).
- Optional C/C++ semantic `CALLS` enrichment with `[clang]`, included in `[all]`.
  Wheel-bundled libclang adds edges only between existing grounded symbols and
  reports recovered call sites and parsed translation units. Ordinary overloads
  retain the graph's existing name-based IDs. Synthesized repository flags ignore
  compilation databases and system SDKs; standard-library resolution remains
  unavailable. See the [parser design](docs/specs/parsing-and-the-pkg.md#optional-clang-semantic-pass)
  and [validation results](docs/evals/clang-semantic-validation.md).

### Changed

- Clang derives additional repository include roots from literal include paths,
  preserving existing precedence and refusing ambiguous or excluded resolutions.
  Local lambda/class scopes can no longer collapse to their enclosing function;
  actual declaration parents must agree with the existing grounded identity.
  See the [Step 3b evaluation](docs/evals/clang-semantic-step3b.md).

- Clang recovers calls inside grounded C++ file-static functions, exact destructors
  and call operators while retaining caller scope and source-file checks.

- Clang verifies the enclosing function against the grounded caller before adding
  a call. Mismatched scope, macro bodies and unrelated entrypoints are refused;
  nodes and IDs remain unchanged. See the [source audit](docs/evals/clang-semantic-correctness-audit.md).
- `state` graph statistics count incoming calls in one edge pass, preserving
  existing counts while avoiding a full edge scan per function on large repos.
- Headers ending in `.h` reached through literal C++ includes use the C++ CST
  parser, including transitive includes. Other `.h` files retain C routing.
- Semantic validation reports why pending calls remain unresolved. Full source
  ranges keep nested calls with the same starting offset distinct.

## 3.34.2 — 2026-09-14

### Added

- **A generic plan skeleton, `docs/specs/templates/track-plan.md`.** Every development plan
  re-derived its shape from whichever roadmap was nearest; the only skeleton was bound to
  adding a language. The generic parent carries §5, a housekeeping block identical across
  tracks (branch off `develop`, the gate, the four generated-artifact checks, the docs audit,
  `episteme/`, rebase-never-re-run, one MR), and §6.1, which assigns every tooling site
  hard-coding a name a track renames to the phase that must carry it, in the same commit.
  `language-track.md` now specialises it ([#376](https://github.com/synaptixs/spine/pull/376)).

### Changed

- **`scripts/roadmap-status.py` accepts paths.** Plans for non-language tracks live outside
  the checkout, so nothing checked their currency. Naming a file applies the same checks to
  it; `check_indexed` skips out-of-tree files by construction, and a relative link falls back
  to the repository root before being reported.

## 3.34.1 — 2026-09-14

### Changed

- **Documentation has one home per task.** [AGENT_GUIDE.md](AGENT_GUIDE.md) replaces
  the two host guides, which remain as redirect stubs; its MCP inventory is generated
  and checked from tool metadata. [SETUP.md](SETUP.md) owns installation, credentials
  and troubleshooting, [USER_GUIDE.md](USER_GUIDE.md) covers the everyday build, and
  [OPERATIONS.md](OPERATIONS.md) holds the pipeline/dashboard/MCP walkthrough. README
  starts with the worked example and absorbs the former capability catalog. Audits,
  the reviewer matrix, release notes and CI checks follow the new file set.

## 3.34.0 — 2026-09-13

- docs: triage the documentation audit's INFO lines — the benchmark's "(there are nine now)", the call-graph language list in `USER_GUIDE.md`, and `regression`'s prerequisite in `CLI_REFERENCE.md` all predate Go/PHP/Perl `CALLS` extraction.

- docs(pkg): the accuracy claim in `parsing-and-the-pkg.md` still read "in all eight languages" two front-ends after PHP and Perl were being scored, and its measured table carried a TypeScript `CALLS` recall of 0.50 against a committed 0.86. All three figures now derive from `scoreboard.json` through `scripts/state-numbers.py`, so the eleventh front-end fails `--check` instead of drifting.

- test(plugin): derive the pitch's language list from the codegen registry instead of a hand-written tuple, and name every language on the operator home page — which still advertised six ([#366](https://github.com/synaptixs/spine/pull/366) fixed the manifests; nothing kept them fixed).

- feat(sdlc): run configured Perl::Critic checks after changed-file syntax validation and before prove; preserve Temporal injection, remove registry import cycles, finish SQL registry dispatch, and reject symlinked Perl lib roots ([C-5](docs/specs/perl-codegen-roadmap.md)).

- test(sdlc): independently prove Perl brownfield codegen against an existing distribution; owning and full prove suites pass, with unchanged regression gaps ([C-4](docs/specs/perl-codegen-roadmap.md)).

- fix(sdlc): align Perl prompts with new-file/anchored-edit payloads and recognize TAP .t files as graph-covering tests; greenfield live proof and clean-checkout evidence are recorded in [C-3](docs/specs/perl-codegen-roadmap.md).

- feat(sdlc): add Perl codegen layout, scaffold, optional CPAN installation, observed conventions and prompts; compile changed sources and run owning/full prove suites, with real green/red integration proof ([C-2](docs/specs/perl-codegen-roadmap.md)).

### Added

- **Perl codegen C-1: shared toolchain registry.** Existing language layout, scaffold,
  environment, runner, availability, preflight and prompt selection now come from one
  registry. Temporal activities retain their injected adapters and worker defaults.
  Perl enablement remains reserved for C-2 ([roadmap](docs/specs/perl-codegen-roadmap.md)).

- **Perl codegen C-0: dispatch characterization.** Tests pin C# runtime targeting, separate C/C++ toolchain guards, Meson brownfield selection, and PHP layout/convention guidance. The committed mutation measurement improves from **4/8 to 8/8**. Perl codegen remains disabled until its complete machinery lands ([roadmap](docs/specs/perl-codegen-roadmap.md)).

- **Perl comprehension (P1 of the Perl support track).** The 10th PKG front-end:
  `pkg/perl_extractor.py` maps `.pl`/`.pm`/`.t` source onto `Module` (always path-keyed —
  a Perl file may hold zero, one, or several packages), `Type` (every `package`/5.38
  `class` — a package is both namespace and class, D2), `Function` (subs and 5.38
  methods), and `Field` (Moo/Moose `has`, `Class::Accessor` `mk_accessors`, 5.38 `field`).
  Inheritance resolves across five literal spellings into `IMPLEMENTS` (`use parent`/
  `use base`, `our @ISA`/`push @ISA`, Moo/Moose `extends`, `use Mojo::Base`, 5.38
  `:isa(...)`) — a computed `@ISA` yields nothing, never a guess. `CALLS`, framework
  routes, and codegen are later phases of the same track
  ([perl-support-roadmap.md](docs/specs/perl-support-roadmap.md)); `perl` stays out of
  `SUPPORTED_LANGUAGES` until the codegen track lands. `pip install 'synaptixs-spine[perl]'`.
- **`scripts/parse-census.py`** — parses every file of a language with its grammar and
  reports the recall ceiling (files with a parse `ERROR`, lines inside `ERROR` spans,
  declaration counts by CST kind) independent of any one front-end's extraction logic;
  reusable by every future language track for its own parser-choice evidence.
- **Perl `CALLS` (P2 of the Perl support track).** Six call shapes, precision-first:
  `$self`/`$class`/`__PACKAGE__`/`shift` calls to a sibling sub or field, `SUPER::` to the
  first D5-resolved parent, a qualified `X->m()`/`X::f()` call, and a bare call resolved
  same-file, via an explicit `use X qw(f)` import, or — verified-only, never guessed — a
  first-party `@EXPORT`/`@ISA` chain. Built on a new shared
  `pkg/finalize_names.py` whole-repo name-resolution helper (`declared_ids` +
  `resolve_or_drop`); Perl's D10 fallback is its first implementation, reusable by C#/PHP
  when next touched. `corpus/perl/{plain,instance_calls,exporter_default,isa_spellings,
  legacy_main,multi_package}` added, labelled from source before scoring.
- **Perl routes + typed receivers (P3).** `pkg/perl_routes.py`: Mojolicious full-app route
  chains (`$r->get('/x')->to(...)`, `under('/api')` groups) and Mojolicious::Lite/Dancer2's
  shared bareword DSL (`get '/x' => sub {...}` / `\&handler`) become `Endpoint` + `EXPOSES`
  — a verb-less/`any` registration or a computed path emits nothing, a closure handler
  emits an `Endpoint` with no `EXPOSES`. `$obj->m()` also now resolves when `$obj` holds a
  literal same-sub constructor (`my $log = Shop::Log->new`). `corpus/perl/mojo_routes`
  added.
- **Perl DBIx::Class data layer (P4).** `pkg/perl_orm.py`: `__PACKAGE__->table('orders')`
  is the literal marker for a Result class (never guessed from base class or shape),
  `add_columns(...)` (bareword list or `name => {...}` hash, keys only) become `Field`s on
  a new `Entity` (`perl:entity:Dotted.Path`, alongside the package's own `Type` node), and
  `belongs_to`/`has_many`/`might_have` become `REFERENCES` — `src` always the declaring
  entity, a target this repo never declares still gets the edge, to an external `Entity`
  placeholder. Runs in `finalize()`, like `CALLS`: a relation target's local-vs-external
  classification needs every file's declarations known first. `data_layer_link.py` needed
  no Perl-specific wiring. `corpus/perl/dbic` added.
- **`scripts/roadmap-status.py`** — the roadmap-currency gate for every language track's
  phase table (§8.1 of `docs/specs/templates/language-track.md`): a DONE phase with no
  Evidence/Finished, a codegen phase started before its named comprehension dependency is
  DONE, a document's own top Status line contradicting its own phase table, an unindexed
  roadmap, or a broken relative link. Deliberately narrower than the free-prose spec-status
  classifier `STATE-OF-SPINE.md` §8 tried and withdrew at 33% precision — every check here
  reads structured table cells or compares a document against itself.
- **`scripts/validate-frontend.py`** — the real-repository smoke test as a script (§8.2):
  shallow-clone, extract, verify, the `state` stack line, node counts by language, top
  unresolved import targets, delete. Reuses the same SSRF-guarded `resolve_repo_source`/
  `materialize_repo_source` the CLI's repo-argument resolution already uses.
- **`docs/specs/templates/language-track.md`** — the shared roadmap skeleton (§8.4),
  generalized from `perl-support-roadmap.md`'s own section shape for the next language
  track to start from instead of re-deriving it.

### Fixed

- **Perl `SUPER::m()` could fabricate a method id nothing declared.** Found live on the
  first full real-repo run this track completed (`validate-frontend.py` against
  `mojolicious/mojo`): 10 dangling `CALLS` edges, all `SUPER::new()` into a `Mojo::Base`
  -style parent that never declares an explicit `sub new` (the constructor comes from
  further up the chain). `perl_extractor.py`'s `SUPER::` handling now reuses the
  already-verified `Shop::Log->new` policy — call the resolved `Type` itself when it
  doesn't declare the method, rather than guessing `<base>.<method>`. `corpus/perl/
  super_calls` added; no case had exercised `SUPER::` before this.
- **Perl `belongs_to`/`has_many`/`might_have` could target the wrong entity (P6 review).**
  `perl_extractor.py` took "the first string literal in the argument list" as the DBIx::Class
  relation target — correct only when the relation name was a bareword
  (`belongs_to(customer => 'App::Schema::Result::Customer', ...)`). A quoted name
  (`belongs_to('customer', 'App::Schema::Result::Customer', ...)`, equally valid
  DBIx::Class) made it pick the *name* as the target, fabricating a `REFERENCES` edge to a
  node the source never declares. Now reads DBIx::Class's own positional signature —
  the target is always argument position 1, regardless of how the name is spelled.
  `corpus/perl/dbic` extended to exercise the quoted spelling.
- **`scripts/validate-frontend.py` could crash on one bad repo and abort the rest of an
  unattended multi-repo run (P6 review).** The clone/extract/verify/state block had no
  exception handling; a clone timeout or an extraction edge case on one URL took the whole
  run down with a raw traceback. Now a per-repo boundary: reports the failure and moves to
  the next URL, matching the script's own purpose.
- **`scripts/roadmap-status.py` could silently drop a whole document from every check (P6
  review).** A malformed first data row after a matched phase-table header (wrong column
  count) made the document vanish from the results with no diagnostic. Now reported
  explicitly as its own finding instead of failing silent.
- **`scripts/roadmap-status.py` could silently drop every row *after* a malformed one,
  even mid-table.** A live instance: `perl-support-roadmap.md`'s own P5 evidence quoted
  an example table row inside a code span, and the literal `|` characters in it split
  that row into 10 cells — the parser treated the bad row as the end of the table,
  dropping P5 and P6 with no diagnostic. `--check` stayed green because every check it
  ran was against the 4 rows it *was* handed. Found only when asked to render the table
  and two phases were missing. A malformed row is now skipped, not treated as
  end-of-table, so later valid rows are no longer swallowed by an earlier bad one — and
  the skip itself is reported.
- **A second, deeper maintainer review of the Perl track found and fixed 11 real
  precision/correctness bugs, all confirmed against the actual code (several against real
  Perl semantics probed with tree-sitter-perl directly).** `PerlExtractor` leaked facts
  across repos in a multi-repo run (`finalize()` never reset its own accumulators);
  `use X qw(f)` imports were treated as file-scoped when Perl scopes them per-package;
  D10 wrongly resolved a bare call through `@ISA` (real Perl never dispatches a bare call
  through inheritance); `&Pkg::f()` embedded a literal `&` in its emitted id instead of
  being excluded like every other ampersand-form call; a fully-qualified `sub
  Pkg::Sub::name {}` kept the wrong owning package; Dancer2's `del` verb uppercased to
  "DEL" instead of the real HTTP method "DELETE"; a Mojolicious route registered from a
  helper method resolved its controller against the helper's own package instead of the
  app class; a hyphenated controller name (`foo-bar`) produced a literal `::` next to
  dots in an id, never matching the real declaration; five of nine corpus cases listed an
  external placeholder as an expected node, silently deflating their own recall numbers
  (the `plain` control scored 0.33/0.75 against its own claimed "1.00/1.00 by design");
  Perl was never added to `scoreboard.json`, so a regression to 0.00 precision would not
  have failed a build; and `exporter_default`'s own fixture set `@EXPORT` without
  inheriting `Exporter`, so the labelled edge wasn't true of the fixture's own code — D10
  now verifies the inheritance too. Every corpus case now scores exactly 1.00 precision
  and 1.00 recall on every node and edge kind except one permanent, predicted known gap.
  Also closed: a role consumed via `with 'Role'` used to feed the same list `SUPER::`
  reads as real inheritance (a role never participates in `SUPER::` dispatch in real
  Perl); `has_one` was missing from the recognized DBIx::Class relations; `use parent`/
  `use base`/`use Mojo::Base` now also emit `IMPORTS` (they really do load the named
  class) except when `-norequire` says otherwise; a whole-repo name-resolution helper
  rebuilt its declared-id set on every single call, genuinely quadratic in repo size, now
  computed once per pass; and a unit test claiming to exercise the `require`-path-join
  end-to-end placed the requiring script under `bin/` (walker-ignored), passing by
  accident for a reason unrelated to what it claimed.
- **`$self->SUPER::m()` always resolved through the first parent in `@ISA`, not the
  first parent that actually declares the method — a re-review of the Perl track found
  it live: `use parent qw(A B)` plus `SUPER::bmeth()`, declared only on `B`, emitted a
  `CALLS` edge to `A` and none to `B`.** Perl's default MRO is depth-first, left to
  right across `@ISA`; the resolver now walks every resolved parent in that order and
  stops at the first one that declares the method, falling back to the old "call the
  type" backstop only when exactly one parent is resolved, and skipping the call
  entirely (rather than naming a guess) when several parents are resolved and none of
  them declares it. Single-parent shapes are unaffected — `corpus/perl/super_calls`
  still scores 1.00 precision/recall on every kind unchanged.

## 3.33.2 — the SDLC runs as a pipeline, and PHP builds

### Added

- **The SDLC as a reusable CI pipeline.** `.github/workflows/spine-sdlc.yml` is a
  `workflow_call` workflow another repository references in a few lines: a `plan` job
  that runs intake → investigate → validity → design from a hand-written spec with **no
  credentials**, publishing the build document, and a `build` job bound to a GitHub
  Environment whose required reviewers *are* the plan gate — their decision is recorded
  with `sdlc approve` before `sdlc autorun` runs, safe by default, `live: true` to open
  the PR. Three inputs name what a single-package repository lets you conflate — the graph
  root, the target directory, the git repo — so a monorepo package, a multi-repo workspace
  (a committed `.spine/repos.yaml` plus a `checkouts` list of sibling clones) and a
  superproject with submodules all plan; delivery into a subdirectory or a submodule is
  refused before a model runs, naming the design record that explains why.
  `scripts/sdlc_shapes.py` builds all four shapes with real git and runs the plan stage on
  each in CI, asserting byte-identical output; `sdlc-dogfood.yml` runs the workflow on this
  checkout. Contract tests pin the interface: no expression inside any script, the plan job
  never sees the model key, no secret required, the gate never bypassed.
- PHP code generation for Composer packages and legacy repositories: PSR-4 scaffolding,
  PHPUnit configuration-aware test placement, checksum-pinned PHAR fallback, changed-file
  syntax checks, modern PHPUnit prompts and sampled conventions. The runner refuses empty
  test runs and tests only files touched by the change. See the
  [PHP codegen roadmap](docs/specs/php-codegen-roadmap.md).

### Fixed

- **A nested git checkout is a boundary, not a subdirectory.** Measured on a superproject
  with one submodule: both walkers descended into it, and declaring both in
  `.spine/repos.yaml` scoped every symbol twice (`py:lib@lib.core.helper` and
  `py:super@libs.lib.lib.core.helper`) with nothing flagging it. `is_nested_repo()` now
  stops code and doc extraction at any child holding a `.git` entry — file *or*
  directory, because a submodule's is a file; `from_mapping` refuses a declared root nested
  inside another unless it is a checkout of its own; single-repo extraction is byte-identical.
- **Worktrees carry their submodules.** `WorkspaceManager` clones with
  `--recurse-submodules` and runs `submodule update` in every worktree and after every base
  refresh — `git worktree add` and `reset --hard` both leave submodule directories empty, so
  the test environment could not import them and codegen wrote into folders git did not own.
- PHP route discovery no longer overflows Python's call stack on deeply nested expressions,
  as measured on the public aiemr repository.
- Plan approval revalidation uses the selected language, so a PHP plan is not compared
  against a regenerated Python plan, and includes the same measured run history as
  the CLI when checking an approved plan.
- Semantic review includes PHP source, PHPUnit XML, Composer lockfiles and scaffold
  dotfiles, so it can inspect generated PHP configuration.

## 3.33.1 — the documents say only what is true, and the help screen shows only what you need

### Changed

- **The maintainer review skill leaves the repository; its reusable halves stay.**
  The Claude Code `/review-pr` skill and the `pr-reviewer` subagent (#337) are
  local tooling and are no longer tracked — `.gitignore` covers `.claude/` again.
  What any maintainer or CI can use without an assistant moves to neutral paths:
  the documentation audit to `scripts/docs_audit.py`, beside the other gates, and
  the docs matrix and the language-front-end checklist to `docs/reviewing/`.
  CONTRIBUTING describes the review checklist in prose and points at those.
  Nothing a user installs is affected — the wheel never carried `.claude/`.

### Added

- **Eight documented commands get their first CLI-level test.** `sdlc explain`,
  `sdlc workflows`, `sdlc workflow`, `mcp call`, `mcp ingest-db`,
  `sdlc address-review`, `sdlc baseline` and `sdlc remediate` each called a
  tested engine, but no test invoked the *command* — a renamed flag or a broken
  import would have shipped. One `CliRunner` test each, the engine patched where
  it needs git, `gh`, a model, a server or a backend, and run for real where it
  is deterministic and local (the profiles, the baseline gate, the schema
  facts). The last item of the CLI command review.

### Changed

- **The cheap rungs say what supersedes them.** `investigate`, `design` and
  `localize` — the read-only first steps — now say in `--help` and in
  CLI_REFERENCE that `sdlc plan` and `rca` are the composites that run them and
  add the rest, and when to run the rung alone. The CLI review found no dead
  command, only a ladder nobody had labelled.
- **One reader, one summary.** The `episteme/` pages a design draws on were read
  by two identical functions, one in the CLI and one in the plugin; they are
  `sdlc.design.design_bank` now. The `sdlc baseline --json` object and the
  plugin's `sdlc_baseline` result were assembled twice from the same scores;
  they are `evals.agent_corpus.baseline_summary` now. Neither surface changed
  its output.

- **Thirteen commands leave the help screen.** The platform substrate — `task submit`
  and the ten `template` / `contract` commands — predates the comprehension and
  SDLC surfaces and is driven today by the integration tests, not by users; the
  two G6 gold-set builders, `pkg labels` and `pkg fix-sites`, are maintainer
  tooling. All are still registered, documented (CLI_REFERENCE's "Hidden" map)
  and invocable; they no longer appear in `orchestrator --help` or
  `orchestrator pkg --help`. Nothing is deleted and the command count is
  unchanged.

### Fixed

- **The first full run of the documentation audit, and what it found.** Ten
  "8 front-ends" counts written before PHP became the ninth (FEATURES,
  USER_GUIDE, KNOWLEDGE_GRAPH, CLAUDE_GUIDE, CLI_REFERENCE, EXAMPLE) now say
  nine and quote the corpus as it is — 38 fixture cases, 34 single-language and
  4 multi-repo, where "19" had stood; BENCHMARK's two counts are pinned to the
  3.29.0 measurement they describe rather than renumbered. CODEX_GUIDE's tool
  table gains the twelve rows CLAUDE_GUIDE gained in 3.31.0 (`understand_repo`,
  `profile_repo`, `design_change`, `sdlc_baseline`, the four `registry_*`
  operator tools, and the four that close the loop after the PR). `pkg labels`
  and `pkg fix-sites`, the G6 gold-set tooling, are documented in
  CLI_REFERENCE as what they are: maintainer commands.

### Added

- **The documentation audit follows links and hunts removed surfaces.** The review
  skill's `docs_audit.py` now fails on any relative link in a user document whose
  file or anchor does not resolve (under GitHub's slug rules — an em dash yields a
  double hyphen), and, given `--base/--head`, lists what the diff removed from the
  CLI, MCP and extras registries and reports every remaining mention; `--removed
  "terminal UI,TUI"` adds the names and synonyms of a feature that had no registry
  entry. A mention inside a version-stamped paragraph is history, not a finding.
  Six dead links in `SETUP.md` and two README sentences about a removed terminal
  UI are what it would have caught. The docs matrix gains a "removed feature"
  row; the script gains `--root` and its first tests. The release-cut guidance in
  the skill now names the lockfile's own entry and every plugin manifest.

## 3.33.0 — a PHP codebase gets the whole graph

### Added

- **PHP is the 9th PKG front-end — comprehension, a call graph, framework routes, and now
  Eloquent/Doctrine entities (P1-P4, all four phases).**
  `PhpExtractor` maps namespaces, classes/interfaces/traits/enums, promoted constructor
  properties, and class constants onto the universal `Module`/`Type`/`Function`/`Field`
  vocabulary, plus `IMPORTS` (`use`, and a literal `require`/`include` — resolved by path
  suffix like C's `#include`), `CONTAINS`, and `IMPLEMENTS` (`extends`/`implements`, and a
  trait `use` inside a class body — the first mixin-as-behavioural-claim edge in the graph).
  `.blade.php` is skipped as a template, not PHP source. `CALLS` resolves `$this->`/`self::`/
  `static::` (a same-file trait's method counts as the class's own, unless overridden),
  `parent::`, `new X()`, `X::m()`, a same-file or `use function`-imported bare call, and a
  typed property/parameter receiver (`$this->prop->m()`, `$obj->m()` on a type-hinted
  parameter) — PHP's global-namespace fallback for a bare function, and any untyped
  receiver, are never guessed (the invention risk this front-end holds to zero by
  construction). Laravel (`Route::get`, array/string/closure handlers, `Route::prefix()->
  group()`), Slim/Lumen (`$app->get(...)`), and Symfony (`#[Route]` attributes, with a
  class-level prefix) all lift into `Endpoint` + `EXPOSES`, so a PHP service can be a
  provider in the multi-repo `http` join. Eloquent models (`extends Model`, with
  `belongsTo`/`hasMany`/`hasOne`/`belongsToMany` relations) and Doctrine entities
  (`#[ORM\Entity]`, with `#[ORM\ManyToOne]`/`OneToMany`/`ManyToMany`/`OneToOne` relations)
  become `Entity` nodes + entity→entity `REFERENCES` — a relation to a class this repo
  never declares still gets an edge, to an external `Entity`, never a dangling one.
  `data_layer_link` reconciles them against a real `.sql` schema in the same repo with no
  PHP-specific code at all. `state` reports "Call graph: available" on a PHP codebase; only
  codegen remains a later phase. `pip install 'synaptixs-spine[php]'`. See
  [php-support-roadmap.md](docs/specs/php-support-roadmap.md).

### Changed

- **`SETUP.md` is the contributor's zero-to-running page again, and says only
  what is true.** It described the platform before the comprehension and SDLC
  work existed — sprint-numbered sections, an eleven-package layout where the
  tree has twenty, a `docs/` "gitignored" that is tracked, six links to files
  that no longer exist, and a "common workflows" section that was the pre-SDLC
  agent-template and task-submit curls. Now: prerequisites, install from source,
  the gate (linked to CONTRIBUTING, the single source), a no-key first run on
  the checkout itself (`state`, `understand`), `orchestrator up` for the full
  stack, tracing, three day-one environment variables with the rest linked, and
  troubleshooting — 371 lines to about 210, every path and anchor checked.
- **README no longer lists a terminal UI** among the surfaces; it was removed in
  3.31.0 and two sentences had outlived it.

### Fixed

- **The persistence cache could not tell whether the PHP extra was installed.** The
  cache key's grammar list (`persistence._GRAMMAR_MODULES`) named every grammar except
  `tree_sitter_php`, so a graph cached before the extra was installed — with no PHP facts
  in it — kept being served after, for as long as the commit did not move. Listed now, and
  a test cross-checks the list against `doctor.EXTRA_PROBES` so the next grammar cannot be
  forgotten the same way.
- **The PHP front-end's static import cycle** (CodeQL `py/cyclic-import`, six alerts):
  `php_routes.py` and `php_orm.py` imported the name resolver and the type record from
  `php_extractor.py`, which imports them back. The shared pieces now live in a leaf module,
  `php_names.py`, that imports nothing from the other three — the shape `go_routes.py`
  has always had. No behaviour change; every PHP fact is emitted exactly as before.
- **Five ways the PHP front-end asserted a fact the source does not contain**, found
  reviewing #334 and each a wrong *grounded* fact rather than a missing one:
  `new self()` / `new static()` were resolved as classes named `self` and `static`, which
  `finalize` then materialised as one phantom `Type` per name that every factory method
  appeared to call — they now resolve to the enclosing class (`new parent()` to its verified
  base, else nothing); a method named by a variable (`X::$m()`, `$obj->$m()`,
  `$this->prop->$m()`) produced a `CALLS` edge to a function called `$m` — now skipped;
  `$this->` inside an anonymous class was attributed to the enclosing class — the walk no
  longer descends into one; a Laravel route group whose prefix was chained
  (`Route::prefix('/v1')->middleware(...)->group(...)`), written in the array form
  (`Route::group(['prefix' => 'v1'], ...)`), or computed emitted every route inside at the
  **wrong path** — chained and array prefixes now compose, and an unreadable prefix or a
  non-`Route` receiver means nothing inside the group is emitted; a Symfony class-level
  `#[Route(path: '/api')]` was ignored and `#[Route(self::PREFIX)]` dropped — the named
  form composes, the unreadable one silences the class; and `belongsTo(self::class)` invented
  an `Entity` called `self` — self-relations now emit nothing. Regression tests for each.

## 3.32.0 — the plugin speaks the whole protocol

### Removed

- **The `sdlc` scope alias.** 3.31.0 accepted the pre-tier `sdlc` scope as all three
  tier scopes for one release, with a warning. That release has shipped. A bearer
  token carrying only `sdlc` now has no tier scope and fails the server's
  `spine:read` floor; grant `spine:read spine:plan spine:run` (or the subset the
  token should have). `ORCHESTRATOR_MCP_REQUIRED_SCOPES` is taken verbatim.

### Added

- **Every MCP tool advertises what it returns.** A type per tool
  (`plugin/outputs.py`) with no required key, attached to the registered function
  so the SDK derives an output schema and validates the result while the tool
  keeps returning a plain dict. A host reads `found`, `matches[].where`,
  `uncovered_elsewhere` before it calls. The types allow extras — pydantic would
  otherwise drop an undeclared key from the structured result silently — and the
  test suite's drift guard wraps every tool and fails on any returned key its type
  does not declare. A tool without a type does not register.
- **Per-principal audit on the MCP server's HTTP transport.** Every run-scope call
  and every scope denial is recorded against the token's principal in the
  registry's audit log through a new `POST /v1/audit` (the actor and tenant come
  from the authenticated principal, never from the body): the principal, tool,
  scope, the argument *names* and a digest of the values — never the values — and
  the outcome. An unreachable registry degrades to a log line; the audit never
  fails the call. Stdio records nothing: a local subprocess acts for one user.

- **Every comprehension tool that can answer across repositories now does.**
  `explain_symbol`, `regression_gaps`, `localize` and `docs_for` take `repos=` (a
  `.spine/repos.yaml`) like `blast_radius` and `investigate`, with the same
  contract and `standing` block. `regression_gaps` reports `uncovered_elsewhere` —
  a change reaching a *different* service nothing tests — which needed the plan
  to follow the one hop that crosses a service boundary (handler → endpoint →
  consumer); the endpoint itself is the wire, not an item. `localize` says which
  repository each frame landed in, and a frame two services could own is reported
  as ambiguous with its candidates instead of the first match winning silently.
  `docs_for` answers each repository on its own rather than merging, because a
  document describes the repository it lives in.

- **The long MCP tools report progress.** `sdlc_feature` per stage in the runner's
  own order (spec, layout, design, implement, tests, refine, judge, PR),
  `sdlc_remediate` per task, `sdlc_address_review` at checkout / respond / done,
  `understand_repo` at extract / write, `audit_repo` at start / done — whenever the
  host sends a progress token; a client that sends none sees what it saw before.
  The phases are the engines' existing log prefixes mapped to ordered steps
  (`plugin/progress.py`), so the CLI and the plugin describe the same stages; the
  bar is monotonic, and an unknown line rides on the current step as its message.
  The tools' schemas are unchanged: the SDK context arrives through an optional
  parameter the scope wrapper passes through and the schema never shows.

- **MCP prompts and resources — the workflow and the documents, for hosts that are
  not Claude Code.** Five prompts carry the `understand-codebase` skill's "which
  tool, in which order" through the protocol (`orient`, `investigate-ticket`,
  `triage-bug`, `plan-then-approve`, `whats-waiting-on-me`), so Codex, Claude
  Desktop and claude.ai get the same ordered guidance a Claude Code skill gives; the
  prompts module is the source, because the skill directory is not in the wheel,
  and a test holds the skill to the same tools. Five `spine://` resources make the
  committed `episteme/` bank, the build documents under `.spine/plans` (with their
  approval state) and the current-state report readable by URI and attachable as
  context. Resources address the default repository — the working directory, or
  `SPINE_REPO_ROOT` — because a URI segment cannot carry a path; the tools keep
  taking `repo_path`.

## 3.31.0 — the plugin grows from twenty tools to thirty-two, and every one says what it can cost

### Added

- **The gated half of the pipeline's back half, as MCP tools — and with it, every
  gap in the plugin's design record is closed.** `sdlc_address_review` addresses
  the human review comments on an open PR and pushes a fix to its branch;
  `sdlc_complete` closes the tracker issue for a merged PR; both have no local
  mode and need `confirm=true` on every call. `sdlc_remediate` turns a drift
  report into remediation runs, safe by default, `live` gated like
  `sdlc_feature`. `audit_repo` runs the codebase-auditor persona and reports
  findings anchored to real `file:line` — writes nothing, spends tokens, so it is
  read-only for the host and run scope for the token. The clone-and-checkout and
  the merge→Done logic moved from the CLI into the engine (`checkout_pr_worktree`,
  `orchestrator.sdlc.complete`), so the CLI and the plugin share one
  implementation.

- **The free half of the pipeline's back half, as MCP tools.** `understand_repo`
  builds a repo's `episteme/` knowledge base — or, with `check=true`, verifies the
  committed one still matches the code, naming the missing, stale and orphaned
  pages — so an assistant can bootstrap a repo that has no bank and then
  `read_memory_bank` it. It refuses a build on a git URL unless `out` is an
  absolute directory, because the clone vanishes. `profile_repo` (languages,
  framework, database, test runner, task type), `design_change` (a grounded
  design with blast radius and unverified references, for the same `spec` object
  `sdlc_plan` takes; `use_llm` opt-in) and `sdlc_baseline` (the agent-corpus
  gate and run metrics) join it. All deterministic, no credentials. There is no
  `state` tool because `map_repo` already is it.

- **Scopes follow the tiers on the MCP server's HTTP transport.** `spine:read`
  (comprehension, observing a run), `spine:plan` (`sdlc_plan`, `sdlc_approve`),
  `spine:run` (anything that spends money or writes where it cannot be taken
  back). Every registered tool is wrapped in a guard that reads the verified
  bearer token at call time and refuses — naming the scope it needed and the
  scopes the token has — when the tier's scope is missing; the SDK only checks
  scopes server-wide, so the per-tool check lives in the plugin. Over stdio there
  is no token and no check. `ORCHESTRATOR_MCP_REQUIRED_SCOPES` now means what the
  static token *carries* (default: all three, so a self-host behaves as before),
  and `spine:read` alone makes it a read-only token. The legacy `sdlc` scope
  expands to all three for one release, with a logged warning, then goes.

- **Operator tools in the MCP plugin — the terminal UI's successor.** `registry_runs`,
  `registry_approvals`, `registry_trace` and `registry_decide` answer "what is
  running, what is waiting on me" and decide a gate, over HTTP to the registry
  (`orchestrator up`) with the same `ORCHESTRATOR_API_URL` / `ORCHESTRATOR_API_KEY`
  every other client reads — so the plugin process needs no database or Temporal
  access, the registry scopes results to the key's tenant, and the audit log
  records the key as the actor. `registry_trace` is bounded: the newest `tail`
  entries plus a `truncated` count. A registry that is down returns `error` + a
  `hint` rather than failing the call. Deciding is annotated destructive, because
  a rejection ends the run.

- **The MCP server's tiers are metadata a host can act on.** Every plugin tool is
  registered with MCP tool annotations derived from its tier — read-only,
  destructive, idempotent, open-world — so a host that confirms before destructive
  calls confirms before `sdlc_feature`, `sdlc_start_run` and `sdlc_decide_gate`,
  and not before `map_repo`. The tier table is total by construction: a tool
  registered without one is refused, and a test says so first. Design record:
  `docs/specs/mcp-plugin-surface.md`, which also fixes the extension order — the
  six known gaps before any new surface.
- **`doctor` says which install is answering.** The tool and the CLI both report
  the package version, interpreter, MCP SDK version and the extras present. The
  case it exists for: a host launched an `orchestrator-mcp` console script left
  behind by an older checkout's venv (Spine 3.9.3, no `mcp` module) and the only
  symptom was "Connection closed".

### Fixed

- **`__all__` in `orchestrator.plugin.server` exports every registered tool.** Four
  (`sdlc_plan`, `sdlc_approve`, `docs_for`, `pkg_joins`) were registered but not
  exported; a test now holds the two in step.

- **An empty codegen submission gets a second correction — a bad draw is not a
  loop.** The corrective retry allowed one correction per failure kind, on the
  reasoning that a kind failing twice is looping rather than fixing. That holds for
  a deterministic failure (the same bad edit anchor, the same unparseable output)
  and not for an empty submission: codegen samples at the provider default, so a
  run that answered `summary='placeholder'` and no files drew badly rather than
  reasoned badly, and the remedy for a bad draw is another draw. `empty` now gets
  two corrections (`_CORRECTIONS_PER_KIND`), every other kind keeps one, and the
  overall attempt cap grows by exactly one so the allowance is spendable. Follows
  #154, which routed placeholder-only submissions into this retry.

### Removed

- **The terminal UI (`orchestrator tui`) and the `tui` extra.** Every action it
  offered — watch runs, clear gates, delegate a run — is the web inbox or a CLI
  command over the same `/v1` API, so it carried an optional `textual` dependency
  and a mypy carve-out for no capability the other surfaces lacked. The
  assistant-facing surface remains the MCP plugin, which already drives the `sdlc`
  pipeline. `pip install 'synaptixs-spine[tui]'` now fails on an unknown extra;
  drop it from your install line.

## 3.30.0 — Three lines in another repository, and a place to plug a vault

### Added

- **A reusable comprehension workflow — G4 Phase 5a.** A repository adds three
  lines and gets a pull-request comment saying where its own change lands in its
  own code, with `file:line` and caller counts:

      jobs:
        spine:
          uses: synaptixs/spine/.github/workflows/spine-comprehension.yml@v3.30.0

  **It declares no `secrets:`.** `investigate` is deterministic and model-free;
  the only token is the caller's own `GITHUB_TOKEN`, used to post the comment, and
  `comment: false` writes nothing at all. No container image — a cold install with
  every language extra measured **21 seconds**, so the usual reason for one does
  not hold. The comment is updated in place rather than appended, and the
  pull-request title reaches the shell through `env:` rather than inline
  interpolation, because a title is attacker-controlled.

  **Note on the pin:** this snippet is the first that works. The one published
  on `develop` before this cut pinned `@v3.29.1`, a tag that predates the file —
  copying it produced "workflow not found". Fixed here, where the snippet and the
  tag land together.

- **A secrets seam.** `core/secrets.py`: `get_secret(name)` with the environment
  as the default provider, `ORCHESTRATOR_SECRETS_PROVIDER` to select another, and
  `register_provider` for anything behind an extra. `Settings`, Temporal, MCP auth
  and the object store all read credentials through it, **byte-identically** —
  under the default it *is* `os.environ.get`. A vault is now a plug rather than a
  rewrite, and a fresh interpreter importing the seam loads no client. The first
  provider, when an operator wants direct fetch rather than injection, will be
  HashiCorp Vault / OpenBao behind `[vault]`; a file provider was considered and
  rejected as a static secret with a new attack surface.

- **The read-only path is now guaranteed to need nothing.** A test runs `state`,
  `understand`, `investigate` and `pkg extract` in a process holding only `PATH`
  and `HOME`. This is the property the developer-adoption story rests on — no
  key, no account, no service — and until now nothing defended it. With a fake
  requirement injected, four of five tests fail; that is the point.

### Changed

- **RBAC was already built and already opt-in**, and `STATE-OF-SPINE` §8 read as
  though it were not. Separated from the secrets work it was bundled with: roles
  are enforced on the decide path, runs and approvals are tenant-scoped, and with
  no `principals` map the single API key resolves to today's behaviour. What
  remains — quorum, the untenanted tables, OIDC — is named and waits on a buyer.

## 3.29.1 — The help screen reads as a workflow

### Changed

- **`orchestrator --help` is grouped into six panels.** It printed 26 entries in
  registration order — the order features were added, which is a changelog, not a
  map. They now read top to bottom as a workflow: *Get started* (init, doctor, up,
  models, tui), *Understand a codebase* (profile, understand, state, audit),
  *Investigate & design a change* (investigate, design, localize, rca,
  regression), *Plan & build* (ingest, backlog, openspec, sdlc), *Knowledge graph*
  (pkg, media), *Registry & integrations* (template, contract, task, mcp,
  catalog). The top-level blurb describes Spine rather than a "registry client",
  and internal roadmap codes are gone from user-facing help. A test pins the panel
  order and fails if a command is left unpanelled. (#306)

- **`cli.py` is a package.** The single 4,300-line module is `orchestrator/cli/`,
  one module per help panel with `sdlc` and `pkg` on their own, `_app.py` holding
  the root app and `_common.py` the shared helpers. Mechanical: no function body
  changed, and `--help` for all 67 entries is byte-identical before and after. The
  console entry point `orchestrator.cli:app` is unchanged. Scripts that counted
  commands by reading `cli.py` now read the package. (#307)

No behaviour, command name, path or option changed in this release.

## 3.29.0 — A Node or Go service can finally be the thing you call

### Added

- **`Endpoint` nodes for TypeScript (Express) and Go (Gin).** Only Java and C#
  emitted them among the tree-sitter front-ends, and that had a consequence
  nobody had stated: the multi-repo `http` joiner matches a consumer's calls
  against the **provider's** endpoints, so **a Node or Go service could not be a
  provider in a cross-repo join at all.** Multi-repo comprehension has shipped
  since 3.22.0 and worked only when the provider was Java, C# or Python.

  Single-repo, it closes the wrong answer `python_routes.py` exists to kill:
  nothing *calls* an HTTP handler — the framework does, at runtime — so a route
  handler had zero callers and `impact_of` answered *"safe to refactor"* about a
  public endpoint. That was live for every Go and TypeScript service.

  Mounts compose: `app.use("/v1", router)` and `r.Group("/v1")`. Three precision
  rules, each pinned by a control in the corpus: **a computed path yields
  nothing** (a template literal, or a path in a variable); **an inline handler
  yields an endpoint but no `EXPOSES`**, because there is no named symbol and
  inventing one is fabrication; and **`net/http`'s verbless `HandleFunc` is
  deliberately unread**, since the joiner matches on verb equality.

      typescript   Endpoint 3/3, EXPOSES 2/2   neither existed
      go           Endpoint 3/3, EXPOSES 2/2   neither existed
      multirepo    CONSUMES 2/3 -> 3/4

- **Prose that names a file by its stem now binds.** `typescript_extractor` means
  `typescript_extractor.py`; `comprehension_labels` means the YAML beside it. 74
  edges, and **98 fewer drift findings** — the larger half, since prose naming a
  real file was being reported as prose naming code that does not exist.

  Restricted to snake-shaped tokens, and the restriction is the rule: matching
  any stem binds `bug`, `enhancement`, `invention` and `validity` to modules that
  happen to share a filename, when the prose meant the English words.

### Changed

- **The doc-binding gap is 51%, and the headline is misleading.** Of the 809
  unbound mentions in sections that bind to nothing, **603 are not code claims at
  all** — `OpenSpec`, `TypeScript`, `GitHub` — which the tier's own rule already
  refuses. **81% of those sections contain nothing bindable**, and at most 60 of
  1,621 hold a mention a smarter tier could reach. That is the population a model
  tier would chase, against a determinism cost on every gated surface, and the
  measurement argues **against** building one.

- Two roadmap blockers cleared, both by scoping rather than building: **G4 gains
  Phase 5** (central adoption, split into a reusable workflow and a deployment
  image — measured: a cold install with every language extra is **21 seconds**,
  so the workflow needs no image), and **the RBAC row is separated from the
  secrets seam it was bundled with** — RBAC is built and already opt-in; the
  seam does not exist in any spec.

## 3.28.0 — A cross-repo edge stops pointing at a node that does not exist

### Fixed

- **`pkg extract --repos` and `investigate --repos` could place a `CONSUMES`
  edge from a node the graph does not contain.** Both pass one extractor for
  every repository, and `unresolved_calls` — the cross-repo join candidates —
  accumulates on the **front-ends**, which are built once and reused.
  `ClientState.clear()` preserves `unmatched` on purpose, so clearing the
  extractor's own list left the previous repository's calls in place for the
  next one to inherit. The joiner scopes a candidate to the repository it is
  examining, so web's call became `py:billing@...` — a fabricated edge, which is
  the one thing this graph exists to refuse.

  `RepoCodeExtractor.reset_unresolved()` clears the front-ends too, and the merge
  loop now resets before each repository rather than trusting a fresh object.

  **Live in 3.27.0.** `pkg joins` was unaffected — it builds a fresh extractor
  per repository — which meant the joins report and the extracted graph could
  disagree with each other.

  The corpus could not have caught it: `pkg/accuracy.py` builds a fresh extractor
  per root, so the multirepo fixtures score a different code path from the one
  the CLI runs.

### Added

- **A document citing a path now binds to the module built from that path.**
  `src/orchestrator/pkg/store.py` resolves, maps to exactly one `Module` node by
  the extractor's own provenance, and drew no edge — the anchor was recorded and
  discarded. **534 `MENTIONS` edges, and 55 `Doc` sections that bound to nothing
  now bind**; sections binding to nothing went from 55% to **52%**. Exactly one
  matching file and exactly one owning module, or no edge.

### Changed

- **What the doc-binding gap actually is, measured.** "52% of sections bind to
  nothing" reads as half the documentation failing to bind. Of the 809 unbound
  mentions in those sections, **603 are not code claims at all** — `OpenSpec`,
  `TypeScript`, `GitHub` — which the tier's own rule already refuses. **82% of
  those sections contain nothing bindable**, and at most **60 sections of 1,602**
  hold a mention a smarter tier could reach. That is the population a model tier
  would chase, against a determinism cost on every gated surface.
  `scripts/classify-unbound-mentions.py` re-derives it.

- **`CONTRIBUTING.md` lists all four of CI's generated-artifact checks**, not
  one. A release failed on a diagram nobody had re-rendered while `mypy`, `ruff`
  and the tests were green. The three things that trip a `develop → main`
  promotion are written down beside them.

- **A two-page multi-repo walkthrough** — declare, extract, merge, join, read —
  beside the roadmap's 624-line design record.

## 3.27.0 — TypeScript stops skipping the calls it could not type

The headline is a number that moved for a real reason, and a second number that
moved because we finally looked at it.

### Added

- **TypeScript call resolution: `CALLS` recall 0.36 → 0.86.** `h.run()` where
  `h` is a parameter annotated `Handler`, or a local from `new Handler()`, was
  skipped rather than guessed — tree-sitter sees an identifier and a property,
  not a type. A bounded local-type pass now reads the receiver's type from the
  annotation or the `new` in the same tree, and a whole-repo `finalize` emits
  the edge **only where the target exists in the merged graph**. Precision holds
  at **1.00** and the invention oracle stays at zero.

  **The TypeScript compiler API was scoped and argued against**
  ([`docs/specs/typescript-call-resolution.md`](docs/specs/typescript-call-resolution.md)).
  It resolves against installed packages, so the same commit yields a different
  graph depending on whether `node_modules` is present — which breaks the
  determinism that makes `understand --check` a gate at all. Every measured miss
  was reachable without it.

- **A recorded-intent tier**: `git blame` plus issue keys in commit messages
  become `Intent` nodes and `SERVES` edges, so `investigate` can answer *why*
  this code exists, not only what calls it.

- **`orchestrator --version`.**

### Fixed

- **The drift list stopped calling 58 filenames missing code.** `md.js`,
  `graph.html` and `compose.dev.yml` are dotted, lowercase and multi-segment —
  shaped exactly like symbol paths — and the FILE pattern does not recognise
  their extensions, so they arrived as symbol claims. Drift **1,658 → 1,600**
  with **not one `MENTIONS` edge changed**: a naming asymmetry, not a coverage
  gap. `README.md` and `src/a/b.py` keep their disk check, because a document
  linking a file that is not there is a real finding.

  Investigated first as a *binding* defect, which it is not. `bind`'s leaf-only
  fallback looked reckless enough to delete on sight; measured, it rescues **76**
  mentions onto a correct anchor (`store.find` → `FactStore.find`) and mis-binds
  no filename at all.

### Changed

- **`scripts/state-numbers.py` re-derives eighteen published claims**, up from
  eight — the TypeScript recall figures quoted in three places, and the eight
  doc-binding figures in the walkthrough. Structural claims are gated; figures
  that move with ordinary commits print `[trend]` and never fail the build.

  This exists because every hand-carried number in this repository has aged
  silently. The TypeScript figure was published as 0.50 while it was actually
  0.571, then 0.357 once the corpus doubled.

- **How a document binds to the graph is now written down**
  ([`docs/specs/doc-binding-walkthrough.md`](docs/specs/doc-binding-walkthrough.md)),
  in six steps and then traced through one real page. It records a wrong table
  it replaced: the first version conflated file anchors with symbol anchors and
  concluded "ambiguity, not absence, is the larger loss", which is false —
  absence is 3,360 mentions against ambiguity's 1,962.

## 3.26.1 — The benchmark's own command stops looking broken

### Fixed

- **Extraction no longer prints the target repository's compile warnings.**
  Running the command [BENCHMARK.md](BENCHMARK.md) publishes emitted roughly
  sixty lines of `SyntaxWarning: invalid escape sequence` before any result —
  from the *corpus repositories'* own Python, because `ast.parse` compiles and
  compiling warns. Spine reads that code rather than running it, and the person
  at the terminal did not write it, so the warning is noise they cannot act on.
  On the one command a benchmark page exists to invite people to run, it read
  as "something is broken".

  Scoped to the single `ast.parse` call and to `SyntaxWarning` only: a genuine
  `SyntaxError` still raises, still reaches the caller, and still marks the file
  skipped.

  Found by running the published command as an outsider would — which is the
  only way this class of thing turns up, and the reason 3.26.0 is superseded
  before it reached PyPI.

## 3.26.0 — Spine measures whether it finds the right file, and publishes the number

The headline is a number that did not exist before: given a real bug report,
**the file that actually fixed it is in Spine's top 10 for 27 of 38 issues, and its
first guess is right for 12**. Picking ten files at random from the same repositories
scores 0.085. See [BENCHMARK.md](BENCHMARK.md), which also states at length what those
numbers do *not* show.

Getting there turned up three defects on the paths that were supposed to be checking
this product, and every one of them had been reporting success.

### Added — the comprehension benchmark (G6, all three phases)

- **A pinned five-repository corpus** — vuejs/core, gin, fmt, libuv, flask, each at an
  exact commit — materialised by `evals/corpus_fetch` and scored offline. Fetch, verify
  `rev-parse`, write the marker **last**, so a process that dies mid-fetch leaves a
  directory that does not read as complete.
- **Provenance validity**: of every `Function`, `Type` and `Field` fact, does the
  recorded line actually name that symbol? 1.0000 on TypeScript and Go, 0.9850 on C.
  The spec proposed `stale_findings` for this and it could not work — on a freshly
  extracted tree it is zero by construction, a constant rather than a measurement.
- **A hand-labelled gold set** of 38 issues, with `pkg fix-sites` to read what a fixing
  commit changed and `pkg labels --check --paths` to refuse anything unreproducible: an
  abbreviated commit, an issue GitHub does not record the PR as closing, a path absent
  from the pinned tree.
- **Top-k localization on the scoreboard, gated** — but only when the gold set's digest
  is unchanged, so reshaping the corpus is a rebaseline rather than a regression.

### Fixed — three checks that were passing while measuring nothing

- **Fact freshness re-extracted every changed file with `PythonExtractor`.** On any
  other language the parse raised and the `except` treated an unparseable file as
  "everything in it is stale", so a polyglot pull request got a stale-graph warning per
  symbol when nothing was stale — Python 0, TypeScript 2/2, Go 3/3 on an unmodified
  repo. Dispatch is now per suffix through the same registry extraction uses.
- **The PKG-grounded review layer never ran on a pull request.** `webhook.py` built
  `ReviewService` without `verifiers` or `impact_source`, and nothing outside tests ever
  constructed `PKGGroundingVerifier`. All of it needs a checkout, and that path had
  none. Opt-in via `ORCHESTRATOR_REVIEW_CHECKOUT=1`; a checkout that cannot be made
  degrades rather than blocking, and the review says so in its body.
- **`doc_findings` rendered a drift finding nothing called.** Now wired, and reported as
  a **delta against the pull request's base** — what this change broke — rather than
  inferred from what the patch removed.

### Changed — a gate shipped and withdrawn the same day

`drift` was gated on a ratchet and un-gated one pull request later, after it failed a
documentation change. Its denominator was sections, which prose edits do not move, and
about a tenth of the population cannot bind by construction — parameters, module
constants, string literals and log event names have no node kind. The denominator is
corrected to *claims made* (0.1005, not 0.5829) and the number is recorded and trended
as an **upper bound**, never failing a build. Gating it needs the population narrowed
first.

### Changed — smaller

- The temperature refusal a model returns is learned **once per process** rather than
  re-discovered on every call, and the determinism warning is said once.
- `MCPRegistry.call` opened two sessions for one structured call; schemas are cached at
  discovery, so it opens one.
- The `current-state` diagram renders instead of falling back to `<pre>`; all 47 mermaid
  blocks across every tracked markdown file now render.

### Documentation

- [BENCHMARK.md](BENCHMARK.md) — the public methodology, with six numbered limitations
  and the commands that produced the figures.
- `docs/specs/watch-items-roadmap.md` — a spec three pages had linked for a month and
  nobody had written.

## 3.25.1 — The README catches up, and is made to stay caught up

### Fixed — the first thing anyone reads was three releases stale

- **`README.md`'s "What's new" said `3.22.0 (current)`** while PyPI served 3.25.0. The release
  cut touches `pyproject.toml`, three manifests, the architecture diagram and two `docs/specs`
  files — and nothing else knew a release had happened, so the section every visitor reads
  first quietly described a version from three cuts ago, with 3.23.0's and 3.25.0's headline
  features still filed under *"on `develop`"*.

  It now lists 3.25.1 through 3.22.0, and **`test_the_readme_whats_new_section_names_the_current
  _version` fails until it names the version in `pyproject.toml`**. That is the same fix, and
  the same argument, as `test_manifest_version_tracks_the_package`: the manifests stood at 2.5.0
  through fifteen releases because nothing tied them to the package, and a document nobody has
  tied to the release is a document that goes stale silently rather than loudly.

## 3.25.0 — The issue type finally reaches the run

### Fixed — the issue-type pipeline was unreachable

- **Nothing carried the ticket's issue type into a run.** Spine is issue-type shaped:
  `select_profile` picks a workflow profile from it, the validity gate decides by it whether a
  ticket must localize, and the `bug`/`enhancement` profiles exist to be selected by it. None of
  it fired. The only thing intake hands a run is a `FeatureSpec`, which forbids extra keys and
  has no field for a type — so the lookup was always `""`, **every run took the `default`
  profile**, and the localization check never once ran outside its own tests. The type reached
  the intent *extractor*, as prose prepended to the body, and nothing else.

  It is now resolved deterministically at intake — `spec.intent_id → Intent → source_doc_ids →
  SourceDocument` — over data already fetched, and carried on the run rather than on the spec. A
  spec is written by a model, and a model choosing which research a ticket gets is what
  `profile_select` exists to prevent. **Two behaviours change with it:** a typed ticket now
  selects `bug`/`enhancement` where it silently took `default`, and a `Bug` whose words match
  nothing in the graph parks as `UNLOCALIZED` — the check working on the first ticket that ever
  reached it.

- **An enhancement was refused for naming what it was about to build.** The gate excuses a
  feature from landing anywhere in the graph — it has no existing behaviour to localize — while
  the unbound-criteria check refused every ticket alike. One gate, two stances on one question,
  and the consequence was perverse: `the compiler returns >= 70 rules` is prose and proceeded,
  while `` `rule_compiler` returns >= 70 rules `` named its subject, was more testable, and
  parked the run. A bug is still refused — its subject is code that already exists, so an unbound
  claim is a false premise — and anything else is told, and proceeds.

- **`incident` was a bug to the profile selector and not to the gate.** It selected the bug
  profile, got root-cause analysis, and was then excused from having to localize. Both now ask
  one predicate.

- **An enhancement's evidence claimed its root-cause section had found nothing.** The
  enhancement profile drops `n_rca` precisely so the report does not print *"Not localized to a
  repo symbol"* — an empty section that reads as a finding. The renderer printed it anyway: it
  saw an empty result and could not tell a skipped node from an empty one. It now says *"Not run
  for this issue type"*, and a bug that genuinely localizes nothing still says the original.

### Fixed — the local gate failed on files you did not touch

- **Two `type: ignore` comments were only correct in some environments.** Whether an ignore is
  "unused" depends on whether the optional dependency it guards is installed, so
  `mypy src tests` failed on `mcp/client.py` with `--extra dev` and on `sdlc/testrunner.py` with
  `--all-extras` — always in a file the contributor had not touched, and never in CI, which
  installs a set that hits neither. Both now name `unused-ignore` alongside the real code, which
  is green in either environment. A gate that fails on someone else's file is a gate people
  learn to distrust.

### Added — two signals that were computed and thrown away

- **`--issue-type` on `sdlc autorun` and `sdlc plan`.** Overrides what the ticket says, and is
  the only way to type a run driven by `--spec`, which has no ticket behind it.

- **Labels select a profile when the issue type does not.** Jira labels have been fetched with
  every issue since the adapter was written and read by nothing. They are consulted **only**
  where the type resolves to nothing — a renamed dropdown, a tracker with no type field — so a
  feature request labelled `bug` for triage does not thereby get root-cause analysis. Sorted
  before matching: first-match-wins over an unordered set is not reproducible.

- **`n_churn` for the enhancement profile.** `build_rca` runs a `git log` recency pass and
  crosses it with the fault file; dropping the node dropped that answer too, for half of all
  tickets, though *"is this area moving?"* does not depend on a symptom. An enhancement's churn
  is crossed with its **landing sites** instead — where a ticket's vocabulary already lives is
  what it will attach to, not what it will touch — which is the weaker reading and is worded as
  one. It never says "regression"; a test asserts the word does not appear.

## 3.24.0 — The plugins catch up with the product

### Added — multi-repo reaches the plugin surface

- **`blast_radius` and `investigate` take `repos`.** Point either at a `.spine/repos.yaml`
  instead of a `repo_path` and the answer crosses a repository boundary: a handler reporting
  `0 caller(s)` at home now names the service that depends on it. 3.23.0 shipped this on the
  CLI only, so the headline capability of that release was invisible to Claude Code and Codex.
- **`pkg_joins`** — `mode="propose"` derives a `joins:` block from the evidence (each candidate
  carrying the edges it would create); `mode="check"` reports the calls no declared join could
  place. Read-only; it never writes a config, same as the CLI.
- **Every multi-repo answer carries a `standing` block** — the repos it covers and whether it is
  `reproducible`. The CLI prints that on stderr; a tool has to *return* it, or a caller quotes a
  number nothing can reproduce and nothing in the payload says so.

### Added — a single-repo answer now says when it is one

- **`multi_repo_available`.** Point a comprehension tool at a repository that declares siblings
  in its own `.spine/repos.yaml` and the answer carries a note naming that config and the repos
  it declares. This is the one case the multi-repo work could not make loud on its own:
  extracting a single directory always *succeeds*, so `0 caller(s)` from an unscoped graph is
  indistinguishable from `0 caller(s)` that was checked across every service — and the first is
  the answer that gets a handler changed.
- A note, not a switch. It never redirects the caller to the merged graph, because which
  repositories an answer covers is theirs to decide. A config too broken to parse still
  produces a note, since an unreadable config is still evidence the project is multi-repo.
- `sdlc_approve` opts out: a decision about one repository's plan is not a question about the
  others.

### Added — `[all]`, because the documented install under-delivered

- **`pip install 'synaptixs-spine[all]'`** — every language front-end, the MCP server, doc and
  office ingestion. Both plugin READMEs and both guides said `[mcp]`, which installs the server
  and a **Python-only** graph: a Java or Go repo yields zero nodes rather than an error, so the
  documented install failed by looking like an empty repository. `[languages]` is the front-ends
  alone.

### Fixed — the manifests had stood at 2.5.0 through fifteen releases

- **Version, tool list and language list are current** across `marketplace.json`, the Claude
  Code `plugin.json` and the Codex `plugin.json`. They advertised 7 tools and 7 languages
  against a registry of 20 and a front-end set of 8, and omitted `sdlc_plan` / `sdlc_approve`
  entirely — both shipped in August and neither appeared in a manifest.
- **`understand-codebase`** gained the cross-repo tools, the `standing` block, the plan/approve
  tier, and SQL.
- **Three tests so this cannot recur quietly.** Manifest versions are asserted against
  `pyproject.toml`; every name in `_TOOLS` must appear in a user-facing guide; the pitch must
  name all eight languages. The old test grepped the manifests for the word "Go" — written for
  3.7.0, and green for every release after it.

### Changed — one helper, not two

- `unresolved_by_repo` moved to `pkg/joins_propose.py`; `cli.py` and the plugin server now share
  it instead of carrying identical copies.

## 3.23.0 — One graph across several repositories

### Added — multi-repo comprehension

- **"What breaks if I change this?" can now answer with a caller in a different repository.**
  Declare your services in `.spine/repos.yaml` and they extract, scope and merge into one
  graph. A ticket landing in one repo reports what depends on it in another:

  ```
  - **billing** · `create_order` (Function, 0 caller(s), **1 dependent(s) in other repos**) — billing:app/routes.py:7
  ```

  That row is the whole point. `0 caller(s)` is **true** — nothing in the source calls an HTTP
  handler — and on its own it is the most dangerous answer the graph can give.

- **Three join kinds, and the topology is declared rather than guessed.** An HTTP call to a
  path another repo serves, a table two repos both write, a library one repo publishes and
  another imports. Declaring a join **narrows the search**; it does not create the edge —
  matching `POST /v1/orders/42` against `POST /v1/orders/{id}` is still resolution, done
  segment-by-segment so a `{param}` can never swallow a `/`, and **refused outright when two
  endpoints match**. Recall pays; precision does not.

- **Nobody authors the topology.** `orchestrator pkg joins --propose` derives it from evidence —
  the calls a repo makes to paths it does not serve, matched against its neighbours' endpoints;
  shared table names; imports another repo defines — and **every candidate carries the number of
  edges it would create**, because a join producing zero is noise. It prints a block to review
  and writes no config.

- **`pkg joins --check`, because a forgotten join is quiet.** A repository nobody listed is loud:
  no nodes, a visibly narrower graph. A missing `joins:` entry looks exactly like two services
  that are not coupled, which reads as health. So unplaced calls are reported by reason **and per
  declared join**, and a stale join shows `** placed nothing **` rather than vanishing into a
  healthy total.

- **Measured, per joiner, against corpus cases written before the joiners worked:** precision
  **1.00** on all three; recall **1.00** for data and package, **0.67** for HTTP. Precision is
  1.00 *by construction* — nothing can join to a repository nobody declared — so recall is the
  number worth watching. The missing third on HTTP is a path built from an f-string, which the
  extractor collects as no call at all; it is labelled a known gap rather than hidden.

- **New surfaces:** `pkg extract --repos`, `pkg joins --propose|--check`, `investigate --repos`.

### Changed — `Provenance` gained a `repo`, and `__str__` deliberately did not

- Node ids are language-prefixed, not repository-prefixed, so `py:shop.cart.Cart` is the same id
  in two repositories and merging collapsed them **silently** — internally consistent, externally
  false, with `pkg verify` reporting zero throughout. Scoping is applied **at merge time only**,
  so single-repo extraction is byte-identical: verified by SHA-256 over the full node and edge
  set against the previous release on leveldb, gin, zod, Newtonsoft.Json and this repo's own
  source, at every phase.
- **`Provenance.__str__` keeps its shape**, and there is a test that fails if it ever stops.
  **Six production call sites parse it back** with `split(":", 1)[0]` to recover a file path,
  two of which are `evidence.py` and `criteria_binding.py` — so a repo in the string makes an
  acceptance criterion bind against a repo name, which is to say against nothing, and **pass**.
  Use `Provenance.qualified()` where the repository matters.

### Added — the corpus can hold a cross-repo case

- `expected.json` accepts `roots:` (repo key → path) alongside `root:`, and a multi-repo case is
  scored through the **real** merged path — scoping, merging, declared joins — or it would
  measure an assembly nothing ships. A `requires:` field separates *what a case measures* from
  *which front-end must be installed*; the two are identical for every single-language case and
  differ for a cross-repo one.

### Documentation

- **The README now argues for the project instead of re-telling a release.** It opened with
  "New in 3.20.0", two releases stale. It leads with what Spine does that other tools do not —
  four points, each with a number — and the contributing section says what the codebase is
  actually like, names four places to start, and states the three rules that matter.
- `CLI_REFERENCE` covers `pkg joins` and both `--repos` flags; `FEATURES` gains multi-repo.
- Four design records: the multi-repo roadmap, an enhancement index, how a ticket becomes a
  `file:line`, and a constitution spec that **may close unbuilt** — its Phase 0 is a blocking
  trigger probe, and the probe already run says all three candidate rules have fired zero times
  in this project's history.
- **spec-kit evaluated and declined**, with the revisit condition written down.

### Known limitations

- **Only Python emits `CONSUMES`**, so the HTTP joiner is Python-client → any-language-endpoint.
  A Java service calling a Java service produces no join at all.
- Only `investigate` reads the joins; `design` and the SDLC pipeline still run against one
  repository. **Multi-repo *delivery* is an explicit non-goal** — N PRs that must land together
  is ordering and rollback, not a graph problem.
- Recall 0.67 on HTTP is against a fixture designed to be joinable. The number from a real pair
  of production repositories — templated paths, gateway rewrites, a declaration written by
  someone who did not build this — **has not been taken.**

## 3.22.0 — Four front-ends stop asserting calls the source does not make

### Fixed — four front-ends fabricated a `CALLS` edge for a shadowed name

- **`pkg accuracy --oracle invention` and `pkg verify` were Python-only, and said `0`.** The
  detector re-parsed with the stdlib `ast`, so on a TypeScript, Go, C++ or C# repository a zero
  meant *nothing ran*. New `pkg/scope.py` walks TypeScript, Go, C#, C++ and C with the same
  tree-sitter parser each front-end uses, and the detector is restated language-neutrally: an
  edge is invented when the call site is a **bare identifier** matching the target and that
  name is **bound inside the calling function**.
- **Four front-ends fabricate a `CALLS` edge for a shadowed name.** `outer(send)` calling its
  own parameter `send` yields `ts:a.outer -CALLS-> ts:a.send` — the module-level function it
  never reaches. Reproduces in Go, C++ and C#. This is the bug Python fixed in 3.18.0, in a
  form the fix did not reach: these do not invent an id, they land on a **real node**, so
  `pkg verify` sees no dangling edge and no corpus fixture carries the shape. C already refuses
  it (`c_extractor._bound_names`) and has a corpus case; the other four have neither.
- **Measured on 11 pinned public repositories:** 47 fabricated edges across 23,746 bare calls —
  **46 in C++** (0.47%; leveldb and fmt), **1 in TypeScript** (vue/core), **0 in Go and C#** on
  8,562 bare calls, and **0 in C** across 14,856, which is the control holding. Roughly an order
  of magnitude rarer than Python's 3.16% was. Record, with SHAs and the reproducing command:
  `docs/specs/invention-oracle-cross-language.md`.
- **Two false positives in the oracle were found by hand and fixed before publishing.** A
  TypeScript destructuring *default* was read as a binding (3 phantom findings on vue/core), and
  Go's `:=` was treated as in scope on its own line, when the spec starts it after the statement
  — so idiomatic `cmd := cmd(path)` read as fiction (5 on grpc-go). Both have regression tests
  written against the language reference.
- **Per front-end in the scoreboard and the CLI, with a `status`.** `measured`, `not-applicable`
  (Java — variables and methods have separate namespaces, JLS §6.5.7; SQL — no lexical scope),
  or `unwalked`. The denominator reported is **bare calls**, not all `CALLS`: only a bare call
  can be reached by a shadow.
- **Fixed by porting C's test to the other four** — a local `_bound_names` per front-end,
  answering *did this function bind the name*, never *can we resolve it*. The difference is the
  design: an unresolved callee in C++ is usually a header declaration linked from another
  translation unit, and a Python-style "skip anything unresolved" fix would have silenced every
  cross-translation-unit call in every C++ repository. The oracle keeps its own independent
  implementation — a detector that imports the code it audits agrees with that code's bugs.
- **Each helper carries the first line a name is in scope**, because two of the four languages
  require it. Go's spec starts a `:=` scope at the end of the statement, so `cmd := cmd(path)`
  calls the package-level `cmd`; C and C++ read the same way. Binding from the top of the
  function would have dropped those edges — 5 in grpc-go alone. C# tracks the bare-call form
  separately: `this.Handle()` is an explicit member access and cannot be shadowed.
- **Re-measured on the same 11 repositories: 0 everywhere, and 47 edges removed for 47
  fabrications found.** vue/core −1, leveldb −3, fmt −43, every other repo unchanged. No true
  edge was lost across 38,602 bare calls; grpc-go kept all 6,285 of its `CALLS`. Precision
  rises, recall is untouched — these edges were never in any expected set.
- **Four corpus cases** — `corpus/{typescript,go,cpp,csharp}/shadowed_calls`, modelled on
  `corpus/c/function_pointers`. Each was written and scored **before** the fix and each failed
  at the predicted point: `CALLS` precision 0.50, recall 1.00. Each pairs the shadowed call
  with an unshadowed sibling in the same file, so a front-end cannot pass by dropping both.
- **`invention` is now gated `strict` at zero per language** — the only metric gated on an
  absolute value rather than against the baseline, because it is the only one with a correct
  value. Comparing to a stored number would let a non-zero baseline become the thing everyone
  agrees to live with. A language whose status is not `measured` is skipped; its 0 means *not
  examined*. The gate is proved by making it fail, and the original objection is preserved: the
  *rate* still moves freely, only the count is held at zero.

### Changed — GraphIR Phase 4 closed without shipping either half

- **The parallel fan-out was measured and declined.** Timed on this repo's own graph, only
  `investigate` (0.034s) and `rca` (0.057s) are independent, so the entire available saving is
  **~30ms** of a ~2.3s research pass — and collecting it means putting two synchronous tools on
  threads over a shared `FactStore`. Preflight is serial too, but cold `mypy` is **16.3s**
  against ruff's 0.1s. The coverage probes cannot be parallelised at all: each `git stash`es the
  shared worktree. And the fan-out with real wall-clock in it — one child workflow per issue,
  bounded by `max_parallel_features` — shipped long before this phase was written.
- **The bounded replan was built, then reverted as unreachable.** `Budget.max_replan_count` was
  honoured by `autorun`, a repair loop fed the design validator's refused references back as a
  bar, and six tests passed. Probing the *trigger* rather than the mechanism showed
  `validate_design` refuses **0 of 6** real specs — including one naming `made_up_pkg/thing.py`.
  `_fallback_design`'s three sources cannot fabricate: stated paths are filesystem-filtered when
  `root` is given (and `autorun` always gives it), while landing and overview files come from the
  graph. The one producer that could fabricate, `_llm_design`, is off because **3.20.0 declined
  that promotion**. The budget could not be spent.
- **Underneath it: re-running a deterministic producer returns the same answer**, so a replan
  loop only means anything for a non-deterministic one. This half was never deliverable while
  `design` stays deterministic. Every test had `monkeypatch`ed the producer to manufacture the
  failure, so the mechanism was verified and the trigger never was — internally perfect,
  externally inert.
- **Reverted with it:** `produce_design(exclude=...)`, `max_replan_count: 2` in the three
  profiles (back to `0`), `Case.attempts`, and `IRValidator.check_structure_preserving`, whose
  only purpose was guarding the replanner.

### Added — per-node wall-clock in the Case

- **Every node row read `0.00s`.** No `case.record(...)` call passed `seconds=`, so *"where did
  the time go"* was unanswerable from the artifact built to answer it. All five recorded nodes —
  the three research tools, the validity gate and the design node — are now timed, and
  `Case.seconds` sums the rows. Deliberately **not** the run's duration: `autorun` does work
  between nodes that no node owns, and reporting it as the run's would credit the graph with
  time it never spent. Excluded from `Case.digest()`, like `cost_usd`: a clock may be reported,
  never computed with.

### Added — parallel-shape rules in the IR validator

- **`parallel_reconvergence` and `parallel_determinism`.** No shipped profile declares a fan-out,
  so **these do not fire today, and their tests say so.** They exist because
  `_check_sequential_shape` inspects only the *agent* condensation, so a fan-out over `tool`
  nodes passed validation **without anything having looked at it** — a concurrency capability
  nobody declared and nothing verified. A branch must reconverge, and only deterministic nodes
  may sit on one: two model calls in flight can each pass the run budget check and jointly
  overrun it.

### Added — the Knowledge Foundation Architecture diagram, as SVG and PNG

- **`assets/knowledge-foundation.svg` + `.png`**, generated by
  `scripts/render_knowledge_foundation_svg.py`. The product-neutral, federated figure described by
  `docs/specs/knowledge-foundation-diagram-prompt.md`: many sources across many repositories, one
  reader each, collapsing through a closed vocabulary — the narrow waist — into enrichment passes,
  a content-addressed store tied to a commit, a query layer, and five projections. Deterministic
  and seeded, like every other visual surface here: same input, same bytes.
- **Text is measured, not estimated.** The generator carries Helvetica's published advance widths
  (units/1000) and names Helvetica first in the SVG, so wrapping is computed against the font the
  renderer will actually use. Every card height derives from its wrapped line count; none is a
  constant. The existing architecture generator estimates at "~7.6px per character" and has a
  comment recording the line that estimate ran through a label.
- **The layout is asserted, not eyeballed.** `--check` re-derives the geometry and fails on text
  outside its card, cards overlapping within a column, text straying out of its column, or
  anything leaving the canvas. Proved able to fail all three ways before being trusted, and wired
  into CI beside the architecture-diagram check.
- **The two ideas the figure exists to carry are drawn, not just written:** every fact shows its
  origin (the provenance band traces one `calls(...)` edge to a file and line, a document and
  section, and a work item), and the opt-in inference path is amber, differently dashed, and
  **enters the store at its own inlet** beside the sentence explaining that hypotheses are a
  separate class. A legend names the three line styles.

### Added — a technical section on the PKG in `STATE-OF-SPINE.md`

- **New §3, written for engineers:** what the PKG is (8 node kinds, 11 edge kinds, `file:line`
  provenance on every fact), **AST vs CST and why Spine uses both** — CPython's own `ast` for
  Python because it is the parser that *runs* the code, tree-sitter CSTs for the six compiled
  languages because they are error-tolerant and community-maintained, `sqlglot` for SQL — and
  then the part that answers *why any of it matters*: the failure mode is **silence, not
  fiction**; determinism is what makes `understand --check` and commit-keyed caching possible at
  all; provenance is what makes a downstream claim falsifiable; and the graph is measurably what
  makes the delivery half work (47/68 vs 3/68, control 122/124).
- **The `_resolve_call` invention bug is carried into it as the argument**, because it is the
  clearest evidence for the trade: inventing an id for unresolved names produced **497 fabricated
  edges on this repo (3.16% of the call graph)** and 14.8% in Flask, and `pkg verify` reported
  **0 dangling edges the whole time** because the inventor created the phantom node too. A graph
  can be internally perfect and externally false.

### Fixed — "real parser, never regex" was three cases too strong

- The capability matrix scored that row ✅ unqualified. Three narrow regex fallbacks exist:
  `java`/`csharp` recover the one-line `package`/`namespace` declaration to *name* a module the
  parser already found, and `sql` recovers `CALL`/`PERFORM proc()` — which sqlglot collapses into
  opaque `Command` nodes — as **real `CALLS` edges**. Only the third emits facts, and its corpus
  evidence is **one labelled call edge**, so SQL call accuracy is now quoted with its
  denominator. The row stays ✅ with footnote ¹⁰; the claim it is scored against — *structure is
  parsed, not pattern-matched* — holds.

### Fixed — the documents that say where Spine stands were disagreeing with each other

- **The capability matrix's headline count was wrong in both places that quoted it.**
  `capability-matrix.md` said **16 rows where Spine stands alone**; `STATE-OF-SPINE.md` said
  **11**, for the same table on the same day. Counting it gives **22 of 47**. Both documents
  open by warning that a hand-authored matrix in this project was once 22% wrong with nothing
  failing — and neither was exempt.
- **`scripts/matrix-count.py` derives the count instead**, and `--check` fails when the prose
  drifts from the table. Wired into CI beside the architecture-diagram check, and proved able to
  fail three ways — a wrong count in either document, and a row added to the table.
- **`competitive-landscape.md` carried a second, older copy of the matrix** that had drifted
  from the real one on four cells, three of which made Spine look *worse* than the source
  supports. The duplicate is gone; that file is the narrative and `capability-matrix.md` is the
  matrix. Its RBAC cell was the one the matrix records as **corrected twice** — it was wrong
  there for a third time because nothing connected the two files.
- **`SPEC-INDEX.md` was missing five specs** while describing itself as the complete inventory —
  including the capability matrix itself and both measurements it cites as evidence. Its spec
  count read **63** against **70** on disk. Both fixed, and the count is now stated as a command.
- **`preflight-baseline-diff.md` still read "Proposed — awaiting approval"** — it shipped, with
  `Baseline`, `capture_baseline` and 11 tests. Fourth instance of the failure `SPEC-INDEX.md`
  exists to catch, and it was in none of them because the spec was not indexed.
- **Test-count drift in `STATE-OF-SPINE.md`** — 2,569 across 293 files, re-measured at **2,598
  across 297**.

## 3.21.0 — The right research for the ticket, and a first run that asks for nothing

A small release with one theme: **fewer steps between someone new and a useful answer**, and
research shaped to the ticket rather than one fixed pass for everything.

### Added — issue-type workflow profiles

- **Three profiles ship** — `default`, `bug`, `enhancement` — and the ticket's issue type chooses
  one. **Selection is a lookup, never a model:** a model deciding which research a ticket gets
  would make the Evidence unreproducible at a commit, and that reproducibility is what every
  stage downstream rests on. An unmapped type falls back to `default` **and the run says why**.
- **The `enhancement` profile has no root-cause node**, which is the reason it exists. RCA
  localizes a *symptom*; a feature request has none, so it resolved nothing and printed *"Not
  localized to a repo symbol"* — an empty section that reads as a finding, bought with a
  `git log` subprocess. An enhancement run now records RCA as **not run for this issue type**.
- **A repo may carry its own profiles** in `.spine/workflows/<name>.yaml`, where a profile of the
  same name **wins** over the shipped one. They are validated exactly like shipped profiles:
  coming from the repo is not a reason to check a graph the SDLC will execute less carefully, or
  more.
- **`orchestrator sdlc workflows`** lists what is available, with its source, and which issue
  types choose it.

### Changed — the first thing a new user runs

- **The quick-start leads with `orchestrator state`**, which needs no API key, no `.env`, and
  **writes nothing**. It opened with `orchestrator init && orchestrator doctor` — implying
  configuration was required before anything worked, which it is not — and then `understand`,
  which writes 62 files into the repo of someone who has not decided to adopt anything.
  `init`/`doctor` now sit under *"when you want it to write code"*. `USER_GUIDE.md` gets the same
  as Step 1.5.
- **`understand` says what it learned** instead of listing what it wrote. Its last output was a
  JSON array of every filename; it now leads with the shape of the graph and names **three**
  files to open first. `--json` keeps the old output for anything that parses it.

Measured cold on a clean machine against 3.20.0: **25s to install, 0.8s to answer** — ~28s from
nothing to a grounded statement about someone else's repository. Recorded in
[`gap4-adoption-distribution-roadmap.md`](docs/specs/gap4-adoption-distribution-roadmap.md).

### Fixed

- **`episteme` regeneration runs on `develop` only.** On `main` it opened a PR the workflow could
  not create, so the branch landed and nothing opened — four releases, four orphan branches
  nobody saw, because the step warned and exited 0. Enabling the setting would not have fixed it:
  `main` receives only what `develop` already regenerated, so the whole diff was one commit SHA
  in a stamp, and merging it created a new commit that made the stamp stale again. A loop that
  could not converge. It also dismissed the promotion PR's approval on every push, which is what
  made the 3.20.0 release take three head changes and two re-approvals.

### Note for operators

Nothing in this release changes what a run does to your repository, and the two refusals
introduced in 3.20.0 — an unbound acceptance criterion, and a design naming a place your
repository does not have — are unchanged. `SPINE_SDLC_IMPERATIVE=1` still restores the pre-3.20
path.

## 3.20.0 — The evidence drives the run

3.19.0 measured what the code graph is worth. This release puts it in front of the work: every
SDLC run now begins with a **deterministic research pass**, and design, codegen and the
acceptance criteria are judged against what it found instead of against the ticket's own
account of itself.

Phases 1, 2a and 2b of [`graphir-sdlc-workflow.md`](docs/specs/graphir-sdlc-workflow.md).

### Fixed — four defects in how a ticket reached the code

The pipeline checked a ticket against the graph **once**, at `validity`, and thereafter the
*ticket* drove every downstream stage. Each of these had shipped for months:

- **Root-cause analysis never ran.** `build_rca` is deterministic and produces a fault site,
  ranked hypotheses and a regression surface — and `sdlc autorun` did not call it. An autonomous
  bug run did no root-cause work at all. It now runs on every ticket.
- **The blast radius described the design's own guess.** `design.py` computed impact from its
  own `files_to_touch`, so a wrong proposal produced a faithful analysis of a fiction — and it
  read as verification. It is now computed from **where the ticket lands**, before design runs,
  and handed in. `design.py` no longer computes one, and a test fails if that call comes back.
- **The research was flattened to filenames.** `Landing{name, where, kind, callers, module}`
  became `where.split(":")[0]` before anything downstream saw it, so design and codegen received
  file paths where the research had proved symbols. The whole fact now survives the stage
  boundary.
- **Acceptance criteria were taken on trust.** They originate in intake — a model reading a
  document, not the code — and were read straight through by design, codegen and grounding.
  Each one is now bound to a symbol and a `file:line`.

### Added — Evidence, and two refusals

- **`Evidence`** — one artifact per run composing `investigate` + `rca` + `blast_radius`. No
  model, so it costs nothing and is reproducible at a commit. Written **even when the run parks**,
  because a parked run's evidence is what a human is being asked to judge. Lands as `evidence.md`
  and `evidence.json`.
- **Criterion binding refuses a false premise.** A criterion naming code the graph does not hold
  stops the run — a criterion nobody can locate is a test nobody can write. **Prose never parks a
  run:** CamelCase, `ALL_CAPS` env vars, tool names and plain English are not claims about your
  code and are reported as "not a code claim". The rule is borrowed from the doc-drift
  reconciler rather than invented, so there is one definition of "names a symbol".
- **A validator on the design.** A design naming a directory or module the repository does not
  have parks the run instead of reaching codegen. A new file in an existing directory is fine —
  that is a file being created. **0 false positives across 100 measured runs.**
- **`orchestrator sdlc explain <run>`** renders the graph a run actually executed, skipped nodes
  included, from a typed per-node `Case` with the digest of what each node produced.
- **`orchestrator sdlc workflow`** prints the validated SDLC profile — `sdlc/profiles/default.yaml`,
  the pipeline as data rather than as Python stage ordering.
- **`NodeType.TOOL`** in GraphIR: a deterministic node with no model call, no network, no clock
  and no RNG, whose output is digested — so "deterministic" is checkable rather than asserted.

### Measured — and one feature declined

- **Phase 1 gate:** 0 divergences over 20 runs across 5 commits, plus a determinism test that
  renders under five `PYTHONHASHSEED` values in subprocesses. **Free** — the compared nodes are
  deterministic.
- **Phase 2a gate:** verdict parity over 20 runs across 5 commits, 0 unexplained mismatches, and
  a parking-rate delta showing 5 new parks, all the one ticket naming a symbol no repository has.
  Also free.
- **Phase 2b: the design promotion was measured and declined.** A 100-run A/B across two frontier
  models found no acceptance difference a 50-run arm can resolve, a held-out rate favouring the
  deterministic design (**0.60 [0.46, 0.72]** against **0.40 [0.28, 0.54]**), and **1.98× the
  cost**. `design` stays deterministic and the model-call budget stays at three. The validator
  ships regardless. The held-out gap is one model, so the finding is *"helped neither and hurt
  one"* — full result, including what would reopen it, in
  [`design-promotion-ab-results.md`](docs/specs/design-promotion-ab-results.md).

### Fixed — three defects the benchmark harness found before it ran

Pre-flighting the A/B with four real calls, about $2.50, caught three things that would each have
produced a confident wrong number:

- **The benchmark had no design stage.** It drives `LLMCodegenAdapter` directly and never called
  `autorun`, so `produce_design` was never in its path — the 200-run grounding study measured
  codegen with no design in the loop whatsoever.
- **`_llm_design` had never worked.** It parsed with `json.loads`, the model answers inside a
  markdown fence, and `produce_design` catches every exception and returns the deterministic
  design. The model arm would have **silently measured the skeleton and reported it as the
  model's work.** It now uses codegen's tolerant loader, and a fallback is recorded as the
  *absence* of a measurement.
- **The validator refused prose.** Models write sentences into `data_changes`; a sentence
  containing a path was judged as a path. Every ticket in the model arm would have been rejected
  and reported as the model inventing code.

### Changed

- **`assets/spine-architecture.png` is generated, not drawn.** Its predecessor was stamped
  `3.8.4`, claimed `41 commands` against 53, and carried `7 node kinds · 9 edge kinds` two
  releases after `ARCHITECTURE.md` had corrected them to **8 and 11**. Every number is now read
  from source at render time by `scripts/render_architecture_svg.py`, the SVG is the committed
  source, and **CI fails if the image no longer matches**.
- `CLI_REFERENCE.md` documented `SPINE_IR_SHADOW` and a `shadow.json` artifact, both removed in
  Phase 2a. Replaced with what a run actually writes.
- `SPINE_SDLC_IMPERATIVE=1` restores the pre-3.20 path for one release: stages re-derive their
  own view, and neither refusal above can park a run. Documented in `.env.example` and
  `OPERATIONS.md`.

### Note for operators

Two verdicts can park a ticket that previously built — an unbound acceptance criterion, and a
design naming a place your repository does not have. Both park with their evidence on disk;
`sdlc runs approve` continues a run you disagree with, and `SPINE_SDLC_IMPERATIVE=1` turns both
off wholesale.

## 3.19.0 — What the graph is worth, measured; and four ways it was wrong

3.18.x claimed the code graph makes generated code better. This release **measures that claim**
in a controlled A/B — and every extraction defect below was found by pointing the measurement at
codebases that are not this one.

### Added — the grounding claim is now a number

- **200 runs, 2 frontier models, 5 passes each, grounded against an identical ungrounded
  control.** Every new module that integrated correctly came from a grounded run — **29/50**
  under `mypy --strict` plus correct placement; the same tickets ungrounded produced **none**.
  On tickets that already name the target file the two arms tie (**98/100**), which rules out a
  generic more-context effect and locates the payoff precisely where the model cannot see.
  Method, Wilson bounds and the abort accounting are in
  [`codegen-model-comparison-results.md`](docs/specs/codegen-model-comparison-results.md).
- **Replicated on an unrelated external codebase**, so the result is not an artefact of Spine
  grading itself — see
  [`external-repo-grounding-results.md`](docs/specs/external-repo-grounding-results.md).
  Combined across both: **47/68 grounded, 3/68 ungrounded**, with the `edit`-ticket control at
  122/124 either arm.
- **Held-out grading.** Each ticket's acceptance suite is authored separately from the ticket;
  the model never writes the test that grades it. A pass that never reached the model is
  recorded as **aborted and excluded**, not silently counted as a failure.
- `scripts/bench_aggregate.py` — Wilson score intervals (they stay sensible at small *n* and
  near 0 or 1, where the normal approximation does not), aborted passes excluded, and a warning
  below five passes.

### Added — preflight judges the change, not the repository

`sdlc` preflight now captures a **baseline** of `ruff check`, `ruff format` and `mypy` findings
before generation and diffs against it, so a repo that already has findings no longer fails
every build on pre-existing debt. A check whose baseline could not be captured is reported as
skipped rather than assumed clean. Design record:
[`preflight-baseline-diff.md`](docs/specs/preflight-baseline-diff.md).

### Fixed — extraction defects, each found on real code

- **A SQL Server database project was 95% invisible.** SSMS scripts UTF-16 by default and
  separates batches with `GO`, a client directive that is not valid T-SQL — so an entire file
  failed to parse and was skipped in silence. Encoding is now sniffed from the BOM and files are
  split on `GO` and parsed batch by batch. On one real project: **676 of 709 `.sql` files
  skipped → 0**, `READS` 2 → 186, `WRITES` 0 → 26. Neither cause was a dialect problem, so
  `--dialect tsql` never helped.
- **931 dangling edges on a real C#/TypeScript/SQL codebase.** TypeScript emitted edges to
  imported symbols with no node to land on (**854** of them); C# qualified an external base type
  into the deriving type's own namespace, inventing a target that does not exist. Both now
  resolve to explicit external nodes.
- **`extractor.py` discarded `finalize()`'s return value** — a front-end's whole-repo post-pass
  computed its corrections and threw them away. C# is the first front-end to need one, which is
  how this surfaced.
- **TypeScript resolved relative imports three different ways**, so the same module reached the
  graph under up to three ids. One resolver now serves declaration, call and type resolution.
- **`state` rendered a different report for identical input.** Areas tying on their symbol
  counts sorted out of a `set` on score alone, and Python randomises string hashing per process
  — five identical runs produced three distinct outputs. The sort key is now total. The
  regression test renders under five `PYTHONHASHSEED` values **in subprocesses**, which is the
  only way to see the bug: the seed is fixed for the life of a process. `state` is byte-stable
  again, as [ARCHITECTURE.md](ARCHITECTURE.md) always claimed; the 3.18.1 caveat is withdrawn.
- **LiteLLM client.** `reasoning_effort` is sent only to models that declare support (from
  `litellm.model_cost`, configurable with `ORCHESTRATOR_REASONING_EFFORT`, default `high`);
  `response_format=json_object` is no longer sent alongside function tools, which OpenAI
  rejects; every completion is wrapped in `asyncio.wait_for`, so a hung request fails the pass
  instead of hanging the run; and the served model name is recorded from the response rather
  than assumed from the request.

### Documented

- [`capability-matrix.md`](docs/specs/capability-matrix.md) and
  [`competitive-landscape.md`](docs/specs/competitive-landscape.md) — Spine's column verified
  against source, every other column marked public-documentation-only. RBAC is scored **🟡**
  after an audit found `has_role` called at exactly one site, while multi-tenancy holds at ✅.
- [`parsing-and-the-pkg.md`](docs/specs/parsing-and-the-pkg.md) — why every front-end is a real
  parser (AST or tree-sitter or `sqlglot`) and never a regex, written for an engineering
  audience.
- `ORCHESTRATOR_REASONING_EFFORT` documented in `.env.example`, `CLI_REFERENCE.md` and
  `USER_GUIDE.md`; the SQL Server capability in `KNOWLEDGE_GRAPH.md` §4 and `FEATURES.md`.

## 3.18.1 — The documentation catches up, and corrects itself

No code changes. 3.17.0 and 3.18.0 shipped two releases of PKG work without the user-facing
documentation following, and this is that sweep across all eleven documents. It **corrects more
than it adds**, because auditing the docs against the CLI and the fact vocabulary turned up
claims that were simply untrue.

### Fixed — documentation that was wrong, not merely incomplete

- **`CLI_REFERENCE.md` was missing four flags and two whole commands.** `--check` and
  `--intents` on `understand`, `--no-timestamp` and `--intents` on `state`, and the commands
  `sdlc baseline` and `sdlc runs` appeared nowhere. The command count read 48; it is **51**.
  The file also claimed to be *"auto-generated from the CLI"* — **there is no generator**, and
  that false claim is precisely how it drifted this far without anyone checking. It now says it
  is maintained by hand, and that `--help` wins any disagreement.
- **`ARCHITECTURE.md` undercounted the fact vocabulary** — "7 node kinds and 9 edge kinds"
  against an actual **8 and 11**. `Intent`, `CONSUMES` and `SERVES` had been shipping
  unmentioned.
- **`KNOWLEDGE_GRAPH.md` carried a literal `</content>` tag at end of file**, omitted Go from
  its parser list, and documented the deprecated `--db` flag instead of `--out`.
- **`CODEX_GUIDE.md`'s `language` parameter listed six values**, missing `go` and `sql`;
  `CLAUDE_GUIDE.md` still said "seven languages".
- **`FEATURES.md` marked the runtime and invention oracles as fully shipped.** Both are
  **Python-only**, which is a material qualification on any accuracy claim made from them.

### Added

- **Measured accuracy, stated wherever the graph is described** — precision **1.00 on every
  node kind and every edge kind across all eight front-ends**, with `CALLS` recall from 1.00
  (C, SQL) to 0.50 (TypeScript). README, USER_GUIDE, KNOWLEDGE_GRAPH (a new §10), ARCHITECTURE
  and EXAMPLE now carry the numbers rather than the adjective "grounded".
- **The four oracles, the `--check` currency gate, and the language extras** documented in the
  guides and OPERATIONS, including that a missing tree-sitter extra makes a language
  *unmeasured* rather than scored zero.

### Documented as limits, rather than smoothed over

Each of these would otherwise be read as a guarantee it does not make:

- **`state` is not byte-stable.** Components tying on their symbol counts sort out of a `set`
  with a non-total key, so identical runs differ — five runs produced three distinct outputs.
  `PYTHONHASHSEED=0` is the workaround. `ARCHITECTURE.md`'s determinism claim now carries the
  exception instead of overstating it; `understand` is unaffected.
- **`--intents` has no reader.** Not merely opt-in: nothing renders the facts, no export format
  emits them, and the only visible effect is a count in the graph-size line. Saying "opt-in"
  alone implied a payoff that is not there.
- **`invention` and `runtime` reporting `0` on a non-Python repository means *not measured*,
  not *clean*.** Every candidate returns unexaminable and the total prints as zero.

### Note on the design records

`docs/specs/pkg-accuracy-gaps.md` and `docs/specs/comprehension-test-plan.md` are committed
rather than left untracked. `understand` ingests markdown from disk regardless of git, so an
untracked *or gitignored* file still becomes a `Doc` node and makes the local knowledge base
disagree with CI's — verified: gitignoring them changed nothing and `--check` still failed on
the same three files. Tracking them is what makes local and CI agree.

## 3.18.0 — Every front-end, and one finding withdrawn

**Scope, first.** 3.17.0 measured two of the eight front-ends. This measures **all eight** —
and that sentence needs a boundary drawn around it, because it is easy to read as more than it
is. The numbers come from **19 hand-written fixture repositories**, two cases per language. They
describe *this extractor's behaviour on shapes we chose*, not anyone's real codebase. The one
oracle that measures real code — the runtime tracer — still covers Python only.

### Added

- **A corpus for every front-end Spine supports.** `java`, `csharp`, `c`, `cpp`, `go` and `sql`
  each gain a control case and one hard shape. Every node kind and every edge kind except
  `CALLS` scores 1.00/1.00 in every language.

  | language | `CALLS` precision / recall |
  |---|---|
  | `c` `sql` | 1.00 / 1.00 |
  | `python` | 1.00 / 0.73 |
  | `cpp` `csharp` `go` `java` | 1.00 / 0.67 |
  | `typescript` | 1.00 / 0.50 |

  `CALLS` is the only kind needing *resolution* rather than parsing, and the only one that
  loses anything. Every language's remaining loss is the documented instance-dispatch skip.

- **CI measures every front-end it ships code for.** The six tree-sitter extras are now
  installed in CI. Before this, only `python` and `sql` front-ends registered there — so 89
  tests skipped, and the TypeScript figures in the committed baseline had been measured once on
  a developer machine and skipped by every run since. A TypeScript regression could not have
  failed a build.

- **`--intents` on `understand` and `state`.** The intent scan is 3× faster (23.0s → 7.6s):
  `git blame` runs concurrently at 8 workers, and the commit lookup is one `git log` instead of
  one per commit. It stays **opt-in** — nothing renders or queries `Intent` facts yet, so
  default-on would cost 10s per build for byte-identical output. It becomes a default when
  something reads it.

### Fixed

- **The graph no longer asserts a call to anything that does not exist — in any language.**
  Two front-ends invented call targets for a name they could not resolve:

  - **Python** emitted `py:{name}` for any bare call that was not a builtin, an import, or a
    module-level def — every parameter, local and nested function. **497 edges here, 3.16% of
    the call graph.** The resolver already stated the right rule for ambiguous attribute
    chains — *skip rather than guess* — three lines below; it simply was not applied to names.
  - **C** emitted `c:{name}` for a call through a function-pointer parameter. C could not take
    Python's fix: an unresolved callee in C is usually a function declared in a header and
    linked from another translation unit, and skipping those would silence every
    cross-translation-unit call in every C repository. The test is *"did this function bind the
    name"*, not *"can we resolve it"*.

  Python `CALLS` precision 0.80 → 1.00, C 0.67 → 1.00, with recall unchanged in both — the
  evidence that nothing true was removed.

- **Per-file parity credits an endpoint to every file that declares it.** `Endpoint` ids are
  keyed on verb+path, so services sharing a route collapse into one node; counting by node
  provenance credited one file and reported every other as short. Attribution now follows the
  `EXPOSES` edge. Shortfall 5 → 0.

### Withdrawn

- **"Two live routes invisible to the graph" (3.17.0) was false.** `GET /healthz` and
  `GET /readyz` were always extracted and `EXPOSES` always reached every handler. The finding
  came from reading *"this file declares 2, graph holds 0"* and not checking whether the nodes
  existed elsewhere — they did. The entire shortfall was the artifact described above.

  It is recorded rather than deleted, here and in the design records, because of what it is: a
  measurement built to catch under-reporting produced a false finding, and nothing in the check
  could tell the difference. That is the failure this work exists to prevent, occurring inside
  it.

## 3.17.0 — Is the graph right?

**Scope, stated first so nothing below is read as more than it is.** This release makes the
PKG *measurable* and measures **two of the eight front-ends** — `python` and `typescript`.
The other six (`java`, `csharp`, `c`, `cpp`, `go`, `sql`) gain the machinery and no numbers:
`pkg accuracy` will score them the moment a corpus exists for them, and reports them as
**skipped** rather than zero until it does. The capability matrix still says what a front-end
*can* emit; only Python and TypeScript now also say how well.

### Added

- **Measured graph accuracy.** Every claim Spine makes about a codebase is a claim about
  the PKG, and until now those claims were adjectives — "grounded", "deterministic",
  "precision-first" — none of which survives *"how do you know?"*. `orchestrator pkg
  accuracy` answers it with four oracles, each a different trade of cost against reach:

  | oracle | needs | answers |
  |---|---|---|
  | corpus *(default)* | hand-labelled fixtures | precision **and** recall, per kind, per language |
  | `--oracle runtime` | a test suite to run | `CALLS` recall from real execution, on any repo |
  | `--oracle parity` | only the source | declared-vs-emitted routes and tables, per file |
  | `--oracle invention` | only the source | `CALLS` edges that are fiction, exactly |

  The corpus is published alongside the numbers, in `corpus/`, with the labelling method —
  a recall figure nobody can reproduce is an assertion with a decimal point in it.

- **An accuracy regression gate.** `pkg accuracy --check` compares against a committed
  baseline and fails a build when a *gated* number drops. It is now in the quality gate
  next to `mypy` and `ruff`. What may be gated depends on what a number is measured
  against: corpus scores come from committed fixtures and are gated strictly; per-file
  parity ratchets one way; the invention count is recorded and never gated, because it is
  measured against the repository itself and moves whenever anyone writes ordinary code.

- **The number where a person will actually see it.** The build document's blast radius now
  states the measured recall for its language — *"Measured `CALLS` recall for python is 0.73
  (against the extractor's own test corpus, not this repository) — treat this list as a
  lower bound."* The parenthetical is not decoration: a reader who takes that figure as a
  statement about their own repository has been misled.

- **The intent layer — `Intent` nodes and `SERVES` edges.** The graph's vocabulary was
  entirely mechanical: what calls what, what contains what. It now records *what a symbol
  was last changed for*, joining `git blame` to the issue key in each commit's message.
  Deterministic, no model, and it reports its own coverage — which depends on the
  repository's history, not on the method.

  **Library only in this release.** `orchestrator.pkg.intent_link.link_intents(batch, root)`
  is not wired into any command, because the scan costs about 11× an extraction (2.0s → 23.0s
  on this repository) and `git blame` runs once per file. Making it default-on needs a
  commit-keyed cache or a single `git log --numstat` walk; that is its own change.

- **CI measures every front-end it ships code for.** Previously CI installed neither the
  `typescript` extra nor the five other tree-sitter ones, so only `python` and `sql`
  front-ends registered — 89 tests skipped, and the TypeScript figures in the committed
  baseline were measured once on a developer machine and skipped by every run since. A
  TypeScript regression could not have failed a build. All six extras are now installed.

### Fixed

- **A systematic false-positive class in the Python front-end, now measured.** A call
  through a parameter, a local variable, or a nested function emits a `CALLS` edge to an
  `external` node that does not exist — `py:echo` where `echo` is a callback argument.
  **496 such edges here, 3.16% of the call graph.** Detected exactly rather than sampled;
  the front-end fix is its own ticket, with this number to prove itself against.

- **`source-parity` counts instead of testing for presence.** It asked *"does this language
  have **any** `Endpoint` node?"*; it now asks, per file and with `file:line`, *"this
  declares 4 route decorators and the graph holds 1 — where did 3 go?"*.

  ~~It found two live routes invisible to the graph: `GET /healthz` and `GET /readyz`.~~
  **Corrected after release:** that finding was false. Both routes were always extracted and
  `EXPOSES` always reached every handler. `Endpoint` ids are keyed on verb+path, so services
  sharing a route collapse into one node, and per-file parity counted by node provenance — so
  every other declaring file read as short. The shortfall was the metric's own artifact. Fixed
  in the next release; the claim is struck rather than deleted because a measurement that
  produced a false finding is worth recording.

## 3.16.2 — A host that owns the model, and four failures that read as crashes

### Added

- **The build document as an MCP tool.** `sdlc_plan` renders the twelve sections for a
  host that has its own model and its own tracker credentials — **no key on Spine's side,
  no credentials, nothing spent.** The case it exists for: an enterprise machine whose
  only model lives in the desktop app and whose Jira sits behind a Server/DC personal
  access token that Spine's own adapter cannot speak to. The answer was not to teach Spine
  to reach that Jira but to invert the dependency — the host reads the ticket, drafts the
  spec, and hands it over; Spine has the graph and the twelve sections. `build_plan` was
  already free of both, so exposing it cost a wrapper.

  A spec arriving from a model is validated before anything renders, and the refusal is
  **returned rather than raised** — the caller is a model that can read the error and fix
  the spec, which an exception on the host's side does not let it do. A test asserts the
  tool and the CLI render byte-identical documents: two surfaces over one renderer must
  not be able to disagree.

- **`sdlc_approve` as an MCP tool**, so the gate has a path on a machine driven entirely
  through the plugin. It binds to a digest of the document exactly as the CLI does, and
  **refuses to invent an approver** — a host may know its user; this process does not, and
  an approval attributed to nobody is a rumour rather than a record.

### Fixed

Four expected conditions printed tracebacks where one line belongs. **Every one was found
by someone running a command, not by a test.**

- **A mistyped `--source` printed forty lines.** `SourceUriError` was uncaught in `ingest`,
  `openspec draft` and `sdlc plan`, while `investigate` had caught it since before any of
  them existed. One line, exit 2.
- **Missing credentials printed sixty, and named the wrong provider** — someone with
  `OPENAI_API_KEY` set was told the *Anthropic* key was missing, because the key and the
  model are separate settings and nothing connected them. The message now names the model
  that actually resolved and the variable that changes it.
- **litellm's "Give Feedback / Get Help" banner is suppressed.** It printed on every
  exception litellm *mapped*, including ones we catch and recover from, so a successful run
  emitted six blocks that read as six failures.
- **The temperature warning reads as a sentence** rather than a bare event name, and says
  what it costs: a spec derived on that model may differ between runs.

### Changed

- **The plugin's tiers are three, not two, and "gated" says what it costs.** It means two
  separate things — every call spends real money whether or not it succeeds, and `live=true`
  writes where you cannot take it back. **Safe mode still costs tokens**; it keeps writes
  local. Between comprehension and the gated run sits a tier that costs nothing and writes
  only under `.spine/`: plan, and approve. Both guides carry the table and the rule — work
  down the tiers, never up.

### Documentation

- Both guides gain `sdlc_plan` and `sdlc_approve`, including the thing an assistant cannot
  infer from a docstring: **`met_criteria` is where the integration earns its place.** A
  host has read the ticket *and* can call `explain_symbol`, so it can spot a criterion the
  code already satisfies — the one judgement no deterministic pass can make.

## 3.16.1 — The paths nobody ran

Everything in 3.16.0 was exercised through `--spec`, which makes no model call. The first
run through `--source` found that path broken, and the sweep that found it is now written
down.

### Fixed

- **A model that refuses our temperature no longer kills the run.** Intake pins
  `temperature=0.0` so a spec is stable for a given intent; `claude-opus-5` — the default
  for every stage — accepts the parameter but only at `1`. Every `--source` path therefore
  died on its first model call: `ingest`, `sdlc plan --source`, and `autorun` without
  `--spec`, each with a forty-line traceback where one line belongs. A refused temperature
  is retried once without it, matched on the message rather than the exception type,
  because litellm raises `UnsupportedParamsError` for several unrelated parameters and
  retrying an unrelated failure turns one clear error into two. **The determinism the pin
  buys is gone when this fires, so it is logged rather than swallowed.**

- **A confidence check that cannot apply is no longer scored as a failure.** Section 12
  read "Root cause: a file at best — no line established" for every feature ticket, capping
  each enhancement a point below every bug and asserting a file had been named when section
  3 said none was. Inapplicable rows now render `n/a` and leave the denominator, so an
  enhancement reads "3 of 4 applicable checks" against a bug's "3 of 5".

### Changed

- **Section 11 prices a swap across providers.** The cost table listed the resolved model
  and its provider siblings, which cannot answer what switching costs. It now shows the
  nearest three by input price from anthropic, openai and gemini, with a provider column.
  By price tier, not recency — the catalog carries no release date, so "the latest model"
  is not a fact available to the renderer, and a hardcoded list of latest ids goes stale
  silently.

### Documentation

- **`docs/specs/cli-test-plan.md`** — every one of the 50 CLI commands with prerequisites
  and acceptance criteria, tiered by blast radius, with per-sweep result recording. Four
  known defects are marked as currently failing rather than written as if they pass.
- **`.env.example` restructured** so its blocks read in the order they are configured, and
  seven variables already in use gain documentation — including that MCP is preferred for
  reads and is not a path for writes at all.

## 3.16.0 — Nothing gets built that nobody read

### Added

- **Plan before code: `orchestrator sdlc plan` renders a build document, and
  `sdlc approve` gates on it.** Six live runs on one ticket produced usable source most
  times and completed zero times, at roughly $4.75 — and every failure was a decision made
  silently: a design naming the wrong files, a prompt rule forbidding the shape the spec
  asked for, and two acceptance criteria describing behaviour the code already had. All
  three were visible in a document that costs nothing to produce. One ticket now yields
  twelve fixed sections — requirement, intent, root cause, what the graph knows, blast
  radius, design, files, criteria, facts for the generator, the codegen prompt, cost and
  confidence — assembled from the ticket, the graph, git and the repo's own tests. **Every
  section carries where it came from**: quoted, computed, inferred, or decided by a person.
  A document that mixes a quoted requirement with an inference and does not say which is
  which lends the authority of the first to the second. See
  [`docs/specs/build-document.md`](docs/specs/build-document.md).

- **Acceptance criteria have three states, not one.** Stated, *stated but already met by
  code that exists*, and proposed. The ticket this was built for filed six criteria of
  which two described behaviour `_check()` already had; a run would have reported them met
  having changed nothing. `FeatureSpec.met_criteria` maps a criterion's exact text to the
  evidence that satisfies it, and the criterion stays on the page marked rather than
  quietly disappearing from the list. A key matching no criterion is reported as a
  mismatch rather than silently ignored.

- **`CONSUMES` — the client half of the route join.** `EXPOSES` gave a route its handler
  and nothing pointed *at* an endpoint, so a public route was a leaf: something the server
  declared that nothing in the repo appeared to want. That is why a ticket about "the
  registry API" retrieved the server modules and never reached the CLI that calls them.
  Python source now yields `CONSUMES` edges from a caller to the endpoint it calls, and
  `impact_of` follows them, so changing a handler reaches the code that would break.
  Literal paths only — a request built from an f-string yields nothing, because a wrong
  edge is worse than an absent one.

- **A plan is approved against a digest of what was read.** `sdlc approve` records who,
  when, why, and the commit it was derived at. `sdlc autorun` re-derives the plan and
  compares: same tree, same digest, approval stands; anything else refuses, because an
  approval that survives the code moving underneath it approves a document nobody has
  read. The gate is on by default (`--no-plan-gate` to skip, and it says so out loud), and
  a refusal parks rather than fails — the ticket is fine, the review has not happened.

- **The journey: each run appends, no run rewrites.** Stage results, the run outcome with
  measured tokens and spend, and — the reason it earns its place — the disagreement between
  what implement touched and what the design named, in both directions. Append-only by
  construction: there is no update and no delete, because a later stage that could tidy an
  earlier one away removes exactly the evidence worth keeping.

### Changed

- **A bug ticket is not a traceback.** Fault localization read anchored exception lines and
  `File "x.py", line N` frames, so a ticket carrying `ConnectError: [Errno 61] Connection
  refused` in plain prose localized to nothing at all. An exception named anywhere in the
  text is now read — still requiring the `SomeError: …` colon form, so "this Error handling
  is poor" stays a complaint — and source paths the text names are resolved against the
  graph. A stated file is a *module*, never a fault site, and the two are never presented
  as each other. `orchestrator rca` and `orchestrator localize` were blind the same way and
  both improve.

- **`pkg extract` counts edges by kind.** One total cannot show a kind that stopped being
  emitted: `edges: 31073` reads identically whether the call graph resolved or collapsed to
  zero while imports doubled. `FactStore.summary()` now carries an `edges_<kind>` count for
  every kind, printed under the scan line and included in `--json`. Kinds with no edges
  report `0` rather than being omitted — `REFERENCES 0` on a repo with entities is the line
  worth reading, and a missing key looks like a question nobody asked. Existing keys are
  unchanged, so the ten callers of `summary()` are unaffected.

### Fixed

- **The repair pass can revise a file it already wrote.** It was told *"do NOT include them
  again"*, which is right about stale anchors and wrong about everything else: a run wrote
  `api_errors.py` on one attempt, rewrote `cli.py` against renamed helpers on the next, and
  could not rename the module to match. The import failed and no later stage could reach
  the file. The instruction now says what it means — leave them out if they are correct,
  and re-anchor against the current content, which is shown, if one needs changing.
- **A refine that describes a change it did not send is refused.** `refine` and `revise`
  allow an empty submission, because a pass with nothing to change is a legitimate no-op.
  One answered *"Rewrote orchestrator/api_errors.py to export api_call…"* and sent no files;
  that read as "nothing to change", the loop stopped, and a run that had correctly diagnosed
  its own failure ended without fixing it. A claim now needs a past-tense change verb **and**
  a path before it counts as one, so "no changes needed in cli.py" stays a no-op.

- **A new module is not a "parallel module".** The implement prompt said *"when the SPEC
  names existing files, change THOSE files; do not create a parallel module instead"* — a
  rule added after a run wrote a helper beside a file and never wired it in. As written it
  also forbade the ordinary shape of an extract-a-wrapper refactor, so a spec asking for
  "one place that wraps the request" left the model choosing between its instructions; three
  runs submitted zero files. The prohibition is now on the outcome it was protecting
  against: the named files must appear with `edits`, and a new module alongside them is
  allowed.

- **A design honours the paths its spec names.** The heuristic design read only the ticket's
  title and summary, matching those words against the graph — so a ticket about "the
  registry API" whose acceptance criteria named `src/orchestrator/cli.py` twice came back
  proposing the registry *server* modules, and omitted the one file the spec named. Codegen
  is handed that design as an instruction, and on a live run it submitted nothing at all
  rather than choose between a spec and a design that disagreed. Paths stated in the
  criteria and technical notes now come first; the graph reading and the overview remain as
  fallbacks. A stated path that does not exist is dropped — naming a file to *create*
  belongs in the approach, not in a list of files to open.

- **The repair path no longer crashes on a symlinked worktree.** `applied_paths` names the
  files an attempt already wrote so the repair retry knows not to resend them — computed
  with a bare `Path.relative_to`, which raised on macOS, where `/tmp` is a symlink to
  `/private/tmp`: the written paths come back resolved and the worktree root does not, so
  nothing is "in the subpath of" anything. The `ValueError` killed the whole run, and fired
  only when an attempt partially succeeded, which is the exact case the information exists
  to rescue. Both sides are resolved now, and a path genuinely outside the worktree is
  reported rather than raised.

## 3.15.0 — Everything that reports success has to be able to report failure

### Added

- **The validity gate weighs codegen's context budget.** A spec can be right-sized by every
  other measure and still not fit in front of the model: `_check_size` counts criteria and
  modules, and neither correlates with bytes — one 56 KB file passes the module count and
  exceeds the whole budget alone. The gate now sums the files a change names and compares
  them to `codegen._MAX_CONTEXT_BYTES`. Past 1.5x it returns `TOO_BIG`; in the margin below
  it warns and still proceeds, because anchor-located excerpting copes there and refusing
  would block runs that work. Inert without a repo root, so callers that cannot measure get
  exactly the verdicts they got before.

### Changed

- **Migrated to the MCP Python SDK v2** (`mcp>=2`). v1 spelled three things differently:
  the client transport is now `streamable_http_client` (and takes a configured
  `http_client` rather than `headers`, yielding two streams not three), the result flag is
  `is_error`, and the server class is `mcp.server.MCPServer` — `mcp.server.fastmcp` is
  gone. Transport settings moved off the server constructor onto the run call, so
  `build_http_server` returns an `HttpServer` carrying both. Reading the error flag with
  `getattr(result, "isError", False)` is also fixed: under v2 that silently reported every
  tool error as a success, which is worse than the AttributeError the default was avoiding.

### Fixed

- **`author_tests` may answer "nothing to test" — but only when that is true.** The stage is
  unconditional and no spec can turn it off, and an empty submission was always treated as a
  skipped job and retried. On a documentation-only change it answered correctly first
  ("no tests submitted: this is a prose edit"), was retried anyway, and the retry invented a
  test module for a paragraph and corrupted it with markup — discarding a change that was
  already complete and correct. Empty is now valid when the change touched no testable
  source, and still refused when it did, so a source change that skips its tests is caught
  exactly as before.

- **v2 renamed `Tool.inputSchema` and `readOnlyHint` too, and the defaulted reads hid it.**
  `_to_tool` fetched both with `getattr(..., None)`, so the rename did not raise —
  `input_schema` silently became `None` and every tool lost its argument types. That took
  the `mcp contracts` type labels with it and stopped `MCPRegistry.call` coercing a
  structured argument a server declares as a string, so a governed `jira_create_issue`
  started failing validation again. Nothing went red, because every existing test builds an
  `MCPTool` directly and never exercises the translation from the SDK's own type. A new test
  module builds a real `mcp.types.Tool`, so the next rename fails a test instead of a run.

- **Generated test fixtures are shown the types they construct.** `author_tests` was given
  the source under test but not the definitions of the types that source imports — so a
  fixture building a fake LLM client or a graph store wrote the constructor from inference.
  Three consecutive runs produced correct source and a broken test module that way:
  `CompletionResult` with an invented `usage` kwarg, then `CompletionResult` missing four
  required fields, then `FactStore()` without its `batch`. Writing one signature into the
  spec did not help — the next run failed on a different type. The stage now reads the
  class-shaped names the code under test imports and pulls their definitions from the
  graph, which covers all three.

- **A stub submission no longer counts as progress.** A run wrote a file literally named
  `PLACEHOLDER` containing `x`, and the refine loop treated it as a file change — its stop
  condition is "no file changes", so a model with nothing to say kept the loop alive and
  spent two of three attempts on it while the real failure went unfixed. Placeholder names
  (`PLACEHOLDER`, `TODO`, `FIXME`, `TBD`, `XXX`, `stub`) and essentially-empty bodies are
  dropped at the write, so the loop's existing stop condition works. `__init__.py`,
  `py.typed` and `.gitkeep` are exempt — they are legitimately empty. A submission that is
  *only* placeholders is recoverable and routes to the same corrective retry as submitting
  nothing at all.

- **A partially-applied codegen attempt can now be repaired.** `apply_files` is per-file
  atomic, not per-batch: a successful file is written before the rest are attempted, so a
  later failure leaves earlier ones on disk. The repair then said "re-emit the full JSON
  object" while showing current content only for the files that *failed* — so the model
  resent edits whose `find` text its own previous attempt had replaced, and the retry could
  not succeed. A live run produced a complete, correct change and failed anyway on `edit 0
  'find' text not found`. The error now carries which files landed, and the repair tells the
  model to omit them.

- **`mcp` pinned below 2.0 until the v2 migration lands, and CI now installs the extra.**
  v2 renames what this package uses — `streamablehttp_client` → `streamable_http_client`,
  `CallToolResult.isError` → `is_error` — and drops `mcp.server.fastmcp` entirely. The bump
  passed CI because CI installed `--extra dev` only, so every MCP test skipped and the
  breakage was invisible until someone installed the extra. An optional extra the repo ships
  code for is not optional to test.

- **The repo-invariant check now catches the criterion it was built from.** It needed both a
  nondeterminism word and a named deterministic surface; SSPN-31's own criterion is phrased
  entirely as output shape, naming the JSON key `"regressions"` and never the `regression`
  command, so no surface matched and the gate returned `PROCEED`. Surface names now match a
  trailing `s`/`es` and a possessive. The corpus fixture asserts the verbatim criterion
  rather than a reworded one that names the command — a fixture easier than the real case
  makes a check read as working while doing less.

## 3.14.0 — The pipeline can be trusted about its own output

### Added

- **The validity gate refuses a criterion that contradicts a documented invariant.** The run
  that started SSPN-31 produced a criterion requiring an ISO-8601 `meta.generated_at`
  timestamp in a comprehension command's JSON — nobody asked for it, and it breaks CLAUDE.md
  invariant 2 (`understand` / `state` are deterministic; never a clock, an LLM call, or
  randomness). An agent building to it would have shipped non-diffable output and passed its
  own tests. `assess()` now returns `CRITERIA_WRONG` with evidence naming the invariant.
  Deterministic string matching, no LLM call — an LLM asked whether an LLM invented a
  requirement is not an answer. Proposed criteria are checked too, since an invented
  criterion is the kind most likely to break a rule. A timestamp in a log line, an HTTP
  header or a tracker comment is untouched: determinism is a property of specific outputs,
  not a ban on the word.

- **`--spec` on `sdlc autorun` and `sdlc feature`** — implement a spec you wrote instead of
  one derived from the source. Intake is skipped entirely and the `[intake]` line reports
  `skipped`, so a run summary never implies a source document was read when none was. For a
  settled spec (a remediation, something agreed in review) — or when intake itself is what
  the ticket is about, where letting a defective stage specify its own repair is circular.
  The file is JSON validated against `FeatureSpec` rather than markdown: a misspelled
  `acceptance-criteria` is an error naming the valid fields, not a run that proceeds with no
  criteria and passes by default. An empty criteria list is refused for the same reason.

### Changed

- **`FeatureSpec.acceptance_criteria` has narrowed to the criteria the source
  *stated*.** Criteria the spec writer infers now go to `FeatureSpec.proposed_criteria`
  instead of being concatenated into `acceptance_criteria`, so anything reading that
  field sees **fewer entries than before**. The acceptance judge
  (`orchestrator.sdlc.review.SemanticReviewAdapter`) verifies only the stated set — a
  change can no longer be rejected for failing a criterion nobody asked for — while the
  codegen SPEC banner (`orchestrator.sdlc.feature_runner.run_feature`) and every human
  render surface (`intake.service.spec_to_issue_request`, `intake.report`,
  `intake.web.app`) show the proposed set under its own label rather than dropping it.
  Specs with no proposed criteria render exactly as before.

- **Codegen's source-context budget goes from 40 KB to 200 KB.** 40 KB is ~10k tokens —
  about 1% of the default model's window, and a leftover rather than a limit. It shaped
  work instead of bounding it: a change spanning five files totalled 113 KB, so every file
  was excerpted and codegen quoted an edit anchor from a region it had only partly seen.
  Failure output stays at 40 KB under its own constant — a pytest dump is not source, and
  refine re-sends context every attempt.

### Fixed

- **A failure kind is now advised about the thing it was charged for.** `_failure_kind`
  ordered syntax before anchors; `_corrective_suffix` ordered anchors before syntax. An
  attempt producing both — a stray `</content>` in a new file *and* an edit whose anchor
  missed — was recorded as `syntax` while the model was handed the anchor-repair block, so
  the syntax kind's one correction was spent on advice about something else and the next
  failure had nothing left. The same shape as the shared-retry-pool bug the per-kind budget
  replaced, arriving through misclassification instead. A test now fails if the two
  orderings drift again.

- **A run branches from the branch it will open a PR into.** A clone with no `--branch`
  checks out the remote's *default* branch, so a run targeting `develop` still built on
  `main` — the generated change was written against a tree predating everything merged to
  develop since the last release, and could revert it with nothing noticing. Found when a
  run silently reverted a fix merged two hours earlier. The base branch is now part of the
  cached base repo's identity too, so a base cloned for `main` is rebuilt rather than reused
  for a `develop` run. With no target given, the default branch is still used — this is not
  a silent switch.

- **Intake constrains its output the way codegen and the judge do.** Intent extraction and
  spec writing both asked for JSON with `json_object=True`. `response_format` is
  OpenAI/Ollama-only — Anthropic drops it, and the default model is `claude-opus-5`, so both
  stages ran completely unconstrained and relied on brace-scanning salvage. 3.13.0 fixed
  exactly this for codegen and the acceptance judge; intake was never converted. Both now
  emit through a forced tool call. The text path remains as the degradation route, since a
  forced call constrains shape but a provider can still answer in prose.

- **A server that declares a structured argument as a JSON string now gets one.**
  `mcp-atlassian` types `jira_create_issue.additional_fields` as `string` rather than
  `object`, so every caller had to `json.dumps` it first and a governed create encoded
  twice — once for the field, once for the transport. `MCPRegistry.call` reads the tool's
  declared type and encodes on the caller's behalf. Narrow on purpose: only when the
  declared type is exactly `string` (or `string|null`) *and* the value is an object or
  array. A declared `object`, an undeclared argument, or an unreadable schema passes
  through untouched, and a caller that already encoded still works. The schema round-trip
  is skipped entirely when every argument is a scalar, and a discovery failure falls back
  to the raw call rather than becoming a new way to fail.

## 3.13.0 — A green suite is not a working change

Found by running one ticket end to end a dozen times. Every stage of the build loop was
working from an input it could not see, and reporting the gap as a defect in the work
rather than in its own view. Three separate runs committed code that did nothing: a helper
appended and never called, a file missing its `typing` import, a command wired through an
attribute its class does not have. All three had green tests.

**Behaviour change:** a run is now stopped by things that previously passed. Type errors on
changed lines, a changed file no test exercises, and an acceptance verdict that cannot be
read all block a commit where they used to be silent. Runs that used to finish with a
useless change now fail with a reason.

### Added

- **`orchestrator sdlc autorun --review`** — prints the full diff and asks before the run's
  first write. The last gate before anything is committed or pushed, and the only one that
  is a person rather than a model. Fails closed when there is no terminal to ask on, so an
  unattended run declines rather than assuming yes.
- **The repo's own type checker runs inside the test loop.** Scoped to the lines the change
  touched, so pre-existing errors elsewhere don't block a run, and its output goes back to
  the refine loop exactly like a test failure. Typing-hygiene codes (`unused-ignore`,
  `no-untyped-def`, `import-*`) are ignored — in a generated worktree those report the
  environment, not the change.
- **Per-file coverage proof.** The existing check reverts the whole change at once, which
  only proves the tests depend on *some* part of it. Each changed file is now reverted on
  its own; anything that leaves the suite green is a file nothing tests, and goes back to
  the test author by name.
- **`sdlc autorun` is documented** in `CLI_REFERENCE.md`, including what each bracketed
  stage line means. It was the primary entry point and had no reference entry.
- **`orchestrator mcp contracts` labels every argument with its declared type**, read from
  the server's own JSON Schema at display time — `string`, `string|null` for a union, and
  `any` when the schema gives no top-level type (`anyOf`, `$ref`, or no schema at all). A
  parallel `input_types` map carries the same labels keyed by name. Display-only: nothing is
  stored and `mcp call` is unaffected. **Note:** the existing `inputs` field changed shape
  from `["name"]` to `["name (type)"]` — if you parse that output, this is a break.
- **`orchestrator models`** — every model the pipeline can be pointed at, with context
  window, price per million tokens, and whether it supports tool calling, plus what
  each stage is resolving to right now. Read from the installed LiteLLM's own catalog
  rather than a list maintained here, so upgrading the client brings new models with
  no code change and the table can't drift from what is actually making the calls.
- **A model per stage.** `SDLC_JUDGE_MODEL` and a global `ORCHESTRATOR_MODEL` join the
  existing `SDLC_CODEGEN_MODEL` / `ORCHESTRATOR_INTAKE_MODEL`. Three of the four
  stages — the acceptance judge, the intent extractor and the spec writer — were
  hardcoded constants that no environment variable could move, so a "model choice"
  only ever applied to codegen.

### Changed

- **The acceptance judge can ask for a correction, not only refuse.** A rejection used to
  end the run, which made any criterion the generator was never told to satisfy an
  unwinnable ticket — most visibly a documentation criterion, which every codegen prompt
  forbids and the judge then failed the run for missing. Blockers now go back for revision,
  bounded; a revision that breaks the suite is repaired rather than fatal.
- **The judge reads documentation and config, not just `.py`.** A run wrote the
  `USER_GUIDE.md` its ticket demanded, twice, and was told both times that no documentation
  was present — true of its input, and unfixable by any change. The same hole meant a Java
  or Go change was judged on its Python files, of which there are none.
- **Codegen is shown the files it is changing.** Paths came only from the spec text, so a
  ticket phrased in behaviour named none and codegen received zero bytes of existing source.
  Paths now come from the spec *and* the design, which knew them all along.
- **A file too large for the prompt budget is excerpted, not dropped.** It used to vanish in
  silence, so a repair prompt could say "copy this snippet verbatim from the content below"
  and then include nothing. Windows are placed by anchor and sized by what is left, and
  whatever is not shown is named.
- **Codegen and the judge both emit through a forced tool call.** `response_format`
  is an OpenAI/Ollama concept that Anthropic drops, so on Anthropic models nothing
  constrained the output and replies arrived wrapped in prose.
- **`--max-refine` now defaults to 5** (was 3) on `sdlc autorun` and `sdlc feature`: tests
  and type errors draw on the same budget.
- **The default model moves to `claude-opus-5`** (from `claude-sonnet-4-6`, a
  previous-generation Sonnet that every stage was pinned to). This costs more per
  token — $5/$25 per Mtok against $3/$15 — so it is a deliberate change rather than a
  silent one; set `ORCHESTRATOR_MODEL` to pin any stage back, and `orchestrator models`
  lists the alternatives with prices. Codegen's output cap also rises 16k → 32k
  tokens: on the current Claude models thinking is on unless disabled and shares that
  budget, so a cap sized around the JSON payload alone truncates mid-object.
- **Generated Python is parsed before it is written.** A file that does not compile used to
  reach disk and surface three stages later as an opaque pytest `rc=2`, with the real
  `SyntaxError` buried in an importlib traceback. It is now refused at the write with the
  file, line, and offending text, and takes a corrective retry.
- **Each kind of failure gets its own corrective attempt.** Unparseable JSON, a failed edit
  anchor, an empty submission and unparseable Python previously shared a single retry, so
  whichever failed first consumed it. A kind that fails twice still stops.
- **No context builder drops a file silently.** Four separate loops — the files fed to
  refine, the acceptance judge's reader, the anchor-repair block, and PKG grounding — stopped
  at the first item too large for the budget and dropped everything after it. A 91 KB
  `cli.py` could hide a 1.8 KB module the model then edited blind and could not repair. All
  four now allocate fairly and name what they omit.
- **Editing the repo's own docs is not inventing a path.** The layout guidance banned every
  file outside the source and test directories, which made any documentation criterion
  impossible to satisfy. New code and tests are still confined; changing a file the repo
  already has — README, USER_GUIDE, CHANGELOG, pyproject.toml — is allowed.
- **A criterion the judge cannot verify no longer ships.** An `uncertain` verdict stays a
  comment, but now carries a blocker and enters the revision loop like any other, instead of
  passing straight through because it was not `request_changes`.
- **The type check no longer discards codes the target repo enforces.** Only `import-not-found`
  and `import-untyped` are filtered, since a generated worktree carries runtime deps only.
  Everything else is the repo's own mypy policy — filtering it made "clean" mean less than
  CI clean.
- **A safe-mode rehearsal no longer counts as a duplicate.** The check refused any second
  run on a ticket whose first run finished, so a ticket became unworkable after its first
  dry run. Only a run that reached a PR blocks another.

### Known limitations

- A run parked at the validity gate holds its ticket until its approval is decided; there is
  no reaper for parked runs.
- `--resume` re-runs its stages and builds a fresh worktree rather than continuing the
  previous one, so approve-then-resume regenerates the change instead of committing the diff
  that was approved.

## 3.12.0 — Read your tracker through a server, not a token

**Behaviour change:** where an MCP server is onboarded that can serve them, `jira://` and
`confluence://` now route through it instead of direct REST credentials. If you have an
`mcp.json` and expected REST, this changes which path your ingest takes. `mcp-jira://` and
`mcp-confluence://` still force MCP; deleting the server from `mcp.json` restores REST.

The case for it is not ideology. Credentials stay with the operator's server rather than
spreading through this process's environment, calls are allow-listed and audited, and the
server tracks upstream API changes that a hand-rolled client does not — which stopped being
hypothetical this cycle. Atlassian removed `GET /rest/api/3/search`; it answers 410 Gone on
Jira Cloud, and every read through our Jira REST adapter died with it, including the
`parent = <key>` traversal that walks an epic to its stories. That is repaired here too, but
the lesson is that the MCP server absorbs this class of breakage on our behalf.

Resolution is config-only and deterministic — building a source never launches a server to
ask what it exposes. An explicit `$MCP_JIRA_SERVER` / `$MCP_CONFLUENCE_SERVER` wins; failing
that, the first enabled server whose `allow` list names the tool; failing that, a lone server
with no allow-list. With several unrestricted servers Spine declines to guess and falls back
to REST, rather than routing your ticket through whichever name sorted first.

### Added

- `orchestrator mcp list` now reports **why** a server produced no tools. Previously every
  cause — a missing `mcp` extra, an unpulled image, a typo in `command`, rejected
  credentials, a genuinely dead server — produced the same empty list and a log line nobody
  reads. Failures are now classified `config` (permanent; you must change something) or
  `unreachable` (may resolve on its own), carry a remedy where one is known, print to stderr,
  and exit non-zero when every configured server failed.

### Fixed

- Jira REST reads use `/search/jql`. The endpoint that replaced it returns no `total`, so
  truncation is now taken from `isLast`/`nextPageToken` — reading a missing `total` as zero
  would have quietly claimed every result set was complete.
- Jira issues read over MCP are parsed correctly. `mcp-atlassian` returns issue attributes
  flattened at the top level and spells the type `issue_type`, where Jira REST nests them
  under `fields.issuetype` — so the fix released in 3.11.1 never actually fired against a
  real server. Also: documents are keyed by issue key rather than the opaque numeric id, the
  project key populates `space`, the server-supplied `browse_url` populates `url`, and an
  issue with no description no longer ships its entire JSON payload as the body.

### Documentation

- `USER_GUIDE.md` step 9 covers running MCP servers under Docker, configuring several at
  once, and the traps: `mcp.json` and `.mcp.json` are one character apart and point in
  opposite directions; the old example put raw API tokens in a file that was not gitignored;
  `--env-file` needs an absolute path and does not strip inline comments.

## 3.11.1 — A Bug should read like a Bug

An epic, story or bug read through an MCP server arrived **untyped**, while the same issue
fetched over REST arrived typed. Whether the extractor could tell a Bug from a Story, or
done from open, depended on which transport happened to fetch it.

The mechanism is easy to miss, and worth writing down. `SourceDocument` has no field for
issue type — the REST adapter encodes it as a header prepended to the body
(`Bug · status: Open · priority: High`), precisely because a Bug reads differently from a
Story. The MCP parser built its own document and never produced that header, so the
information was not stored elsewhere; it was dropped. The fix puts the header in one
function both adapters call, and the test asserts the two produce *equal* documents for
identical input rather than checking fields one at a time — which is what stops them
drifting apart again.

> **Note on 3.11.0.** It was tagged and released on GitHub but never published to PyPI;
> this release supersedes it and contains everything it did. Publishing 3.11.0 after this
> fix landed would have put an artifact on PyPI that did not match its own tag.

### Fixed

- Jira issue type, status and priority now survive ingestion over MCP, matching the REST
  adapter. MCP documents also set `space` to the project key. They still carry no `url` —
  an MCP server abstracts the host away, so there is no base to build a browse link from.
  Confluence pages read through the same parser are unaffected.

## 3.11.0 — Take the graph somewhere else

The visualization gap was never really about our own renderer. A user who wanted to explore
the graph in Gephi, yEd, Cytoscape or Obsidian couldn't: the only projection was a
kind-per-table SQLite file. `pkg export` now writes GraphML, DOT, JSON and an Obsidian
vault, and the honest conclusion of building it is that we should stop there — Gephi
already does filtering, search, clustering and click-through-to-source on our own export,
and it does them better than a UI with no build step ever will.

Exports are **complete, never truncated**, which is the opposite of what the built-in
visuals do and deliberately so: a diagram with 9,000 nodes communicates nothing, but a
silently truncated GraphML lets a reader draw conclusions from a subset without knowing it
is one. They are also byte-identical for an identical commit, so a committed export diffs
cleanly — asserted by a test that exports twice and compares bytes, because "deterministic"
that nothing checks stops being true quietly.

The architecture diagram now groups by structural community rather than by name prefix.
Grouping by name answers "what did someone call this"; the coupling graph answers "what
actually clusters", and on a single-namespace project the first answer is one box around
everything. Both the report SVG and the committed `episteme/` diagram use the same
partition, so they can't drift apart.

### Added

- `orchestrator pkg export --format graphml|dot|json|obsidian --out <path>`. GraphML and DOT
  open in Gephi, yEd, Cytoscape and Graphviz; JSON carries nodes **and edges**, unlike
  `pkg extract --json`; `obsidian` writes a vault — a copy of the repo's `episteme/` with
  `[[wikilink]]` syntax, never editing it in place. The existing `--db` keeps working as a
  deprecated alias, and combining it with a non-SQLite format is rejected rather than
  silently ignored.
- Deterministic community detection over the coupling graph
  (`orchestrator.knowledge.clustering`), used to band the architecture diagram. Label
  propagation with sorted iteration, seeded labels, stable tie-breaks and communities
  renumbered by first member — so adding one unrelated area cannot renumber everything and
  make an unchanged architecture look like it moved. Partition quality (modularity) is
  reported in the diagram's `aria-label`.

### Fixed

- Graph exports now include `Doc` nodes and `MENTIONS` edges. `pkg export` ran raw
  extraction, but documentation enters the graph through a post-pass — so exports were
  missing 920 `Doc` nodes and 1,576 `MENTIONS` edges on this repo, and with them every
  media transcript, which reuses `Doc`.
- `media --help` said image OCR and "local", omitting that `--asr api` uploads audio
  off-machine. The consent gate was always enforced in code; the summary was narrower than
  the behaviour, which is the wrong way round for a privacy claim.

### Documentation

- `CLI_REFERENCE.md` documents the export formats, and warns that a naive read of `IMPORTS`
  loses 29% of the dependency graph: 2,746 of 5,895 import edges target a `Type` or
  `Function` rather than a `Module`, so filtering for module-to-module edges yields 3,033
  dependencies where resolving through `CONTAINS` yields 4,287. It fails in the direction
  that looks plausible — a tidier architecture than the real one — so the recipe is spelled
  out.

## 3.10.0 — Diagrams and recordings become facts

A codebase's knowledge was never only in its code and its prose. Architecture diagrams,
screenshots of a dashboard, a recorded design review where the one person who remembers why
explains it — Spine could read none of it. Media ingestion closes that: images go through
OCR, audio and video through speech-to-text, and both land as reviewable artifacts under
`.spine-media/`.

The split is deliberate. `media extract` is the only thing that runs a model; the
deterministic graph build *reads* the committed artifacts and never produces them. So
`understand` and `state` stay no-LLM and reproducible — same commit in, same graph out —
while the slow, non-deterministic part is an explicit, reviewable step you run and commit.
An artifact you can read and diff is also an artifact you can correct when OCR mangles a
label.

Transcription is the first path in Spine that can leave your machine, so it carries a
structural consent gate rather than a warning in the docs. A backend advertises whether it
is off-machine; a remote one refuses to run without per-run `--allow-remote`. The default
backend is local, the default consent is absent, and the API key is read from the
environment rather than a flag that would land in shell history.

### Added

- `orchestrator media extract` — OCR for images (the `[media]` extra: pytesseract,
  Pillow) and speech-to-text for audio and video (the `[asr]` extra: Whisper, kept
  separate because it pulls a full ML stack including torch). Output goes to
  `.spine-media/` as reviewable, committable artifacts keyed by content hash;
  re-extraction is skipped when an artifact is already current unless `--force`.
- A consent gate on off-machine transcription. `--asr local` (the default) and image OCR
  run entirely on this machine; `--asr api` uploads audio and refuses to run without
  `--allow-remote`. Oversized files are skipped rather than truncated silently.
- `docs/specs/` and `docs/evals/` are tracked in the repo again — the design records that
  say *why* a subsystem is shaped the way it is. This also removes a class of CI failure:
  doc ingestion reads markdown from disk whether or not git tracks it, so docs that existed
  locally but not in the repo made a contributor's `episteme/` describe pages CI could not
  see, and `understand --check` failed on a diff nobody could reproduce.

### Changed

- CodeQL query selection moved into `.github/codeql/codeql-config.yml`. The full
  `security-and-quality` suite still runs; two quality queries that misread idiomatic typed
  Python are excluded — `py/ineffectual-statement` fired on `...` as a `Protocol` method-stub
  body (PEP 544), and `py/unused-global-variable` on constants consumed only through a
  function-local import. Between them they accounted for 78 open alerts, none real, and
  because CodeQL posts alerts as review *threads* they blocked merges on a branch ruleset
  that requires thread resolution.

## 3.9.3 — Endpoints that are actually endpoints

A REST client is not a REST server, but the Java front-end couldn't tell them apart. It
matched annotations by their final segment, so Retrofit's `retrofit2.http.GET` read as
JAX-RS's `jakarta.ws.rs.GET` and every client method in a file collapsed into one
`java:endpoint:GET /` — a confident, wrong fact in a graph whose whole claim is that it
only asserts what it can ground.

Annotations now resolve through the file's imports, the way Java itself resolves names.
The second half is less visible and mattered just as much: the fact cache is keyed on the
*analyzed repo's* HEAD, not on Spine's version, so a corrected extractor doesn't reach a
repository that hasn't moved. Fixing the extractor without bumping the cache format would
have left the bad endpoints in place for exactly the users who had already run Spine.

### Fixed

- Java endpoint extraction now resolves unqualified HTTP verb and `@Path`
  annotations through explicit or wildcard `javax.ws.rs` / `jakarta.ws.rs`
  imports, with explicit non-JAX-RS imports taking precedence over JAX-RS
  wildcards as Java name resolution does. Fully qualified annotations still work
  without an import. Annotations from client frameworks such as Retrofit are no
  longer misclassified as server endpoints.
- The fact cache format is now v3, so the corrected endpoints reach repositories
  that haven't moved since they were last extracted. The cache is keyed on the
  analyzed repo's HEAD rather than on Spine's version, so without the bump an
  unchanged tree would keep serving the misclassified endpoints after an upgrade
  — and a false endpoint is indistinguishable from a real one.

### Notes

- An unqualified annotation with no resolving import is skipped rather than
  guessed at. In compilable Java the import is always present, so this only
  affects fragments.
- Known gap: a JAX-RS verb combined with a *non*-JAX-RS `@Path` in the same file
  drops the path rather than skipping the endpoint. Uncommon, and not a
  regression — tracked as a follow-up.

Thanks to [@pritam0802](https://github.com/pritam0802) for the fix
([synaptixs/spine#60](https://github.com/synaptixs/spine/pull/60)).

## 3.9.2 — Prose that survives contact with C

The graph was right; the sentences wrapped around it weren't. Running `understand` on a
large C codebase (open5gs, ~8.6k `.c`/`.h` files) exposed three places where the rendered
prose carried Python-shaped assumptions — the same class of confident-but-wrong claim
3.9.0 set out to remove, but only visible on a non-Python repo.

### Fixed

- **An un-imported area is no longer called "the safer place to change."** The fact was
  correct — nothing else depended on it — but the conclusion wasn't: that is exactly what
  an application entry point looks like. It fired on 14 of 25 open5gs areas, including a
  390-function 5G network function. Being un-imported bounds what a change reaches
  *outward*; it says nothing about how much lives inside.
- **The public/internal split now uses each language's own rule.** Applying Python's
  leading-underscore convention everywhere reported *19,212 public · 32 internal* on C —
  a number that looks computed and means nothing. C and C++ now use `static` (internal
  linkage, which the front-end already encodes), Go uses the upper-case initial, and
  Python/TypeScript/JavaScript keep the underscore. Java and C# express visibility with
  keywords the graph doesn't record, so those symbols are excluded from both counts
  rather than defaulted into "public", and the page names the rule it applied.
- **Import-cycle severity is language-aware.** "A hazard for import order" is true for
  Python and overstated for C, where include guards make mutual `#include` compile
  cleanly — a design smell, not a defect.
- **Possibly-unused candidates never include symbols of unknown visibility.** With
  `is_public` gaining a third state, an unreadable verdict would otherwise have read as
  "internal", putting real Java/C# API on a possibly-unused list.

### Changed

- Public sync commits no longer carry a hardcoded assistant co-author trailer.

## 3.9.1 — Java REST endpoints in the graph

Java joins C# in having its web framework understood, not just its classes. JAX-RS /
Jakarta REST resource methods are lifted into the same `Endpoint` nodes and `EXPOSES`
edges the C# front-end already emits — so "what does this service expose, and which
method handles it?" is now answerable for a Java codebase, and the API surface shows up
in `understand` and `state` without a new concept to learn.

Precision-first, like the rest of the Java front-end: a route is only emitted when it can
be grounded exactly. Deterministic and LLM-free, as ever.

### Added

- **JAX-RS / Jakarta REST endpoint extraction** (`pkg/java_extractor.py`) — `@GET`,
  `@POST`, `@PUT`, `@DELETE`, `@PATCH`, `@HEAD` and `@OPTIONS` become `Endpoint` nodes
  with `EXPOSES` edges to the handler method, carrying the handler's provenance. Class-
  and method-level `@Path` values are joined into one absolute route, preserving templates
  like `{id}`. Annotations are matched on their final name segment, so both `javax.ws.rs`
  and `jakarta.ws.rs` — plain or fully qualified — are recognized.
- Endpoints flow through every surface that already renders them: the `Endpoint`/`EXPOSES`
  vocabulary, RDF projection, and the API-surface section of the rendered reports were
  already language-neutral, so nothing downstream needed a new case.

### Notes

- A `@Path` with a non-literal value, and a `@Path` with no HTTP verb (a sub-resource
  locator), are deliberately skipped — a guessed route poisons grounding.
- `@Produces` / `@Consumes` and cross-file `@ApplicationPath` resolution are out of scope.
- The fact cache is keyed on the repo's HEAD commit, so a clean tree that hasn't moved
  since the last extraction will keep its cached facts. Commit, or clear the cache dir, to
  see the new endpoints on an unchanged repo.

Thanks to [@pritam0802](https://github.com/pritam0802) for the contribution
([#55](https://github.com/synaptixs/spine/pull/55), implementing
[discussion #54](https://github.com/synaptixs/spine/discussions/54)).

## 3.9.0 — Comprehension you can trust

A six-phase overhaul of the understanding layer, driven by an assessment against a real
public repo. The headline: **the dependency graph stopped lying** — relative and
intra-package imports never resolved, so the graph saw almost no internal dependencies
and confidently called a codebase's most central module "a leaf, so it's the safer place
to change". That is fixed in every language front-end at once. On `pallets/click`, import
edges naming a submodule went from 27/232 joined to **321/321**, and
`impact_across("Context")` from **0 symbols to 61**.

Built on that, the committed knowledge base went from 4 sections to 18, gained a
provenance stamp and a CI gate that **proves** it still matches the code, and turned each
module page into a pre-change briefing. Spine now commits its own `episteme/` and fails
its own CI if that knowledge base degrades.

Everything here stays deterministic and LLM-free: same commit in, byte-identical output.

### The import graph stops lying

Phase 0 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
relative and intra-package imports now join the modules they denote, in every language
front-end at once — and a standing invariant check makes sure the bug class can't return
unnoticed. Measured on `pallets/click`: import edges naming a click submodule went from
27/232 joined (all from tests) to **321/321**; `impact_across(Context)` from **0 symbols to
61**; the `click.core` area page from *"it's a leaf, so it's the safer place to change"* to
*"it sits in the middle of the graph: 10 areas below it, 8 above."*

#### Fixed

- **Python front-end reads `stmt.level`** — `from .types import X` resolves against the
  module's own package (`py:click.types.X`), so it can no longer be conflated with the
  stdlib `types`. An import that climbs past the scanned tree keeps its dots and never
  falsely joins.
- **`link_imports` post-pass** (`pkg/import_link.py`) — a whole-repo join that repoints
  `IMPORTS` edges at the first-party modules they denote and drops the orphaned phantom
  nodes. One shared resolver; per-language matchers only: dotted-prefix walk (Python /
  Java / C#), relative-specifier resolution (TypeScript), `go.mod` module-path matching
  (Go), unique path-suffix matching for `-I`-style includes (C / C++). Runs inside
  `RepoCodeExtractor.extract`, so every consumer — `understand`, `state`, grounding,
  `pkg export`, the MCP tools — gets resolved imports with no extra wiring.
- Fact-cache format bumped to v2: pre-fix caches would silently reintroduce the dangling
  imports, so they re-extract.

#### Added

- **`orchestrator pkg verify`** — Tier-1 graph invariants, no oracle needed: every edge
  endpoint exists, every grounded provenance resolves to a real `file:line`, per-language
  orphan-rate and external-ratio tripwires (the completeness failures a does-it-run test
  can't see), and phantom-basename warnings. Non-zero exit on error, so it can stand guard
  in CI. Per-language regression fixtures pin the join: a repo using relative imports must
  show non-zero importers for the imported module.

### Episteme can prove it's current

Phase 1 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
a committed knowledge base whose entire value is being *code-true* could not tell a reader
whether it described HEAD or a commit from six months ago. Now it says where it came from,
and CI can prove whether it still holds.

#### Added

- **A provenance stamp on `episteme/README.md`** — which commit the bank was generated
  from, whether that tree was dirty, and which Spine rendered it. Deliberately carries **no
  timestamp**: invariant #2 requires the same code to produce byte-identical output, and a
  date would break the property the artifact is trusted for.
- **`orchestrator understand --check`** — writes nothing, re-renders, and diffs against the
  committed bank, exiting non-zero when they disagree. It reports what to look at: pages out
  of date, missing, or still describing code that's gone. The comparison ignores the fenced
  stamp, because committing the episteme itself creates a new commit — content, not the
  stamp, is what proves currency.

#### Fixed

- **`understand` no longer reads its own output.** `episteme/` and the legacy `memory-bank/`
  join the ignored directories. A committed bank was being ingested as the repo's own
  documentation: on a small fixture it turned 6 grounded nodes into 32, all 26 `Doc` nodes
  coming from Spine's own prose. Worse, it made the artifact unable to ever be self-
  consistent — writing the bank changed the graph that rendered it, so no bank could
  describe its own repo twice the same way, and `--check` could never pass.

#### Changed

- `build_memory_bank` is now a thin writer over a new `render_memory_bank`, so the build and
  the check share one rendering path and cannot drift apart.

### One analysis layer, two renderings

Phase 2 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
`state` computed sixteen sections and printed them, while the *committed* episteme rendered
four — the ephemeral report was richer than the knowledge base a team reads and an AI tool
grounds on. Both surfaces now read one analysis. On `pallets/click`, episteme goes from 4
top-level sections to 13.

#### Added

- **`knowledge/analysis.py`** — the single pipeline (extract → migrations → data layer →
  docs → profile → metrics) that both `understand` and `state` go through. What differs
  downstream is only rendering.
- **`architecture.md`** gains the **system-architecture diagram** and the strongest
  component dependencies (drawn from `current_state`'s own bounded `architecture_graph`, so
  the two surfaces can't disagree), **layers**, and **test coverage**.
- **`tech-context.md`** gains **infrastructure & runtime**, **entry points**, and
  **most-used external imports** — it was a six-row table, mostly `—`.
- **`progress.md`** leads with computed **suggested next steps** instead of only pointing at
  a `BACKLOG.md` that doesn't exist unless Spine built the repo.

#### Fixed

- **Test coverage measured what it claimed.** An area counted as tested if it *contained* a
  type with "test" in the name — which answers "which areas are tests", not "which areas
  have tests", and reported `click.core`, the most-tested module in click, as untested.
  Coverage is now test→source imports, a lookup that only became possible once Phase 0 made
  intra-package imports resolve. click reads 13 of 27 components exercised, and the untested
  list is now genuinely untested code (`click._winconsole`, the `examples/` trees).
- **Entry points exclude tests.** `main()` inside a test file is a fixture, not how the
  system starts; click's entry-point list was two test functions ahead of the real one.

#### Note

Git-history metrics stay out of the committed bank on purpose. `state`'s "Recent activity"
reads the last ~60 commits, so its value moves on every commit — including the one that
lands the bank — which would make episteme stale the moment it was committed and
`understand --check` fail forever after.

### The module page becomes a briefing

Phase 3 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
a module page told you what the code *is*. It now answers what depends on it, what breaks
if it changes, what isn't tested, what it inherits from, and which docs describe it.
Findings 3, 8 and 9 — all of it graph computation, no LLM.

#### Added

- **"Changing this safely" on every module page** — who tests the module, the symbols other
  code most depends on with their blast radius, and which of those have no visible test
  path. This was the product's most persuasive answer and it was only reachable by running
  `regression` against a symbol you had already chosen to change.
- **`store.implementors_of()` / `store.implements_of()`** and inheritance rendered from both
  ends. click's 42 `IMPLEMENTS` edges rendered nowhere; `click.exceptions` now shows all 12
  exception subclasses, and `Parameter` → `Argument`/`Option` is walkable in either direction.
- **"Documented in …"** on modules and symbols, from `MENTIONS`, plus a repo-level
  **Documentation** section with coverage and drift. Four releases of doc ingestion had
  reduced, in the committed bank, to a single `Doc: 264` line; click now reports 6% doc
  coverage and 250 potential drift where only `state` used to.
- **`api-surface.md`** — every route and the code behind it, keyed on `Endpoint`/`EXPOSES`.
  Written only for repos that have routes.
- **`CoverageIndex`** (`sdlc/coverage.py`) — whole-repo test reachability and blast radius
  indexed once. `build_regression_plan` rebuilds a predecessor index per call, which is
  quadratic when every module page needs it; `understand` on click stays at ~1.4s.

#### Note on honesty

The first cut of the safety block reported "16 of 20 symbols have no test" for `click.core`,
naming `Context` — one of the most tested classes in the Python ecosystem. Call resolution is
precision-first (ambiguous `obj.method()` chains are skipped rather than guessed), so an
invisible test path is not an absent one. It now flags only the actionable intersection —
depended upon **and** no visible path — says plainly that invisible ≠ absent, and takes
module-level "tested by" from test **imports**, which are complete in a way call edges aren't.

### No page is a stub or a directory listing

Phase 4 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md) —
Findings 4, 5 and 6 — plus the CI dogfooding left open since Phase 0.

#### Changed

- **`domain-model.md` is ranked, not alphabetised.** With no database it listed 40+ classes
  A–Z and called them *prominent* without computing anything; `Abort` led click's page
  because it starts with A. Now ranked by subtypes, production call-sites, members and doc
  mentions — click's page opens with `Context`, `ProgressBar`, `Command`, `Parameter` — and
  each row says *why* it matters. Retitled too: on a repo with no database, "domain model"
  promises a schema that isn't there.
- **`glossary.md` no longer promises definitions it can't write.** It was 60 alphabetical
  lines of `**Abort** — _TODO: definition_`. Each term now links to where it's defined and
  to the doc that explains it (from `MENTIONS`); private types are excluded.
- **The "Node kinds" dump is gone from `architecture.md`.** `Function: 1938, Field: 400` was
  database statistics in a section of its own. The counts survive as scale on the graph-size
  line, and **Complexity** (size distribution + largest types) takes the slot.
- **`conventions.md`** gains counted naming conventions, test layout, and the error idiom —
  it was four sampled rules and a lint config. **`tech-context.md`** gains the declared
  version and language floor.
- **Production and test call-sites are counted apart** (Finding 6). `echo`'s
  "most-depended-upon" callers were `test_echo`, `test_echo_color_flag`,
  `test_echo_custom_file`. Rankings now use production call-sites only — being called by
  thirty tests makes a symbol well covered, not central — while both numbers are displayed,
  and caller lists lead with production.

#### Fixed

- **Unresolved base classes are recorded instead of dropped.** The Python front-end emitted
  an `IMPLEMENTS` edge only when a base resolved to an import or a local definition, so a
  class extending a *builtin* had no base at all in the graph: `class Abort(RuntimeError)`
  and even `class ClickException(Exception)` answered "extends nothing", and anything
  walking a hierarchy under-counted it. Bare-name bases now emit an external node, exactly
  as unresolved bare *calls* already did. Click's exception hierarchy reads 12 types rooted
  at `ClickException`, matching the source; the name-matching approach found 4.
- A symbol with no edges rendered as a heading followed by silence. It now says so.

#### Added

- **CI runs `orchestrator pkg verify .` and `orchestrator understand . --check`**, and Spine
  commits its own `episteme/`. The product's flagship claim is detecting when docs drift
  from code; until now its own knowledge base could drift silently.

### Answers to questions nobody was asking yet

Phase 5, the last of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md).
Finding 10's opportunistic list: findings that need no new facts, only questions aimed at
the *reader* ("where do I start?", "what can I ignore?", "what's tangled?") rather than at
the extractor.

#### Added

- **Onboarding path** on the index — "New here? Read these first": where execution starts,
  then what the most code depends on, each step saying why it's there.
- **Public surface split** — "400 public · 306 internal, of 706". The same codebase,
  reframed as approachable, with the most-depended-upon public symbols listed.
- **Import cycles** — strongly-connected components of the module graph. Undetectable
  before Phase 0, because the graph had almost no first-party import edges to form a cycle
  with; click turns out to have an 11-module core cycle. Iterative Tarjan, because a real
  dependency chain would blow the recursion limit.
- **Possibly-unused candidates** — internal symbols with no caller, subclass or doc
  reference. Restricted to *internal* on purpose: a public function with no in-repo caller
  is what an API looks like. Labelled candidates, not verdicts.
- **`symbol-index.md`** — every first-party symbol A–Z with the page that describes it, so
  the bank is searchable by name without grep.

#### Not done, deliberately

**Churn per module** is the one item from Finding 10 left out. It reads the last ~60
commits, so its value changes on *every* commit — including the one that lands the
knowledge base — which would make the bank stale the moment it was written and
`understand --check` fail permanently. It stays in the ephemeral `state` report.
Tests→module shipped earlier, as Phase 3's "Tested by".

## 3.8.4 — The architecture diagram now explains itself

The 3.8.3 diagram named its components but didn't say what they *do* — boxes read
`CLI · cli.py`, which tells you a module exists, not why it's there. Redrawn so every box
answers "what is this for?", and every layer carries a plain-English line describing what
happens there. Documentation only; no code change.

### Changed

- **Every box now has a purpose line** — `Command line · 41 commands · the main surface`,
  `Hand out credentials · only at the moment of use`. Package paths (`plugin/`, `runtime/`)
  drop to a dimmed third line: useful to a contributor, noise to everyone else. No box is
  labelled with a filename any more.
- **Each layer is narrated.** A sentence under every layer heading says what is happening —
  *"Before writing anything, Spine reads."* — and both gates now read **"Stop."**, spelling out
  that nothing has been written before gate one and nothing pushed before gate two.
- Plainer names over internal jargon: *Read the requirement* rather than `Intake`, *The plan,
  typed* rather than `GraphIR`.
- The image is **72% smaller** (1.3 MB → 0.37 MB) at the same resolution.

## 3.8.3 — Architecture diagram

Adds a full **architecture diagram** and an [ARCHITECTURE.md](ARCHITECTURE.md) that walks the whole
platform end to end — the six layers, every component, the two human gates, and the Product
Knowledge Graph they all read from. Documentation only; no code or behaviour change.

### Added

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how Spine fits together, layer by layer, with a diagram
  that renders on GitHub and in Spine's own web UI.
- A **static architecture image** (`assets/spine-architecture.png`), shown in the README.

## 3.8.2 — Doc ingestion reaches HTML and Office

3.8.1 folded Markdown, reST, plain text and PDF into the graph. This release adds the
remaining **file** formats teams actually keep specs in — **HTML** and **Word/Excel** — so a
`.docx` architecture doc or an exported HTML spec sitting in your repo becomes `Doc` nodes
`MENTIONS`-linked to the code it describes, exactly like a README.

Still deterministic, still no LLM, still a no-op on a repo with no docs.

### Added

- **HTML ingestion** (`.html`/`.htm`) — no extra needed. `<h1>`…`<h6>` become section
  boundaries (so an HTML doc sections exactly like markdown), and inline `<code>` is
  preserved as a code claim, so the symbols a doc names actually bind. `<script>`/`<style>`
  bodies are ignored; malformed markup is skipped rather than fatal.
- **Word & Excel ingestion** behind a new **`[office]`** extra
  (`pip install 'synaptixs-spine[office]'`). `.docx` maps Word's heading styles to sections
  and treats monospace runs as code claims — the Word equivalent of backticks — and keeps
  table text, which is where spec documents put API and field lists. `.xlsx` gives one
  section per sheet and keeps string cells only (numbers and formula results are data, not
  prose about code). Encrypted or corrupt documents are skipped.
- **Markdown front matter** is now read as prose: the *values* of a `---` block (`title:`,
  `module:`, `tags:`) bind like the text they stand for, while the keys and fences no longer
  leak into the graph as noise.

### Changed

- Documentation formats are now **registered readers** rather than hard-coded branches, so
  adding a format touches no existing one. Behaviour for existing formats is unchanged.
- Standalone `.yaml`/`.yml` files are **deliberately not ingested**. A repo's YAML is
  overwhelmingly configuration, and treating it as documentation would inflate the doc
  coverage `state` reports and flood doc-drift with config values that were never prose.
  YAML's documentary case — front matter — is covered above.

## 3.8.1 — Doc & PDF ingestion: your docs become code-linked facts

Spine now reads a repository's **documentation** — Markdown, reStructuredText, plain text,
and **PDF** — into the Product Knowledge Graph as first-class **`Doc` nodes**, each
**`MENTIONS`**-linked to the code symbol it describes. So comprehension can answer *"which
docs describe `X`?"*, *"how documented is this?"*, and *"do the docs still match the code?"* —
all deterministic, no LLM. This is the *knowledge-doc* half of Spine's doc story; the
*structured-doc* half (OpenSpec `openspec://` → intents) already shipped. It closes the
biggest remaining reach gap vs. doc-graph tools.

Nothing to configure: docs are folded in automatically when you run `orchestrator understand`
or `orchestrator state`. A repo with no docs behaves exactly as before.

### Added

- **Doc ingestion** — `understand`/`state` now emit `Doc` nodes + `MENTIONS` edges. Binding is
  **precision-first**: a mention becomes an edge only when it resolves to exactly one symbol.
  Reuses the deterministic doc→symbol binder already in `pkg/docs.py`.
- **PDF support** behind a new **`[docs]`** extra (`pip install 'synaptixs-spine[docs]'`, lazy
  `pypdf`). The base install stays stdlib-only; malformed or scanned (image-only) PDFs are
  skipped, never fatal — no OCR.
- **`state` Documentation section** — doc count, **symbol coverage %** (how much of the code the
  docs describe), and top **doc drift** (doc claims about code the graph can't resolve —
  renamed/removed symbols), filtered to real symbols so paths/URLs/filenames don't drown it.
- **`docs_for` `/spine` MCP tool** — with a `symbol`, the docs that describe it; with no symbol,
  a doc-coverage summary + top drift. Joins the read-only comprehension tool set; documented in
  the Claude/Codex guides and the `understand-codebase` skill.
- **Section-granular `Doc` nodes** — Markdown is split by heading into `doc:README.md#usage`
  nodes (bounded), so a `MENTIONS` edge points at the *section* that names a symbol, with
  provenance at the heading line.
- **Doc-grounded codegen** — `sdlc feature` grounding now folds a reused symbol's documenting
  prose into the codegen context, so generated code sees not just an API but what it's for.
- **Doc-drift review finding** — `GroundingVerifier.doc_findings` surfaces stale-doc symbol
  claims as an informational, source-anchored finding.

## 3.8.0 — The `/spine` comprehension skill

Spine's read-only comprehension is now a **drop-in skill** any assistant can call — Codex
(plugin) and Claude Code (an `understand-codebase` Agent Skill) — so you can ask about a
codebase in plain language and get engineering *decisions*, not just a map: what a change
breaks, what's untested, and where a ticket or bug lands, each grounded to `file:line`.

### Added

- **Comprehension MCP tools** on the Spine plugin server, all read-only, deterministic
  (no LLM), and needing no credentials: `map_repo` (structure, call-hotspots, coverage
  gaps, recommendations), `blast_radius` ("what breaks if I change X" — callers +
  cross-layer reach), `explain_symbol`, `investigate` (where a ticket lands), `localize`
  (stack trace → fault site), and `regression_gaps` (blast-radius symbols with no covering
  test). Each returns structured fields **plus** a `markdown` rendering. They join
  `read_memory_bank` (a repo's committed `episteme/`).
- **`root_cause`** — a grounded root-cause report (fault site, ranked hypotheses with
  evidence, regression surface, fix approach). Deterministic by default; `use_llm=true`
  opts into LLM-enriched hypotheses.
- **`understand-codebase` Agent Skill** bundled with the Claude Code plugin — tells Claude
  which tool to reach for, so you just ask in plain language.
- **git-URL support** across the comprehension tools — point them at a local path *or* a
  git URL (shallow-cloned behind the same host allow-list as the CLI). Serve them to a
  remote host over HTTP with `orchestrator-mcp --http`.

## 3.7.0 — Go: the 8th PKG language

Go is now a first-class language across the whole stack — comprehension, the call and
interface graph, and greenfield **and** brownfield codegen — so `understand`, `state`,
`design`, `investigate`, `localize`, `rca`, `regression`, grounding, and
`sdlc feature --language go` all work on Go repos. Install with the `go` extra
(`pip install 'synaptixs-spine[go]'`); codegen needs the `go` toolchain on PATH.

### Added

- **Go comprehension** (`go` extra, tree-sitter-go) — `Module`/`Type`/`Function`/`Field` +
  `IMPORTS`/`CONTAINS`. Go's module unit is the **package = its directory**, so every `.go`
  file in a dir merges into one component (the first front-end where that holds).
- **Go call + data + interface graph** — `CALLS` (same-file package functions and
  receiver-method calls), `REFERENCES` (same-package struct-field types), and the Go
  highlight, **`IMPLEMENTS` by method-set matching**: because Go has no `implements` keyword,
  a concrete type is linked to each in-repo interface it structurally satisfies (matched by
  method name + arity over value **and** pointer receivers). So blast-radius, `design`,
  `rca`, and `regression` light up on Go.
- **Go codegen** (`sdlc feature --language go`) — scaffolds/extends a module and builds +
  tests it with `go build ./...` / `go test ./...`, with co-located `_test.go` tests. It is
  **multi-module aware**: the runner builds and tests the module(s) a change actually
  touches (not just the repo root), so code generated into a sub-module is never a false
  green.

### Changed

- **`sdlc feature --language` is now validated** against the supported set — an unknown value
  errors instead of silently scaffolding a Python project.

## 3.6.1 — Shareable codebase-intelligence report

`orchestrator state . --out report.html` now emits a single **self-contained HTML file** you
open in a browser and forward to your team — the engineering-decision counterpart to a
concept-map `graph.html`. Deterministic, no LLM, nothing fetched. It packages the analysis
`state` already computes, so this is rendering, not new comprehension.

### Added

- **Shareable HTML report** — `orchestrator state . --out report.html` writes one
  self-contained, theme-aware (light/dark) file with a provenance header, plain-language
  overview, architecture diagram, blast-radius hotspots, risk & health, test-coverage gaps,
  security surface, recent activity, and prioritized recommendations. `--out *.html` selects
  HTML; any other extension keeps today's markdown. `--no-timestamp` gives byte-stable output
  for CI diffs. The `--lens stakeholder` view drops the jargon-heavy sections.
- **Deterministic architecture diagram** — an inline SVG (components grouped into zones,
  weighted dependency arrows) laid out seeded-in-Python, so the same commit renders the same
  picture; it grid-wraps large zones to stay legible and themes with the page (no mermaid, no
  external assets).
- **Graph-quantified blast radius** — the spotlight quantifies the cross-layer impact of the
  top hotspot via `impact_across` ("changing X → N dependents across M files") and lists
  blast-radius symbols with no covering test via the regression plan (`build_regression_plan`).
- **In-browser filter** — a client-side search box hides non-matching rows, dims non-matching
  architecture components, and collapses emptied sections; vanilla JS, no build step, still one
  self-contained file.

## 3.6.0 — Knowledge-graph-grounded design & RCA

A suite of new, deterministic-first CLI commands that ground engineering work — design,
debugging, and root-cause analysis — in the Product Knowledge Graph, plus the call-graph
extraction that makes them work across languages. Every command is inspectable and states
its own limits rather than implying certainty.

### Added

- **`orchestrator design`** — spec × knowledge graph → a grounded design with a **blast
  radius** (which modules a change touches, who imports them, the call hotspots) and an
  **unverified-references** flag for named paths absent from the graph. Deterministic by
  default; `--llm` writes the prose.
- **`orchestrator investigate`** — research a ticket against the codebase before designing:
  where it lands in the code (real symbols with `file:line` + caller counts) and the relevant
  committed `episteme/` knowledge. Ticket from a source URI or inline.
- **`orchestrator localize`** — parse a stack trace / pytest failure and resolve each frame to
  the repo symbol it names, pointing at the likely fault site and its callers.
- **`orchestrator rca`** — a gated root-cause report: fault site, ranked root-cause
  *hypotheses* with evidence (exception priors, recent git churn, call sites), the regression
  surface a fix must cover, and a scoped fix approach. Stops at analysis — no autonomous code.
- **`orchestrator regression`** — blast-radius regression coverage: split the call-graph
  impact of a change into tests that already exercise it vs production code with no covering
  test (the gaps).
- **Jira as a read source** (`jira://PROJ-123` / `jira://PROJ` / `jira://jql/…`) — ingest
  existing issues as requirements, the read counterpart to the Jira issue-tracker sink.
- **Generalized MCP-backed sources** — `mcp-jira` and `mcp-confluence` presets plus a generic
  `mcp` escape hatch, so any onboarded MCP server can back intake (route access through a
  governed server instead of spreading REST tokens).

### Changed

- **Call graphs across the stack:** the Java and TypeScript front-ends now extract `CALLS`
  edges (precision-first; TypeScript resolves relative imports to the definition, so
  cross-file call graphs connect). Impact, RCA, and regression coverage now work on Python,
  C, C++, C#, Java, and TypeScript.
- **`FactStore.impact_across`** — composed transitive blast radius over CALLS + IMPORTS +
  REFERENCES, so impact traces across the code, module, and data layers.
- The README banner now shows the platform's full capability map rather than a single pipeline.

## 3.5.0 — Security hardening

This release is the output of a security baseline of Spine's own source tree. Nothing
here is a claim that the codebase is "secure" — it is a description, verifiable against
this repository, of the checks we now run and the issues we found and fixed.

### 🔒 Security

- **Continuous checks in CI, on every pull request:**
  - **CodeQL** dataflow analysis for Python and JavaScript.
  - **`pip-audit`** over the resolved lockfile (not the ambient environment — bare
    `pip-audit` in a uv checkout audits the wrong thing and false-passes).
  - **`bandit`-class static analysis** via ruff's flake8-bandit (`S`) rules, wired
    into the existing lint gate.
  - **Dependabot** for weekly dependency and GitHub-Actions updates.
- **A multi-model adversarial self-review** across the full source tree: 863 candidate
  findings were triaged by one model, then independently verified by a stronger model
  instructed to *refute* each one. 174 of the high-severity candidates were refuted as
  safe-by-design; **7 confirmed issues were fixed, each with a regression test.**
- **All patchable dependency CVEs resolved** — 17 of 18 known advisories fixed by
  version bumps (aiohttp, starlette, cryptography, langsmith, langgraph, pydantic-
  settings). The one remaining (`click`'s `click.edit()` command injection) is
  unreachable — Spine never calls that function — and is documented rather than
  force-fixed, because the fix would regress the `semgrep` scanner by ~2 years.
- Coordinated disclosure via [SECURITY.md](SECURITY.md).

### Fixed

Security fixes from the review above, described at the level of *what class of issue*
rather than a reproduction:

- **Path traversal** in the knowledge-base reader and the `memory-bank` capability
  endpoint — an untrusted section name or a symlink committed in a cloned repo could
  read files outside the intended directory. Reads are now confined to the bank dir.
- **Stored XSS** in the operator web UI — the shared HTML escaper escaped `&<>` but not
  quotes, so an untrusted value (e.g. a cloned-repo file name) placed in a quoted HTML
  attribute could break out. The escaper now escapes quotes across all web UI files.
- **SSRF backstop** for remote-repo cloning — the internal-host guard missed obfuscated
  IPv4 encodings (integer, hex, octal, short-form) that resolve to loopback. These are
  now normalized and blocked. (The guard was already robust under its default
  restrictive host allow-list; this hardens the opt-in `*` mode.)
- **Prompt-injection hardening** in the codegen/design/review pipeline — untrusted
  cloned-repo content fed into LLM prompts is now fenced and marked as data, and the
  review judge is instructed to ignore injected verdicts. This is defense-in-depth; the
  human merge approval remains the authoritative gate.

### Added

- `SECURITY.md` disclosure policy surfaced in the README.
- Security review plan and methodology in `docs/specs/security-review-plan.md`.
