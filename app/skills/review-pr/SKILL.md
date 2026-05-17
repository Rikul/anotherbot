# Pull Request Review Skill

## Overview
This skill provides a structured methodology for reviewing GitHub pull requests. It produces thorough, organized, and actionable reviews by following a four-section template that covers the change itself, each modified file, testing, and code quality.

## When to Use
Use this skill when asked to:
- Review a specific GitHub pull request
- Review a diff or patch
- Evaluate a proposed code change
- Assess the quality, correctness, and completeness of a PR

## Workflow

### 1. Fetch the PR
Use a web fetch method to get the PR diff, or if the URL is a GitHub PR, fetch the diff URL directly (append `.diff` or `.patch` to the PR URL).

### 2. Analyze the PR using the four-section template

---

## PR Review Template

### 1. Change Description
Answer these three questions clearly and concisely:
- **What change does this PR make?** — Summarize the overall purpose and mechanism of the change in plain language.
- **What was the behavior before this PR?** — Describe the old behavior, including any existing bugs, missing features, or dead code.
- **What is the behavior after this PR?** — Describe the new behavior. Be specific about what fields, APIs, or user-visible outputs changed.

### 2. File-by-File Analysis
For each file changed in the PR, explain:
- **What changes were made?** — Be specific: added fields, modified queries, refactored methods, removed imports, etc.
- **Why these changes were made in the context of this PR?** — Connect each file change back to the overall goal of the PR. Explain how each file contributes.

Use a consistent format per file, e.g.:
```
### `path/to/file.go`
- **Changes**: ...
- **Why**: ...
```

### 3. Testing Review
- **What tests are recommended to test this PR?** — Think about what a complete test suite would look like. Cover: happy path, edge cases, nil/empty inputs, error paths, all layers (state → service → API → CLI).
- **If tests are included: which tests are missing?** — Explicitly list gaps.
- **If tests are included: provide a short review of the added tests** — Assess coverage, quality, edge case handling, and realism of test data.

Use ✅/❌ markers for covered vs. missing test cases.

### 4. Code Review
Create a list of issues found, organized by severity. For each, include:
- **Severity** (🔴 = potential bug / security / correctness, 🟡 = concern / best practice / missing coverage, 🟢 = positive observation)
- **A short title** describing the issue
- **File path and line numbers** where applicable
- **Explanation** of the issue and its impact

Include at minimum:
- Things that are missing
- Things that are not done correctly
- Edge cases not handled
- Missing tests or documentation
- Code quality concerns
- Potential bugs or security issues
- Positive observations (well-done parts)

End with a **Summary table**:

| Area | Verdict |
|------|---------|
| **Problem** | ... |
| **Root cause** | ... |
| **Fix quality** | Solid / Needs work / etc. |
| **Bug fixes** | ... |
| **Code quality** | ... |
| **Test coverage** | ... |
| **Edge cases** | ... |

And a **bottom line** sentence summarizing the overall assessment.

---

## Tips
- Always verify file paths and line numbers from the actual diff, not from memory
- Distinguish between subjective style preferences and actual bugs
- For large PRs, group related file changes together
- When in doubt about a behavior, note the uncertainty explicitly (e.g., "the reviewer should confirm that...")
- Separate observations into **positive** (🟢), **concern** (🟡), and **critical** (🔴) categories
