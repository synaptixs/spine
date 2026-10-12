# Real `dotnet test` output (xunit 2.7, .NET 10, vstest console logger)

Captured from real runs of tiny projects on 2026-10-12, with the machine's paths replaced by `/w`.
Every name in them is invented. They exist because the first version of the .NET baseline was
tested on output written by hand, and a code review found three defects in what real output looks
like (B73, SSPN-156).

| File | What it shows |
|---|---|
| `xunit_failures.txt` | five failures: multi-line messages with `Expected:` / `Actual:` at column 0, a `StrDiff` with arrows, theory rows whose names hold quotes and brackets, `[xUnit.net …] [FAIL]` lines before the results |
| `xunit_diagnostic_text.txt` | a healthy build whose failing tests print `error CS0103: …` as message text |
| `xunit_host_crash.txt` | one old failure, then a test that kills the host: `Test Run Aborted.` and a summary that does not count the crashed test |
| `xunit_two_projects_one_red.txt` | a solution of two projects: B does not compile (`error CS0029 … [B.csproj]`), yet `dotnet test` still builds and runs A's tests, prints A's summary, and exits 1 |
| `xunit_two_projects_custom_error.txt` | the same solution, but B fails through a custom MSBuild `<Error>` task: `B.csproj(12,51): error : custom gate failed` — no error code, no project suffix — and A's tests still run and print their summary |
