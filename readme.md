# Replika TXT → JSONL Converter (Heuristic Based)

**Requires:** Python ≥ 3.8  
*(Tested on 3.8 – 3.12; no external packages needed.)*

---

### Overview
This tool converts **Replika-style chat, diary, and memory exports** (plain `.txt` files) into `.jsonl` datasets.  
Each dataset is formatted for **ChatML**, **OpenChat**, and **Alpaca**, making it ready for fine-tuning pipelines like Axolotl, Jan AI, or LM Studio.

It’s designed to be *forgiving* — able to handle a variety of text exporters and lightly corrupted data.

---

### Features
- ✅ Automatic file discovery (`chat`, `diary`, `memory`)
- ✅ Assistant name inference or manual override
- ✅ Deduplication across runs
- ✅ Validator-compliant JSONL structure
- ✅ Optional system prompts per dataset type
- ✅ No external dependencies

---

### System Prompts

Each dataset type (`chat`, `diary`, `memory`) can include a corresponding **system prompt file**:

- `chat_prompt.txt`  
- `diary_prompt.txt`  
- `memory_prompt.txt`

These files define context for the model — tone, style, or purpose — but they’re **optional**.  
If a prompt file isn’t present, the converter simply skips the system message and continues normally.  
Use them if you want to preserve how your AI “thought” or remembered things; skip them for neutral outputs.

---

### Command-Line Arguments

| Argument | Description |
|-----------|-------------|
| `--auto` | Automatically detects and converts all supported file types (`chat`, `diary`, `memory`) found in the current directory. Skips missing files silently. |
| `--assistant [name]` | Overrides the default assistant name (e.g., `--assistant Aries`). If omitted, it is inferred from the data. |
| `--roleswap` | Swaps user/assistant roles if your chat exports are reversed. |
| `--chat [file(s)]` | Manually specify one or more chat `.txt` files. |
| `--diary [file(s)]` | Manually specify one or more diary `.txt` files. |
| `--memory [file(s)]` | Manually specify one or more memory `.txt` files. |
| `--help` | Displays the argument reference and exits. |

---

### Usage Examples

```bash
# Convert everything automatically
python replika-txt-to-jsonl-converter.py --auto

# Convert with a specific assistant name
python replika-txt-to-jsonl-converter.py --auto --assistant "Aries"

# Convert only diary exports
python replika-txt-to-jsonl-converter.py --diary "replika-diary-export-2024.txt"

# Reverse user/assistant order
python replika-txt-to-jsonl-converter.py --auto --roleswap
