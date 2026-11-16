# STEM learning guide — azazael-core

azazael-core is the installable repackaging of the AZAZAEL tree: the same components, arranged so that another project can depend on them instead of copying files.

This guide is for teachers, mentors, club leaders, parents and students who want to
use this repository as a vehicle for real STEM learning — in particular for girls and
other groups still under-represented in computing, engineering and mathematics. It
says what the project actually teaches, how to run a session with it, and what to
remove so that nobody is excluded by accident.

## What this project teaches

**Subject areas**

- Python packaging
- dependency management
- reproducible environments
- API design

**Skills practised**

- writing a pyproject.toml that says what it means
- pinning a version and explaining why
- reading an install error instead of guessing

## Prerequisites

Python 3.13 and pip. No prior packaging experience: the first exercise is deliberately about reading metadata, not writing it.

Nothing else is assumed for the entry-level activity below: no prior programming
experience, no particular hardware, and no home internet connection.

## Six practices this repository is set up to support

These follow the published guidance on gender-equitable STEM education — connect the
work to learners' lives, let them investigate real problems, treat struggle as part of
the work, name and challenge stereotypes, make the work collaborative, and put
learners in front of diverse role models.

1. **Connect it to their lives.** every time an install fails on a learner's laptop, packaging is why — this is the invisible layer under every app they use
2. **Let them investigate.** give each pair a broken pyproject.toml and ask them to predict the pip error before running it
3. **Make struggle normal.** Build the session so that the first attempt is *expected*
   to fail. Say out loud that debugging is the subject, not a penalty. Ask "what did
   that error tell you?" before offering the answer.
4. **Challenge the stereotype.** Name who does this work and why it is not a gendered
   skill. Praise the method ("you isolated the variable"), not the person ("you're a
   natural") — the second kind of praise teaches learners that ability is fixed.
5. **Make it social.** pairs swap their manifests and try to install each other's package; the author may not speak while it runs
6. **Show diverse role models.** maintainers of the packaging tools everyone already uses, and the people who write the error messages learners will meet

## A 90-minute session

1. *(15 min)* Read the manifest and translate each field into plain English.
2. *(25 min)* Predict, then trigger, three different install failures.
3. *(30 min)* Fix the manifest so the package installs on the oldest supported interpreter.
4. *(20 min)* Write the error message you wish you had been given.

## Vocabulary to define before the session

Specialist vocabulary is a hidden entry test if nobody explains it. Define each of
these the first time it appears, in one sentence, in the learners' own words:

1. **wheel** — a pre-built package archive.
2. **editable install** — linking the source tree so edits take effect without reinstalling.
3. **requires-python** — the interpreter versions the package claims to support.

## Accessibility

Read [ACCESSIBILITY.md](ACCESSIBILITY.md) before running a session. Two rules matter
most in a mixed-ability room:

- Every activity must be completable with a keyboard alone, with a visible focus ring.
- Every instruction names the control or the heading ("the **Run** button in the
  toolbar"), never a colour or a position.

Pair learners rather than singling anyone out, and always offer a text-only route for
a visual step.

## Contributing as a learner

Small, well-scoped contributions are the point, not a consolation prize:

- Browse issues labelled
  [`good first issue`](https://github.com/myzticalbabe/azazael-core/labels/good%20first%20issue).
- Read [CONTRIBUTING.md](CONTRIBUTING.md) — it has a section written for first-time
  contributors.
- Ask in [SUPPORT.md](SUPPORT.md) if anything is unclear. Asking is expected here; it
  is not a failure.

## Licence and classroom reuse

Classroom, club and workshop use is welcome. See `LICENSE` for the exact terms, and
check whether your institution requires a separate agreement before redistributing
modified copies.
