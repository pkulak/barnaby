#!/usr/bin/env python3
"""Turn idle Pi sessions into notes, then archive the raw JSONL.

Each --source is NAME:KIND:SESSIONS:NOTES. KIND is `chat` when SESSIONS holds
one agent's sessions, or `coding` when it holds a directory per project, like
~/.pi/agent/sessions. Notes go in NOTES/YYYY/MM/, listed in NOTES/INDEX.md, and
raw sessions are compressed into ARCHIVE/NAME/.

run     plan, one compactor `pi -p` per job (four at a time), then finish.
plan    Pick idle sessions, archive the ones not worth a note, and write
        transcripts for the rest under STATE/work.
finish  Move finished notes into place and archive their sessions.
index   Rebuild each source's INDEX.md (finish does this too).
"""

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# Chat agents never resume old sessions, so their memory can be fresher.
IDLE_DAYS = {"chat": 3, "coding": 14}
TRIVIAL_CHARS = 2_000
PART_CHARS = 400_000  # about 100K tokens
HOME = Path.home()
HERE = Path(__file__).resolve().parent
# Set from the command line in main().
SOURCES = []
ARCHIVE = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share") / "pi" / "session-archive"
STATE = Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local/state") / "session-compact"
WORK = STATE / "work"
MODEL = None
PROMPTS = {"chat": HERE / "chat.md"}
MAX_FAILURES = 2
JOB_TIMEOUT = 30 * 60
SELF_MARKER = '<skill name="session-compact"'
SKILL_BLOCK = re.compile(r'<skill name="([^"]+)"[^>]*>.*?</skill>', re.S)
# Per-message Matrix metadata that only adds noise to a transcript: XML tags in
# older sessions, then frontmatter, and IDs on room messages.
CHAT_NOISE = re.compile(r"^<(room-id|from-id|message-id|is-dm|room-size)>.*</\1>\n?", re.M)
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
ROOM_MESSAGE_ID = re.compile(r'(<room-message [^>]*?) id="[^"]*"')
LINE_CHARS = 4_000  # keep every line readable in one read call
RUN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CUTOFF = time.time() - IDLE_DAYS["coding"] * 86400


def parse_source(spec):
    try:
        name, kind, sessions, notes = spec.split(":")
    except ValueError:
        raise argparse.ArgumentTypeError("a source is NAME:KIND:SESSIONS:NOTES") from None
    if kind not in IDLE_DAYS:
        raise argparse.ArgumentTypeError(f"KIND must be one of {', '.join(IDLE_DAYS)}")
    return {"name": name, "kind": kind, "root": Path(sessions).expanduser(), "notes": Path(notes).expanduser()}


def parse_prompt(spec):
    kind, _, path = spec.partition("=")
    if kind not in IDLE_DAYS or not path:
        raise argparse.ArgumentTypeError("a prompt is KIND=FILE")
    return kind, Path(path).expanduser()


def sources():
    """Yield (source, session dirs) for each source whose sessions exist."""
    for src in SOURCES:
        root = src["root"]
        if not os.access(root, os.R_OK | os.X_OK):
            continue
        dirs = sorted(p for p in root.iterdir() if p.is_dir()) if src["kind"] == "coding" else [root]
        yield src, dirs


def idle_sessions():
    """Idle sessions, oldest first. The newest session in each dir is kept for --continue."""
    found = []
    for src, dirs in sources():
        cutoff = time.time() - IDLE_DAYS[src["kind"]] * 86400
        for d in dirs:
            files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
            found += [(f.stat().st_mtime, src, f) for f in files[:-1] if f.stat().st_mtime < cutoff]
    found.sort(key=lambda t: t[0])
    return [t[1:] for t in found]


def noted_ids():
    ids = set()
    for note in (n for src in SOURCES for n in src["notes"].rglob("*.md")):
        with open(note, errors="replace") as fh:
            m = re.search(r"^session: (\S+)$", fh.read(2048), re.M)
        if m:
            ids.add(m.group(1))
    return ids


def load_failures():
    path = STATE / "failures.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_failures(failures):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "failures.json").write_text(json.dumps(failures, indent=1))


def load(path):
    entries = []
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def text_of(content, limit=None):
    if isinstance(content, str):
        parts = [content]
    else:
        parts = [c.get("text", "") if c.get("type") == "text" else "[image]" for c in content or []]
    text = "\n".join(parts)
    if limit and len(text) > limit:
        text = text[:limit] + f"\n[... {len(text) - limit} chars truncated]"
    return text


def active_path(entries):
    by_id = {e["id"]: e for e in entries if e.get("id") and e.get("type") != "session"}
    if not by_id:
        return []
    node, path = list(by_id.values())[-1], []
    while node:
        path.append(node)
        node = by_id.get(node.get("parentId"))
    return path[::-1]


def first_user_text(path):
    for e in path:
        if e.get("type") == "message" and e["message"].get("role") == "user":
            return text_of(e["message"].get("content"))
    return ""


def blocks(path, tool_limit, arg_limit):
    out = []
    for e in path:
        kind = e.get("type")
        if kind == "branch_summary":
            out.append(f"[Summary of an abandoned branch]: {e.get('summary', '')}")
        elif kind == "compaction":
            out.append(f"[Pi compaction summary of the conversation so far]: {e.get('summary', '')}")
        elif kind == "custom_message" and e.get("customType") == "room-context":
            text = ROOM_MESSAGE_ID.sub(r"\1", text_of(e.get("content")))
            out.append(f"[Room message]: {text}")
        elif kind == "custom_message":
            out.append(f"[Extension message ({e.get('customType')})]: {text_of(e.get('content'), tool_limit)}")
        elif kind == "message":
            m = e["message"]
            role = m.get("role")
            if role == "user":
                text = SKILL_BLOCK.sub(r"[Loaded skill: \1]", text_of(m.get("content")))
                text = strip_chat_noise(text)
                out.append(f"[User {e.get('timestamp', '')[:16]}]: {text}")
            elif role == "assistant":
                for c in m.get("content") or []:
                    if c.get("type") == "text" and c.get("text", "").strip():
                        out.append(f"[Assistant]: {c['text']}")
                    elif c.get("type") == "toolCall":
                        args = json.dumps(c.get("arguments"), ensure_ascii=False)
                        if len(args) > arg_limit:
                            args = args[:arg_limit] + "…"
                        out.append(f"[Tool call {c.get('name')}]: {args}")
                if m.get("errorMessage"):
                    out.append(f"[Assistant error]: {m['errorMessage']}")
            elif role == "toolResult":
                err = " (error)" if m.get("isError") else ""
                out.append(f"[Tool result {m.get('toolName')}{err}]: {text_of(m.get('content'), tool_limit)}")
            elif role == "bashExecution":
                output = text_of(m.get("output", ""), tool_limit)
                out.append(f"[User shell command]: $ {m.get('command')}\n{output}")
            elif role == "custom":
                out.append(f"[Extension message ({m.get('customType')})]: {text_of(m.get('content'), tool_limit)}")
    return out


def strip_chat_noise(text):
    """Drop Matrix IDs and the time from a chat message's metadata, keeping the sender and room names."""
    text = CHAT_NOISE.sub("", text)
    header = FRONTMATTER.match(text)
    if not header:
        return text
    kept = []
    for line in header.group(1).splitlines():
        key, _, value = line.partition(": ")
        if key == "from":
            kept.append("from: " + (re.sub(r" @\S+$", "", value) if " " in value else value))
        elif key == "room":
            kept.append("room: " + re.sub(r"(^| )!\S+( \(\d+ members\))?$", "", value))
    return "\n".join(kept + [text[header.end() :]])


def serialize(path):
    """Return transcript parts, shrinking tool output and then splitting when too large."""
    text = blocks(path, 2000, 1000)
    if sum(len(b) + 2 for b in text) > PART_CHARS:
        text = blocks(path, 300, 300)
    long_line = re.compile(f"[^\n]{{{LINE_CHARS}}}(?=[^\n])")
    text = [long_line.sub(lambda m: m.group(0) + "\n", b) for b in text]
    parts, cur = [], ""
    for b in text:
        while len(b) > PART_CHARS:
            parts.append(cur + b[: PART_CHARS - len(cur)])
            b, cur = b[PART_CHARS - len(cur) :], ""
        if len(cur) + len(b) + 2 > PART_CHARS:
            parts.append(cur)
            cur = ""
        cur += b + "\n\n"
    if cur:
        parts.append(cur)
    return parts


def tilde(p):
    return "~/" + str(Path(p).relative_to(HOME)) if Path(p).is_relative_to(HOME) else str(p)


def archive_dest(path, src):
    return ARCHIVE / src["name"] / f"{path.relative_to(src['root'])}.zst"


def archive(path, src):
    dest = archive_dest(path, src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["zstd", "-q", "-f", "-o", str(dest), str(path)], check=True)
    st = path.stat()
    os.utime(dest, (st.st_atime, st.st_mtime))
    path.unlink()


def sweep_artifacts():
    """Archive old subagent artifacts once no live session mentions their run ID."""
    count = 0
    for src, dirs in sources():
        for d in dirs:
            art = d / "subagent-artifacts"
            if not art.is_dir():
                continue
            groups = {}
            for p in [*art.iterdir(), *(art / "outputs").glob("*")]:
                m = RUN_ID.match(p.name)
                if m:
                    files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
                    groups.setdefault(m.group(0), []).extend(files)
            old = {rid: fs for rid, fs in groups.items() if all(f.stat().st_mtime < CUTOFF for f in fs)}
            if not old:
                continue
            live = [f.read_bytes() for f in d.glob("*.jsonl")]
            for rid, files in old.items():
                if any(rid.encode() in data for data in live):
                    continue
                for f in files:
                    archive(f, src)
                    count += 1
            for p in sorted((art / "outputs").rglob("*"), reverse=True) if (art / "outputs").is_dir() else []:
                if p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
    return count


def local_time(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()


def plan(limit, dry_run):
    if not dry_run:
        shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir(parents=True)
    missing = {src["kind"] for src in SOURCES} - PROMPTS.keys()
    if missing:
        sys.exit(f"No prompt for {', '.join(sorted(missing))} sessions; pass --prompt KIND=FILE.")
    noted = noted_ids()
    failures = load_failures()
    skipped = {"already-noted": 0, "fork": 0, "self": 0, "trivial": 0}
    gave_up = []
    jobs = []
    idle = idle_sessions()
    for src, f in idle:
        if len(jobs) >= limit:
            break
        entries = load(f)
        header = next((e for e in entries if e.get("type") == "session"), {})
        path = active_path(entries)
        sid = header.get("id") or f.stem.split("_")[-1]
        if failures.get(sid, 0) >= MAX_FAILURES:
            gave_up.append(str(f))
            continue
        reason = None
        if sid in noted:
            reason = "already-noted"
        elif header.get("parentSession"):
            reason = "fork"
        elif first_user_text(path).startswith(SELF_MARKER):
            reason = "self"
        else:
            parts = serialize(path)
            if sum(map(len, parts)) < TRIVIAL_CHARS:
                reason = "trivial"
        if reason:
            skipped[reason] += 1
            if not dry_run:
                archive(f, src)
            continue

        started = local_time(header.get("timestamp") or path[0]["timestamp"])
        last = local_time(max(e["timestamp"] for e in entries if isinstance(e.get("timestamp"), str)))
        name = next((e.get("name") for e in reversed(entries) if e.get("type") == "session_info"), None)
        job = {
            "id": sid,
            "name": src["name"],
            "kind": src["kind"],
            "source": str(f),
            "root": str(src["root"]),
            "notes": str(src["notes"]),
            "date": started.strftime("%Y-%m-%d"),
            "dir": str(WORK / sid),
            "parts": len(parts),
            "chars": sum(map(len, parts)),
        }
        jobs.append(job)
        if dry_run:
            continue
        d = Path(job["dir"])
        d.mkdir()
        names = []
        for i, part in enumerate(parts, 1):
            (d / f"part-{i}.txt").write_text(part)
            names.append(str(d / f"part-{i}.txt"))
        frontmatter = "\n".join(
            [
                "---",
                f"session: {sid}",
                f"machine: {src['name']}",
                f"cwd: {header.get('cwd', '')}",
                f"started: {started.isoformat(timespec='minutes')}",
                f"last_active: {last.isoformat(timespec='minutes')}",
                f"archive: {tilde(archive_dest(f, src))}",
                "tags: []",
                "---",
            ]
        )
        (d / "job.md").write_text(
            f"Frontmatter (copy verbatim, replacing only `tags: []`):\n\n{frontmatter}\n\n"
            + (f"Session name set in Pi: {name}\n\n" if name else "")
            + "Transcript parts, read all of them in order:\n"
            + "".join(f"- {n}\n" for n in names)
            + f"\nWrite the note to: {d / 'note.md'}\n"
        )

    swept = 0 if dry_run else sweep_artifacts()
    if not dry_run:
        (WORK / "manifest.json").write_text(json.dumps(jobs, indent=1))
    remaining = len(idle) - len(jobs) - sum(skipped.values()) - len(gave_up)
    return {
        "dry_run": dry_run,
        "jobs": [{"dir": j["dir"], "parts": j["parts"], "chars": j["chars"]} for j in jobs],
        "archived_without_note": skipped,
        "artifacts_archived": swept,
        f"skipped_after_{MAX_FAILURES}_failures": gave_up,
        "idle_sessions_left_after_this_run": remaining,
    }


def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"['’]", "", title.lower())).strip("-")
    return slug[:60].rsplit("-", 1)[0] if len(slug) > 60 else slug or "session"


def compact(job):
    """Run one bare compactor. Returns (whether a failure counts against the session, its last output line)."""
    cmd = ["pi", "-p", "--no-session", "--no-extensions", "--no-skills", "--no-context-files"]
    cmd += ["--no-prompt-templates", "--no-themes", "--tools", "read,write", "--model", MODEL]
    prompt = (HERE / "header.md").read_text().rstrip() + "\n\n" + PROMPTS[job["kind"]].read_text()
    cmd += ["--system-prompt", prompt]
    cmd += [f"Compact the session prepared in {job['dir']}. Start by reading {job['dir']}/job.md."]
    try:
        r = subprocess.run(
            cmd, cwd=job["dir"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=JOB_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return True, f"timed out after {JOB_TIMEOUT // 60} minutes"
    last = (r.stdout.strip().splitlines() or [""])[-1]
    if r.returncode:
        # Usually the model or network was unreachable, so the next run should simply retry.
        return False, f"pi exited {r.returncode}: {(r.stderr.strip() or last)[-300:]}"
    return True, last


def finish(outcomes=None):
    manifest = WORK / "manifest.json"
    if not manifest.exists():
        sys.exit(f"No {manifest}; run plan first.")
    failures = load_failures()
    done, failed = [], []
    for job in json.loads(manifest.read_text()):
        note = Path(job["dir"]) / "note.md"
        text = note.read_text() if note.exists() else ""
        title = re.search(r"^# (.+)$", text, re.M)
        if not re.search(rf"^session: {re.escape(job['id'])}$", text[:2048], re.M) or not title:
            counts, reason = (outcomes or {}).get(job["dir"], (True, ""))
            if not reason.startswith(("FAIL", "timed out", "pi exited")):
                reason = "no valid note written"
            if counts:
                failures[job["id"]] = failures.get(job["id"], 0) + 1
                if failures[job["id"]] >= MAX_FAILURES:
                    reason += f"; failed {failures[job['id']]} times, so later runs skip it"
            failed.append({"session": job["source"], "reason": reason})
            continue
        failures.pop(job["id"], None)
        date = job["date"]
        dest_dir = Path(job["notes"]) / date[:4] / date[5:7]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{date}-{slugify(title.group(1))}.md"
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}-{job['id'][:8]}.md")
        shutil.move(note, dest)
        src = Path(job["source"])
        if src.exists():
            archive(src, {"name": job["name"], "root": Path(job["root"])})
        shutil.rmtree(job["dir"])
        done.append(str(dest))
    save_failures(failures)
    swept = sweep_artifacts()
    write_indexes()
    if not failed:
        shutil.rmtree(WORK, ignore_errors=True)
    return {"notes": done, "failed": failed, "artifacts_archived": swept}


def run(limit):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / "lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("Another session-compact run is in progress.")
        report = plan(limit, False)
        jobs = json.loads((WORK / "manifest.json").read_text())
        with ThreadPoolExecutor(4) as pool:
            outcomes = dict(zip((j["dir"] for j in jobs), pool.map(compact, jobs)))
        result = finish(outcomes)
    del report["dry_run"], report["jobs"]
    result["artifacts_archived"] += report.pop("artifacts_archived")
    return result | report


def field(text, key):
    return (re.search(rf"^{key}: *(.*)$", text, re.M) or ["", ""])[1].strip()


def write_indexes():
    """Write each source's INDEX.md: one line per note, newest first, for agents to load as memory."""
    for src in SOURCES:
        notes = src["notes"]
        if not notes.is_dir():
            continue
        lines = []
        for note in notes.glob("*/*/*.md"):
            text = note.read_text(errors="replace")
            title = (re.search(r"^# (.+)$", text, re.M) or ["", note.stem])[1]
            start, end = field(text, "started")[:10], field(text, "last_active")[:10]
            span = start if end in ("", start) else f"{start} → {end[5:] if end[:4] == start[:4] else end}"
            tags = field(text, "tags").strip("[]")
            tags = f" [{tags}]" if tags else ""
            # No path: it repeats the date and title and costs agents tokens on every start.
            lines.append((start, f"- {span}: {title}{tags}"))
        header = (
            f"# {src['name']} session notes\n\n"
            f"Newest first. Each note is at {tilde(notes)}/YYYY/MM/<start date>-*.md.\n\n"
        )
        (notes / "INDEX.md").write_text(header + "\n".join(line for _, line in sorted(lines, reverse=True)) + "\n")


def main():
    global ARCHIVE, STATE, WORK, MODEL
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=parse_source, action="append", required=True, help="NAME:KIND:SESSIONS:NOTES")
    ap.add_argument("--prompt", type=parse_prompt, action="append", default=[], help="KIND=FILE, the note format")
    ap.add_argument("--model", help="pi model for the compactor, such as provider/id:thinking")
    ap.add_argument("--archive", type=Path, default=ARCHIVE, help="where raw sessions are compressed")
    ap.add_argument("--state", type=Path, default=STATE, help="work files, the lock, and failure counts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--limit", type=int, default=5)
    p = sub.add_parser("plan")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--dry-run", action="store_true", help="report only; archive and write nothing")
    sub.add_parser("finish")
    sub.add_parser("index")
    args = ap.parse_args()
    SOURCES[:] = args.source
    PROMPTS.update(args.prompt)
    ARCHIVE, STATE, MODEL = args.archive.expanduser(), args.state.expanduser(), args.model
    WORK = STATE / "work"
    if args.cmd == "run" and not MODEL:
        ap.error("run needs --model")
    if args.cmd == "index":
        write_indexes()
        return
    report = {"run": lambda: run(args.limit), "plan": lambda: plan(args.limit, args.dry_run), "finish": finish}[
        args.cmd
    ]()
    print(json.dumps(report, indent=1))
    if report.get("failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
