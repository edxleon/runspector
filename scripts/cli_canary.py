"""Does the newest Claude Code still run a coach card the way this app needs?

What a CLI release can change without a line here moving:

1. `claude -p` turning into bare mode. Claude Code's docs say `--bare` "will
   become the default for `-p` in a future release", and bare mode never reads
   the Claude sign-in: every athlete's every card would fail at once. A plain
   `-p` and an explicit `--bare` are told apart by the init event they print
   before the missing sign-in stops them (2.1.317: 23 tools and an auto-memory
   path against 3 tools and none). The day they look alike, bare is the default.
2. The card run itself - the app's own command (`agent.command`), every option
   in it: it must still start, no option or option value may be refused, and
   the init event must show the isolation held (no memory path, no skill, no
   built-in tool, the plain permission mode).
3. Whether the isolation still keeps the athlete's own Claude Code out of what
   the model is SENT. A throw-away home holds what an athlete's can: CLAUDE.md
   files, rules, an agent, an output style, a language, ultracode, a hook, an
   extra request body, a model mapping, an effort cap - each leaves a marker
   in the request when read. The
   card runs against a local endpoint that only records (a dummy key, no model
   call): once without isolation (it must show every marker, else the plant is
   stale), once as with a plan sign-in (`--setting-sources ""`: none may
   arrive), once as with a key or cloud sign-in (the `agent.isolation()`
   overrides alone: none but the mapping and the cap, which only unread
   settings keep out). An init
   event cannot show this: an unknown - renamed - settings key is ignored
   without a word, and only the request shows it.

In CI the newest CLI runs in a container that cannot reach the runner (no
token, no cache): this script writes the commands (`--write DIR`), the
container runs them, this script judges what came back (`--judge DIR`). With
no argument it does all three on this machine, with the installed CLI and
node, and with no sign-in in the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from runspector.onboarding import strips_billing
from runspector.web import agent

BASE = ["-p", "--output-format", "stream-json", "--verbose"]
REFUSED = agent._REFUSED
#: "Warning: Unknown --effort value 'x' - ignoring it": the run goes on, on
#: the athlete's own setting.
UNKNOWN_VALUE = re.compile(r"Unknown (--[\w-]+) value")
#: More than any of the runs prints (an init event is a few kB).
LIMIT = 4 * 1024 * 1024
PORT = 47913
RUNS = ("control", "plan", "other")
OUTPUTS = ("version.txt", "plain.jsonl", "bare.jsonl", "card.jsonl", "card.err",
           *(f"planted-{r}.jsonl" for r in RUNS))

#: An athlete's own Claude Code, planted under `planted/` (their home): `cfg`
#: is its config folder (not called .claude: CLAUDE_CONFIG_DIR may name any),
#: `proj/work` and `agents/work` are where the card runs, below a project's
#: instruction files.
SETTINGS = {"agent": "planted", "outputStyle": "planted", "language": "german", "ultracode": True,
            "permissions": {"defaultMode": "bypassPermissions"}, "maxEffortLevel": "low",
            "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo MARKER_HOOK"}]}]},
            "env": {"CLAUDE_CODE_EXTRA_BODY": json.dumps({"canary": "MARKER_EXTRA_BODY"}),
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5-5-canary"}}
PLANT = {
    "cfg/settings.json": json.dumps(SETTINGS),
    "cfg/CLAUDE.md": "MARKER_USER_CLAUDE_MD\n",
    "cfg/rules/user.md": "MARKER_USER_RULES\n",
    "cfg/agents/planted.md": "---\nname: planted\ndescription: planted\n---\nMARKER_AGENT\n",
    "cfg/output-styles/planted.md": "---\nname: planted\ndescription: planted\n---\nMARKER_OUTPUT_STYLE\n",
    "proj/CLAUDE.md": "MARKER_PROJECT_CLAUDE_MD\n",
    "proj/CLAUDE.local.md": "MARKER_CLAUDE_LOCAL_MD\n",
    # AGENTS.md is read only where no CLAUDE.md or CLAUDE.local.md is above: a
    # project of its own, a second folder each run goes through.
    "agents/work/AGENTS.md": "MARKER_AGENTS_MD\n",
    "proj/.claude/rules/project.md": "MARKER_PROJECT_RULES\n",
    "proj/work/.keep": "",
}
#: Settings that leave a sentence or a request field, not a marker (2.1.317).
SIGNS = {"MARKER_LANGUAGE": re.compile(r"respond in german"),
         "MARKER_ULTRACODE": re.compile(r"Ultracode is on"),
         "MARKER_MODEL_MAPPING": re.compile(r'"model"\s*:\s*"[^"]*-canary"'),
         "MARKER_MAX_EFFORT": re.compile(r'"effort"\s*:\s*"low"')}
#: What only leaving the settings unread keeps out - no override can: the
#: lowest effort cap wins, a model mapping is the provider's. Expected in the
#: run whose sign-in keeps the settings read (docs/architecture.md).
ONLY_UNREAD = frozenset({"MARKER_MODEL_MAPPING", "MARKER_MAX_EFFORT"} if agent.COACH_EFFORT != "low"
                        else {"MARKER_MODEL_MAPPING"})
MARK = re.compile(r"MARKER_[A-Z_]+")
PLANTED = (frozenset(MARK.findall(json.dumps(PLANT))) | frozenset(SIGNS)) - (
    set() if agent.COACH_EFFORT != "low" else {"MARKER_MAX_EFFORT"})

CAPTURE = """import fs from "node:fs";
import http from "node:http";
const [port, dir] = process.argv.slice(2);
let n = 0;
http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    const run = (req.url.split("/")[1] || "").replace(/[^a-z]/g, "") || "x";
    fs.mkdirSync(`${dir}/${run}`, { recursive: true });
    fs.writeFileSync(`${dir}/${run}/${String(n++).padStart(3, "0")}.json`, Buffer.concat(chunks));
    res.writeHead(400, { "content-type": "application/json" });
    res.end(JSON.stringify({ type: "error", error: { type: "invalid_request_error", message: "canary" } }));
  });
}).listen(Number(port), "127.0.0.1");
"""


def read(path: Path) -> str | None:
    """A file the container wrote, as text - or None when it is missing or no
    regular file. The judge runs on the runner: a link the container left would
    point it at a runner file and print that into the public log."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return None
        return f.read(LIMIT).decode("utf-8", errors="replace")


def unlinked(out: Path) -> list[str]:
    """The outputs that are not regular files (a link, a FIFO ...), and the
    recording folders that are links."""
    files = [n for n in OUTPUTS if (out / n).is_symlink() or ((out / n).exists() and not (out / n).is_file())]
    return files + [f"cap/{r}" for r in ("", *RUNS) if (out / "cap" / r).is_symlink()]


def safe(text: str) -> str:
    """Container text on one line: a line break in it would start a line the
    runner reads as a workflow command (`::add-mask::`, `::warning::` ...)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _card(out: Path, inside: str, claude: str, *, unread: bool, config_dir: str | None = None) -> list[str]:
    """The app's card command - leaving the settings unread (a plan sign-in
    that needs none of them) or not - with the paths where `out` is mounted.
    `config_dir`: the CLAUDE_CONFIG_DIR the athlete has, if any."""
    kept = os.environ.pop("CLAUDE_CONFIG_DIR", None)
    try:
        if config_dir:
            os.environ["CLAUDE_CONFIG_DIR"] = config_dir
        cmd = agent.command(out, None, unread_settings=unread)   # writes out/mcp.json, as a card run does
    finally:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        if kept is not None:
            os.environ["CLAUDE_CONFIG_DIR"] = kept
    return [claude, *(a.replace(str(out), inside) for a in cmd[1:])]


def _without(cmd: list[str], *flags: str) -> list[str]:
    """`cmd` without these options and their values."""
    out, skip = [], False
    for a in cmd:
        if skip:
            skip = False
        elif a in flags:
            skip = True
        else:
            out.append(a)
    return out


def write(out: Path, *, inside: str = "/canary", claude: str = "claude") -> None:
    """`run.sh` in `out`, with the planted home beside it: every run, with its
    output in files there. `inside` is where `out` is mounted when it runs."""
    out.mkdir(parents=True, exist_ok=True)
    for rel, text in PLANT.items():
        (out / "planted" / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / "planted" / rel).write_text(text, encoding="utf-8")
    (out / "capture.mjs").write_text(CAPTURE, encoding="utf-8")
    card = _card(out, inside, claude, unread=True)
    planted_cfg = f"{inside}/planted/cfg"
    plan = _card(out, inside, claude, unread=True, config_dir=planted_cfg)
    runs = {"control": _without(plan, "--settings", "--setting-sources"), "plan": plan,
            "other": _card(out, inside, claude, unread=False, config_dir=planted_cfg)}
    lines = [
        "set -u",
        f'export HOME="{inside}/home" CLAUDE_CONFIG_DIR="{inside}/home/cfg"',
        'mkdir -p "$CLAUDE_CONFIG_DIR" && cd "$HOME"',
        f"{claude} --version > {inside}/version.txt 2>&1",
        f"echo 'say ok' | timeout 120 {shlex.join([claude, *BASE])} "
        f"> {inside}/plain.jsonl 2> {inside}/plain.err",
        f"echo 'say ok' | timeout 120 {shlex.join([claude, *BASE, '--bare'])} "
        f"> {inside}/bare.jsonl 2> {inside}/bare.err",
        f"echo 'say ok' | timeout 120 {shlex.join(card)} > {inside}/card.jsonl 2> {inside}/card.err",
        f"node {inside}/capture.mjs {PORT} {inside}/cap & CAPTURE=$!",
        "sleep 1",
        f'export HOME="{inside}/planted" CLAUDE_CONFIG_DIR="{planted_cfg}"',
    ]
    # The dummy key is not shaped like a real one: secret scanners flag that.
    for name, cmd in runs.items():
        for project, to in (("proj", ">"), ("agents", ">>")):
            lines.append(f'cd "{inside}/planted/{project}/work" && echo \'say ok\' | '
                         f"ANTHROPIC_API_KEY=canary-dummy-not-a-key "
                         f"ANTHROPIC_BASE_URL=http://127.0.0.1:{PORT}/{name} timeout 60 {shlex.join(cmd)} "
                         f"{to} {inside}/planted-{name}.jsonl 2{to} {inside}/planted-{name}.err")
    lines += ['kill "$CAPTURE"', "exit 0"]
    (out / "run.sh").write_text("\n".join(lines) + "\n", encoding="utf-8")


def init_event(path: Path) -> dict | None:
    text = read(path)
    if text is None:
        return None
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("subtype") == "init":
            return ev
    return None


def shape(ev: dict | None) -> tuple[int, bool]:
    """(tools offered, an auto-memory path): what tells plain -p from bare."""
    if not ev:
        return (0, False)
    return (len(ev.get("tools") or []), bool(ev.get("memory_paths")))


def leaks(ev: dict) -> list[str]:
    """What a card run's init event shows of the athlete's own Claude Code."""
    found = []
    if ev.get("memory_paths"):
        found.append("an auto-memory path")
    if ev.get("permissionMode") not in ("default", "manual"):
        found.append(f"permission mode {ev.get('permissionMode')!r}")
    if ev.get("skills"):
        found.append(f"{len(ev['skills'])} skills")
    if builtin := [t for t in ev.get("tools") or [] if not str(t).startswith("mcp__")]:
        found.append(f"built-in tools ({', '.join(map(str, builtin[:3]))})")
    if any("agents-md" in str((p or {}).get("name", "")) for p in ev.get("plugins") or []
           if isinstance(p, dict)):
        found.append("the AGENTS.md plugin")
    return found


def problems(plain: dict | None, bare: dict | None, card: dict | None, card_err: str) -> list[str]:
    found = []
    if plain is None or bare is None:
        found.append("no init event from `claude -p` - the stream format changed; "
                     "read the CLI's release notes")
    elif shape(plain) == shape(bare) or shape(plain)[0] <= shape(bare)[0]:
        found.append(f"`claude -p` now starts like `--bare` (tools, memory: {shape(plain)} vs "
                     f"{shape(bare)}): bare mode does not read the Claude sign-in, every card would fail")
    for flag in sorted(set(REFUSED.findall(card_err))):
        how = ("cards run without it (agent._OPTIONAL)" if flag in agent._OPTIONAL
               else "every card fails on it")
        found.append(f"the CLI no longer knows {flag}: {how}")
    for flag in sorted(set(UNKNOWN_VALUE.findall(card_err))):
        found.append(f"the CLI no longer takes the value runspector passes to {flag}: cards run on the "
                     "athlete's own setting")
    if card is None:
        found.append("the card run printed no init event - it did not start: "
                     + (card_err.strip()[-200:] or "no error text"))
    elif held := leaks(card):
        found.append("the card run's isolation did not hold (" + ", ".join(held) + ")")
    return found


def recorded(out: Path, run: str) -> set[str] | None:
    """The planted markers in what the `run` sent - None when it sent nothing.
    Only marker NAMES come out of the files, never their text."""
    folder = out / "cap" / run
    if (out / "cap").is_symlink() or folder.is_symlink() or not folder.is_dir():
        return None
    texts = [read(f) or "" for f in sorted(folder.iterdir())[:50]]
    if not texts:
        return None
    blob = "\n".join(texts)
    return ({m for m in MARK.findall(blob) if m in PLANTED}
            | {m for m, sign in SIGNS.items() if m in PLANTED and sign.search(blob)})


def planted_problems(sent: dict[str, set[str] | None],
                     inits: dict[str, dict | None]) -> tuple[list[str], list[str]]:
    """(reds, notes) from the three planted runs."""
    reds, notes = [], []
    control = sent.get("control")
    if not control:
        what = ("sent nothing to the recording endpoint" if control is None
                else "was sent none of the planted markers")
        reds.append(f"the run without isolation {what} - the planted check did not run")
    elif stale := sorted(PLANTED - control):
        notes.append("the plant no longer reaches a run without isolation: " + ", ".join(stale)
                     + " - the check of these is empty; update PLANT in scripts/cli_canary.py")
    why = {"plan": "with a plan sign-in the card was sent the athlete's own {}: `--setting-sources \"\"` "
                   "no longer keeps their files out",
           "other": "with a key or cloud sign-in the card was sent the athlete's own {}: a key of "
                    "agent.isolation() was renamed or dropped"}
    for run, text in why.items():
        hits = sent.get(run)
        if hits and run == "other":
            hits = hits - ONLY_UNREAD
        if hits is None:
            reds.append(f"the {run} card run sent nothing to the recording endpoint - its isolation was "
                        "not checked")
        elif hits:
            reds.append(text.format(", ".join(sorted(hits))))
        if (ev := inits.get(run)) and (held := leaks(ev)):
            reds.append(f"the {run} card run's init event shows the athlete's " + ", ".join(held))
    return reds, notes


def judge(out: Path) -> int:
    version = (read(out / "version.txt") or "?").strip()[:200]
    plain, bare, card = (init_event(out / f"{n}.jsonl") for n in ("plain", "bare", "card"))
    err = read(out / "card.err") or ""
    found = problems(plain, bare, card, err)
    sent = {r: recorded(out, r) for r in RUNS}
    reds, notes = planted_problems(sent, {r: init_event(out / f"planted-{r}.jsonl") for r in RUNS})
    found += reds
    found += [f"{n} is no regular file - not followed, not read" for n in unlinked(out)]
    print(safe(f"Claude Code {version}: plain -p {shape(plain)}, --bare {shape(bare)} (tools, auto memory); "
               f"card run: {'started' if card else 'did not start'}"))
    print(safe("planted markers sent: " + "; ".join(
        f"{r} {len(s) if s is not None else '-'} of {len(PLANTED)}" for r, s in sent.items())))
    for line in notes:
        print(f"::warning::{safe(line)}")
    for line in found:
        print(f"::error::{safe(line)}")
    if not found:
        print("a card run starts as this app needs it, and is sent nothing of the athlete's own")
    return 1 if found else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", type=Path, help="write the runs into this folder (mounted at /canary)")
    ap.add_argument("--judge", type=Path, help="judge what the runs in this folder printed")
    args = ap.parse_args(argv)
    if args.write:
        write(args.write)
        return 0
    if args.judge:
        return judge(args.judge)
    with tempfile.TemporaryDirectory(prefix="runspector-canary-") as tmp:
        out = Path(tmp)
        # This machine's runspector data stays out of the card's MCP server, and
        # its sign-in out of the runs: they are meant to make no model call.
        os.environ["RUNSPECTOR_HOME"] = str(out / "runspector-home")
        write(out, inside=str(out), claude=shutil.which("claude") or "claude")
        env = {k: v for k, v in os.environ.items()
               if not strips_billing(k) and k != "CLAUDE_CODE_OAUTH_TOKEN"}
        subprocess.run(["sh", str(out / "run.sh")], check=False, timeout=900, env=env)
        return judge(out)


if __name__ == "__main__":
    sys.exit(main())
