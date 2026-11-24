# Security Policy

## Scope

This repository is an AI-agent security demonstration with fictional data. This policy does not imply a production security certification.

## Reporting a vulnerability

Please report security problems **privately** to the maintainer using the contact listed on the author's GitHub profile or GitHub private vulnerability reporting if it is enabled for this repository. **Do not put secrets, private data, attack details, or exploit samples in public issues.**

Include the affected component, version or commit, a concise impact statement, and a minimal safe reproduction. No remediation response time is guaranteed by this document.

## Responsible practices

- Never commit API keys, customer credentials, live patient data, private client documents or cloud access tokens.
- Keep dependencies updated and review automated security reports.
- Follow least-privilege permissions and use dummy data in public demonstrations.
- Verify deployment-specific access controls before processing sensitive information.
- Treat passing scans as limited evidence, not a guarantee of security.

### Agent boundary

Prompt instructions are not a substitute for authorization. Keep tool permissions and approval boundaries enforced outside the model; do not operate on live print-shop/customer data without independent review.

