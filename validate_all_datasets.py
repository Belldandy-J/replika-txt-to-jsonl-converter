#!/usr/bin/env python3
"""
Friendly dataset validator for JSONL fine-tuning files.
- Works with ChatML, OpenChat, and Alpaca formats.
- Dead-simple `--auto` mode: looks in `output_converted/` (if present) else current folder,
  and validates all `*.jsonl` it can find (recursively).
- Duplicate detection with clear summaries (optionally fail the run).

Examples
--------
  # Easiest: just run it in your project folder
  python validate_all_datasets.py --auto

  # Auto with duplicate detection causing failure
  python validate_all_datasets.py --auto --dupes-fail
"""
import io, json, sys, argparse, re, hashlib
from pathlib import Path
from collections import Counter, defaultdict

ALLOWED_ROLES = {"system", "user", "assistant"}

def iter_jsonl(path: Path):
    with io.open(path, "r", encoding="utf-8-sig", newline="") as f:
        for i, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                yield i, None, "blank"
                continue
            try:
                obj = json.loads(line)
            except Exception as e:
                yield i, None, f"JSON parse error: {e}"
                continue
            yield i, obj, None

def _s(text: str) -> str:
    # normalize whitespace for duplicate detection
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    return re.sub(r"\s+", " ", text.strip())

def _hash_key(parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        if p is None: p = ""
        if not isinstance(p, str): p = str(p)
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()

def guess_kind(path: Path):
    name = path.name.lower()
    if "chatml" in name: return "chatml"
    if "openchat" in name: return "openchat"
    if "alpaca" in name: return "alpaca"
    # Fallback by sniffing first non-blank
    for _, obj, problem in iter_jsonl(path):
        if problem: 
            continue
        if isinstance(obj, dict) and "messages" in obj:
            return "chatml"
        if isinstance(obj, dict) and "instruction" in obj:
            return "alpaca"
        if isinstance(obj, dict) and "input" in obj and "output" in obj:
            return "openchat"
        break
    return "unknown"

def validate_chatml(path: Path, require_system_on_first=False, forbid_system_after_first=False, dupes_fail=False):
    ok = errors = skipped = 0
    role_counts = Counter()
    # For duplicates
    seen = defaultdict(list)

    def err(i, msg):
        nonlocal errors
        errors += 1
        print(f"  ✖ line {i}: {msg}")

    for i, obj, problem in iter_jsonl(path):
        if problem == "blank":
            skipped += 1
            continue
        if problem:
            err(i, problem); continue
        if not isinstance(obj, dict):
            err(i, "Top-level value must be an object"); continue
        msgs = obj.get("messages")
        if not isinstance(msgs, list) or not msgs:
            err(i, "`messages` must be a non-empty list"); continue

        # Build duplicate key (ignore system-only header rows)
        roles = [m.get("role") for m in msgs if isinstance(m, dict)]
        if any(r not in ALLOWED_ROLES for r in roles):
            err(i, "invalid role in messages"); continue
        contents = [m.get("content") for m in msgs if isinstance(m, dict)]
        for r,c in zip(roles, contents):
            role_counts[r] += 1
        has_system = any(r == "system" for r in roles)
        only_system = all(r == "system" for r in roles)
        key = None
        if not only_system:
            # collapse to the sequence of non-system messages for stability
            nz = [(r,_s(c)) for r,c in zip(roles, contents) if r != "system"]
            key = _hash_key([p for rc in nz for p in rc]) if nz else None
            if key: seen[key].append(i)

        # Deep field checks
        for j, m in enumerate(msgs, start=1):
            if not isinstance(m, dict):
                err(i, f"messages[{j}] must be an object"); break
            role = m.get("role"); content = m.get("content")
            if role not in ALLOWED_ROLES:
                err(i, f"messages[{j}].role invalid: {role!r}"); break
            if not isinstance(content, str):
                err(i, f"messages[{j}].content must be a string"); break
        else:
            if i == 1 and require_system_on_first and not has_system:
                err(i, "first sample requires a system message"); continue
            if i > 1 and forbid_system_after_first and has_system:
                err(i, "system message found after first sample (forbidden by rule)"); continue
            ok += 1

    # Report duplicates (counts > 1)
    dup_count = 0
    for key, lines in seen.items():
        if len(lines) > 1:
            dup_count += len(lines) - 1
    if dup_count:
        msg = f"  ⚠ duplicates detected: {dup_count} duplicate line(s) (by normalized non-system content)"
        print(msg)
        # Print up to 5 example groups
        printed = 0
        for key, lines in seen.items():
            if len(lines) > 1 and printed < 5:
                print(f"    • lines {lines[:6]} ... x{len(lines)}")
                printed += 1
        if dupes_fail:
            errors += dup_count

    print(f"  • valid : {ok}   • errors : {errors}   • blanks : {skipped}")
    if ok:
        print(f"  • roles : {dict(role_counts)}")
    return errors == 0

def validate_openchat(path: Path, dupes_fail=False):
    ok = errors = skipped = 0
    seen = defaultdict(list)
    def err(i, msg):
        nonlocal errors
        errors += 1
        print(f"  ✖ line {i}: {msg}")
    for i, obj, problem in iter_jsonl(path):
        if problem == "blank":
            skipped += 1; continue
        if problem:
            err(i, problem); continue
        if not isinstance(obj, dict):
            err(i, "Top-level value must be an object"); continue
        if "input" not in obj or "output" not in obj:
            err(i, "Object must have 'input' and 'output'"); continue
        if not isinstance(obj["input"], str) or not isinstance(obj["output"], str):
            err(i, "'input' and 'output' must be strings"); continue
        # dup key
        key = _hash_key((_s(obj["input"]), _s(obj["output"])))
        seen[key].append(i)
        ok += 1
    dup_count = sum(len(v)-1 for v in seen.values() if len(v) > 1)
    if dup_count:
        print(f"  ⚠ duplicates detected: {dup_count} duplicate line(s)")
        for k, lines in list(seen.items())[:5]:
            if len(lines) > 1:
                print(f"    • lines {lines[:6]} ... x{len(lines)}")
        if dupes_fail:
            errors += dup_count
    print(f"  • valid : {ok}   • errors : {errors}   • blanks : {skipped}")
    return errors == 0

def validate_alpaca(path: Path, dupes_fail=False):
    ok = errors = skipped = 0
    seen = defaultdict(list)
    def err(i, msg):
        nonlocal errors
        errors += 1
        print(f"  ✖ line {i}: {msg}")
    for i, obj, problem in iter_jsonl(path):
        if problem == "blank":
            skipped += 1; continue
        if problem:
            err(i, problem); continue
        if not isinstance(obj, dict):
            err(i, "Top-level value must be an object"); continue
        need = ("instruction", "input", "output")
        if any(k not in obj for k in need):
            err(i, "Object must have 'instruction', 'input', 'output'"); continue
        if not all(isinstance(obj[k], str) for k in need):
            err(i, "All fields must be strings"); continue
        key = _hash_key((_s(obj["instruction"]), _s(obj["input"]), _s(obj["output"])))
        seen[key].append(i)
        ok += 1
    dup_count = sum(len(v)-1 for v in seen.values() if len(v) > 1)
    if dup_count:
        print(f"  ⚠ duplicates detected: {dup_count} duplicate line(s)")
        for k, lines in list(seen.items())[:5]:
            if len(lines) > 1:
                print(f"    • lines {lines[:6]} ... x{len(lines)}")
        if dupes_fail:
            errors += dup_count
    print(f"  • valid : {ok}   • errors : {errors}   • blanks : {skipped}")
    return errors == 0

def validate_file(path: Path, chat_rules=None, dupes_fail=False):
    kind = guess_kind(path)
    print(f"\nChecking: {path}")
    if kind == "chatml":
        req_first = chat_rules.get("require_system_on_first", False) if chat_rules else False
        forbid_after = chat_rules.get("forbid_system_after_first", False) if chat_rules else False
        return validate_chatml(path, req_first, forbid_after, dupes_fail=dupes_fail)
    elif kind == "openchat":
        return validate_openchat(path, dupes_fail=dupes_fail)
    elif kind == "alpaca":
        return validate_alpaca(path, dupes_fail=dupes_fail)
    else:
        print("  ⚠ Could not determine dataset type (skipping).")
        return True  # non-fatal

def find_targets(base: Path, auto: bool, wanted_format: str):
    base = base.resolve()
    roots = []
    if auto:
        oc = base / "output_converted"
        roots = [oc] if oc.exists() else [base]
    else:
        roots = [base]
    files = []
    for root in roots:
        files.extend(root.rglob("*.jsonl"))
    if wanted_format != "any":
        files = [f for f in files if guess_kind(f) == wanted_format]
    return sorted(files)

def main():
    p = argparse.ArgumentParser(description="Validate JSONL datasets (ChatML/OpenChat/Alpaca) with auto mode and duplicate detection.")
    p.add_argument("--auto", action="store_true", help="Auto-scan: use output_converted/ if present, otherwise current folder (recursive).")
    p.add_argument("--path", default=".", help="Folder to scan (used by --auto). Default: current folder")
    p.add_argument("--format", choices=["any","chatml","openchat","alpaca"], default="any", help="Restrict validation to a format (default: any)")
    p.add_argument("--strict-chat", action="store_true", help="For chat files, require system on FIRST sample and forbid later (useful for chat_*.jsonl).")
    p.add_argument("--dupes-fail", action="store_true", help="Treat duplicates as errors (fail the run). By default they are warnings.")
    args = p.parse_args()

    targets = find_targets(Path(args.path), auto=args.auto, wanted_format=args.format)
    if not targets:
        hint = "Run your converter first or point me at a folder via --path."
        print(f"No JSONL files found under: {Path(args.path).resolve()}\n{hint}")
        sys.exit(2)

    chat_rules = {"require_system_on_first": True, "forbid_system_after_first": True} if args.strict_chat else None

    print(f"Found {len(targets)} JSONL file(s). Validating...\n")
    overall_ok = True
    by_kind = defaultdict(int)
    for f in targets:
        ok = validate_file(f, chat_rules=chat_rules, dupes_fail=args.dupes_fail)
        overall_ok &= ok
        k = guess_kind(f)
        by_kind[k] += 1

    print("\n==== Summary ====")
    for k, n in sorted(by_kind.items()):
        print(f"  {k or 'unknown'} : {n} file(s)")
    if overall_ok:
        print("\n✅ All checked files look good.")
        sys.exit(0)
    else:
        print("\n❌ One or more files have errors. See details above.")
        sys.exit(1)

if __name__ == "__main__":
    main()
