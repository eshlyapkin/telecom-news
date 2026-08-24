Project root:
/home/joe/Projects/telecom-news

SOURCE OF TRUTH

The source of truth is:
1. actual project files;
2. Git state;
3. actual tool/command results.

Previous assistant statements and plans are not evidence.

Never claim that:
- a file was created or changed;
- a command was executed;
- a test passed;
- a source was selected or verified;
- an endpoint exists;
- a milestone was completed;

unless this was confirmed by an actual tool result or by reading the
corresponding project file.

If information is not verified, explicitly say "not verified".

PROJECT STARTUP

At the beginning of a new work session:

1. confirm project root;
2. read docs/CURRENT.md;
3. run:
   git status --short
   git log -3 --oneline
4. report current milestone, next step and blockers.

Do not automatically read the whole project documentation.

Load ROADMAP, ARCHITECTURE and DECISIONS lazily and only when required
for the current task.

WORKING RULES

Work on one milestone or one explicitly defined subtask at a time.

Before modifying code:
- inspect the files relevant to the task;
- check current Git state;
- identify the acceptance criteria for the task.

Do not implement functionality belonging to later milestones unless
explicitly approved.

Do not invent dependencies, files, APIs, endpoints or project state.

EXTERNAL RESEARCH

For external facts such as RSS/Atom endpoints, APIs, publication URLs,
vendor capabilities, and current product/service information:

- do not rely on model memory;
- use search results only for discovery;
- verify final claims against the official source;
- do not invent RSS/API endpoints;
- if a fact cannot be confirmed, mark it as NOT VERIFIED;
- do not repeat essentially the same failed search query;
- after several poor searches, change strategy.

For source discovery, evaluate publication relevance first.
Do not search for RSS/API until the content source itself has been shown
to regularly publish material relevant to the project's domain.

VERIFICATION

After changing code:

1. run the tests relevant to the change;
2. run the full test suite when completing a milestone;
3. run:
   git diff --check
   git status --short
   git diff --stat
4. compare the actual result with the milestone acceptance criteria.

A task is not complete merely because code was written.

A task is complete only when its acceptance criteria have been verified.

If verification fails:
- diagnose the failure;
- fix only within the current task scope;
- rerun verification;
- do not report the task as completed until verification succeeds.

DOCUMENTATION

docs/CURRENT.md is the canonical short handoff.

Update it when project state changes.

Do not duplicate the same detailed state across multiple documents.

Update ROADMAP only when project scope or milestone definition changes.
Update ARCHITECTURE only when architecture changes.
Update DECISIONS only when an architectural/project decision is made.

GIT

Do not create commits unless explicitly requested by the user.

Before a requested commit:
- verify tests;
- run git diff --check;
- inspect git status and diff;
- confirm only expected files will be committed.

Never start the next milestone automatically after completing the current one.
Stop and report the result.