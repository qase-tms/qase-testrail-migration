# Security Policy

## Reporting a vulnerability

**Do not open a public issue or discussion, and do not email a vulnerability report to the general migrations address.**

Report privately through GitHub's private vulnerability reporting on this repository: **Security > Report a vulnerability**. This creates a private advisory visible only to the maintainers.

Please include the affected version or commit, what an attacker could achieve, and the steps to reproduce.

We will acknowledge the report and keep you updated until it is resolved.

## What this tool handles

This script reads from a source test management system and writes into Qase. Understanding what it touches will help you judge whether something is a vulnerability:

- **Credentials.** API tokens for both systems are read from `config.json`, or from the environment where the standard supports it. They are held in memory for the duration of the run. Tokens are never written to logs at any logging level.
- **Customer data.** Test cases, runs, results, attachments and user identities pass through the process. Logs written to `logs/` and statistics written to `stats/` can contain test case content, email addresses and internal URLs.
- **Local artifacts.** `config.json`, `logs/` and `stats/` are gitignored and must never be committed. Delete them once a migration is complete.

## Handling your own credentials

- Use a token scoped to the minimum permissions the migration needs.
- Revoke the tokens used for a migration once it is finished.
- Never commit `config.json`. It is gitignored, but a file added under a different name will not be.
