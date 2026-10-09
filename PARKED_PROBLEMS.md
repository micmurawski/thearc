# Parked problems

Questions deliberately left open, with what we know so far. Pick one up by turning it into a
glossary definition first.

## Which history the merged journal keeps

**Where:** Merging → Recording a merge (`GLOSSARY.md`).

**Current decision (provisional):** the merged journal holds the base journal, every candidate's
reflection entries (Rated), and change entries for the merger's own commits only. The curators'
change entries (and their Kept entries) stay on their candidate branches.

**Why it is open:**

- A section's history in the merged journal then skips the changes curators proposed — including
  the ones the merger took verbatim, which are re-recorded as the merger's commits with the
  merger's reason, not the curator's.
- Keeping every candidate's change entries instead would preserve their history but list changes
  that never reached the merged text, which misleads anyone reading a section's history.
- Changes are often neither accepted nor rejected but combined or rewritten, so a simple
  accepted/rejected entry (e.g. `Rejected`, by analogy with `Kept`) does not describe them well.

**Options to weigh later:** link entries (a merger commit's entry naming the candidate commits it
drew on, already possible through `extra`); carrying candidate entries under a separate kind that
policies and change logs ignore; or keeping the candidates' branches as the full record and the
journal deliberately short.
