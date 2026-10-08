# 115-MCP-Server

基于 **FastMCP** 和 **p115client** 的 115 网盘 MCP 服务器。

## 项目来源与致谢

本项目是一个基于以下开源项目进行封装与扩展的 MCP 服务器：

- **p115client**：115 网盘 Python 客户端与接口封装
  - 文档：https://p115client.readthedocs.io/en/latest/
- **FastMCP**：用于快速构建 MCP 服务器的 Python 框架

本项目的大部分 115 能力都封装自 **p115client** 提供的客户端与文件系统接口，在此对原项目作者与贡献者表示感谢。

同时也感谢 **FastMCP** 提供了稳定、清晰的 MCP 服务构建能力，使这些 115 功能可以以 MCP 工具的形式对外提供。

## 本 fork 的改动（兼容新版 p115client + 登录支持）

> 上游代码是照着**并不存在的 p115client 接口**写的：`offline_*` / `offline_*_open`
> 这些方法名、以及 `P115Client(check_for_relogin=...)` 这个参数，在 PyPI 上**任何**
> p115client 版本里都不存在（PyPI 最早的版本是 0.0.9，方法名是 `clouddownload_*`）。
> 也就是说上游仓库按默认依赖装完，启动能成功、工具能列出，但**任何工具一调用就报错**。
> 本 fork 把它改到能真正跑起来。

### 1. 新增兼容层 `src/mcp_115_server/p115_compat.py`

- `create_client()`：按运行时签名过滤参数，去掉已删除的 `check_for_relogin`。
- `resolve_client_method()`：把历史名字映射到真实接口。
  - 只改名的（`offline_list` → `clouddownload_task_list`、`offline_remove` →
    `clouddownload_task_del`、`offline_sign` → `clouddownload_sign`、
    `offline_task_count` → `clouddownload_task_cnt`、`offline_download_path` →
    `clouddownload_downpath` 等）直接转发；
  - 原来的 `*_open` 名字对应开放平台接口，p115client 新版没有等价方法，
    改为直接请求 `proapi.115.com/open/offline/*`。
- `create_fs()`：绕开 p115client **0.0.9.7 的一个回归**。该版本把
  `id_to_attr` / `_pid_name_to_attr` 换成了 `WeakValueDictionary`，但写入的是普通
  `dict`（不支持弱引用），于是「按 id 查元数据」会抛
  `TypeError: cannot create weak reference to 'dict' object`。这里把这两个缓存换回普通 `dict`。

### 2. 单条目的改/移/拷/删改走 web 接口

`P115FileSystem` 的 `rename` / `move` / `copy` / `remove` 内部调用的是
`proapi.115.com/{app}/files/...`（默认 `app="android"`）。用**网页扫码登录**的 cookies
调用会返回 `{"error": "请重新登录", "errno": 99}`。因此 `rename_entry` /
`move_entry` / `copy_entry` / `remove_entry` 改为使用 `webapi.115.com` 的
`fs_rename` / `fs_move` / `fs_copy` / `fs_delete`（与仓库里原本就能用的
`batch_*` 接口保持一致）。

### 3. 登录支持增强

#### 3.1 新增账号 / 手机号 + 密码登录（设备方式）

新增 `src/mcp_115_server/p115_login.py` 和三个工具：

- `login_with_password(account, password, app, code, code_id, device_id)`
- `get_login_captcha()`
- `submit_login_sms(account, code, app)`

**登录一律走「设备（app）方式」**，默认登录成 115 安卓端（`app="android"`，`F1` 会话），
**不接受 `app="web"`**：web 方式会把你浏览器端的登录顶掉，而且 web 不算一台设备。

`device_id` 由账号名按**固定算法**推出（UUIDv5，见 `p115_login.derive_device_id`），
所以不需要存储、也不需要你准备：同一个账号永远登录成同一台设备，
不会每次都被 115 当成新设备。想改用别的设备，可以在调用时显式传 `device_id=`。

之所以没有直接用 p115client 的 `login_with_password`：那个高层函数一旦碰到
「已开启两步验证」，就会 `input("请输入短信验证码: ")` **阻塞在终端上**，
放进 MCP 服务器里会把进程卡死。所以按它的实现拆成了可分步调用的版本
（密码密文仍然是 115 要求的 `base64(RSA(sha1(password)_时间戳))`，用 `p115cipher` 算）。

图形验证码走 `captcha_sign` + `captcha_code` + `captcha_all`：返回 `code_id`
和两张图（4 个目标汉字、10 个带编号的候选汉字），调用方把编号拼成 `code` 再重试。

#### 3.2 扫码登录的三个工具增强

三个登录工具（`start_qrcode_login` / `get_qrcode_login_status` / `finish_qrcode_login`）现在：

- `start_qrcode_login` 除了 `qrcode_url`，还返回两种可直接展示的二维码：
  - **`qrcode_image`**：PNG data URI，能给客户端渲染图片；
  - **`qrcode_ascii`**：**纯文本**二维码（用 `▀ ▄ █` 半块字符，2 个模块行压成 1 个字符行），
    在等宽字体下原样打印即可，适合不能显示图片的 MCP 客户端。

  实测：把 `qrcode_ascii` 还原成位图后用 zxing-cpp 解码，能正确读出原始的扫码 URL。

  默认设备从 `alipaymini` 改为 `web`。
- `get_qrcode_login_status` 增加 `timeout` 参数（默认 5 秒）。`/get/status/` 是
  **长轮询**接口，没有状态变化时会挂住连接；这里带超时并按「继续等待」处理，
  同时关闭 urllib3 的自动重试（否则实际耗时是 timeout 的 4 倍）。
- `finish_qrcode_login` 默认把 cookies **写回 `P115_COOKIES_PATH`**（可用
  `output_path` 覆盖），并立即生效，后续工具无需重启即可使用。
- 顺带修了一个 cookie 格式问题：115 登录响应里的 `data.cookie` 可能是 dict 也可能
  是字符串，原来直接 `str()` 会把 dict 写成 Python repr，导致写回的 cookies 文件
  根本无法解析。现在统一用 `_cookies_to_str()` 处理成 `k=v; k=v`。

### 4. 依赖与测试

- `pyproject.toml`：`p115client>=0.0.9.7.2,<0.1`，新增 `qrcode`、`pillow`。
- 测试用的假客户端同步改成真实方法名，并新增 `tests/test_p115_compat.py`；
  另外修掉一个会**覆盖真实 cookies 文件**的测试（`finish_qrcode_login` 不传
  `output_path` 时会写回 `P115_COOKIES_PATH`）。
- 新增两个联网冒烟脚本：`scripts/smoke_live.py`（服务层）与 `scripts/smoke_mcp.py`
  （走 stdio 的真实 MCP 调用）。

> 注意：上游文档里的 `P115_CHECK_FOR_RELOGIN` 已经移除。新版 p115client（0.0.9+）
> 不再接受 `check_for_relogin` 参数，本 fork 也已删掉这个配置项。

## 快速开始

如果想最快跑起来，按这个顺序：

1. 安装 [uv](https://docs.astral.sh/uv/)（一次性，见下面的「安装 uv」）
2. 同步依赖：`uv sync`
3. 配置 `P115_COOKIES` 或 `P115_COOKIES_PATH`
4. 启动 MCP 服务：`uv run 115-MCP-Server`
5. 在 MCP 客户端里接入

最简命令：

```bash
uv sync
uv run 115-MCP-Server
```

## 功能

- 查询认证状态
- 列出目录与读取元数据
- 搜索文件/目录
- 创建目录
- 上传本地文件到 115
- 下载 115 文件到本地
- 移动、复制、重命名、删除文件或目录
- 批量移动、批量复制、批量删除
- 获取文件下载直链
- 查询空间信息
- 将目录路径解析为目录 ID
- 判断文件是否存在、统计目录数量、获取祖先链
- 按 glob 匹配文件、递归遍历目录、获取 stat 信息
- 离线下载任务创建、查询、清理、删除、BT 信息查询
- 离线下载默认目录查询/设置、任务重启、任务计数
- 离线任务高级筛选、批量删除、sign 信息、详细套餐/配额查询
- 回收站列表、详情、恢复
- 回收站清空或永久删除指定回收站条目
- 标签列表与标签设置
- 分享列表、分享详情、接收码、接收分享、分享下载链接、访问用户、分享下载配额
- 查询账号信息与索引首页统计
- 通过 MCP 登录并保存 cookies
  - 扫码登录：`start_qrcode_login` / `get_qrcode_login_status` / `finish_qrcode_login`
    （二维码同时以 PNG 和纯文本 ASCII 两种形式返回）
  - 账号密码登录（**设备方式**，默认 `app="android"`）：`login_with_password` /
    `get_login_captcha` / `submit_login_sms`
    （支持图形验证码和两步验证短信；`device_id` 由账号名按固定算法推出）
  - 设备与信任列表：**本服务不做任何查询或维护**（`device_id` 由账号名按固定算法推出）

## 安装

### 环境要求

- [uv](https://docs.astral.sh/uv/)：用来创建虚拟环境、安装依赖
- Python 3.12 或更高版本（uv 会自己下载合适版本，不需要你手动装）
- Windows 环境已验证可用
- 需要能访问 115 相关接口

### 安装 uv（一次性）

```powershell
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

也可以用 `pip install uv`，或通过 winget / Scoop / Homebrew 安装。

### 安装依赖（推荐）

在项目根目录执行：

```bash
uv sync
```

它会自动完成：

- 创建 `.venv`
- 按 `uv.lock` 安装**锁定版本**的依赖（含 p115client）
- 以可编辑模式安装本项目，生成 `115-MCP-Server` 入口

> 仓库里已经带了 `uv.lock`。p115client 在 0.0.9.x 期间接口改动很频繁，
> 锁版本可以避免「装到不兼容的新版本」这类问题。

### 不想用锁文件的话

```bash
uv venv
uv pip install -e .
```

### 改了代码要重新安装吗

不用。`uv sync` 是**可编辑安装**，改源码立即生效；只有改了 `pyproject.toml`
里的依赖时才需要再跑一次 `uv sync`。

### 验证安装是否成功

```bash
uv run 115-MCP-Server --help
```

能看到命令行帮助，就说明安装成功。

## 配置

### 认证方式

本项目支持三种认证方式：

1. 直接传入环境变量 `P115_COOKIES`
2. 通过环境变量 `P115_COOKIES_PATH` 指向 cookies 文件
3. 通过 MCP 工具执行二维码登录

> 本项目**不支持**通过项目根目录 `.env` 文件自动加载配置。配置来源只保留两种：运行进程时注入的环境变量，以及 `P115_COOKIES_PATH` 指向的本地 `.txt` cookies 文件。

推荐使用环境变量 + `.txt` cookies 文件：

```env
P115_COOKIES_PATH=~/115-cookies.txt
P115_ALLOW_QRCODE_LOGIN=false
P115_CONSOLE_QRCODE=false
```

也支持直接通过环境变量传入 cookies：

```env
P115_COOKIES=UID=...; CID=...; SEID=...; KID=...
```

说明：

- `P115_COOKIES` 优先级高于 `P115_COOKIES_PATH`
- `P115_COOKIES_PATH` 应指向本机上的 `.txt` cookies 文件，例如 `115-cookies.txt`
- 未配置 cookies 时，默认不会触发扫码登录
- 若要允许 `p115client` 在需要时尝试扫码登录，请设置 `P115_ALLOW_QRCODE_LOGIN=true`

- ### Cookie 平台自动推断

服务默认依赖 `p115client` 根据 cookie 自动推断首选登录平台。

当前行为：

1. 不需要手动配置 cookie 平台。
2. 服务会优先使用 `P115Client` 从 cookie 推断出的平台。
3. 如果某个接口在当前平台下失败，服务只做最小必要的接口级回退，而不会对大量平台进行轮询。
4. 这样可以减少失败请求数量，降低触发风控的风险。

### 推荐的环境变量写法

```env
P115_COOKIES_PATH=C:\Users\your-name\115-cookies.txt
P115_ALLOW_QRCODE_LOGIN=false
P115_CONSOLE_QRCODE=false
FASTMCP_TRANSPORT=stdio
```

这些变量应通过以下任一方式提供：

- MCP 客户端配置中的 `environment`
- 当前终端 / 系统环境变量
- 进程启动器（如 OpenCode、Claude Desktop、Cursor 等）的环境注入

不要把配置写到项目根目录 `.env` 中；服务不会读取它。

## 启动

### stdio

适用于绝大多数 MCP 桌面客户端。

```bash
uv run 115-MCP-Server
```

如果已经在虚拟环境里、不想让 uv 再做一次同步检查，也可以直接运行入口：

```bash
# Windows
.\.venv\Scripts\115-MCP-Server.exe

# macOS / Linux
./.venv/bin/115-MCP-Server
```

### HTTP

适用于需要通过 URL 接入 MCP 的客户端或调试场景。

```bash
uv run 115-MCP-Server --transport http --host 127.0.0.1 --port 8000 --path /mcp
```

Windows 也可以直接运行脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-stdio.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run-http.ps1
```

或：

```cmd
scripts\run-stdio.cmd
scripts\run-http.cmd
```

### 启动参数说明

- `--transport`：`stdio` / `http` / `streamable-http` / `sse`
- `--host`：HTTP 监听地址
- `--port`：HTTP 监听端口
- `--path`：HTTP MCP 路径
- `--log-level`：日志级别

例如：

```bash
uv run 115-MCP-Server --transport http --host 127.0.0.1 --port 8010 --path /mcp --log-level debug
```

## MCP 客户端接入示例

在开始之前，先准备这几个信息：

- 项目根目录：就是有 `pyproject.toml` 的那一层，例如 `C:/Users/your-name/115-MCP-Server`
- 启动方式：`uv run --directory <项目根目录> 115-MCP-Server`
- 推荐环境变量：

```text
P115_COOKIES_PATH=C:/Users/your-name/115-cookies.txt
P115_ALLOW_QRCODE_LOGIN=false
P115_CONSOLE_QRCODE=false
```

### 通用 stdio 模板（uv，推荐）

如果某个客户端支持以 `command + args + env` 的方式添加 MCP 服务，可直接套用：

```json
{
  "mcpServers": {
    "115-MCP-Server": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:/Users/your-name/115-MCP-Server",
        "115-MCP-Server"
      ],
      "env": {
        "P115_COOKIES_PATH": "C:/Users/your-name/115-cookies.txt",
        "P115_ALLOW_QRCODE_LOGIN": "false",
        "P115_CONSOLE_QRCODE": "false"
      }
    }
  }
}
```

几个注意点：

- `command` 建议写 `uv` 可执行文件的**绝对路径**（例如 `C:/Users/your-name/.local/bin/uv.exe`），
  因为部分 MCP 客户端不会继承系统的 `PATH`
- `--directory` 必须指向项目根目录
- 路径统一用正斜杠 `/`，省掉 JSON 里的反斜杠转义
- 首次启动时 uv 会自动 `sync` 一次依赖，可能多花几秒

### 备选：直接用虚拟环境里的入口

不想让客户端依赖 `uv` 时，可以先 `uv sync`，再把入口写死：

```json
{
  "mcpServers": {
    "115-MCP-Server": {
      "command": "C:\\Users\\your-name\\115-MCP-Server\\.venv\\Scripts\\115-MCP-Server.exe",
      "args": [],
      "env": {
        "P115_COOKIES_PATH": "C:\\Users\\your-name\\115-cookies.txt"
      }
    }
  }
}
```

或者用模块入口：

```json
{
  "command": "C:\\Users\\your-name\\115-MCP-Server\\.venv\\Scripts\\python.exe",
  "args": ["-m", "mcp_115_server"]
}
```

大多数支持 MCP 的客户端，本质上都只需要以下两种接入方式之一：

- `stdio`
- `http`

无论你使用的是 OpenCode、Claude Code、Claude Desktop、Cursor、Gemini、Kiro、Antigravity、Cherry Studio，还是其它支持 MCP 的客户端，都建议优先按下面的通用模板配置。

### HTTP 模式接入

如果客户端支持通过 URL 连接 MCP，可使用 HTTP 模式。

先启动服务：

```bash
uv run 115-MCP-Server --transport http --host 127.0.0.1 --port 8000 --path /mcp
```

默认地址：

```text
http://127.0.0.1:8000/mcp
```

然后在客户端中填入这个地址即可。

### 接入排查建议

如果某个客户端接入失败，优先检查：

1. 客户端版本是否真的支持 MCP
2. 是否选择了 `stdio` 或 HTTP MCP 模式
3. `uv` 是否在 PATH 里（客户端启动的进程可能没有继承你的 PATH，建议写绝对路径）
4. `--directory` 是否指向项目根目录，且该目录下能跑通 `uv run 115-MCP-Server --help`
5. cookies 是否已正确配置
6. HTTP 模式下端口和路径是否为 `127.0.0.1:8000/mcp`

## 使用方式

### 常见使用流程 1：已有 cookies，直接使用

1. 配置环境变量
2. 启动服务
3. 在客户端中调用：
   - `auth_status`
   - `list_directory`
   - `search_entries`
   - `offline_add_urls`

### 常见使用流程 2：没有 cookies，先登录

两条路都行，**扫码更稳**（密码登录在网页端常要过验证码，也更容易触发风控）。

#### 方式 A：扫码登录（推荐）

1. 启动服务
2. 调用 `start_qrcode_login`，拿到 `qrcode_url` / `qrcode_image` / `qrcode_ascii`
3. 用 115 App 扫二维码（客户端能显示图片就用 `qrcode_image`，只能显示文字就把
   `qrcode_ascii` 原样打印出来）
4. 调用 `get_qrcode_login_status` 轮询，直到 `signed_in`
5. 调用 `finish_qrcode_login`，cookies 会自动写回 `P115_COOKIES_PATH`

#### 方式 B：账号 / 手机号 + 密码登录（以「设备」身份）

密码登录**一律是设备（app）方式**，默认登录成 115 安卓端（`app="android"`），
不会用 `app="web"` —— web 方式会把你浏览器端的登录顶掉，而且 web 不算设备。

`device_id` 不用你准备：服务会拿**账号名**按固定算法算出一个 UUID（`UUIDv5`），
同一个账号永远算出同一个 `device_id`，所以永远是同一台设备，不需要任何存储。
想改用别的设备（比如一个已经在信任列表里的），调用时显式传 `device_id=` 即可。

1. 调用 `login_with_password(account, password)`，看返回的 `stage`：
   - `done`：已登录，cookies 已写回 `P115_COOKIES_PATH`
   - `captcha`：需要图形验证码 → 调用 `get_login_captcha`，返回 `code_id` 和两张图
     （`target_image` 是要找的 4 个字，`pool_image` 是 10 个候选字，按从左到右、
     从上到下编号 0-9）。把 4 个目标字的编号按顺序拼成 `code`，再调用
     `login_with_password(..., code=code, code_id=code_id)` 重试
   - `sms`：账号开了两步验证，短信已发出 → 调用 `submit_login_sms(account, code)` 完成
   - `error`：直接返回 115 的错误码和文案
2. 登录成功后，cookies 会写回 `P115_COOKIES_PATH`，后续工具直接可用
3. 如果 115 要求短信：说明这个账号还没用自己的推导设备登录过（对 115 来说是新设备）。
   过一次短信后它就不再是新设备了；想连这次都免掉，可以把这个推导出的 `device_id`
   在 115 App 的「账号安全 → 两步验证」里加入信任设备，或者调用时用 `device_id=`
   指定一个已经在信任列表里的设备

> 登录成功后**会把该设备之前的登录顶掉**（同一台设备只保留最近一次登录）。

#### device_id 从哪来

**默认不用你管**：`login_with_password` 会拿**账号名**算出一个 `device_id`：

```python
derive_device_id(account) = uuid5(DEVICE_ID_NAMESPACE, account.strip().casefold())
```

也就是 UUIDv5（名字空间 + SHA-1），输出正好是 115 安卓端 `device_id` 的 UUID 形式。
同一个账号永远算出同一个值，所以不需要存储、也不需要配置 —— 它就是「这台账号的那台设备」。

`DEVICE_ID_NAMESPACE` 是 `p115_login.py` 里的一个固定常量。改它等于换设备
（115 会把它当成一台新设备，于是要过短信），所以别随手改。

背景知识：`app="web"` 不需要 `device_id`（服务端按 cookie 生成 `cookie_<hash>` 形式的值）；
其它 `app` 需要一个**设备标识**。想用别的设备时，调用 `login_with_password` 时传
`device_id=` 即可，比如：

1. **复用已有设备**——用 115 App / 网页端的「登录设备管理」看已有设备的 `device_id`，
   然后在调用时用 `device_id=` 指定它。

2. **用已信任的设备（可跳过短信）**——在 115 App 的「账号安全 → 两步验证」里能看到
   已信任的设备及其 `device_id`。**用这些 `device_id` 登录不会再被要求短信验证码**：

   ```
   23078RKD5C / Android 16  device_id=e73daa2a-574c-4446-93a2-e9c41e798a95
   M2007J1SC / Android 13   device_id=1e5bad9d-cc02-4393-97ef-b3f37aa0341f
   ```

   反之，用一个**全新的** `device_id` 登录，一定会触发两步验证短信。

> **本服务不碰设备与信任列表**：既不查询、也不新增、也不移除。
> 原因是：只要有一个有效会话，就能给任意 `device_id` 登记免短信，这等于发放绕过短信的
> 通行证。所以这类操作请一律在 115 App / 网页端的「账号安全」里做
> （正常登录一次本身也会把该设备记入信任列表）。

> ⚠️ 无论用哪种方式，**用某个 `device_id` 登录都会顶掉该设备上的旧会话**。
> 比如用你手机对应的 `device_id` 登录，手机上那台设备就会被挤下线。

### 常见使用流程 3：离线下载资源到 115

1. 先用 `create_directory` 创建目标目录
2. 用 `offline_add_urls` 添加下载链接
3. 用 `offline_list_tasks` 或 `offline_list_tasks_advanced` 查询进度
4. 必要时用：
   - `offline_restart_task`
   - `offline_remove_task`
   - `offline_remove_tasks`
   - `offline_clear_tasks`

### 常见使用流程 4：整理和管理文件

可组合调用：

- `list_directory`
- `search_entries`
- `move_entry` / `batch_move_entries`
- `rename_entry`
- `set_entry_labels`
- `get_download_url`

## 项目结构

```text
pyproject.toml        # 项目定义与依赖
uv.lock               # 锁定的依赖版本（uv sync 使用）
src/mcp_115_server/
  config.py           # 环境变量与服务配置
  service.py          # 115 业务封装层
  server.py           # FastMCP 工具注册与服务入口
  p115_compat.py      # p115client 版本兼容层（本 fork 新增）
tests/
  test_service.py
  test_p115_compat.py
  test_qrcode_ascii.py
scripts/
  smoke_live.py       # 服务层联网冒烟测试
  smoke_mcp.py        # 通过 stdio 的真实 MCP 端到端测试
  run-stdio.ps1
  run-http.ps1
  run-stdio.cmd
  run-http.cmd
  build-exe.ps1
```

## MCP 工具

- `auth_status`
- `list_directory`
- `get_metadata`
- `search_entries`
- `create_directory`
- `resolve_directory`
- `get_storage_info`
- `start_qrcode_login`
- `get_qrcode_login_status`
- `finish_qrcode_login`
- `get_account_info`
- `get_index_info`
- `path_exists`
- `count_directory`
- `get_ancestors`
- `glob_entries`
- `walk_directory`
- `get_stat`
- `offline_add_urls`
- `offline_get_torrent_info`
- `offline_add_torrent`
- `offline_list_tasks`
- `offline_find_tasks`
- `offline_remove_task`
- `offline_clear_tasks`
- `offline_get_quota_info`
- `offline_get_download_paths`
- `offline_set_download_path`
- `offline_restart_task`
- `offline_get_task_count`
- `offline_list_tasks_advanced`
- `offline_remove_tasks`
- `offline_get_sign_info`
- `offline_get_quota_package_array`
- `offline_get_quota_package_info`
- `list_recycle_bin`
- `get_recycle_bin_entry`
- `restore_recycle_bin_entries`
- `clear_recycle_bin`
- `list_labels`
- `set_entry_labels`
- `list_shares`
- `get_share_info`
- `get_share_receive_code`
- `receive_share_entries`
- `get_share_download_url`
- `list_share_access_users`
- `get_share_download_quota`
- `upload_local_file`
- `download_file`
- `move_entry`
- `batch_move_entries`
- `copy_entry`
- `batch_copy_entries`
- `rename_entry`
- `remove_entry`
- `batch_remove_entries`
- `get_download_url`

## 关键返回约定

- `get_download_url` 始终返回对象，而不是裸字符串，包含：
  - `url`: 当前下载地址
  - `target`: 目标文件的元数据

- `auth_status(validate_remote=true)` 在远端校验失败时会返回：
  - `remote_logged_in: false`
  - `remote_error`: 规范化后的错误消息

- 批量操作工具统一支持两种来源之一：
  - `source_ids: list[int]`
  - `source_paths: list[str]`

  两者只能传一个。

- `get_share_download_url` 始终返回对象，包含：
  - `url`
  - `file_id`
  - `mode`

## 新增能力说明

### 1. 批量移动

一次移动多个文件或目录到目标目录：

- `batch_move_entries(source_ids=[...], destination_dir_path="/目标目录")`
- `batch_move_entries(source_paths=["/a.txt", "/b"], destination_dir_id=123456)`

### 2. 批量复制

一次复制多个文件或目录到目标目录：

- `batch_copy_entries(source_ids=[...], destination_dir_path="/目标目录")`

### 3. 批量删除

一次删除多个文件或目录：

- `batch_remove_entries(source_ids=[...])`
- `batch_remove_entries(source_paths=["/旧文件.txt", "/旧目录"])`

### 4. 查询目录 ID

如果你手里是一个目录路径，可以先调用：

- `resolve_directory(remote_path="/文档/项目")`

### 5. 查询空间信息

获取当前账号空间配额信息：

- `get_storage_info()`

### 5.1 扫码登录获取 cookies

现在可以直接通过 MCP 工具完成二维码登录，不必预先手动准备 cookies。

步骤 1：启动扫码会话

- `start_qrcode_login(app="alipaymini")`

返回：

- `session_id`
- `uid`
- `qrcode_url`
- `app`

步骤 2：轮询扫码状态

- `get_qrcode_login_status(session_id="...")`

可能的 `status_name`：

- `waiting`
- `scanned`
- `signed_in`
- `expired`
- `canceled`

步骤 3：完成登录并保存 cookies

- `finish_qrcode_login(session_id="...", output_path="C:\\Users\\your-name\\115-cookies.txt")`

返回：

- `cookies`
- `saved_to`
- `session_id`

完成后，这个 MCP 服务实例会立即切换到新的 cookies。

### 6. 路径/目录辅助查询

- `path_exists(remote_path="/文档")`
- `count_directory(remote_path="/文档")`
- `get_ancestors(remote_path="/文档/项目/demo.txt")`
- `glob_entries("*.txt", directory_path="/文档")`
- `walk_directory(remote_path="/文档", max_depth=2)`
- `get_stat(remote_path="/文档/demo.txt")`

### 7. 离线下载（重点能力）

已实现的离线下载工具：

- `offline_add_urls(urls=[...], remote_dir_id=123)`
- `offline_get_torrent_info(torrent_sha1="...", pick_code="...")`
- `offline_add_torrent(torrent_sha1="...", pick_code="...", wanted_indexes=[0,1])`
- `offline_list_tasks(page=1)`
- `offline_find_tasks(query="字幕组", status="completed")`
- `offline_remove_task(info_hash="...", delete_source_file=false)`
- `offline_clear_tasks(scope="completed")`
- `offline_get_quota_info()`
- `offline_get_download_paths()`
- `offline_set_download_path(remote_dir_path="/文档/离线")`
- `offline_restart_task(info_hash="...")`
- `offline_get_task_count(flag=0)`
- `offline_list_tasks_advanced(page=1, page_size=30, status="completed")`
- `offline_remove_tasks(info_hashes=["...", "..."], delete_source_file=false)`
- `offline_get_sign_info()`
- `offline_get_quota_package_array()`
- `offline_get_quota_package_info()`

`offline_clear_tasks` 支持的 `scope`：

- `completed`
- `all`
- `failed`
- `in_progress`
- `completed_and_delete_source`
- `all_and_delete_source`

说明：

- `offline_add_urls` 适合 HTTP / HTTPS / FTP / magnet / ed2k
- `offline_add_urls` **不支持直接提交 `.torrent` 文件**
- 如果你要添加 BT 种子任务，应使用：
  - `offline_get_torrent_info`
  - `offline_add_torrent`
- `offline_get_torrent_info` 可先查看 BT 内容，再配合 `wanted_indexes` 精选文件
- `offline_list_tasks` 已经对 Open API 的响应结构做了整形，直接返回 `count / page_count / tasks`
- `offline_find_tasks` 会在 MCP 层基于分页结果执行本地搜索/过滤，支持：
  - `query`
  - `info_hash`
  - `status`
  - `limit`
  - `offset`
- `offline_set_download_path` 可以用 `remote_dir_id` 或 `remote_dir_path` 指定默认离线目录
- `offline_restart_task` 适合失败后的任务重试
- `offline_get_task_count` 直接返回后端的离线任务计数信息
- `offline_list_tasks_advanced` 支持传统离线任务状态筛选，当前支持：
  - `failed`
  - `completed`
  - `in_progress`
- `offline_remove_tasks` 用于按 `info_hash` 批量删除离线任务
- `offline_get_sign_info` 返回低层离线接口会用到的 `sign/time` 等信息
- `offline_get_quota_package_array` / `offline_get_quota_package_info` 返回更详细的离线套餐/配额信息

### 8. 回收站

- `list_recycle_bin(limit=32, offset=0)`
- `get_recycle_bin_entry(rid=123)`
- `restore_recycle_bin_entries(entry_ids=[1,2,3])`
- `clear_recycle_bin(entry_ids=[1,2], password="123456")`

说明：

- 不传 `entry_ids` 时表示清空整个回收站
- 如果你的 115 账号开启了安全密钥校验，需传 `password`

### 9. 标签

- `list_labels(keyword="项目")`
- `set_entry_labels(remote_id=123456, label_ids=[1,2])`

注意：`set_entry_labels` 是**替换**当前标签集合，不是追加。

- 传 `label_ids=[]` 表示清空这个条目的全部标签

### 10. 分享

- `list_shares(limit=32, offset=0)`
- `get_share_info(share_code="xxxx")`
- `get_share_receive_code(share_code="xxxx")`
- `receive_share_entries(share_code="xxxx", receive_code="abcd", file_ids=[1,2], remote_dir_path="/接收目录")`
- `get_share_download_url(file_id=123, share_code="xxxx", receive_code="abcd")`
- `list_share_access_users(share_code="xxxx")`
- `get_share_download_quota()`

说明：

- `get_share_download_url` 支持两种方式：
  - 显式传 `share_code + receive_code + file_id`
  - 或直接传 `share_url + file_id`
- `get_share_download_url` 始终返回对象：
  - `url`: 下载地址
  - `file_id`: 请求的分享文件 id
  - `mode`: `share_code` 或 `share_url`
- `receive_share_entries` 会把分享中的指定文件/目录接收到你自己的网盘目录中

### 11. 账号与首页统计

- `get_account_info()`
- `get_index_info(include_space_numbers=true)`

## 目标定位规则

大多数工具都支持以下两种方式之一：

- `remote_id`: 115 文件/目录 ID
- `remote_path`: 115 路径，例如 `/文档/项目`

两者只能传一个；目录类工具若都不传，则默认根目录。

### 远程 ID 传参规则（重要）

所有远程 ID 都建议并推荐按**字符串**传入，不要按 JSON number / 数值传入。

正确示例：

```json
{
  "remote_id": "3398357158620823140"
}
```

不推荐示例：

```json
{
  "remote_id": 3398357158620823140
}
```

原因：

- 115 的很多远程 ID 很长
- 在某些客户端、语言运行时、JSON 处理中，超长整数可能发生精度丢失
- 一旦被改写，例如尾数被截断或归整，就会导致操作到错误的文件或目录

因此：

- `remote_id`
- `parent_id`
- `directory_id`
- `remote_dir_id`
- `source_id`
- `destination_dir_id`
- `file_id`
- `rid`
- `source_ids`
- `entry_ids`
- `file_ids`
- `label_ids`

都应优先按字符串或字符串数组传入。

## 测试

```bash
uv run python -m unittest discover -s tests -v
```

如果习惯 pytest，可以用 uv 临时把 pytest 拉进来跑，不污染项目依赖：

```bash
uv run --with pytest pytest tests -q
```

## 本地打包为 Windows exe

如果你需要在本地构建 Windows 可执行文件：

```powershell
uv pip install pyinstaller
powershell -ExecutionPolicy Bypass -File .\scripts\build-exe.ps1
```

> `uv pip install pyinstaller` 只是临时装进当前的 `.venv`，下次 `uv sync` 会把它清掉
> （想保留可以加 `--inexact`）。

构建完成后输出位于：

```text
dist\115-MCP-Server.exe
```

说明：

- 这是基于当前虚拟环境依赖打包出的 Windows 可执行文件
- 配置仍然通过环境变量或 MCP 客户端 `environment` 传入

## 部署建议

- 本地桌面客户端优先使用 `stdio`
- 需要多客户端共享时使用 `http`
- cookies 推荐放在本机文件中，通过 `P115_COOKIES_PATH` 引用
- 不建议把真实 cookies 直接写进公开配置文件

## 常见问题

### 1. 提示未配置认证

说明没有设置 `P115_COOKIES` 或 `P115_COOKIES_PATH`。

### 2. 提示 cookies 文件不存在

确认 `P115_COOKIES_PATH` 指向真实文件，路径需要是运行客户端那台机器上的本地路径。

### 3. 客户端连不上 MCP

检查：

- 项目根目录下能否直接跑通 `uv run 115-MCP-Server --help`
- 客户端配置里的 `command` 是否写成了 `uv` 的绝对路径（客户端往往没有继承系统 `PATH`）
- `--directory` 是否指向项目根目录
- 如果没用 uv 启动，`command` 是否指向 `.venv` 里真实的 `115-MCP-Server.exe`；
  用模块入口时 `args` 是否为 `["-m", "mcp_115_server"]`

### 4. HTTP 模式无法访问

检查端口是否被占用，以及客户端填写的地址是否为：

```text
http://127.0.0.1:8000/mcp
```
