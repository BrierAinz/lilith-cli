# Security Policy

## Supported versions

Security fixes target the current `main` branch and the latest published release.

| Version | Supported |
|---|---|
| Latest release | Yes |
| `main` | Yes |
| Older releases | No |

## Reporting a vulnerability

Do not report security vulnerabilities in a public issue, discussion, pull request, or log.

Use GitHub's private vulnerability reporting flow:

1. Open the repository's **Security** tab.
2. Select **Report a vulnerability**.
3. Include the affected component and version, impact, reproduction steps, and any known mitigation.
4. Remove tokens, personal data, and unrelated private material from the report.

You should receive an initial acknowledgement through GitHub. Please allow time for validation before publishing details.

## Scope

Reports are especially useful for:

- Credential or secret exposure.
- Unsafe command execution or path handling.
- Authentication and authorization bypasses.
- Prompt or tool injection that crosses an established trust boundary.
- Memory, session, or local data disclosure.
- Dependency vulnerabilities with a demonstrated impact on Lilith.

General hardening suggestions and dependency updates without a demonstrated security impact should use the normal issue forms.
