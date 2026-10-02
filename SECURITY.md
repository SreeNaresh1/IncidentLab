# Security Policy

## Supported Versions

IncidentLab is continuously developed and validated. Security updates are applied directly to the `main` branch.

| Version | Supported |
|---|---|
| `main` | :white_check_mark: |
| Older commits / tags | :x: |

---

## Scope Note

IncidentLab is a distributed systems failure laboratory. By design, the experiment runner intentionally triggers faults — such as severing local Docker networks, saturating queue semaphores, and simulating split-brain conditions — within sandboxed Docker container environments.

These intentional failure scenarios are not vulnerabilities. Vulnerability reports should focus on unintended flaws, such as:
- Arbitrary code execution or path traversal on the host machine.
- Privilege escalation outside the container boundary.
- Unsanitized input or sensitive data leakage in the shared tools (`verify.py`, `static_check.py`).

---

## Reporting a Vulnerability

We take the security of IncidentLab seriously. If you discover a potential security vulnerability, please report it responsibly:

### Preferred Method
Submit a report privately using GitHub's **[Private Vulnerability Reporting](https://github.com/SreeNaresh1/ai-developer-toolkit/security/advisories/new)** feature.

### Alternative Method
If you are unable to use GitHub Private Vulnerability Reporting, you can email the maintainer directly at:
**`sreenaresh07@gmail.com`** with the subject line `[IncidentLab Security Report]`.

Please include:
1. A description of the vulnerability and its potential impact.
2. Steps or scripts to reproduce the issue.
3. Relevant environment details (OS, Docker version, Python version).

---

## What to Expect

- **Acknowledgment**: You will receive an initial response within 48 hours confirming receipt of your report.
- **Assessment**: We will assess the severity and work on a fix in a private branch.
- **Disclosure**: Once resolved, we will publish an advisory and release the patch, crediting you for the responsible disclosure (unless you prefer anonymity).
