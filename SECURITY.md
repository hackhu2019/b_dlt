# Security Policy

## Supported Versions

This project is still in an early stage.

Security fixes, when accepted, are applied on the latest `main` branch first.
Older snapshots or forks are not guaranteed to receive backports.

## Reporting a Vulnerability

Do not open a public GitHub issue for security-sensitive problems.

Report security issues privately through GitHub Security Advisories or contact the
repository owner directly if a private reporting channel is configured later.

When reporting, include:

- affected script or workflow stage
- impact and realistic attack path
- minimal reproduction steps
- whether any secret, cookie, token, or local data exposure is involved

Do not include real secrets in the report.

## Secret Handling

This repository must never contain:

- Bilibili cookies
- raw `Cookie` headers
- OpenAI API keys
- personal browser profile data
- real transcript, audio, or database artifacts generated from private content

If you accidentally commit sensitive material:

1. revoke or rotate the secret immediately
2. remove it from the repository history
3. document the cleanup steps in the incident response

## Security Scope

The main risks in this repository are practical rather than theoretical:

- secret leakage through logs, issues, or commits
- over-broad file handling when processing local artifacts
- unsafe assumptions around third-party CLI behavior
- misuse of downloaded or transformed content outside authorized contexts
