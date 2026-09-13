# Security

> **Status.** Upload safety and the authorisation model are implemented and
> tested (41 + 24 tests). Authentication, API permission enforcement and audit
> logging are `PARTIAL` — the model and settings exist; the enforcement layer
> is being built. Nothing below is described as working unless it is.

## Threat model

Three properties shape this system:

1. **Documents come from adversaries.** In a dispute, the other side's
   submissions are ingested. Uploads are untrusted input handed to
   memory-unsafe parsers.
2. **The data is commercially sensitive.** A contract, a claim position and an
   evidence assessment are material non-public information.
3. **Deployment is isolated but not hardened.** A LAN server administered by
   people who are not platform engineers. Defaults must be safe.

Out of scope: public internet exposure, multi-tenant hosting by an untrusted
operator, and defence against an attacker with database access.

## Baseline

| Control | Where | Status |
| --- | --- | --- |
| Argon2 password hashing | `settings/base.py` `PASSWORD_HASHERS` | Implemented |
| Minimum 12-character passwords + validators | `AUTH_PASSWORD_VALIDATORS` | Implemented |
| Email-based auth, no username enumeration surface | `accounts.User` | Implemented |
| Account lockout fields | `User.failed_login_attempts`, `locked_until` | Model only |
| HttpOnly, SameSite=Lax session cookies | `settings/base.py` | Implemented |
| CSRF protection | Django middleware; SPA echoes the token | Implemented |
| Deny-by-default DRF permissions | `DEFAULT_PERMISSION_CLASSES` | Implemented |
| Scoped rate limits (auth / upload / ai / search) | `DEFAULT_THROTTLE_RATES` | Implemented |
| Security headers + CSP | `docker/nginx/gateway.conf` | Authored, unverified |
| Non-root container user (uid 10001) | `docker/backend.Dockerfile` | Authored, unverified |
| Secrets from the environment only | `config/env.py` | Implemented |

Every secret is read via `env_str(..., required=True)`, which raises at import
time when absent. A missing `SECRET_KEY` stops the process rather than
surfacing later as a confusing auth failure. There is no insecure fallback
value anywhere.

## Authorisation

Two scopes, deliberately separate (`accounts/domain/permissions.py`):

- **Organization** — administering the tenant: users, roles, knowledge bases,
  model configuration.
- **Project** — working inside one project: documents, claims, evidence.

**Organization membership grants no access to project contents.** An
Organization Administrator can manage users and knowledge bases but cannot read
a project's documents without project membership. Administering a tenant and
reading its commercially sensitive material are different privileges, and a
test asserts the separation:

```python
def test_organization_admin_cannot_read_project_content_without_membership():
    effective = resolve_permissions(ROLE_ORGANIZATION_ADMINISTRATOR.code)
    assert ORG_MANAGE_USERS.code in effective
    assert DOCUMENT_VIEW.code not in effective
```

System Administrator is the single exception, stated explicitly rather than
implied.

Unknown role codes resolve to **no permissions** rather than raising, so a
stale role reference degrades to less access, never more.

`validate_role_definitions()` runs as a test and at startup: it catches a
project role granted an organization permission, which would silently widen
access for every member holding it.

### Enforcement points

| Layer | Mechanism | Status |
| --- | --- | --- |
| API | DRF permission classes per viewset | `PLANNED` |
| Service | Explicit permission check in each use case | `PLANNED` |
| Retrieval | `RetrievalScope.accessible_project_ids` as a SQL predicate | Implemented (domain) |
| Media | nginx `internal;` — files served only via a permission-checked view | Authored |

Checking at both the API and the service layer is deliberate duplication: a
service called from a Celery task or a management command has no request to
carry a permission check.

The prototype had none of this. Every endpoint was open, and `/contracts`
listed every contract in the system.

## Upload handling

Implemented and tested in `ingestion/domain/file_safety.py` (41 tests). All
checks run **before** any parser opens the file.

| Check | Behaviour |
| --- | --- |
| Filename sanitisation | Strips POSIX and Windows path components, control characters, NTFS ADS suffixes; neutralises reserved device names (`CON.pdf`); truncates preserving the extension |
| Magic-byte detection | Content decides the type. A client `Content-Type` is a claim, not evidence |
| Executable rejection | `MZ`, `ELF`, Mach-O, shebang, `<?php` rejected regardless of extension |
| Allow-list | Deny by default |
| Size limit | Measured server-side, not from a header |
| PDF active content | `/JavaScript`, `/Launch`, `/EmbeddedFile`, `/OpenAction` flagged |

```python
def test_executable_renamed_as_pdf_is_rejected():
    report = check(filename="invoice.pdf", header=b"MZ\x90\x00" + b"\x00" * 60)
    assert report.is_rejected is True
```

PDF active content is **flagged, not blocked**. Blocking would reject genuine
documents produced by tools that add an `/OpenAction`, and the pipeline extracts
text without executing anything. The finding is recorded on the document for
review — a deliberate trade, not an oversight.

`validate_upload` returns a report; `enforce_safety` raises. They are separate
so a rejected upload can still be audited: an attempted executable upload is
exactly the event a trail should retain.

## Data handling

- **UUID primary keys** everywhere. The prototype exposed 8-character id
  prefixes on unauthenticated endpoints, making every contract enumerable.
- **Soft delete** on documents, claims, evidence and correspondence. An audit
  trail referencing a vanished row is not an audit trail.
- **Media is never statically served.** nginx marks `/media/` `internal;`.
- **No secrets in errors.** The exception handler returns a generic 500 with a
  request id and logs the detail server-side. The prototype returned
  `{"found": false, "text": str(e)}`.
- **No absolute paths, credentials or stack traces in `details`.** Stated as a
  rule on `ClaimIQError`.

## Air-gapped operation

No code path reaches an external service. `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1` are set in settings *and* the Dockerfile, so a missing
local model fails loudly instead of attempting a download. The CSP allows no
external origin.

## Known gaps

Named rather than omitted:

| Gap | Impact | Plan |
| --- | --- | --- |
| Login throttling/lockout not wired | Brute force limited only by the DRF `auth` throttle | Phase 8 |
| No MFA | Password-only authentication | Post-1.0; TOTP |
| Audit log model not implemented | Actions not yet recorded | Phase 5 |
| No malware scanning | Structural checks only, no signature scan | Optional ClamAV sidecar; deliberately not a hard dependency for air-gapped installs |
| API tokens modelled, not issued | `ApiToken` exists; no issuance flow | Phase 8 |
| Field-level encryption absent | Sensitive fields are plaintext at rest | Relies on disk encryption; revisit if required |
| Nothing runtime-verified | No penetration testing has been performed | Requires a running stack |
