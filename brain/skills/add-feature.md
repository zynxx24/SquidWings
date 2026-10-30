---
name: add-feature
description: Add a feature that fits the existing code and is verified to work.
triggers: fitur, feature, tambah, tambahkan, implement, implementasi, implementasikan, buat fitur, endpoint baru, halaman baru, add
---
# Adding a feature

1. **Pin down "done".** Restate the requirement as behaviour you could demonstrate: input, output, and what happens on failure. If a decision would change the design and you cannot infer it, ask one question. Otherwise pick the conventional option and state it.
2. **Learn the project.** Call `project_overview`. Then find the closest existing feature with `file_search` or `find_symbol` and read it. Copy its structure, naming, and error handling.
3. **List the files you will touch** before editing. If it is more than a few, split the work into steps that each leave the project working.
4. **Reuse before you write.** Check for an existing helper, a standard library function, and an installed dependency. Add a new dependency only if the task cannot work without it.
5. **Work from the inside out:** data and logic first, then the interface around them. Test each layer as it comes up rather than wiring everything at once.
6. **Handle the unhappy paths:** invalid input, empty results, missing permissions, a failed network call, and concurrent use where that applies.
7. **Add one small test** for any logic with a branch, loop, parser, money, or security, following how this project already tests.
8. **Verify by using it.** Run `run_checks`, then actually exercise the feature: call the endpoint, run the command, or load the page. "It compiles" does not mean it works.
9. **Report** the files changed, how you verified it, and anything you decided or left out.

Do not add configuration, abstractions, or extension points that nobody asked for. Three similar lines are better than a framework for one use.
