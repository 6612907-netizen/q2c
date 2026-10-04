TASK:
Q2C-PRODUCT-01
q2c Public Product Convergence & GitHub v0.1.0

ROLE:
Qoder Experts
Primary implementation owner

TECHNICAL LEAD:
Codex

PRODUCT OWNER:
ChatGPT / Human

MISSION:

Turn the existing q2c implementation into a focused,
publicly releasable product:

q2c — Reliable handoffs between AI coding agents.

Core product principle:

q2c guarantees the handoff,
not the outcome.

==================================================
1. DO NOT REWRITE THE WORKING TRANSPORT
==================================================

Existing verified communication/wake/restart/idempotency
capabilities are valuable.

Preserve them.

This task is:

convergence
boundary cleanup
protocol formalization
productization

NOT:

greenfield rewrite.

==================================================
2. PRODUCT OWNERSHIP
==================================================

q2c owns only:

message delivery
correlation
idempotency
transport ACK
transport retry
logical session mapping
agent adapters
artifact references
transport recovery
communication trace
transport security

q2c MUST NOT own:

workflow task state
project scheduling
CI truth
code quality judgment
review approval
release decision
project retry budget
workflow recovery
human approval
task completion

==================================================
3. FIRST PHASE — BOUNDARY AUDIT
==================================================

Locate all code that currently implements:

acceptance grading
project completion
review approval authority
workflow recovery
worker retry budgets
project scheduling
release gating

Classify each as:

KEEP_AS_TRANSPORT
RENAME_AS_TRANSPORT
DEPRECATE
REMOVE_FROM_PRODUCT_PATH

Do not delete evidence/history blindly.

Produce:

Q2C-BOUNDARY-AUDIT.md

==================================================
4. PROTOCOL V1
==================================================

Create:

PROTOCOL.md

Freeze:

protocol_version
message_id
request_id
correlation_id
sender
receiver
handoff_type
session_id
workspace_ref
repo_ref
commit_ref
payload
artifact_refs
idempotency_key
created_at
expires_at
metadata

Message types:

HANDOFF_REQUEST
HANDOFF_STARTED
HANDOFF_PROGRESS
HANDOFF_RESULT
HANDOFF_FAILED
HANDOFF_ACK

Transport states:

CREATED
QUEUED
DELIVERING
DELIVERED
STARTED
RESPONDED
ACKED
DELIVERY_RETRY
DELIVERY_FAILED
EXPIRED
CANCELLED

Do not introduce TASK_COMPLETED or READY_TO_RELEASE.

==================================================
5. TRANSPORT RETRY
==================================================

Retry means:

retry message delivery.

Retry MUST NOT mean:

rerun Agent task.

Document and machine-test this boundary.

==================================================
6. ADAPTER CONTRACT
==================================================

Define a minimal adapter interface:

start
resume
send
receive
status
cancel
capabilities

Official v0.1 adapters:

Codex
Qoder

Existing integrations should be migrated,
not unnecessarily rewritten.

==================================================
7. LOGICAL SESSION
==================================================

Introduce stable q2c logical session IDs.

Map:

q2c_session_id
↔ provider session ID

Provider restart must not change the logical
handoff identity.

==================================================
8. ARTIFACT REFERENCES
==================================================

Support typed refs for:

git_commit
file
diff
patch
log
test_report
screenshot
evidence_package
generic_uri

Prefer:

reference + digest

over raw artifact duplication.

==================================================
9. TRACE
==================================================

Implement transport trace sufficient to answer:

who sent it
who received it
when delivered
when started
which session
which artifacts
which retries
what response
when acknowledged

Trace is:

communication truth.

It is NOT:

project completion truth.

==================================================
10. CLI
==================================================

Provide:

q2c init
q2c doctor
q2c adapters
q2c send
q2c inspect
q2c list
q2c trace
q2c retry-delivery
q2c cancel
q2c sessions
q2c version

Avoid expanding CLI into project management.

==================================================
11. SECURITY
==================================================

q2c must:

not own credentials
not copy raw tokens
not persist secrets

Add:

credential redaction
payload secret protection
safe process spawning
process-group cleanup
workspace restrictions
path validation
safe logging

Produce:

SECURITY.md

==================================================
12. PUBLIC REPOSITORY
==================================================

Prepare:

README.md
LICENSE
SECURITY.md
CONTRIBUTING.md
CHANGELOG.md
ARCHITECTURE.md
PROTOCOL.md
ADAPTERS.md
examples/
tests/
.github/workflows/

README Quick Start must be executable by a new user.

==================================================
13. DO NOT BUILD YET
==================================================

Do NOT add:

workflow engine
DAG
project scheduler
quality engine
release manager
AI reviewer
cloud service
billing
web dashboard

unless explicitly authorized in a later product phase.

==================================================
14. TESTS
==================================================

Required:

Codex → Qoder real handoff
Qoder → Codex real handoff

ACK

idempotency

duplicate delivery

transport retry

restart recovery

session resume

artifact reference integrity

credential unavailable

process cleanup

expired handoff

cancelled handoff

adapter failure

1000 synthetic handoffs

Required result:

0 silent loss

0 unexpected duplicate handoff effects

==================================================
15. EXISTING BUG REGRESSION
==================================================

Keep regression coverage for previously discovered:

recursive runner/reentry

orphan/nested child behavior

restart duplicate review risk

credential unavailable handling

Do not regress these fixes.

==================================================
16. PACKAGING
==================================================

Produce a clean install path.

Prefer current implementation language/package layout.

Do not rewrite solely for packaging aesthetics.

Clean-machine test:

install
q2c doctor
configure adapters
send
receive
trace

==================================================
17. RELEASE
==================================================

Target:

q2c v0.1.0

Release is allowed only when:

protocol documented
tests green
real bidirectional handoff green
restart/idempotency green
security docs exist
quickstart verified
working tree clean
release artifacts reproducible

==================================================
18. REPORT
==================================================

Produce:

Q2C-v0.1.0-RELEASE-REPORT.md

Include:

PRODUCT_SCOPE=
PROTOCOL_STATUS=
CODEX_ADAPTER=
QODER_ADAPTER=
DELIVERY_STATUS=
SESSION_STATUS=
ARTIFACT_STATUS=
TRACE_STATUS=
SECURITY_STATUS=
TEST_STATUS=
1000_HANDOFF_RESULT=
INSTALL_TEST=
KNOWN_LIMITATIONS=
REMOVED_WORKFLOW_RESPONSIBILITIES=
RELEASE_READY=

Final verdict only:

Q2C_V0_1_RELEASE_READY

or

Q2C_V0_1_RELEASE_BLOCKED

==================================================
19. EXECUTION MODE
==================================================

Continue autonomously.

Do not ask for approval for:

file names
module layout
test placement
small refactors
bug fixes
documentation wording
package metadata
normal compatibility changes

Stop only for:

destructive action
new credentials
paid service
major protocol redesign
new architecture subsystem
security weakening
irreversible migration
3 failed bounded fix attempts

==================================================
START
==================================================

First perform the boundary audit.

Then implement:

Protocol
→ Transport Convergence
→ Adapters
→ Sessions
→ Artifacts
→ Trace
→ Security
→ Packaging
→ Tests
→ GitHub release readiness

Do not turn q2c back into an orchestrator.