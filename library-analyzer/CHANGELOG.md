# CHANGELOG — library-analyzer

**变更记录** — 从 cpp-httplib 初始分析到通用 C/C++ 库分析器 v1.3.0

---

## v1.3.0 — 2026-05-26

### 新增
- **模板函数 Pattern 3（GLM 风格）**: 匹配 `GLM_FUNC_QUALIFIER` / 宏限定符修饰的函数模板，支持 vec/mat/qua 作为返回类型的自由函数模板
- **类/结构体字段提取**: `extract_classes` 现在同时收集数据成员字段（`T x;` `int count;`），输出为 `{'methods': [...], 'fields': [...]}` 格式
- **模板偏特化参数捕获**: Pattern 1 支持 `struct vec<2, T, Q>` 的 `<...>` 偏特化参数额外捕获

### 测试验证
- ✅ glm v1.0.0: 576 模板特化 (138 类 + 438 函数), 10 字段
- ✅ shadowsocks-libev v3.3.6: 95 函数, 32 struct (纯 C)

---

## v1.2.0 — 2026-05-26

### 修复
- **缩进空格**: 类定义行前有缩进时，`^` 锚点不匹配。加 `\s*` 后修复（cereal 0→162 candidate 类）
- **版本号错误**: `auto_detect_version` 始终返回 `MAJOR.MAJOR`。修正为 `major_m.minor_m`
- **原始字符串 `\\s` 翻倍**: 多行 regex 拼接后反斜杠翻倍。改用单行 regex 避免拼接
- **模板跨行不匹配**: `template<...>\nclass Name` 需要 `(?:\n\s*)?` 模式

### 新增
- `extract_template_specializations()`: 识别模板类/函数特化

### 测试验证
- ✅ fmt v12.1.1: 35 类, 248 模板, 34 枚举
- ✅ protobuf v7.36-dev: 219 类, 974 方法, 96 枚举, 258 函数（目前最大提取）

---

## v1.1.0 — 2026-05-26

### 修复
- **`//` 注释内 `"` 破坏 brace 匹配**: 添加内联 `//` 跳过逻辑（nlohmann/json `basic_json` 类 body 正确提取 6784 字符）
- **模板类 `{` 不在同一行**: 从类行锚定改为行锚定 + 向后扫描 `{`
- **`class MACRO Name` 模式**: 添加 `(?:\w+\s+)?` 跳过导出宏（spdlog, OpenCV, protobuf）

### 新增
- C enum Pattern 3: `enum { ... };` 匿名枚举支持
- 自动跳过 `*.pb.h`（1MB+ 描述符文件）
- 自动跳过 `*test*` / `*unittest*` 头文件

### 测试验证
- ✅ OpenCV core v4.6.050: 70 类, 593 方法, 133 枚举, 250 宏
- ✅ protobuf v7.36-dev: 首次运行（251 非 pb 头文件）

---

## v1.0.3 — 2026-05-26

### 修复
- **`\s+` 在 `#define` 中吞跨行**: 用 `[ \t]*` 替代 `\s+`（stb 宏从 5→149）
- **C enum 不匹配 `enum\n{`**: 添加 Pattern 3 匿名枚举模式（stb 枚举从 1→39）
- **ALL_CAPS 3 字母类名被错误过滤**: 阈值从 `>2` 改为 `>4`（修复 OpenCV `Mat` 类）

### 新增
- **C 函数 MACRODEF 模式**: `STBIDEF type name(params)` 模式提取（stb 函数从 50→340）
- **`extract_c_structs()`**: C struct 定义提取（`typedef struct { } Name;` + `struct Name { };`）
- **`extract_c_typedefs()`**: 非 struct/enum 的 C typedef 提取（含函数指针）
- **`generate_skill()` C 支持**: 新增 **C Structs** 和 **C Typedefs** 章节
- 自动跳过 `deprecated/` 和 `tests/` 目录

### 测试验证
- ✅ stb: 91 struct, 319 函数, 99 宏, 39 枚举（纯 C 首次完整验证）

### 关键 Bug 记录
| Bug | 原因 | 影响 | 修复 |
|-----|------|------|------|
| B001 | `#define` 的 `\s+` 匹配 | 宏值丢失 | `[ \t]*` |
| B002 | `' const' in method_string` | 带 const 参数的方法被过滤 | 细化过滤器 |
| B003 | `//` 注释内引号 | brace 匹配失败 | 添加 `//` 跳过 |
| B004 | 模板类 `{` 不同行 | 类匹配失败 | 行锚定扫描 |
| B005 | `class MACRO Name` 模式 | 类名被当宏过滤 | `(?:\w+\s+)?` |
| B006 | C 匿名枚举 | 枚举从 1→39 | Pattern 3 |
| B007 | `*.pb.h` 1MB+ | 解析超时 | 自动跳过 |
| B008 | C MACRODEF 函数 | 函数从 0→340 | C 函数模式 |
| B009 | ALL_CAPS 类名 `Mat` | 3 字母类名丢失 | 阈值 `>2`→`>4` |
| B010 | 版本 `MAJOR.MAJOR` | cereal 1.1 应为 1.3 | 修正 `minor_m` |
| B011 | 缩进空格 | 类检测 0→162 | `^` 后加 `\s*` |

---

## v1.0.0 — 2026-05-26

初始版本。基于 cpp-httplib v0.46.0 源码手动构建。

### 核心设计
- 语言: Python 3 (regex 驱动的 C/C++ 解析器)
- 核心提取: 类方法/枚举/宏/类型别名/自由函数
- 输出: `api_map.json` + `SKILL.md`

### 功能
- 类定义检测（class/struct 关键字）
- 继承关系识别
- public/private/protected 区段分隔
- 方法签名提取（含跨行累积）
- `#define` 宏提取
- `enum` / `enum class` 提取
- `using` 类型别名提取
- 自由函数提取
- namespace 嵌套类提取
- 版本号自动检测
- `--include-dir` / `--header` 双模式
- `--manual` 模板模式

### 测试验证
- ✅ cpp-httplib v0.46.0: 33 类, 136 方法, 48 宏
- ⚠️ nlohmann/json v3.12.0: 25 类, 52 方法（模板密集, 部分提取）
- ⚠️ spdlog v2.0.0: 21 类, 64 方法

---

## 技术债务（未解决）

1. **模板偏特化主体提取**: `struct vec<L,T,Q>` 被识别为模板类但主体未完全解析（GLM 196 候选只有 3 类有方法）
2. **元模板元函数**: `enable_if`, `void_t`, SFINAE 类型未被提取为 API
3. **`*.cpp` 源中的函数**: 仅从头文件提取，实现在 `.cpp` 中的函数遗漏
4. **内联函数 `inline`**: 头文件中 `inline` 实现的函数可能被遗漏
5. **20+ 命名空间递归**: 当前只检查一层 namespace 嵌套
6. **`consteval` / `consteval if`**: C++20 模式需额外处理
7. **自动 `#include` 解析**: 当前不解析头文件依赖链，需用户手动指定

---

## 测试覆盖率（10 库基准）

| 库 | 版本 | 语言 | 类 | 函数 | 宏 | 枚举 | struct | 模板 |
|---|------|------|----|------|-----|------|--------|------|
| cpp-httplib | 0.46.0 | C++ | 21 | 86 | 53 | 13 | — | — |
| nlohmann/json | 3.12.0 | C++ | 25 | 3 | 125 | 6 | — | — |
| spdlog | 2.0.0 | C++ | 21 | 0 | 34 | 3 | — | — |
| OpenCV(core) | 4.6.050 | C++ | 70 | 9 | 250 | 133 | 41 | — |
| protobuf | 7.36-dev | C++ | 219 | 258 | 49 | 96 | 83 | — |
| cereal | 1.3.2 | C++ | 13 | 0 | 113 | 9 | — | 129 |
| fmt | 12.1.1 | C++ | 35 | 2 | 137 | 34 | 20 | 248 |
| glm | 1.0.0 | C++ | 3 | 0 | 221 | 4 | 1 | 576 |
| stb | — | C | — | 319 | 99 | 39 | 91 | — |
| shadowsocks-libev | 3.3.6 | C | — | 95 | 6 | 2 | 32 | — |
| **合计** | | | **407** | **772** | **1087** | **339** | **268** | **953** |

---

*Generated from iterative analysis of 10 C/C++ libraries. Last updated 2026-05-26.*
