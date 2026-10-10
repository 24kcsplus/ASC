# ASC MCP

把 ASC 的静态分析能力开放给 MCP 客户端。无需 Qt 窗口：MCP 进程处理协议，独立分析进程复用 ASC 的 APK/DEX 后端以及 GUI 已验证的所属类、定义和引用解析。

采用 [官方 Python SDK 1.x 的 FastMCP](https://py.sdk.modelcontextprotocol.io/v1/)，固定 `mcp==1.30.0`；本版本只提供本地 stdio。每个客户端连接独立启动一个服务，服务一次保留一个 APK，会话不与正在运行的 GUI 共享。

## 安装与启动

本机交付目录已配置 `.venv-mcp`，与 GUI 的 `.venv` 分开。已验证环境：Linux x86_64、Python 3.12.3。

```bash
# 新环境首次安装
./install-mcp.sh
# 如需指定具备 venv 支持的 Python
ASC_MCP_BOOTSTRAP_PYTHON=/path/to/python3 ./install-mcp.sh

# 由 MCP 客户端启动；默认单次分析超时 180 秒
./run-mcp.sh --timeout 180
```

标准输出专用于 MCP；手动运行后等待输入是正常现象。分析日志进入标准错误。更换安装目录后，更新客户端配置中的绝对路径即可。

客户端配置示例（也提供 `mcp-config.example.json`）：

```json
{
  "mcpServers": {
    "asc": {
      "command": "/mnt/tiplus7100/ctf/tools/asc_newgui/run-mcp.sh",
      "args": ["--timeout", "180"]
    }
  }
}
```

本实现不自动改写客户端全局配置。连接后先调用 `asc_open_apk`，参数例如：

```json
{"path": "/mnt/tiplus7100/ctf/tools/funnyV3.apk"}
```

`path` 推荐使用绝对路径；相对路径按服务仓库目录解析。

然后可以调用：

```text
asc_list_classes(query="MainActivity")
asc_class_members(class_name="com.yuuki.ukiinjector.MainActivity")
asc_source(class_name="com.yuuki.ukiinjector.MainActivity")
asc_find_references(class_name="G", kind="method", name="a", arity=1)
```

## 工具

| 工具 | 用途 |
|---|---|
| `asc_open_apk` | 静态加载本地 APK，返回会话、DEX 和类数量；成功才替换原会话 |
| `asc_close_apk` | 释放分析会话，不删除文件 |
| `asc_status` | 查看当前 APK 会话 |
| `asc_list_classes` | 按名称筛选类并分页 |
| `asc_class_members` | 类直接声明的方法签名、返回类型、字段及父类/接口 |
| `asc_source` | 反编译源码，按字符分页 |
| `asc_manifest` | 解码 Manifest，按字符分页 |
| `asc_find_definitions` | 指定类及继承层次内的精确方法定义 |
| `asc_find_references` | 精确类/成员引用；类引用包含该类的方法和字段访问 |
| `asc_search_members` | 显式全局名称包含搜索，用于发现未知成员所属类 |
| `asc_resolve_symbol` | 按源码行列解析类、方法调用、参数或局部变量声明 |

另提供 `asc://session` 资源，返回当前会话信息。

## 查询语义

- 类名接受 `com.example.A` 或 `Lcom/example/A;`；内部类使用 `$`，默认包中的混淆类可以传 `G` 或 `LG;`。不按简单类名跨包猜测目标。
- 引用查询的 `class_name` 是被引用成员所属类，而不是调用方过滤条件。其他类通过该类实例发起的访问仍会返回。
- 方法/字段名称精确匹配；同参数数量重载仍可能合并，结果中显示签名。不区分同一类的不同运行时实例，不分析反射和完整动态分派。
- `asc_search_members` 是明确的全局名称包含查询；结果可能是被引用的外部成员 ID，不等于 APK 内真实定义。需要定义时继续调用 `asc_find_definitions`。
- 列表 `offset` 从 0 开始，`limit` 为 1–500。源文本 `offset` / `limit` 按 Unicode 字符计数，单次最多 64,000 字符；返回 `start_line` 方便定位。`asc_resolve_symbol` 的行/列从 1 开始，列也按 Unicode 字符计数。
- 使用 `next_offset` 继续分页。名称/引用查询保留后端最多 5,000 条结果，`total` 是总数、`available` 是可分页条数；`truncated=true` 时应缩小目标。定义解析的候选也经过分页。
- 所有查询携带 `session_id` 和 `apk_path`。打开另一个 APK 会建立新会话；同一连接的并发工具调用按顺序执行。

## 生命周期和限制

- 仅静态读取 APK，不运行 APK，也不提供 shell、任意代码执行或修改 APK 的工具。文本来自待分析文件，应作为数据处理。
- 分析器需要本机文件访问权限；本版本服务不监听网络端口。
- 单次分析超过 `--timeout`、请求被取消或连接关闭时会回收分析进程；超时/取消之后再次查询需要重新打开 APK。普通参数错误或打开无效文件不会丢失原会话。
- 反编译器在运行时调整模块状态，因此与 MCP 协议层隔离；分析任务串行执行。源码缓存最多 32 个类，继续沿用原 ASC 的全 DEX 加载方式。
- 源码质量、特殊 DEX 格式支持和继承/引用的语义边界沿用现有 ASC 后端；部分反编译失败会作为 MCP 工具错误返回。
- 尚未提供 HTTP 服务、GUI 当前选择同步、远程文件上传、Smali 和资源文件浏览。当前进程组回收测试针对 Linux。

## 验证

```bash
ASC_MCP_APK_DIR=/path/to/apk-directory \
  .venv-mcp/bin/python tests/mcp/test_stdio.py
```

该目录可包含 `funnyV3.apk` 和 `check_env.apk`，仅在测试时读取，仓库不附带 APK。测试覆盖真实 stdio 初始化、11 个工具、资源、Schema 边界、两个 APK、分页、精确引用、并发调用、无效请求恢复、超时、取消和断开后进程回收。

`requirements-mcp-lock.txt` 记录本次 Linux/Python 3.12 验证环境的依赖闭包；需要复现时在同类环境中执行：

```bash
.venv-mcp/bin/python -m pip install -r requirements-mcp-lock.txt
```
