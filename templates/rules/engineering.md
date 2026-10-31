## Software engineering rules (MUST)

The goal is to get the foundations right in the first version: once the foundations are crooked, later code copies the
crooked pattern all the way down. "Foundations" means what is most expensive to change and most easily imitated, not
"the more abstraction the better".

**Must:**

- **Dependencies come in from outside**: core logic doesn't create or fetch its external dependencies (API clients,
  databases, the file system, the clock, configuration, global singletons) itself; they are always passed in through
  the constructor or parameters. Where the concrete implementations are wired together is one place, the program's
  entry point. In languages like Python, passing a function or an object in as a parameter counts; there is no need to
  force a class hierarchy.
- **Clear boundaries**: core logic is kept apart from I/O, the platform and external services; dependencies always point
  from the outside in, onto the core's abstractions, never from the core onto the outside's concrete implementations.
- **Known points of variation are done right in the first version**: as soon as there are, or are certain to be, two or
  more ways of doing something (different platforms, providers, models, notification channels, output formats, scoring
  methods…), make one common interface with one implementation per way, and let the caller inject which one to use.
  Don't write an if/elif/switch first, planning to change it later.
- **No point of variation, no abstraction in advance**: where there is only one way and no sign of a second, don't make
  an interface, a factory or an empty layer first. A wrong abstraction gets copied just like a wrong branch, and is
  harder to take apart.

**Design criteria** (for examining a design, not a checklist to tick off line by line): each module is responsible for
one thing; extending means adding an implementation, not changing existing code; swapping an implementation needs no
change in its callers; interfaces are small and focused; high-level logic depends on abstractions, not on concrete
implementations.

**Existing code is not a template**: where existing code conflicts with these rules, the rules win. Don't copy a flawed
pattern to "stay consistent with the existing style"; point it out and propose a refactor (or do it, when it is in
scope). Refactor while the project is still small; don't wait for the crooked pattern to spread.

**Don't write where these rules come from**: in code, comments, README, docs, commit messages and PR descriptions, don't
write wording that says a rule was given as an instruction, such as "as the user/{{owner}} asked", "according to the
project rules" or "because the rules require it". Write design reasons as usual (e.g. "the platforms must be
interchangeable, so this is an interface"); names of design principles or patterns may be used as usual too; just don't
present them as rules someone handed down.
