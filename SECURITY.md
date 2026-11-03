# Security

Runspector holds a Garmin session on your computer and can put workouts on your watch, so a
flaw here can reach an account and a training day. Please report one privately.

## Reporting

Use **Report a vulnerability** on the repository's
[Security tab](https://github.com/edxleon/runspector/security/advisories/new). That opens a
private advisory only the maintainer sees. Please do not open a public issue for it.

Say what an attacker can do, the steps or a proof of concept, and the version
(`runspector --version`). This is a one-person project: reports are read as soon as possible,
and a fix goes out as a release with a note in its release text.

## What counts

- **The Garmin session:** anything that reads or sends the stored session tokens
  (`runspector login` writes them under `~/.runspector/`) somewhere other than Garmin.
- **The write path:** a workout put on, moved or taken off the watch without the athlete's yes
  to that proposal - through the web app, the MCP tools or the coach.
- **Text from Garmin taken as an instruction:** a workout name, calendar label or other free
  text that makes the coach act, approve, or skip a safety rule (pain, illness, energy).
- **The local web server** (`runspector serve`): a page on another site or another machine that
  reaches its API, despite the loopback default, the Host check and the token.
- **Files:** a path built from outside text that leaves `~/.runspector/`.
- **The release:** the workflows that build, attest and publish the package.

## Not in scope

Garmin's own services, the Claude apps and models, and a computer that is already compromised.

## Supported versions

The latest release on PyPI. Fixes are not backported.
