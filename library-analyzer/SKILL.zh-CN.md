# 库分析器 Skill — AI 智能体参考文档

> **版本:** v1.3.0 (2026-05-26) | `analyze_library.py` — 20 个函数, ~1000 行
> **已验证:** 10 个 C/C++ 库, 累计提取 407 类 / 772 函数 / 1087 宏 / 339 枚举 / 268 struct / 953 模板
> **详见:** [`CHANGELOG.md`](./CHANGELOG.md)

**目标:** 为任意 C/C++ 依赖库生成防幻觉的 skill 文档。
**流程:** 源码 → `api_map.json`（结构化 API 数据）→ `SKILL.md`（AI 智能体参考文档）
**作用:** 通过将 AI 智能体锚定在精确的库 API 上，消除代码生成中的 API 幻觉。

---

## 1. 触发条件

当用户发出类似以下指令时：

> "分析库 X，路径在 Y"
> "为库 X 生成 AI 参考文档"
> "Validate library X and create a skill doc for it"

或者在代码生成过程中，AI 检测到引用了某个尚无 skill 文档的依赖库时。

## 2. 工作流程概览

```
步骤 1: DISCOVER   — 理解库的结构（单头文件？多头文件？构建系统？）
步骤 2: EXTRACT    — 程序化解析头文件 → api_map.json
步骤 3: ENRICH     — 识别分类、模式、调用链路、反模式
步骤 4: GENERATE   — 从模板 + 增强数据渲染 SKILL.md
步骤 5: VALIDATE   — 对照源码抽查关键方法
```

---

## 3. 步骤 1: DISCOVER — 库结构分析

确定库的结构以选择提取策略：

| 特征                               | 策略                                 | 示例                                  |
| ---------------------------------- | ------------------------------------ | ------------------------------------- |
| 单 `.h` 文件（header-only）        | 直接解析一个文件                     | `httplib.h`, `json.hpp`, `fmt/core.h` |
| 多 `.h`/`.hpp` 文件（header-only） | 解析所有公开头文件；跳过实现头文件   | `asio/`, `boost/`                     |
| `.h` + `.cpp`（编译库）            | 仅解析公开头文件；记录链接标志       | `libcurl`, `openssl`                  |
| CMake/PkgConfig 项目               | 解析头文件；从 CMakeLists 提取配置宏 | Catch2, spdlog                        |

### 发现命令

```bash
# 库结构
ls <库根目录>/          # 顶层文件
ls <库根目录>/include/  # 公开头文件（常见模式）
find <库根目录> -name "*.h" -o -name "*.hpp" 2>/dev/null | head -30

# 构建系统
ls <库根目录>/CMakeLists.txt 2>/dev/null && echo "CMake 项目"
ls <库根目录>/Makefile 2>/dev/null && echo "Makefile 项目"
ls <库根目录>/meson.build 2>/dev/null && echo "Meson 项目"

# 版本检测
grep -rn "VERSION\|version" <库根目录>/include/*.h 2>/dev/null | head -5
grep -rn "VERSION\|version" <库根目录>/CMakeLists.txt 2>/dev/null | head -5
```

### 输出

将以下信息记录到发现 JSON 中：

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

## 4. 步骤 2: EXTRACT — 程序化 API 解析

### 4.1 提取脚本

使用配套脚本 `analyze_library.py`：

```bash
# 基础：单头文件库
python analyze_library.py --header <头文件路径> --name <库名> --out <输出目录>

# 带命名空间
python analyze_library.py --header <路径> --name <名称> --out <目录> --namespace <ns>

# 多头文件：指定文件夹
python analyze_library.py --include-dir <包含目录路径> --name <名称> --out <目录> [--header-pattern "*.hpp"]

# 自定义宏前缀（用于配置宏提取）
python analyze_library.py --header <路径> --name <名称> --out <目录> --define-prefix MYLIB_

# 指定输出 JSON（默认：输出目录下的 api_map.json）
python analyze_library.py --header <路径> --name <名称> --out <目录> --json <JSON路径>
```

### 4.2 脚本提取的内容

脚本生成 `api_map.json`，包含以下部分：

| 章节             | 内容                      | 来源                           |
| ---------------- | ------------------------- | ------------------------------ |
| `version`        | 库版本号                  | `#define *_VERSION` 或自动检测 |
| `config_macros`  | 所有 `#define *_*` 宏     | 头文件 `#define` 扫描          |
| `enums`          | `enum class` 和 `enum` 值 | 直接枚举提取                   |
| `type_aliases`   | `using X = Y;` 顶层声明   | 顶层 `using` 提取              |
| `structs`        | 公开结构体字段 + 方法     | 结构体主体解析                 |
| `classes`        | 类的公开方法（按类分组）  | 类主体解析（含区段追踪）       |
| `free_functions` | 顶层函数签名              | 基于正则的函数匹配             |
| `namespaces`     | 嵌套命名空间类/函数       | 命名空间块提取                 |

### 4.3 提取规则（脚本自身的防幻觉）

**提取脚本必须：**

- 使用 `[ \t]+` 而非 `\s+` 来分隔 `#define 名称 值`——`\s` 会匹配换行导致跨行捕获（这是一个真实 bug）
- 匹配 `{`/`}` 大括号时需考虑字符串/字符字面量，以正确处理 C++ 模板尖括号
- 跳过类中的 `private:`/`protected:` 区段
- 在公开方法列表中跳过 `operator=` 和析构函数重载
- 报告每个类提取的项目数量，用于人工验证

**重要的正则模式：**

```python
# DEFINE — 仅使用空格/制表符避免换行捕获
r'#\s*define\s+(NAME_PREFIX\w+)[ \t]+(.*)'

# CLASS / STRUCT 起始
r'^(?:class|struct)\s+(\w+)(?:\s+final)?(?:\s*:\s*public\s+\w+(?:\s*,\s*public\s+\w+)*)?\s*\{'

# 匹配大括号 — 处理嵌套大括号、字符串/字符字面量
# 追踪深度；跳过 "...", '...' 内部内容

# 公开方法（类主体后以 ; 结尾的行）
'public:' 之后直到 'private:'/'protected:'/结束之间的行
```

### 4.4 回退：手动提取

如果脚本因特殊的 C++ 结构（模板密集、大量预处理器使用）而失败，回退到：

1. 使用脚本的 `--manual` 标志生成模板 JSON
2. 通过阅读源码手动填充模板
3. 在填充好的模板上运行生成脚本

---

## 5. 步骤 3: ENRICH — 模式分析与知识整理

提取后，用以下内容丰富原始 API 数据：

### 5.1 功能分类

将类与函数分组到功能类别中：

| 类别      | 包含                                     | 示例（cpp-httplib）   |
| --------- | ---------------------------------------- | --------------------- |
| 核心类型  | Request, Response, Stream, Result        | HTTP 消息类型         |
| 服务端    | Server, SSLServer                        | 路由处理器、配置      |
| 客户端    | Client, ClientImpl, SSLClient            | HTTP 方法、认证、代理 |
| 流式传输  | DataSink, ContentProvider, ContentReader | 分块传输              |
| WebSocket | ws::WebSocket, ws::WebSocketClient       | 双向通信              |
| SSE       | sse::SSEClient                           | 服务端推送事件        |
| TLS       | tls::PeerCert, tls::VerifyContext        | 证书处理              |
| 配置      | 所有编译期宏                             | 功能开关、超时设置    |

### 5.2 调用链路分析

对于每个类别，追踪**典型调用链路**——当用户 API 被调用时库内部执行的操作：

```markdown
模式: Server::listen → bind → accept → parse request → route → handler → write response
  - svr.listen("0.0.0.0", 8080) 创建监听 socket
  - Accept 循环调用 process_and_close_socket 处理每个连接
  - routing() 匹配模式 → dispatch_request() 调用处理器
  - write_response() 发送回复
```

这有助于 AI 智能体理解架构，避免生成不可能的调用序列。

### 5.3 防幻觉规则

审查库后，为最常见的错误 API 模式创建特定规则：

**黑名单来源：**

- 该库中**不存在**的常见 C++ Web 框架模式（Express/Flask 风格路由？）
- 与相似库的模式混淆（Boost.Beast vs cpp-httplib）
- 听起来应该存在但实际上没有的方法（`set_body()`, `on_message()`）
- 类似但略有错误的 API 命名（`encode_uri` vs `url_encode`）

**格式：**

```markdown
### 不要生成以下 API — 它们在 [LibraryName] 中不存在

| 错误（幻觉） | 正确替代 |
|---|---|
| `Response::set_body(str)` | `res.set_content(str, ct)` |
| ... | ... |
```

### 5.4 编译分析

从发现阶段生成：

```markdown
| 模式 | 链接标志 |
|------|---------|
| 最小 | (无 — header-only) |
| +FeatureX | `-lfoo` |
| +FeatureY | `-lbar` |
```

---

## 6. 步骤 4: GENERATE — SKILL.md 渲染

### 6.1 生成脚本

此 skill 包含的 `generate_skill.py` 读取 `api_map.json` 并生成格式化的 SKILL.md：

```bash
# 基础生成
python generate_skill.py --json <api_map.json> --out <SKILL.md>

# 带增强数据
python generate_skill.py \
  --json <api_map.json> \
  --out <SKILL.md> \
  --categories <categories.json> \   # 功能分类
  --patterns <patterns.json> \       # 调用链路/常见模式
  --blacklist <blacklist.json>       # 防幻觉规则

# 带库特定模板覆盖
python generate_skill.py --json <api_map.json> --out <SKILL.md> --template <自定义模板.md>
```

### 6.2 输出结构

每个生成的 SKILL.md 遵循以下模板：

```
# [库名] Skill — AI 智能体参考文档
版本信息 | 源码链接 | Token 预估

## 🚫 防幻觉规则
### 不要生成以下 API — 它们不存在
表格: 错误 API → 正确替代
### 架构约束（例如阻塞 I/O, HTTP 版本）

## 编译期配置
功能开关 | 运行时默认值

## 枚举
紧凑列表（截断 StatusCode 等大型枚举为常用值）

## 类型别名
using 声明的紧凑列表

## 核心类型
关键类/结构体及其方法签名

## [功能分类...]
每个主要功能组：
  - 类 API 签名（紧凑）
  - 重度重载方法的参数矩阵
  - 快速模式 / 示例

## 编译
表格: 模式 → 链接标志

## 常见模式与调用链路
非显而易见的用法模式

## 版本
精确的库版本
```

### 6.3 Token 优化规则

生成 markdown 时，应用以下压缩规则：

1. **使用参数矩阵替代列出所有重载**
   - 按内容体类型分组方法（body, params, multipart 等）
   - 显示可选维度（headers, receiver, progress）作为注释
   - 文本：`Method(path, [headers?], [body...], [receiver?], [progress?])`
   - 列出 body 变体：no body, raw bytes, content provider, params, multipart 等

2. **截断大型枚举**（>15 个值）：仅显示常用值，添加 "见源码获取完整列表"

3. **使用表格展示编译选项** 替代重复的 bash 代码块

4. **最小化代码块**：对于 API 列表，偏好缩进签名而非代码围栏

5. **合并重复 API**：如果类 B 委托给类 A（如 Client→ClientImpl），仅列出一个并附带委托说明

6. **去除叙事性赘余**：调用链路最多 3 行，不要整段描述

---

## 7. 步骤 5: VALIDATE — 精度验证

### 7.1 自动化检查

针对生成的 JSON 运行以下检查：

```bash
# 检查：JSON 中的每个方法是否都出现在源码中？（往返测试）
python -c "
import json, re
src = open('header.h').read()
api = json.load(open('api_map.json'))
missing = []
for cls_name, cls_data in api['classes'].items():
    for m in cls_data['public_methods']:
        name = m.split('(')[0].split()[-1]
        if name not in src:
            missing.append(f'{cls_name}::{name}')
if missing:
    print('缺失的方法（可能的提取错误）：')
    for m in missing: print(f'  {m}')
"
```

### 7.2 人工抽查

对比源码验证每个主要类中至少 3 个方法：

- 构造函数签名（参数数量与类型）
- 方法名及其返回类型
- 参数默认值

### 7.3 常见提取错误（检查表）

| 错误                      | 症状                     | 修复                                               |
| ------------------------- | ------------------------ | -------------------------------------------------- |
| define 正则中的 `\s+`     | 宏值丢失                 | 改用 `[ \t]+`                                      |
| 模板 <...> 中的嵌套大括号 | 类/结构体提取中断        | 用大括号计数 + 字符串/字符跳过追踪深度             |
| `#ifdef` 块内的 `public:` | 方法归属到错误类         | 处理预处理器条件编译                               |
| 带 `const` 参数的方法     | 被误过滤为"拷贝构造函数" | 仅在方法末尾过滤 `const`，而非中间位置             |
| 多行方法签名              | 方法缺失                 | 累加行直到找到 `;`                                 |
| `enum class X : type`     | 解析失败                 | 正则：`enum\s+(?:class\s+)?(\w+)\s*(?::\s*(\w+))?` |

---

## 8. 完整管道脚本

此 skill 附带 `analyze_library.py`，它将步骤 2+4 封装为单个命令：

```bash
# 一键操作：分析 + 生成
python analyze_library.py --header <路径> --name <名称> --out <输出目录>

# 这会生成：
#   <输出目录>/api_map.json
#   <输出目录>/SKILL.md
```

脚本会：

1. 解析头文件并提取 API 结构
2. 自动检测版本、命名空间、define 前缀
3. 应用压缩规则（参数矩阵、枚举截断）
4. 生成 markdown skill 文档
5. 写入验证报告

### 脚本选项

| 选项               | 默认值               | 描述                        |
| ------------------ | -------------------- | --------------------------- |
| `--header`         | 必填                 | 主头文件路径                |
| `--include-dir`    | (禁用)               | 多头文件库的包含目录路径    |
| `--name`           | 必填                 | 库显示名称                  |
| `--out`            | 当前目录             | SKILL.md 和 JSON 的输出目录 |
| `--json`           | `<out>/api_map.json` | JSON 数据的输出路径         |
| `--namespace`      | (自动检测)           | 库的 C++ 命名空间           |
| `--define-prefix`  | (自动检测)           | 配置宏的前缀                |
| `--header-pattern` | `*.h`                | 多头文件库的 glob 模式      |
| `--manual`         | false                | 生成模板 JSON 供手动填充    |

---

## 9. 复用：已知库配置

已分析库的预配置参数（随分析不断添加）：

| 库            | 配置参数                                                   |
| ------------- | ---------------------------------------------------------- |
| cpp-httplib   | `--define-prefix CPPHTTPLIB_` `--namespace httplib`        |
| nlohmann/json | `--namespace nlohmann` `--single-header`                   |
| spdlog        | `--namespace spdlog` `--include-dir include/spdlog`        |
| fmt           | `--namespace fmt` `--header-pattern "*.h"`                 |
| protobuf      | `--namespace google::protobuf` `--define-prefix PROTOBUF_` |
| glm           | `--namespace glm` `--define-prefix GLM_`                   |
| cereal        | `--namespace cereal` `--define-prefix CEREAL_`             |

---

## 10. 故障排除

| 问题           | 可能原因                               | 解决方案                             |
| -------------- | -------------------------------------- | ------------------------------------ |
| 提取到 0 个类  | 正则未匹配 `class X {`                 | 检查类是否使用 `__declspec` 或宏前缀 |
| 方法缺少 const | 解析器混淆了 `const` 参数与 const 方法 | 更新提取逻辑中的 const 过滤器        |
| 一半的方法缺失 | `#ifdef` 块分割了类                    | 跨预处理器边界提取                   |
| 枚举值为空     | 枚举与 `//` 注释在同一行               | 解析枚举前去除行注释                 |
| 未找到版本     | 版本存储在 CMakeLists.txt 而非头文件   | 手动提供 `--version`                 |

## 11. 已知局限（来自真实世界测试）

| 局限                          | 示例库                     | 影响                        | 变通方案                                     |
| ----------------------------- | -------------------------- | --------------------------- | -------------------------------------------- |
| 模板密集类未找到              | nlohmann/json `basic_json` | 主类缺失                    | 使用 `--manual` 模式；手动填充模板           |
| 内联 `//` 注释包含 `"` 大括号 | 任意库                     | 大括号匹配在嵌入引号处中断  | `find_matching_brace` 现在自动跳过 `//` 注释 |
| 前向声明干扰检测              | 任何先前向声明再定义的类   | 早期 `{` 触发错误的主体结束 | 检测现在扫描类行后的 `{`，而非同一行         |
| 多行方法签名                  | SFINAE 密集的库            | 仅捕获第一行                | 累加行直到找到 `;`                           |
| 带反斜杠续行的 `#define`      | 平台库                     | 宏值缺失                    | 非自动处理；使用手动填充                     |
| 与 `//` 注释在同一行的枚举值  | spdlog, fmt                | 值中包含残留 `//` 文本      | 枚举解析器在提取前去除 `//`                  |

---

**已验证库：**

- cpp-httplib v0.46.0 — 33 类, 136 方法, 52 配置宏 ✅
- nlohmann/json v3.12.0 — 25 类, 52 方法, 125 配置宏 ⚠️ (basic_json 需手动补充)
- spdlog v2.0.0 — 21 类, 64 方法, 34 配置宏 ✅
- stb — 91 struct, 319 函数, 99 宏, 39 枚举 ✅
- OpenCV (core) v4.6.050 — 70 类, 593 方法, 133 枚举 ✅
- protobuf v7.36-dev — 219 类, 974 方法, 96 枚举, 258 函数 ✅
- cereal v1.3.2 — 13 类, 129 模板特化, 113 宏 ✅
- fmt v12.1.1 — 35 类, 248 模板特化, 34 枚举 ✅
- glm v1.0.0 — 576 模板特化, 221 宏 ✅
- shadowsocks-libev v3.3.6 — 95 函数, 32 struct（纯 C）✅

---

*通过 10 个 C/C++ 库的迭代精炼构建。完整迭代历史见 [CHANGELOG.md](./CHANGELOG.md)。最后更新 2026-05-26。*
