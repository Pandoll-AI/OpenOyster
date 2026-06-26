# Security Policy

OpenOyster processes potentially sensitive documents and can be extended with autonomous actions. Security defects may affect confidentiality, integrity, availability, or downstream decisions.

## Supported version

Security fixes target the latest tagged release and the current `main` branch. Pre-`1.0` releases may contain breaking fixes.

## Reporting a vulnerability

Use the hosting platform's private security-advisory mechanism once the repository is published. If that is unavailable, contact the repository owner through a private channel. Do not open a public issue containing credentials, private data, exploit steps, or unpatched vulnerability details.

Include:

- affected version/commit;
- attack preconditions;
- minimal reproduction;
- likely impact;
- suggested mitigation if known.

## Default posture

The default runtime:

- reads local files and explicitly supplied public URLs;
- writes only to its database and workspace;
- exposes a read-only escaped dashboard;
- disables mutation endpoints until an API key is configured;
- limits execution to registered internal tools;
- does not send email, modify records, deploy code, trade, or perform other external writes;
- does not automatically change the mission charter.

## Operator responsibilities

- terminate TLS and apply identity/network access control;
- store keys and DB credentials in a secret manager;
- protect database and backups;
- determine whether remote model processing is permitted for source data;
- restrict outbound network access;
- review policy promotions and premise-change proposals;
- maintain retention/deletion and incident-response procedures.

See `docs/THREAT_MODEL.md` for detailed threats and residual risks.
