# Library Analyzer Skill — AI Agent Reference

> **Version:** v1.3.0 (2026-05-26) | `analyze_library.py` — 20 functions, ~1000 lines
> **已验证:** 10 个 C/C++ 库, 累计提取 407 类 / 772 函数 / 1087 宏 / 339 枚举 / 268 struct / 953 模板
> **详见:** [`CHANGELOG.md`](./CHANGELOG.md)

**Purpose:** Generate hallucination-proof skill documents for arbitrary C/C++ dependency libraries.
**Workflow:** Source code → `api_map.json` (structured API data) → `SKILL.md` (AI agent reference)
**Target:** Eliminate API hallucination in generated code by grounding AI agents in exact library APIs.

---

## 1. Trigger

When the user says something like:

> "分析库 X，路径在 Y"  
> "为库 X 生成 AI 参考文档"  
> "Validate library X and create a skill doc for it"

OR when, during code generation, the AI detects it's referencing a library without a skill document.

## 2. Workflow Overview

```
Step 1: DISCOVER   — Understand library structure (single-header? multi-file? build system?)
Step 2: EXTRACT    — Parse headers programmatically → api_map.json
Step 3: ENRICH     — Identify categories, patterns, call chains, anti-patterns
Step 4: GENERATE   — Render SKILL.md from template + enriched data
Step 5: VALIDATE   — Spot-check critical methods against source
```

---

## 3. Step 1: DISCOVER — Library Structure Analysis

Determine the library's structure to choose the extraction strategy:

| Characteristic | Strategy | Example |
|---|---|---|
| Single `.h` file (header-only) | Direct parse of one file | `httplib.h`, `json.hpp`, `fmt/core.h` |
| Multiple `.h`/`.hpp` files (header-only) | Parse all public headers; skip impl headers | `asio/`, `boost/` |
| `.h` + `.cpp` (compiled library) | Parse public headers only; note link flags | `libcurl`, `openssl` |
| CMake/PkgConfig project | Parse headers; extract config macros from CMakeLists | Catch2, spdlog |

### Discovery Commands

```bash
# Library structure
ls <lib-root>/          # top-level files
ls <lib-root>/include/  # public headers (common pattern)
find <lib-root> -name "*.h" -o -name "*.hpp" 2>/dev/null | head -30

# Build system
ls <lib-root>/CMakeLists.txt 2>/dev/null && echo "CMake project"
ls <lib-root>/Makefile 2>/dev/null && echo "Makefile project"
ls <lib-root>/meson.build 2>/dev/null && echo "Meson project"

# Version detection
grep -rn "VERSION\|version" <lib-root>/include/*.h 2>/dev/null | head -5
grep -rn "VERSION\|version" <lib-root>/CMakeLists.txt 2>/dev/null | head -5
```

### Output

Record the following into a discovery JSON:

```json
{
  "library_name": "cpp-httplib",
  "library_type": "single-header",
  "main_header": "httplib.h",
  "version": "0.46.0",
  "namespace": "httplib",
  "compilation_flags": ["-lssl", "-lcrypto", "-lz"],
  "define_prefix": "CPPHTTPLIB_",
  "define_features": ["OPENSSL_SUPPORT", "ZLIB_SUPPORT", "MBEDTLS_SUPPORT", ...]
}
```

---

## 4. Step 2: EXTRACT — Programmatic API Parsing

### 4.1 Extraction Script

Use the companion script `analyze_library.py`. Usage:

```bash
# Basic: single-header library
python analyze_library.py --header <path-to-header> --name <LibraryName> --out <output-dir>

# With namespace
python analyze_library.py --header <path> --name <Name> --out <dir> --namespace <ns>

# Multi-header: specify folder
python analyze_library.py --include-dir <path-to-include> --name <Name> --out <dir> [--header-pattern "*.hpp"]

# With custom define prefix (for config macros extraction)
python analyze_library.py --header <path> --name <Name> --out <dir> --define-prefix MYLIB_

# Specify output JSON (default: api_map.json in output dir)
python analyze_library.py --header <path> --name <Name> --out <dir> --json <path-to-json>
```

### 4.2 What the Script Extracts

The script produces `api_map.json` with:

| Section | Content | Source |
|---------|---------|--------|
| `version` | Library version string | `#define *_VERSION` or auto-detected |
| `config_macros` | All `#define *_*` macros | Header file `#define` scanning |
| `enums` | `enum class` and `enum` values | Direct enum extraction |
| `type_aliases` | `using X = Y;` top-level declarations | Top-level `using` extraction |
| `structs` | Public struct fields + methods | Struct body parsing |
| `classes` | Public class methods (by class) | Class body parsing with section tracking |
| `free_functions` | Top-level function signatures | Regex-based function matching |
| `namespaces` | Nested namespace classes/functions | Namespace block extraction |

### 4.3 Extraction Rules (Anti-Hallucination for the Script Itself)

**The extraction script MUST:**
- Use `[ \t]+` NOT `\s+` to separate `#define name value` — `\s` matches newlines and causes cross-line captures (this was a real bug)
- Match `{`/`}` braces accounting for string/char literals to handle C++ template angle brackets
- Skip `private:`/`protected:` sections in classes
- Skip `operator=` and destructor overloads in public method listings
- Report count of extracted items per class for manual validation

**Important regex patterns:**

```python
# DEFINE — use space/tab only to avoid newline capture
r'#\s*define\s+(NAME_PREFIX\w+)[ \t]+(.*)'

# CLASS / STRUCT start
r'^(?:class|struct)\s+(\w+)(?:\s+final)?(?:\s*:\s*public\s+\w+(?:\s*,\s*public\s+\w+)*)?\s*\{'

# MATCHING BRACE — handle nested braces, string/char literals
# Track depth; skip content inside "...", '...'

# PUBLIC METHOD (line ending with ; after class body)
lines after 'public:' until 'private:'/'protected:'/end
```

### 4.4 Fallback: Manual Extraction

If the script fails due to unusual C++ constructs (template-heavy, heavy preprocessor use), fall back to:

1. Use the script with `--manual` flag to generate a template JSON
2. Manually fill in the template by reading the source
3. Run the generator script on the filled template

---

## 5. Step 3: ENRICH — Pattern Analysis & Knowledge Curation

After extraction, enrich the raw API data with:

### 5.1 Feature Categorization

Group classes and functions into feature categories:

| Category | Includes | Example (cpp-httplib) |
|----------|----------|----------------------|
| Core types | Request, Response, Stream, Result | HTTP message types |
| Server | Server, SSLServer | Route handlers, config |
| Client | Client, ClientImpl, SSLClient | HTTP methods, auth, proxy |
| Streaming | DataSink, ContentProvider, ContentReader | Chunked transfer |
| WebSocket | ws::WebSocket, ws::WebSocketClient | Bidirectional comms |
| SSE | sse::SSEClient | Server-sent events |
| TLS | tls::PeerCert, tls::VerifyContext | Certificate handling |
| Config | All compile-time macros | Feature toggles, timeouts |

### 5.2 Call Chain Analysis

For each category, trace the **typical call chain** — what the library does internally when a user API is called:

```markdown
Pattern: Server::listen → bind → accept → parse request → route → handler → write response
  - svr.listen("0.0.0.0", 8080) creates listening socket
  - Accept loop calls process_and_close_socket per connection
  - routing() matches pattern → dispatch_request() calls handler
  - write_response() sends reply
```

This helps AI agents understand the architecture and avoid generating impossible call sequences.

### 5.3 Anti-Hallucination Rules

After reviewing the library, create specific rules for the most common wrong-API patterns:

**Sources of the blacklist:**
- Common C++ web framework patterns that DON'T exist in this library (Express/Flask-style routing?)
- Pattern confusion with similar libraries (Boost.Beast vs cpp-httplib)
- Methods that sound like they should exist but don't (`set_body()`, `on_message()`)
- API naming that's similar but slightly wrong (`encode_uri` vs `url_encode`)

**Format:**
```markdown
### DO NOT generate these APIs — they do NOT exist in [LibraryName]

| Wrong (hallucinated) | Correct alternative |
|---|---|
| `Response::set_body(str)` | `res.set_content(str, ct)` |
| ... | ... |
```

### 5.4 Compilation Analysis

From discovery phase, produce:

```markdown
| Mode | Link flags |
|---|---|
| Minimal | (none — header-only) |
| +FeatureX | `-lfoo` |
| +FeatureY | `-lbar` |
```

---

## 6. Step 4: GENERATE — SKILL.md Rendering

### 6.1 Generation Script

Use `generate_skill.py` included in this skill. It reads `api_map.json` and produces a formatted SKILL.md:

```bash
# Basic generation
python generate_skill.py --json <api_map.json> --out <SKILL.md>

# With enrichment data
python generate_skill.py \
  --json <api_map.json> \
  --out <SKILL.md> \
  --categories <categories.json> \   # feature categorization
  --patterns <patterns.json> \       # call chain / common patterns
  --blacklist <blacklist.json>       # anti-hallucination rules

# With library-specific template overrides
python generate_skill.py --json <api_map.json> --out <SKILL.md> --template <custom_template.md>
```

### 6.2 Output Structure

Every generated SKILL.md follows this template:

```
# [Library Name] Skill — AI Agent Reference
Version info | Source link | Token estimate

## 🚫 Anti-Hallucination Rules
### DO NOT generate these APIs — they do NOT exist
Table: wrong API → correct alternative
### Architecture constraints (e.g. blocking I/O, HTTP version)

## Compile-Time Configuration
Feature toggles | Runtime defaults

## Enums
Compact listing (truncate StatusCode-like large enums to common values)

## Type Aliases
Compact listing of using declarations

## Core Types
Key classes/structs with method signatures

## [Feature Categories...]
Each major feature group:
  - Class API signatures (compact)
  - Parameter matrices for heavily overloaded methods
  - Quick patterns / examples

## Compilation
Table: mode → link flags

## Common Patterns & Call Chains
Non-obvious usage patterns

## Version
Exact library version
```

### 6.3 Token Optimization Rules

When generating the markdown, apply these compression rules:

1. **Use parameter matrices instead of listing all overloads**
   - Groups methods by content-body type (body, params, multipart, etc.)
   - Shows optional axes (headers, receiver, progress) as annotations
   - Text: `Method(path, [headers?], [body...], [receiver?], [progress?])`
   - Lists body variants: no body, raw bytes, content provider, params, multipart, etc.

2. **Truncate large enums** (>15 values): show only commonly used values, add "see source for full list"

3. **Use tables for compilation** instead of repetitive bash blocks

4. **Minimize code blocks**: prefer indented signatures over code fences for API listings

5. **Consolidate duplicate APIs**: if Class B delegates to Class A (e.g. Client→ClientImpl), list only one with a delegation note

6. **Remove narrative bloat**: call chains in 3 lines max, not paragraphs

---

## 7. Step 5: VALIDATE — Accuracy Verification

### 7.1 Automated Checks

Run these against the generated JSON:

```bash
# Check: does every method in JSON appear in source? (round-trip test)
python -c "
import json, re
src = open('header.h').read()
api = json.load(open('api_map.json'))
missing = []
for cls_name, cls_data in api['classes'].items():
    for m in cls_data['public_methods']:
        # Extract method name
        name = m.split('(')[0].split()[-1]
        if name not in src:
            missing.append(f'{cls_name}::{name}')
if missing:
    print(f'MISSING METHODS (possible extraction error):')
    for m in missing: print(f'  {m}')
"
```

### 7.2 Manual Spot Checks

Verify at least 3 methods from each major class against the actual source:
- Constructor signatures (parameter count and types)
- Method names and their return types
- Parameter default values

### 7.3 Common Extraction Errors (Checklist)

| Error | Symptom | Fix |
|-------|---------|-----|
| `\s+` in define regex | Missing macro values | Use `[ \t]+` instead |
| Nested braces in template <...> | Broken class/struct extraction | Track depth with brace counting + string/char skip |
| `public:` inside `#ifdef` block | Methods attributed to wrong class | Handle preprocessor conditionals |
| Method with `const` parameter | Filtered out as "copy constructor" | Only filter `const` at end of method, not in middle |
| Multi-line method signatures | Missing methods | Accumulate lines until `;` found |
| `enum class X : type` | Failed to parse | Regex: `enum\s+(?:class\s+)?(\w+)\s*(?::\s*(\w+))?` |

---

## 8. The Full Pipeline Script

This skill ships `analyze_library.py` which wraps Steps 2+4 into a single command:

```bash
# One-shot: analyze + generate
python analyze_library.py --header <path> --name <Name> --out <output-dir>

# This produces:
#   <output-dir>/api_map.json
#   <output-dir>/SKILL.md
```

The script:
1. Parses the header and extracts API structure
2. Auto-detects version, namespace, define prefixes
3. Applies compression rules (parameter matrices, enum truncation)
4. Generates the markdown skill document
5. Writes a validation report

### Script Options

| Option | Default | Description |
|--------|---------|-------------|
| `--header` | required | Path to primary header file |
| `--include-dir` | (disabled) | Path to include directory for multi-header libs |
| `--name` | required | Library display name |
| `--out` | current dir | Output directory for SKILL.md and JSON |
| `--json` | `<out>/api_map.json` | Output path for JSON data |
| `--namespace` | (auto-detect) | C++ namespace of the library |
| `--define-prefix` | (auto-detect) | Prefix of config macros to extract |
| `--header-pattern` | `*.h` | Glob pattern for multi-header libs |
| `--manual` | false | Generate template JSON for manual fill |

---

## 9. Reuse: Known Library Profiles

Pre-configured profiles for common libraries (added as they are analyzed):

| Library | Profile Flags |
|---------|--------------|
| cpp-httplib | `--define-prefix CPPHTTPLIB_` `--namespace httplib` |
| nlohmann/json | `--namespace nlohmann` `--single-header` |
| spdlog | `--namespace spdlog` `--include-dir include/spdlog` |
| fmt | `--namespace fmt` `--header-pattern "*.h"` |

---

## 10. Troubleshooting

| Problem | Likely Cause | Solution |
|---------|-------------|----------|
| Extracted 0 classes | Regex didn't match `class X {` | Check if class uses `__declspec` or macro prefix |
| Methods missing const | Parser confusing `const` param with const method | Update the const-filter in extraction logic |
| Half the methods missing | `#ifdef` blocks split the class | Extract across preprocessor boundaries |
| Enum values empty | Enum has `// comment` on same line | Strip line comments before parsing enums |
| Version not found | Version stored in CMakeLists.txt not header | Manually supply `--version` |

## 11. Known Limitations (from Real-World Testing)

| Limitation | Example Library | Impact | Workaround |
|------------|----------------|--------|------------|
| Template-heavy classes not found | nlohmann/json `basic_json` | Main class missing | Use `--manual` mode; fill template manually |
| Inline `//` comments with `"` braces | Any library | Brace matching breaks at embedded quotes | `find_matching_brace` now auto-skips `//` comments |
| Forward declarations confuse detection | Any class forward-declared before definition | Early `{` triggers false body end | Detection now scans for `{` after class line, not on same line |
| Multi-line method signatures | SFINAE-heavy libraries | Only first line captured | Accumulate lines until `;` found |
| `#define` with backslash continuation | Platform libraries | Missing macro values | Not auto-handled; use manual fill |
| `enum` values on same line as `//` comment | spdlog, fmt | Stale `//` text in value | `enum` parser strips `//` before extraction |

**Verified on:**
- cpp-httplib v0.46.0 — 33 classes, 136 methods, 52 config macros ✅
- nlohmann/json v3.12.0 — 25 classes, 52 methods, 125 config macros ⚠️ (basic_json needs manual enrichment)

---

*Created from iterative refinement across 10 C/C++ libraries. See [CHANGELOG.md](./CHANGELOG.md) for full iteration history. Last updated 2026-05-26.*
