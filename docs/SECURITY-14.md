# OPTPIPE: fourteen security requirements

Status: local engineering contribution dated 2026-11-23. Baseline: main commit
`7deb5caece83dbbebd40e31b168031d7375f4cfc`. This document records Eugene's
instruction to prevent the fourteen abuse cases and defines the evidence required
when relevant features are introduced. It is not a declaration of production
security, deployment, penetration testing, or independent team acceptance.

## Current boundary

The inspected Python application is a local, paper-only research and replay
system. The dashboard exposes read-only GET routes over an operator-selected
output directory. Its CLI binds only to literal loopback addresses. The inspected
backend has no account signup, administrator service, checkout, payment,
redemption, photo-upload, SQL API, or LLM-controlled tool executor. Existing
classes named agents implement deterministic Python stages.

Absence of a feature is an observed scope limitation, not a passing test for its
future implementation. A separate public application, uninspected integration,
new platform, or later source revision needs its own assessment. An identifier in
the local dashboard selects a session from the same local corpus; the current
dashboard does not implement per-user or tenant access control.

This contribution adds a strict HTTP Host boundary, strict positive integer
counts at the risk-evaluation boundary, and repository credential hygiene. It
preserves existing file-descriptor containment, GET-only behavior, paper-only
rules, generated models, the pinned specification, and risk thresholds. The
associated receipt identifies the actual source, tests, results, and limitations.

## Required controls and acceptance evidence

| ID / prohibited outcome | Required protection | Current scope and acceptance evidence |
| --- | --- | --- |
| S14-01: obtaining administrator access by typing an admin URL | Enforce authenticated role and operation permissions on the server, deny by default, and check every administrative request. Hiding links is not authorization. | No admin service in the inspected backend. Before introduction, ordinary and unauthenticated callers must receive denial with no privileged state change; an authorized administrator must retain the intended function. The local Host check is not an admin permission system. |
| S14-02: reading or changing another owner's record by changing an ID | Resolve ownership and tenant membership from the authenticated principal and enforce object-plus-operation authorization for read, update, delete, export, and bulk operations. Opaque IDs alone are insufficient. | Existing local session/file containment remains required. The traversal, symlink, hardlink, and descriptor-race checks protect a filesystem boundary, not multiple users. A multiuser service requires a two-user/two-tenant positive and negative access matrix, including downloads. |
| S14-03: exposing a secret API key in the browser | Keep secret credentials on the server, with least privilege and redacted logs. Never place secrets in HTML, scripts, source maps, client storage, URLs, or client-readable error responses. Treat provider-designated publishable identifiers separately from secret keys. | No populated provider credential was found in the bounded source review; that is not a complete historical or deployed-artifact audit. Use synthetic sentinels to check built client artifacts and error paths before connecting a real provider. The hygiene changes below are an additional control, not proof of no leakage. |
| S14-04: changing an authoritative price in a client request | Compute authoritative prices, currency, discounts, fees, balances, and totals on the server from trusted records. Accept only the client selections it is authorized to make. Validate the final operation against server state. | No commerce checkout exists. Paper option prices are research inputs, not charges; do not invent commerce semantics or silently replace the pinned risk policy. A future payment or credit feature must test altered, missing, negative, non-finite, and mismatched totals and demonstrate that persisted amounts remain correct. |
| S14-05: negative, zero, or malformed item counts changing a calculation | Require strict positive integers at the actual use boundary. Apply approved business limits separately. Revalidate nested typed instances as well as incoming dictionaries so mutation or unchecked object construction cannot bypass validation. | Local correction covers `RTicket.quantity` and `RLeg.ratio` at `evaluate()`. Regression cases include negative/zero counts, booleans, floats, numeric strings, and altered/copied/constructed instances, with valid integer controls. No arbitrary new maximum or risk threshold is introduced. |
| S14-06: granting value for a fake payment | Verify payment status on the server with the provider, authenticate callbacks, match the expected order, amount, and currency, and make fulfillment idempotent. Browser redirects or a client-supplied paid flag are not payment evidence. | No payment feature exists. Before introduction, test forged callbacks, wrong amounts/currencies/orders, duplicate and concurrent events, out-of-order delivery, and reconciliation using sandbox fixtures with no real charges. Exactly the authorized value may be granted. |
| S14-07: redeeming a one-use benefit repeatedly | Enforce eligibility and consumption atomically using transactions and uniqueness or equivalent durable concurrency controls. Bind idempotency keys to the principal, operation, and request parameters. | No redemption feature exists. Acceptance must include concurrent attempts, retry after timeout, duplicate requests, and altered idempotency payloads; verify the final reward count and balance, not only response codes. |
| S14-08: choosing an administrator role during signup | Allowlist caller-editable fields; assign the ordinary role on the server. Reject or ignore privileged fields safely, and use a separately authorized operation for role changes. | No signup feature exists. Before introduction, send role/admin/permission and nested variants and inspect the effective persisted privileges. Merely hiding an admin form field is insufficient. |
| S14-09: guessing or replaying a login code | Use a maintained authentication implementation with short-lived, single-use, account/challenge-bound codes, protected transport, and account/challenge attempt limits. Resending must not reset abuse limits. Do not log codes; protect recovery and concurrent consumption. | No login-code verifier exists. Acceptance must cover wrong, expired, cross-account, already-used, concurrently used, and repeatedly resent codes, plus legitimate login and recovery. Set numerical limits through a reviewed authentication design. |
| S14-10: making AI execute an unauthorized instruction from content | Treat retrieved pages, messages, files, tool output, and model output as untrusted data. Enforce capability allowlists, caller authorization, argument validation, and consequential-action approval outside the model. Give it only the credentials and permissions its authorized task needs. | No LLM tool executor was found in the inspected backend. Future tests must inspect the actual tool trace and resulting state for direct and indirect prompt injection, including paraphrases and benign controls. A model's verbal refusal, prompt filter, or system prompt alone is not an enforcement boundary. Do not promise complete prompt-injection elimination. |
| S14-11: executing script embedded in a displayed name or other text | Use context-appropriate output encoding and safe text sinks. Preserve template autoescaping. Permit rich HTML only through an approved maintained sanitizer; use a suitable CSP as an additional layer when a hosting design exists. | Existing Jinja autoescaping is preserved. New server-rendered HTML tests exercise hostile script/image/SVG text and ordinary text in real ticket-thesis and idea-description fields, and verify the record bytes remain unchanged. These checks do not execute a browser or prove every future rendering context safe. |
| S14-12: uploading an executable or malicious file as a photo | Allow only needed formats; verify actual decoded content rather than trusting extension or MIME. Bound bytes, pixels, frames, and processing resources. Decode and re-encode accepted images, generate server filenames, and isolate nonexecutable storage. Require uploader authorization. | No upload feature exists. Before introduction, test spoofed types, malformed/truncated data, executable or SVG input, oversized/decompression cases, path-like filenames, and valid photographs. Serving and downloading require access checks too. |
| S14-13: committing environment secrets or leaking them in checks | Ignore local credential files and independently reject tracked prohibited credential filenames, including forced additions. Use safe diagnostic output and a maintained approved scanner before release; define its exact tree/history scope. | The local patch adds case-insensitive environment/key filename protection, a strict blank-value `.env.example` exception, NUL-delimited Git filename handling, and redacted detection output. Disposable Git tests cover spaces, newlines, Unicode, force-adds, templates, and unquoted environment-style assignments. This lightweight scanner is not a complete secret audit. A real exposure requires incident handling and authorized revocation/rotation, not merely deleting the file. |
| S14-14: turning input into arbitrary database queries | Use parameterized values, application-owned allowlists for identifiers/query shape, least-privilege database accounts, and per-user/tenant filters. Do not expose an arbitrary SQL endpoint or let model-generated SQL acquire unrestricted authority. | No SQL query service exists in the inspected backend. Before introduction, verify adversarial values remain data, authorized queries still work, and another user's rows cannot be returned. Review exports and background jobs as well as interactive routes. |

## Implemented local boundaries

### HTTP Host validation

`src/optpipe/dashboard/security.py` rejects missing, duplicate, malformed, and
nonlocal Host values before routing or reading session files. The fixed accepted
authorities are `127.0.0.1`, `[::1]`, and `localhost`, optionally followed by a
decimal port from 1 through 65535. Names are matched without case sensitivity;
alternate numeric address spellings, suffixes, trailing dots, and `testserver`
are not exceptions. Forwarding headers do not grant authority.

This complements loopback binding against requests addressed to an unrelated
host. It is not authentication, a full cross-origin policy, protection from a
malicious local process, or authorization to expose this application publicly.
The exporter and in-process test clients use an explicit accepted local base URL.

### Strict counts and compatibility

The local `_M` model family revalidates instances on use, including the nested
count-bearing ticket and legs. The separate `RField` model is unchanged; this
does not establish universal revalidation of every sourced-data field.
Quantity and leg ratio require an actual positive integer. Callers previously
relying on coercion from `"1"`, `1.0`, or `true` must supply `1` instead. A JSON
Schema may consider `1.0` mathematically integral; this typed boundary deliberately
rejects the floating representation. The specification and generated models are
unchanged. Buy/sell direction remains an explicit field, not a negative count.

### Repository credentials

The one template filename exception is exact lowercase `.env.example` with
blank assignments and comments. Populated values, including harmless nonsecret
defaults, do not belong in that exception. Put normal nonsecret defaults in the
existing configuration format. Private-key and credential-container filename
rules are conservative; adding a public certificate fixture requires a specific
reviewed policy adjustment rather than a broad ignore or scanner bypass.

Checks emit a location and redacted diagnostic, not matched credential text.
They inspect the checked-out files and tracked filenames; they do not certify
every prior commit, remote deployment, ignored local file, artifact, or arbitrary
secret format. Findings must not be forwarded with the credential value.

## Team workflow and feature activation

Keep the established owners: Dot integrates Lesson001 v4; Soodle coordinates;
Moodle manages repository/build; Doodle owns specification and risk policy;
Poodle supports implementation; Zoodle executes acceptance; Noodle reviews
evidence without becoming the test executor. Claude CC-001 remains a separate
contribution unless the owners explicitly coordinate its integration.

For each change, identify the exact source commit and changed paths, applicable
S14 IDs, trust boundary, expected legitimate behavior, misuse case, and precise
acceptance evidence. Implement the smallest coherent increment, retain raw
failures, then obtain independent review. A feature with an unmet applicable
security requirement stays unavailable. A documentation requirement is not an
automated implementation; attach the actual enforcement and test evidence before
marking it satisfied. Do not use a broad insecure feature flag as an exception.

Keep statuses distinct: **reported**, **source reviewed**, **executed on named
source/environment**, **independently accepted**, and **deployed**. A changed
source does not inherit another source's test count. Existing Lesson001 or
CC-001 receipts do not prove this patch; this patch does not close their unrelated
portability or security findings.

This work authorizes local application hardening. It does not change the existing
restrictions on dependency installation, network/security-policy modification,
broker access, live trading, spending, remote publication, merges, or deployment.
Check current assignments before starting to avoid duplicate writers. A missing
runtime guard must stop affected execution; environment variables are not guard
attestation. Never work around a blocked browser, DOM, or network capability.

## Platform and verification requirements

Use operating-system-independent validation rules, explicit encodings, and
repository-relative handoff paths. Preserve fail-closed filesystem behavior on
platforms lacking a required primitive; do not remove symlink, hardlink, or race
protections to make a portability check pass. A local HTTP Host allowlist must
not grow to a wildcard merely to make a phone reach the service.

The current execution receipts identify Linux and the actual Python version.
Native Windows/macOS and physical mobile/browser behavior require separate
verification against the integrated source. Static HTML assertions and in-process
ASGI requests are not browser, JavaScript, device, or deployed-service tests.

Focused modules for this contribution are `tests/test_dashboard_security.py`,
`tests/test_security14_risk_input.py`, `tests/test_repo_hygiene_security.py`, and
`tests/test_security14_rendering.py`. Existing dashboard containment/bind,
paper-only, hygiene, risk, adapter, and scenario tests provide relevant regression
coverage. The receipt must state exactly which ran and any exclusions, warnings,
failures, and overlapping counts. Existing tests already enforce GET-only routes;
keep that invariant instead of creating admin/payment/upload endpoints to test.

## Primary references

These sources inform the requirements; local execution evidence is in the
separate patch receipt, not in these external documents.

- [OWASP Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
- [OWASP Business Logic Security](https://cheatsheetseries.owasp.org/cheatsheets/Business_Logic_Security_Cheat_Sheet.html)
- [OWASP Third Party Payment Gateway Integration](https://cheatsheetseries.owasp.org/cheatsheets/Third_Party_Payment_Gateway_Integration_Cheat_Sheet.html)
- [OWASP Mass Assignment](https://cheatsheetseries.owasp.org/cheatsheets/Mass_Assignment_Cheat_Sheet.html)
- [NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b.html)
- [OWASP LLM Prompt Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- [OWASP Cross Site Scripting Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html)
- [OWASP File Upload](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)
- [OWASP Secrets Management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [OWASP SQL Injection Prevention](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html)
