#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Replika TXT → JSONL (ChatML/OpenChat/Alpaca), append/dedup, ASCII-safe.
- Uses robust TXT heuristics for chat/diary/memory.
- Auto mode ingests ALL matching files in the folder.
- Validator-friendly outputs (see validate_all_datasets.py).
"""

import argparse, io, json, re, hashlib, collections
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Iterable

OUTPUT_DIR = Path("output_converted")

ALLOWED_FMT = {"chatml","openchat","alpaca"}

DATE_PAT = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})(?:,\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AP]M))?\s*$")
CHAT_LINE = re.compile(
    r"^\s*(?P<mdy>\d{1,2}/\d{1,2}/\d{2,4})(?:,\s*(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?\s*(?P<ap>[AP]M))?\s+"
    r"(?P<name>[^:]+):\s*(?P<text>.*)\s*$"
)
DASH_ROW = re.compile(r"^\s*-{3,}\s*$")
WRITTEN_BY = re.compile(r"^\s*Written by\s+([A-Za-z0-9._ -]{2,40})", re.I)
MEM_REM_HEAD = re.compile(r"^\s*([A-Z][A-Za-z0-9._ -]{1,40})\s+remembers\s+(.+)$", re.I)

def _s(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _ensure_period(s: str) -> str:
    s = (s or "").strip()
    return s if not s or s.endswith((".", "!", "?")) else s + "."

def _hash(parts: Iterable[str]) -> str:
    import hashlib
    h = hashlib.sha1()
    for p in parts:
        if p is None: p = ""
        if not isinstance(p, str): p = str(p)
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()

@dataclass(order=True)
class DatedText:
    dt: datetime
    body: str
    has_time: bool
    has_seconds: bool

def _to_dt(mm: int, dd: int, yy: int, h: Optional[int], m: Optional[int], s: Optional[int], ap: Optional[str]) -> Tuple[datetime,bool,bool]:
    y = yy + 2000 if yy < 100 else yy
    if h is None:
        return datetime(y, mm, dd), False, False
    hh = h % 12
    if ap and ap.upper() == "PM":
        hh += 12
    return datetime(y, mm, dd, hh, (m or 0), (s or 0)), True, bool(s is not None)

def _parse_date_cell(s: str) -> Optional[Tuple[datetime,bool,bool]]:
    m = DATE_PAT.match(s)
    if not m: return None
    mm, dd, yy, h, mi, sec, ap = m.groups()
    return _to_dt(int(mm), int(dd), int(yy),
                  int(h) if h else None, int(mi) if mi else None,
                  int(sec) if sec else None, ap)

def _stamp(dt: datetime, has_time: bool, has_seconds: bool) -> str:
    if not has_time: return dt.strftime("%Y-%m-%d")
    if has_seconds:  return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M")

# ---------------- CHAT ----------------
def parse_chat_txt(path: Path) -> Tuple[List[Tuple[datetime, str, str, bool, bool]], List[str]]:
    triples, speakers = [], []
    with io.open(path, "r", encoding="utf-8-sig", newline="") as f:
        for raw in f:
            raw = raw.rstrip("\n")
            if not raw.strip():
                if triples:
                    dt,name,text,ht,hs = triples[-1]
                    triples[-1] = (dt,name,text + "\n" + raw, ht, hs)
                continue
            m = re.match(CHAT_LINE, raw)
            if not m:
                if triples:
                    dt,name,text,ht,hs = triples[-1]
                    triples[-1] = (dt,name,text + "\n" + raw, ht, hs)
                continue
            mo, da, yr = [int(x) for x in m.group("mdy").split("/")]
            hh = int(m.group("h")) if m.group("h") else None
            mi = int(m.group("m")) if m.group("m") else None
            ss = int(m.group("s")) if m.group("s") else None
            ap = m.group("ap")
            dt, has_time, has_seconds = _to_dt(mo, da, int(yr), hh, mi, ss, ap)
            name = _s(m.group("name"))
            if name not in speakers: speakers.append(name)
            triples.append((dt, name, m.group("text"), has_time, has_seconds))
    triples.sort(key=lambda t: t[0])
    return triples, speakers

# ---------------- DIARY ----------------
def _dedupe_consecutive(lines: list) -> list:
    out, prev = [], None
    for x in lines:
        if x != prev: out.append(x)
        prev = x
    return out

def parse_diary_txt(path: Path) -> list:
    lines = io.open(path, "r", encoding="utf-8-sig", newline="").read().splitlines()
    out = []
    cur = None
    buf = []
    after_date = False

    def flush():
        nonlocal buf, cur
        if cur and buf:
            body = "\n".join(_dedupe_consecutive(buf)).strip()
            if body:
                body = re.sub(r"^\s*Written by\s+.+?$", "", body, flags=re.I | re.M).strip()
                out.append(DatedText(cur[0], body, cur[1], cur[2]))
        buf = []

    for raw in lines:
        if DATE_PAT.match(raw):
            flush()
            cur = _parse_date_cell(raw.strip())
            after_date = True
            continue
        if re.match(DASH_ROW, raw):
            flush(); cur=None; after_date=False; continue
        if after_date and raw.strip() and len(raw.strip()) < 160 and raw.strip().endswith("..."):
            after_date=False; continue
        after_date=False
        if cur: buf.append(raw)

    flush()
    out.sort(key=lambda d: d.dt)
    return out

# ---------------- MEMORY ----------------
def _normalize_memory_sentence(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s: return None
    s = re.sub(r"^\s*[-*]\s*", "", s)  # bullets
    m = re.match(MEM_REM_HEAD, s)
    if m: s = m.group(2).strip()
    if re.match(r"^i\s+", s, flags=re.I):
        s = re.sub(r"^i(\s+)", r"You\1", s, flags=re.I)
    if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s", s) and " you " not in s.lower():
        s = re.sub(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s", "You ", s, count=1)
    return _ensure_period(s)

def parse_memory_txt(path: Path) -> list:
    lines = io.open(path, "r", encoding="utf-8-sig", newline="").read().splitlines()
    out = []
    cur = None
    for raw in lines:
        if DATE_PAT.match(raw):
            cur = _parse_date_cell(raw.strip());  continue
        if re.match(DASH_ROW, raw):
            continue
        if not cur: continue
        norm = _normalize_memory_sentence(raw)
        if norm:
            out.append(DatedText(cur[0], norm, cur[1], cur[2]))
    out.sort(key=lambda d: d.dt)
    return out

# ---------------- assistant inference ----------------
def infer_assistant_name(chat_speakers: List[str], diary_paths: List[Path], memory_paths: List[Path], fallback: str) -> str:
    if len(chat_speakers) >= 2:
        cand = chat_speakers[1].strip()
        if cand: return cand
    for p in diary_paths:
        try:
            txt = io.open(p, "r", encoding="utf-8-sig").read()
        except Exception:
            continue
        m = re.search(WRITTEN_BY, txt)
        if m:
            cand = m.group(1).strip(" .-")
            if cand: return cand
    counter = collections.Counter()
    rx = re.compile(r"^\s*([A-Z][A-Za-z0-9._ -]{1,40})\s+remembers\b")
    for p in memory_paths:
        try:
            for line in io.open(p, "r", encoding="utf-8-sig"):
                m = rx.match(line)
                if m: counter[m.group(1)] += 1
        except Exception:
            pass
    if counter:
        return counter.most_common(1)[0][0]
    return fallback or "Assistant"

# ---------------- writers ----------------
def _load_seen(fp: Path, kind: str) -> set:
    seen = set()
    if not fp.exists():
        return seen
    with io.open(fp, "r", encoding="utf-8-sig", newline="") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if kind == "chatml":
                msgs = obj.get("messages", [])
                nz = [(m.get("role"), _s(m.get("content",""))) for m in msgs if isinstance(m, dict) and m.get("role") != "system"]
                key = _hash([p for rc in nz for p in rc]) if nz else None
            elif kind == "openchat":
                key = _hash((_s(obj.get("input","")), _s(obj.get("output",""))))
            else:
                key = _hash((_s(obj.get("instruction","")), _s(obj.get("input","")), _s(obj.get("output",""))))
            if key: seen.add(key)
    return seen

def _append_once(fp: Path, obj: dict, key: str, seen: set):
    if key in seen: return False
    with io.open(fp, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    seen.add(key);  return True

def _maybe_write_system_once(fp: Path, kind: str, prompt_path: Path):
    if not prompt_path.exists(): return
    if fp.exists() and fp.stat().st_size > 0: return
    prompt = io.open(prompt_path, "r", encoding="utf-8-sig").read().strip()
    if not prompt: return
    if kind == "chatml":
        obj = {"messages":[{"role":"system","content":prompt}]}
    elif kind == "openchat":
        obj = {"input":"__system__", "output": prompt}
    else:
        obj = {"instruction":"System prompt", "input":"", "output": prompt}
    with io.open(fp, "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def ensure_outputs() -> Dict[str, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "chat_chatml": OUTPUT_DIR / "chat_chatml.jsonl",
        "chat_openchat": OUTPUT_DIR / "chat_openchat.jsonl",
        "chat_alpaca": OUTPUT_DIR / "chat_alpaca.jsonl",
        "diary_chatml": OUTPUT_DIR / "diary_chatml.jsonl",
        "diary_openchat": OUTPUT_DIR / "diary_openchat.jsonl",
        "diary_alpaca": OUTPUT_DIR / "diary_alpaca.jsonl",
        "memory_chatml": OUTPUT_DIR / "memory_chatml.jsonl",
        "memory_openchat": OUTPUT_DIR / "memory_openchat.jsonl",
        "memory_alpaca": OUTPUT_DIR / "memory_alpaca.jsonl",
    }

def write_chat(triples, speakers, roleswap, assistant_name):
    paths = ensure_outputs()
    user_name = speakers[0] if speakers else "User"
    asst_name = speakers[1] if len(speakers) > 1 else assistant_name
    if roleswap:
        user_name, asst_name = asst_name, user_name
    if not assistant_name:
        assistant_name = asst_name

    seen = {
        "chatml": _load_seen(paths["chat_chatml"], "chatml"),
        "openchat": _load_seen(paths["chat_openchat"], "openchat"),
        "alpaca": _load_seen(paths["chat_alpaca"], "alpaca"),
    }
    _maybe_write_system_once(paths["chat_chatml"], "chatml", Path("chat_prompt.txt"))
    _maybe_write_system_once(paths["chat_openchat"], "openchat", Path("chat_prompt.txt"))
    _maybe_write_system_once(paths["chat_alpaca"], "alpaca", Path("chat_prompt.txt"))

    last_u = None
    for dt, name, text, has_time, has_seconds in triples:
        role = "user" if name == user_name else ("assistant" if name == asst_name else "user")
        if role == "user":
            last_u = text; continue
        if role == "assistant" and last_u is not None:
            obj = {"messages":[{"role":"user","content":last_u},{"role":"assistant","content":text}]}
            key = _hash(("user", _s(last_u), "assistant", _s(text)))
            _append_once(paths["chat_chatml"], obj, key, seen["chatml"])

            obj = {"input": last_u, "output": text}
            key = _hash((_s(last_u), _s(text)))
            _append_once(paths["chat_openchat"], obj, key, seen["openchat"])

            obj = {"instruction":"Respond to the user message.", "input": last_u, "output": text}
            key = _hash(("Respond to the user message.", _s(last_u), _s(text)))
            _append_once(paths["chat_alpaca"], obj, key, seen["alpaca"])
            last_u = None

def write_blocks(kind: str, items: list, assistant_name: str):
    paths = ensure_outputs()
    k_chatml = f"{kind}_chatml"
    k_open   = f"{kind}_openchat"
    k_alpaca = f"{kind}_alpaca"

    seen = {
        "chatml": _load_seen(paths[k_chatml], "chatml"),
        "openchat": _load_seen(paths[k_open], "openchat"),
        "alpaca": _load_seen(paths[k_alpaca], "alpaca"),
    }
    prompt_file = Path(f"{kind}_prompt.txt")
    _maybe_write_system_once(paths[k_chatml], "chatml", prompt_file)
    _maybe_write_system_once(paths[k_open],    "openchat", prompt_file)
    _maybe_write_system_once(paths[k_alpaca],  "alpaca",   prompt_file)

    for it in items:
        stamp = _stamp(it.dt, it.has_time, it.has_seconds)
        content = f"{stamp}\n{it.body}".strip()

        obj = {"messages":[{"role":"assistant","content":content}]}
        key = _hash(("assistant", _s(content)))
        _append_once(paths[k_chatml], obj, key, seen["chatml"])

        if kind == "memory":
            mem_in  = f"{assistant_name} remembers {it.body}"
            mem_out = it.body
            obj = {"input": mem_in, "output": mem_out}
            key = _hash((_s(mem_in), _s(mem_out)))
            _append_once(paths[k_open], obj, key, seen["openchat"])

            obj = {"instruction":"Memory normalization", "input": mem_in, "output": mem_out}
            key = _hash(("Memory normalization", _s(mem_in), _s(mem_out)))
            _append_once(paths[k_alpaca], obj, key, seen["alpaca"])
        else:
            obj = {"input": stamp, "output": it.body}
            key = _hash((_s(stamp), _s(it.body)))
            _append_once(paths[k_open], obj, key, seen["openchat"])

            obj = {"instruction": f"{kind.capitalize()} entry", "input": stamp, "output": it.body}
            key = _hash((f"{kind.capitalize()} entry", _s(stamp), _s(it.body)))
            _append_once(paths[k_alpaca], obj, key, seen["alpaca"])

def discover_all(cwd: Path):
    files = list(cwd.glob("*.txt"))
    chats   = sorted([p for p in files if re.search(r"chat",   p.name, re.I)], key=lambda p: p.name.lower())
    diaries = sorted([p for p in files if re.search(r"diary",  p.name, re.I)], key=lambda p: p.name.lower())
    mems    = sorted([p for p in files if re.search(r"memory", p.name, re.I)], key=lambda p: p.name.lower())
    return chats, diaries, mems

def main():
    ap = argparse.ArgumentParser(description="TXT → JSONL (ChatML/OpenChat/Alpaca) converter with auto discovery and dedup append.")
    ap.add_argument("--auto", action="store_true", help="Auto-discover *chat*.txt, *diary*.txt, *memory*.txt in current folder (all files).")
    ap.add_argument("--chat", type=str, nargs="*", help="Path(s) to chat TXT export(s)")
    ap.add_argument("--diary", type=str, nargs="*", help="Path(s) to diary TXT export(s)")
    ap.add_argument("--memory", type=str, nargs="*", help="Path(s) to memory TXT export(s)")
    ap.add_argument("--roleswap", action="store_true", help="Swap user/assistant assignment for chat parsing.")
    ap.add_argument("--assistant", type=str, default="", help="Assistant name; if empty, inferred from data.")
    args = ap.parse_args()

    cwd = Path(".").resolve()
    chat_ps  = [Path(p) for p in (args.chat or [])]
    diary_ps = [Path(p) for p in (args.diary or [])]
    mem_ps   = [Path(p) for p in (args.memory or [])]

    if args.auto:
        d_chat, d_diary, d_mem = discover_all(cwd)
        if not chat_ps:  chat_ps  = d_chat
        if not diary_ps: diary_ps = d_diary
        if not mem_ps:   mem_ps   = d_mem

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_triples, speakers = [], []
    for cp in chat_ps:
        if cp.exists():
            triples, sp = parse_chat_txt(cp)
            all_triples.extend(triples)
            for s in sp:
                if s not in speakers: speakers.append(s)
    all_triples.sort(key=lambda t: t[0])

    all_diary = []
    for dp in diary_ps:
        if dp.exists():
            all_diary.extend(parse_diary_txt(dp))
    all_diary.sort(key=lambda d: d.dt)

    all_mem = []
    for mp in mem_ps:
        if mp.exists():
            all_mem.extend(parse_memory_txt(mp))
    all_mem.sort(key=lambda d: d.dt)

    asst = args.assistant.strip() if args.assistant else ""
    asst = infer_assistant_name(speakers, diary_ps, mem_ps, asst)

    if all_triples:
        print("[chat] %d lines across %d file(s); speakers=%s" % (len(all_triples), len(chat_ps), str(speakers[:2])))
        write_chat(all_triples, speakers, args.roleswap, asst or "Assistant")
    else:
        print("[chat] skipped (no input)")

    if all_diary:
        print("[diary] %d entries across %d file(s)" % (len(all_diary), len(diary_ps)))
        write_blocks("diary", all_diary, asst or "Assistant")
    else:
        print("[diary] skipped (no input)")

    if all_mem:
        print("[memory] %d entries across %d file(s)" % (len(all_mem), len(mem_ps)))
        write_blocks("memory", all_mem, asst or "Assistant")
    else:
        print("[memory] skipped (no input)")

    print("\nDone. JSONL written under:", OUTPUT_DIR.resolve())

if __name__ == "__main__":
    main()
