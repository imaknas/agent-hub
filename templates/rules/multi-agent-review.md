## Multi-agent review rules

When several agents/models look at the same thing together (a design, a plan, code, a document, an answer), first
decide what you want, then choose how.

**You want a judgment or an answer (is this design feasible, which option is right) → parallel blind review, not a
debate.**

- Each reviewer answers independently, without seeing the others' results; combine them only at the end. Agreement
  reached after seeing each other's answers is not evidence: models drift toward the majority, or toward whoever looks
  stronger, and even those who had it right get pulled away.
- How to combine: majority vote for options or values; for open-ended answers, hand them to one aggregator and require
  it to list the disagreements, not smooth them over.
- Use reviewers from different model families where possible: their errors are less correlated.
- Only agreement among independent answers counts as evidence. A disagreement means "look at this yourself", not noise
  to be removed.

**You want to find problems (holes, risks, bugs, omissions) → parallel blind review, union of the problems.**

- Each reviewer reviews independently, preferably each from a given angle (security, cost, user experience, devil's
  advocate…).
- "What problems are there" is a union: list a problem as soon as one reviewer raises it, and say who raised it. Don't
  filter problems by majority vote: what only a few see is often the most valuable.
- "Does this problem hold" is checked item by item against the source (code, data, a reproduction), or left to a person
  to judge.

**You want the arguments laid out clearly and a person decides → only then use a debate.**

- The value of a debate is putting each side's strongest arguments in front of the judge (a person). Participants are
  anonymous (A/B/C, no model or role names); 1–2 rounds are enough: the more rounds, the more conformity.
- Don't treat "they agreed in the end" as evidence that the conclusion is right.

**A middle way:** answer independently first → then anonymous cross-review, but no one may change their own answer →
the judgment is still based on the original independent answers; the cross-review counts only as evidence.

**In general:**

- Don't let reviewers know who the other reviewers are or which models they are, and give no authority cues in the
  prompt ("a senior expert thinks…").
- A reviewer's report is evidence, not a conclusion. Key claims are checked against the source before they are adopted;
  when passing them on, say which have been checked and which have not.
- These principles rest on two kinds of grounds, to be kept apart:
  - "Stay independent until combining" and "union of the problems found" are statistical reasoning, and stronger models
    don't make them obsolete.
  - "Does a debate make answers more accurate" and "how strong is conformity" are how current models behave, and change
    with the models. On questions that can be verified (math, code) strong models may conform blindly less; on ones that
    can't be verified on the spot (facts, judgments) conformity may persist. When a new generation of models arrives,
    measure again and adjust.
