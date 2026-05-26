#!/usr/bin/env python3
"""
analyze_library.py — Programmatic C/C++ Library API Extraction & Skill Generation

Usage:
    python analyze_library.py --header path/to/header.h --name LibName --out ./output-dir

Full options:
    python analyze_library.py \\
        --header path/to/header.h \\
        --name "Library Name" \\
        --out ./output-dir \\
        --json api_map.json \\
        --namespace mylib \\
        --define-prefix MYLIB_ \\
        --header-pattern "*.hpp" \\
        --include-dir ./include \\
        --manual

Output:
    api_map.json  — Structured API extraction result
    SKILL.md      — Generated AI agent reference document
"""

import re, json, os, sys, argparse, glob
from pathlib import Path

__version__ = "1.0.0"

# ──────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ──────────────────────────────────────────────────────────────────────────────

def clean_line(l):
    l = re.sub(r'//.*', '', l)
    return l.strip()

def remove_cpp_comments(text):
    return re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)

def find_matching_brace(text, start, ob='{', cb='}'):
    """Find matching close brace. Skips // and /* */ comments inline."""
    depth = 0
    in_str = False
    in_char = False
    i = start
    while i < len(text) and text[i] != ob:
        i += 1
    if i >= len(text):
        return -1
    j = i
    while j < len(text):
        c = text[j]
        # Skip // comments
        if c == '/' and j + 1 < len(text) and text[j+1] == '/' and not in_str and not in_char:
            nl = text.find(chr(10), j)
            if nl < 0:
                return -1
            j = nl + 1
            continue
        if in_char:
            if c == '\\': j += 2; continue
            if c == "'": in_char = False
            j += 1
            continue
        if in_str:
            if c == '\\': j += 2; continue
            if c == '"': in_str = False
            j += 1
            continue
        if c == "'": in_char = True
        elif c == '"': in_str = True
        elif c == ob: depth += 1
        elif c == cb:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1

def extract_class_body(text, start_pos):
    """Extract body text from class/struct definition.
    Uses updated find_matching_brace (handles // comments)."""
    ob = text.find('{', start_pos)
    if ob < 0: return None
    cb = find_matching_brace(text, ob)
    if cb < 0: return None
    return text[ob+1:cb]

def accumulate_methods(lines, start_idx):
    """Accumulate multi-line method signatures starting at start_idx."""
    buf = ''
    result = []
    for i in range(start_idx, len(lines)):
        s = clean_line(lines[i])
        if not s:
            if buf:
                result.append(buf)
                buf = ''
            continue
        buf += ' ' + s if buf else s
        if buf.rstrip().endswith(';'):
            result.append(buf.strip())
            buf = ''
    if buf:
        result.append(buf.strip())
    return result

# ──────────────────────────────────────────────────────────────────────────────
# Extraction
# ──────────────────────────────────────────────────────────────────────────────

def extract_macros(text, prefix='CPPHTTPLIB_'):
    """Extract #define macros with given prefix.
    Handles both 'define X value' and 'define X' (empty value).
    """
    macros = {}
    for m in re.finditer(r'#\s*define\s+(' + prefix + r'\w+)[ \t]*(.*)', text):
        name = m.group(1)
        val = m.group(2).strip()
        val = re.sub(r'\s*//.*', '', val).strip()
        macros[name] = val
    return macros

def extract_enums(clean):
    """Extract all enum class/enum definitions (C++ and C style).
    Handles:
      - C++: enum class Name { ... };
      - C:   enum Name { ... };
      - C:   typedef enum { ... } Name;
      - C:   typedef enum Name { ... } Name;
    """
    enums = {}
    
    # Pattern 1: C++ style enum { ... } or enum class Name { ... }
    pat1 = re.compile(
        r'(enum\s+(?:class\s+)?(\w+)(?:\s*:\s*(\w+))?\s*\{([^}]*)\})',
        re.DOTALL
    )
    for m in pat1.finditer(clean):
        name = m.group(2)
        body = m.group(4)
        values = []
        for vline in body.split('\n'):
            v = clean_line(vline)
            if not v: continue
            v = re.sub(r',\s*$', '', v)
            v = re.sub(r'\s*//.*', '', v).strip()
            if not v: continue
            parts = v.split('=', 1)
            values.append({
                'name': parts[0].strip(),
                'value': parts[1].strip() if len(parts) > 1 else None
            })
        enums[name] = {'values': values}
    
    # Pattern 2: C style 'typedef enum { ... } Name;' or 'typedef enum Name { ... } Name;'
    pat2 = re.compile(
        r'typedef\s+enum\s+(?:\w+\s+)?\{([^}]*)\}\\s*(\w+);',
        re.DOTALL
    )
    for m in pat2.finditer(clean):
        name = m.group(2)
        body = m.group(1)
        if name in enums:
            continue  # already extracted from Pattern 1
        values = []
        for vline in body.split('\n'):
            v = clean_line(vline)
            if not v: continue
            v = re.sub(r',\s*$', '', v)
            v = re.sub(r'\s*//.*', '', v).strip()
            if not v: continue
            parts = v.split('=', 1)
            values.append({
                'name': parts[0].strip(),
                'value': parts[1].strip() if len(parts) > 1 else None
            })
        enums[name] = {'values': values}
    
    # Pattern 3: C anonymous enum (no name) — 'enum { ... };'
    # Give it a synthetic name based on first value
    pat3 = re.compile(
        r'enum\s*\{([^}]*)\}\s*;',
        re.DOTALL
    )
    enum_counter = 0
    for m in pat3.finditer(clean):
        body = m.group(1)
        values = []
        for vline in body.split('\n'):
            v = clean_line(vline)
            if not v: continue
            v = re.sub(r',\s*$', '', v)
            v = re.sub(r'\s*//.*', '', v).strip()
            if not v: continue
            parts = v.split('=', 1)
            values.append({
                'name': parts[0].strip(),
                'value': parts[1].strip() if len(parts) > 1 else None
            })
        if values:
            # Use first value as synthetic name, or generate one
            synth_name = values[0]['name'] if len(values) == 1 else f'enum_{enum_counter}'
            enum_counter += 1
            if synth_name not in enums:
                enums[synth_name] = {'values': values}
    
    return enums


def extract_c_structs(clean):
    """
    Extract C struct definitions with their fields.
    """
    structs = {}
    pat1 = re.compile(
        r'typedef\s+struct\s+(?:\w+\s+)?\{([^}]*)\}\s*(\w+);',
        re.DOTALL
    )
    for m in pat1.finditer(clean):
        name = m.group(2)
        body = m.group(1)
        fields = []
        for line in body.split(chr(10)):
            s = clean_line(line)
            if not s:
                continue
            if s.startswith('//') or s.startswith('#') or s.startswith('/*'):
                continue
            if s.endswith(';') and not s.startswith('}'):
                s2 = re.sub(r'//.*', '', s).strip()
                if s2:
                    fields.append(s2)
        if fields:
            structs[name] = {'fields': fields, 'kind': 'typedef struct'}

    pat2 = re.compile(
        r'^struct\s+(\w+)\s*\{([^}]*)\}',
        re.MULTILINE | re.DOTALL
    )
    for m in pat2.finditer(clean):
        name = m.group(1)
        body = m.group(2)
        if name in structs:
            continue
        fields = []
        for line in body.split(chr(10)):
            s = clean_line(line)
            if not s:
                continue
            if s.startswith('//') or s.startswith('#') or s.startswith('/*'):
                continue
            if s.endswith(';') and not s.startswith('}'):
                s2 = re.sub(r'//.*', '', s).strip()
                if s2:
                    fields.append(s2)
        if fields:
            structs[name] = {'fields': fields, 'kind': 'struct'}
    return structs


def extract_c_typedefs(clean):
    """
    Extract C typedef declarations (non-struct, non-enum).
    """
    typedefs = {}
    pat = re.compile(
        r'^typedef\s+(?!(?:struct|enum)\b)([^;]+?)\s+(\w+);',
        re.MULTILINE
    )
    for m in pat.finditer(clean):
        existing = m.group(1).strip()
        new_name = m.group(2).strip()
        if '(' in existing:
            fname = re.search(r'\(\s*\*\s*(\w+)\s*\)', m.group(0))
            if fname:
                new_name = fname.group(1)
                existing = 'function pointer'
            else:
                continue
        typedefs[new_name] = existing
    return typedefs


def extract_template_specializations(clean):
    """Extract template class/struct/function definitions.
    Patterns:
      P1: template<...> class/struct Name (with <> specialization)
      P2: template<...> ReturnType name(params) standard functions
      P3: template<...> FUNC_QUALIFIER ReturnType name(params) GLM-style
    """
    specs = []
    seen = set()

    # Pattern 1: template <...> class/struct Name (possibly with <specialization>)
    pat1 = re.compile(
        r'template\s*<[^>]*>\s*(?:\n\s*)?(?:class|struct)\s+(?:\w+\s+)?(\w+)(\s*<[^>]*>)?',
        re.DOTALL
    )
    for m in pat1.finditer(clean):
        name = m.group(1)
        spec = (m.group(2) or '').strip()
        key = name + spec
        if key not in seen:
            seen.add(key)
            items = {'kind': 'template_class', 'name': name,
                     'signature': m.group(0).strip()[:200]}
            if spec:
                items['specialization'] = spec
            specs.append(items)

    # Pattern 2: function templates (standard return types)
    pat2 = re.compile(
        r'template\s*<[^>]*>\s*(?:\n\s*)?(?:inline\s+)?'
        r'(?:void|bool|int|size_t|std::\w+|auto|char|double|float)'
        r'\s+[\*&]?\s*(\w+)\s*\(',
        re.DOTALL
    )
    for m in pat2.finditer(clean):
        name = m.group(1)
        if name in ('if', 'for', 'while', 'switch', 'return', 'static_cast', 'const_cast'):
            continue
        if name not in seen:
            seen.add(name)
            specs.append({'kind': 'template_function', 'name': name,
                         'signature': m.group(0).strip()[:200]})

    # Pattern 3: GLM-style function templates
    # template<...>\nQUALIFIER ReturnType name(params)
    # GLM uses: template<...>\nGLM_FUNC_QUALIFIER return_type name(...)
    # Also catch: template<...>\ntypename\s+name(params) SFINAE
    # Match: template<...> then optional qualifier macro then return type then name(
    pat3 = re.compile(
        r'template\s*<[^>]*>\s*(?:\n\s*)?'  # template<...>
        r'(?:\w+\s+)?'  # optional GLM_FUNC_QUALIFIER or inline
        r'(?:\w+(?:<[^>]*>)?\s+[\*&]?\s*)?'  # return type (possibly template)
        r'(\w+)\s*\(',  # function name(
        re.DOTALL
    )
    for m in pat3.finditer(clean):
        name = m.group(1)
        if name in ('if', 'for', 'while', 'switch', 'return', 'static_cast',
                     'const_cast', 'typedef', 'using', 'typename', 'class',
                     'struct', 'namespace', 'template'):
            continue
        # Skip if already caught by Pattern 2 (duplicate)
        if name not in seen:
            seen.add(name)
            specs.append({'kind': 'template_function', 'name': name,
                         'signature': m.group(0).strip()[:200]})

    return specs
def extract_using(clean):

    """Extract top-level using declarations."""
    usings = {}
    for m in re.finditer(r'^using\s+(\w+)\s*=\s*([^;]+);', clean, re.MULTILINE):
        usings[m.group(1)] = m.group(2).strip()
    return usings

def extract_free_functions(clean):
    """Extract top-level free function signatures.
    Handles both C++ patterns and C-style MACRODEF name(params); declarations.
    """
    funcs = []
    
    # Pattern 1: C-style MACRODEF return_type name(params);
    # (e.g. STBIDEF, STBIWDEF, STBTT_DEF, STBRP_DEF, STBIRDEF, etc.)
    # The macro name is typically [A-Z_]{3,}DEF
    pat_macro = re.compile(
        r'^([A-Z_]{3,}DEF\s+.*?\b(\w+)\s*\(([^;]*?)\)\s*;)\s*$',
        re.MULTILINE | re.DOTALL
    )
    for m in pat_macro.finditer(clean):
        full_sig = m.group(1).strip()
        name = m.group(2)
        # Extract params more carefully
        params_part = m.group(3).strip()
        # Remove newlines from params
        params_part = re.sub(r'\s+', ' ', params_part).strip()
        # Get just the signature up to the name
        funcs.append({
            'signature': full_sig,
            'name': name,
            'params': params_part
        })
    
    # Pattern 2: C++ standard declaration (return_type name(params);)
    type_pattern = (
        r'(?:bool|int|size_t|ssize_t|void|char|double|float|long|unsigned|'
        r'socket_t|std::string|std::pair\s*<[^>]+>|'
        r'std::function\s*<[^>]+>'
        r'|(?:inline|static)\s+(?:bool|int|size_t|void|char|double|float))'
    )
    pat_cpp = re.compile(
        r'^(' + type_pattern + r'\s+[\*&]?\s*(\w+)\s*\(([^)]*)\)\s*(?:const\s*)?;)',
        re.MULTILINE
    )
    for m in pat_cpp.finditer(clean):
        # Deduplicate: skip if already captured by macro pattern
        if any(f['name'] == m.group(2) for f in funcs):
            continue
        funcs.append({
            'signature': m.group(1).strip(),
            'name': m.group(2),
            'params': m.group(3).strip()
        })
    
    return funcs

def extract_classes(text, class_names):
    """Extract public methods from named classes.
    Line-based: find 'class NAME' then extract_class_body, then collect public methods.
    Handles: multi-line inheritance, template<...>, // comments, { on separate lines,
    optional MACRO/API attributes between 'class' and name (e.g. 'class SPDLOG_API logger').
    """
    clean = remove_cpp_comments(text)
    lines = clean.split(chr(10))
    results = {}
    
    for cn in class_names:
        # Find the line with 'class NAME'
        # Allow optional attributes between 'class' and name: class MACRO_NAME OptionalAttr Name
        cls_offset = -1
        for i, line in enumerate(lines):
            stripped = re.sub(r'//.*', '', line)
            pattern = (r'^\s*(?:template\s*<[^>]*>\s*)?' +  # optional template<...>
                       r'(?:class|struct)\s+' +  # class or struct
                       r'(?:\w+\s+)?' +  # optional attribute/macro (e.g. SPDLOG_API)
                       re.escape(cn) + r'\b')  # class name
            if re.match(pattern, stripped):
                cls_offset = sum(len(lines[j]) + 1 for j in range(i))
                break
        
        if cls_offset < 0:
            continue
        
        body = extract_class_body(clean, cls_offset)
        if body is None:
            continue
        
        # Extract public methods from body
        in_public = False
        methods = []
        fields = []
        for line in body.split(chr(10)):
            s = re.sub(r'//.*', '', line).strip()
            if not s:
                continue
            if s == 'public:':
                in_public = True
                continue
            if s in ('private:', 'protected:'):
                in_public = False
                continue
            if s.startswith('}'):
                break
            if not in_public:
                continue
            if s.startswith('#') or s.startswith('//'):
                continue
            if 'operator=' in s:
                continue
            if s.endswith(';') and '(' in s and ')' in s:
                methods.append(s)
                continue
            # Also check for data member fields
            if s.endswith(';') and not s.startswith('#') and not s.startswith('typedef') and 'template' not in s and 'operator' not in s and '(' not in s:
                fields.append(s)
        
        if methods or fields:
            results[cn] = {'methods': sorted(set(methods)), 'fields': sorted(set(fields))}
    
    return results
def extract_namespace_classes(clean, ns_name):
    """Extract classes within a namespace."""
    result = {}
    ns_start = clean.find(f'namespace {ns_name} {{')
    if ns_start < 0:
        return result
    
    ns_end = find_matching_brace(clean, ns_start + len(f'namespace {ns_name}') - 1)
    if ns_end < 0:
        return result
    
    ns_body = clean[ns_start:ns_end + 1]
    
    # Find class-like things in the namespace body
    for m in re.finditer(r'^(class|struct)\s+(\w+)', ns_body, re.MULTILINE):
        cn = m.group(2)
        
        # Find the opening brace
        ob = ns_body.find('{', m.start())
        if ob < 0 or ob > ns_end: continue
        
        cb = find_matching_brace(ns_body, ob)
        if cb < 0: continue
        
        class_body = ns_body[ob+1:cb]
        in_pub = False
        methods = []
        
        for line in class_body.split('\n'):
            s = clean_line(line)
            if not s: continue
            if s == 'public:': in_pub = True; continue
            if s in ('private:', 'protected:'): in_pub = False; continue
            if in_pub and s.endswith(';') and '(' in s and ')' in s:
                methods.append(s)
        
        if methods:
            result[cn] = sorted(set(methods))
    
    return result

def auto_detect_namespace(text):
    """Try to detect the primary namespace from the header."""
    clean = remove_cpp_comments(text)
    # Find namespace definitions (excluding std::, detail::)
    namespaces = []
    for m in re.finditer(r'^namespace\s+(\w+)\s*\{', clean, re.MULTILINE):
        ns = m.group(1)
        if ns not in ('detail', 'std', '__detail', 'internal'):
            namespaces.append(ns)
    return namespaces[0] if namespaces else None

def auto_detect_define_prefix(text):
    """Try to detect the library's config macro prefix."""
    # Look for #define patterns that look like library config
    for m in re.finditer(r'#\s*define\s+(([A-Z]+)_(\w+))', text):
        prefix = m.group(2) + '_'
        # Check how many defines have this prefix
        count = len(re.findall(r'#\s*define\s+' + re.escape(prefix), text))
        if count >= 3:
            return prefix
    return None

def auto_detect_version(text):
    """Try to find version string in header.
    Handles: MAJOR.MINOR.PATCH, VERSION macros, STBI_VERSION-style.
    """
    # Look for numeric version patterns first
    for m in re.finditer(r'#\s*define\s+\w*VERSION\w*\s+(\d+)$', text, re.MULTILINE):
        minor_m = re.search(r'#\s*define\s+\w*VERSION_MINOR\s+(\d+)', text)
        major_m = re.search(r'#\s*define\s+\w*VERSION_MAJOR\s+(\d+)', text)
        if major_m and minor_m:
            return f'{major_m.group(1)}.{minor_m.group(1)}'
        if major_m:
            return f'{major_m.group(1)}.{m.group(1)}'
        return m.group(1)
    for m in re.finditer(r'#\s*define\s+\w*VERSION\w*\s+"([^"]+)"', text):
        return m.group(1)
    for m in re.finditer(r'#\s*define\s+\w*VERSION\w*\s+(\d+\.\d+\.\d+)', text):
        return m.group(1)
    # Skip non-library version strings (e.g. GLSL '#version 150')
    return 'unknown'

# ──────────────────────────────────────────────────────────────────────────────
# SKILL.md Generation
# ──────────────────────────────────────────────────────────────────────────────

def generate_skill(api, library_name, output_path):
    """Generate SKILL.md from api data."""
    md = []
    classes = api.get('classes', {})
    enums = api.get('enums', {})
    free_funcs = api.get('free_functions', [])
    macros = api.get('config_macros', {})
    version = api.get('version', 'unknown')
    namespace = api.get('namespace', '')
    
    ns_prefix = f'{namespace}::' if namespace else ''
    
    # Header
    md.append(f'# {library_name} Skill — AI Agent Reference')
    md.append(f'')
    md.append(f'> **Version:** {version} | Generated from source analysis')
    md.append(f'> **Extracted programmatically.** If an API is not listed, it does not exist.')
    md.append(f'')
    
    # Anti-hallucination placeholder
    md.append('## 🚫 Anti-Hallucination Rules')
    md.append('')
    md.append('> **IMPORTANT:** The following APIs do NOT exist in this library.')
    md.append('> Review before generating code.')
    md.append('')
    md.append('(Add after manual review — see library-analyzer SKILL.md Step 5.3)')
    md.append('')
    
    # Compile-time config
    if macros:
        md.append('## Compile-Time Configuration')
        md.append('')
        md.append('Feature toggles (define before include):')
        md.append('')
        md.append('```cpp')
        for name, val in sorted(macros.items()):
            md.append(f'#define {name} {val}')
        md.append('```')
        md.append('')
    
    # Enums
    if enums:
        md.append('## Enums')
        md.append('')
        for enum_name, enum_data in sorted(enums.items()):
            vals = enum_data.get('values', [])
            if not vals:
                continue
            md.append(f'### {enum_name}')
            md.append('')
            md.append('```cpp')
            show_all = len(vals) <= 15
            for v in vals[:15 if not show_all else len(vals)]:
                if v.get('value'):
                    md.append(f'  {v["name"]} = {v["value"]},')
                else:
                    md.append(f'  {v["name"]},')
            if not show_all:
                md.append(f'  // ... ({len(vals)} total values)')
            md.append('```')
            md.append('')
    
    # Type aliases
    using_data = api.get('type_aliases', {})
    if using_data:
        md.append('## Type Aliases')
        md.append('')
        md.append('```cpp')
        for name, type_val in sorted(using_data.items()):
            md.append(f'using {name} = {type_val};')
        md.append('```')
        md.append('')
    
    # C structs
    c_structs = api.get('c_structs', {})
    if c_structs:
        md.append('## C Structs')
        md.append('')
        for struct_name, struct_data in sorted(c_structs.items()):
            fields = struct_data.get('fields', [])
            md.append(f'### {struct_name}')
            md.append('')
            md.append('```c')
            for f in fields:
                md.append(f'  {f}')
            md.append('```')
            md.append('')

    # C typedefs
    c_typedefs = api.get('c_typedefs', {})
    if c_typedefs:
        md.append('## C Typedefs')
        md.append('')
        md.append('| New name | Existing type |')
        md.append('|---|---|')
        for name, existing in sorted(c_typedefs.items()):
            md.append(f'| {name} | {existing} |')
        md.append('')

    # Classes
    if classes:
        md.append('## API Reference')
        md.append('')
        for cls_name, cls_data in sorted(classes.items()):
            # Support both list format (legacy) and dict format (new with fields)
            if isinstance(cls_data, dict):
                methods = cls_data.get('methods', [])
                fields = cls_data.get('fields', [])
            elif isinstance(cls_data, list):
                methods = cls_data
                fields = []
            else:
                methods = cls_data if isinstance(cls_data, list) else cls_data.get('public_methods', [])
                fields = []
            if not methods and not fields:
                continue
            
            md.append(f'### {ns_prefix}{cls_name}')
            md.append('')
            
            if methods:
                md.append('Methods:')
                md.append('')
                md.append('```cpp')
                for m in methods:
                    md.append(f'  {m}')
                md.append('```')
                md.append('')
            
            if fields:
                md.append('Fields:')
                md.append('')
                md.append('```cpp')
                for f in fields:
                    md.append(f'  {f}')
                md.append('```')
                md.append('')
    
    # Free functions
    # Template specializations
    tpl_specs = api.get('template_specializations', [])
    if tpl_specs:
        md.append('## Template Specializations')
        md.append('')
        by_kind = {}
        for t in tpl_specs:
            k = t.get('kind', 'unknown')
            if k not in by_kind:
                by_kind[k] = []
            by_kind[k].append(t)
        for kind, items in sorted(by_kind.items()):
            md.append(f'### {kind}s')
            md.append('')
            md.append('```cpp')
            for item in sorted(items, key=lambda x: x['name']):
                md.append(f'  {item["signature"]}')
            md.append('```')
            md.append('')

    if free_funcs:
        md.append('## Free Functions')
        md.append('')
        md.append('```cpp')
        for f in free_funcs:
            md.append(f['signature'])
        md.append('```')
        md.append('')
    
    # Compilation
    md.append('## Compilation')
    md.append('')
    md.append('(Add after manual review)')
    md.append('')
    md.append('| Mode | Command/Link flags |')
    md.append('|---|---|')
    md.append('| Minimal | (add compile instructions) |')
    md.append('')
    
    # Version
    md.append('## Version')
    md.append('')
    md.append(f'`{version}`')
    md.append('')
    total_apis = sum(len(c) if isinstance(c, list) else len(c.get('public_methods', [])) for c in classes.values())
    md.append(f'*{len(classes)} classes, {total_apis} methods, {len(free_funcs)} free functions catalogued.*')
    
    output = '\n'.join(md)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output)
    
    return output

# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def auto_detect_key_classes(text):
    """Find likely-public class names by looking for 'class X' at file scope.
    Skips class names that look like macros (ALL_CAPS).
    """
    clean = remove_cpp_comments(text)
    classes = []
    for m in re.finditer(
        r'^\s*(?:template\s*<[^>]*>\s*)?(?:class|struct)\s+'  # optional template + class keyword
        r'(?:\w+\s+)?'  # optional attribute/macro (SPDLOG_API, etc.)
        r'(\w+)',  # actual class name
        clean, re.MULTILINE
    ):
        name = m.group(1)
        # Skip internal/detail classes
        if any(skip in name.lower() for skip in ['detail', 'helper', 'internal', 'impl', 'private', 'factory']):
            continue
        # Skip ALL_CAPS macro-looking "classes" (length > 4 to preserve short names like Mat, UMat)
        if name == name.upper() and len(name) > 4:
            continue
        # Only include if actual class body follows (not forward declaration)
        pos = m.end()
        rest = clean[pos:pos+100]
        if '{' in rest:
            classes.append(name)
    return classes

def main():
    parser = argparse.ArgumentParser(
        description='Analyze C/C++ library and generate AI skill document',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    # Source
    parser.add_argument('--header', help='Path to main header file')
    parser.add_argument('--include-dir', help='Include directory for multi-header libs')
    parser.add_argument('--header-pattern', default='*.h',
                        help='Header glob pattern (default: *.h)')
    
    # Output
    parser.add_argument('--name', required=True, help='Library display name')
    parser.add_argument('--out', default='.', help='Output directory')
    parser.add_argument('--json', help='Path for api_map.json (default: <out>/api_map.json)')
    
    # Overrides
    parser.add_argument('--namespace', help='C++ namespace (auto-detected if omitted)')
    parser.add_argument('--define-prefix', help='Config macro prefix (auto-detected)')
    parser.add_argument('--manual', action='store_true',
                        help='Generate template JSON only (for manual fill)')
    
    args = parser.parse_args()
    
    # Validate
    if not args.header and not args.include_dir:
        parser.error('Either --header or --include-dir is required')
    
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    json_path = args.json or str(out_dir / 'api_map.json')
    skill_path = str(out_dir / 'SKILL.md')
    
    # Read headers
    all_text = ''
    headers_read = []
    
    if args.header:
        with open(args.header, 'r', encoding='utf-8', errors='replace') as f:
            all_text = f.read()
        headers_read.append(args.header)
    
    if args.include_dir:
        pattern = os.path.join(args.include_dir, '**', args.header_pattern)
        for h in sorted(glob.glob(pattern, recursive=True)):
            # Skip test/ and deprecated/ subdirectories
            rel = os.path.relpath(h, args.include_dir)
            if rel.startswith('deprecated' + os.sep) or rel.startswith('tests' + os.sep):
                continue
            # Skip auto-generated protobuf .pb.h files (can be 1MB+)
            base = os.path.basename(h)
            if base.endswith('.pb.h'):
                continue
            # Skip test and unittest headers (likely internal)
            if 'test' in base.lower() or 'unittest' in base.lower():
                continue
            with open(h, 'r', encoding='utf-8', errors='replace') as f:
                all_text += '\n' + f.read()
            headers_read.append(h)
    
    if not all_text:
        parser.error('No headers found')
    
    print(f'Read {len(headers_read)} header(s):')
    for h in headers_read:
        print(f'  {h}')
    print(f'  Total: {len(all_text)} chars')
    
    # Auto-detect
    ns = args.namespace or auto_detect_namespace(all_text) or ''
    prefix = args.define_prefix or auto_detect_define_prefix(all_text) or ''
    version = auto_detect_version(all_text)
    
    print(f'\nAuto-detected: namespace={ns or "(none)"}, define_prefix={prefix or "(none)"}, version={version}')
    
    # Clean for certain extraction steps
    clean = remove_cpp_comments(all_text)
    
    # Extract
    print('\nExtracting macros...')
    macros = extract_macros(all_text, prefix) if prefix else {}
    print(f'  {len(macros)} macros')
    
    print('Extracting enums...')
    enums = extract_enums(clean)
    print(f'  {len(enums)} enums')

    # C-specific extractions
    print('Extracting C structs...')
    c_structs = extract_c_structs(clean)
    print(f'  {len(c_structs)} structs')
    print('Extracting C typedefs...')
    c_typedefs = extract_c_typedefs(clean)
    print(f'  {len(c_typedefs)} typedefs')
    
    print('Extracting type aliases...')
    usings = extract_using(clean)
    print(f'  {len(usings)} aliases')
    
    print('Extracting classes...')
    key_classes = auto_detect_key_classes(clean)
    print(f'  Detected {len(key_classes)} candidate classes')
    if len(key_classes) > 50:
        print(f'  Too many classes ({len(key_classes)}), showing first 50:')
    else:
        for c in key_classes[:50]:
            print(f'    {c}')
    
    classes = extract_classes(all_text, key_classes)
    print(f'  Extracted: {len(classes)} classes with methods')
    for name, methods in sorted(classes.items()):
        print(f'    {name}: {len(methods)} methods')
    
    print('Extracting template specializations...')
    tpl_specs = extract_template_specializations(clean)
    print(f'  {len(tpl_specs)} template specs')

    print('Extracting free functions...')
    funcs = extract_free_functions(clean) if not args.manual else []
    print(f'  {len(funcs)} free functions')
    
    # Namespace classes
    namespace_classes = {}
    if ns:
        print(f'Extracting {ns} namespace classes...')
        namespace_classes = extract_namespace_classes(clean, ns)
        for name, methods in namespace_classes.items():
            if name not in classes:
                classes[name] = methods
    
    # Build output API data
    if args.manual:
        api_data = {
            'version': version,
            'namespace': ns,
            'config_macros': {},
            'enums': {},
            'c_structs': {},
            'c_typedefs': {},
            'type_aliases': {},
            'classes': {},
            'free_functions': [],
            'template_specializations': [],
            '_manual_fill': {
                'instructions': 'This is a template. Fill in each section manually from the source code.',
                'auto_detected_prefix': prefix,
                'auto_detected_namespace': ns,
                'candidate_classes': key_classes
            }
        }
        print('\nGenerating manual-fill template...')
    else:
        api_data = {
            'version': version,
            'namespace': ns,
            'config_macros': dict(sorted(macros.items())),
            'enums': enums,
        'c_structs': c_structs,
        'c_typedefs': c_typedefs,
            'type_aliases': usings,
            'classes': classes,
            'template_specializations': tpl_specs,
        'free_functions': funcs,
        }
    
    # Write JSON
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(api_data, f, indent=1, ensure_ascii=False)
    print(f'\nWrote: {json_path}')
    
    # Generate SKILL.md
    if not args.manual:
        print('Generating SKILL.md...')
        generate_skill(api_data, args.name, skill_path)
        print(f'Wrote: {skill_path}')
        
        # Summary
        total_methods = sum(len(c) if isinstance(c, list) else (len(c.get('methods', [])) if isinstance(c, dict) else len(c.get('public_methods', []))) for c in classes.values())
        total_fields = sum(len(c.get("fields", [])) for c in classes.values() if isinstance(c, dict))
        print(f'\n{"="*50}')
        print(f'SUMMARY')
        print(f'{"="*50}')
        print(f'  Library:  {args.name}')
        print(f'  Version:  {version}')
        print(f'  Namespace:{ns or "(none)"}')
        if total_fields:
            print(f'  Classes:  {len(classes)} ({total_methods} methods, {total_fields} fields)')
        else:
            print(f'  Classes:  {len(classes)} ({total_methods} methods)')
        print(f'  Enums:    {len(enums)}')
        print(f'  Structs:  {len(c_structs)}')
        print(f'  Typedefs: {len(c_typedefs)}')
        print(f'  Templates:{len(tpl_specs)}')
        print(f'  Functions:{len(funcs)}')
        print(f'  Macros:   {len(macros)}')
        print()
        print('NEXT STEPS:')
        print('  1. Review SKILL.md for accuracy')
        print('  2. Add anti-hallucination rules (see library-analyzer SKILL.md §5.3)')
        print('  3. Add compilation instructions')
        print('  4. Add common patterns & examples')
        print(f'\nDone.')
    else:
        print(f'\nManual fill template generated.')
        print(f'1. Fill in api_map.json with extracted APIs')
        print(f'2. Run: python analyze_library.py --json {json_path} --name "{args.name}" --out {out_dir}')

if __name__ == '__main__':
    main()
