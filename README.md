# 中兴 SR6110 单会话复用网关

给「限速管理 Android APP」用的后端桥：**网页管理和 APP 同时可用，路由器只看到 1 个会话。**

路由器只允许 1 个 Web 会话，且新登录会被拒绝（不是抢占）。所以会话由**网关自己持有**：
网关用配置里的路由器凭据登录一次，之后网页和 APP 都复用这一个会话。浏览器和 APP
都不需要知道路由器的账号密码。

```
浏览器 ──网关登录(cookie)──┐
                    ├─→ 网关（自己登录路由器，独占 SID）──→ 路由器
APP ──Bearer token──┘
```

技术栈：**aiohttp 负责对路由器的原始 HTTP 传输，FastAPI + uvicorn 负责网关自身的 API 与网页反代。**

## 1. 启动

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync

ROUTER_URL=http://192.168.100.1 \
ROUTER_PWD='路由器管理密码' \
GW_USER=admin \
GW_PWD='网关网页密码' \
APP_TOKEN='换成你的长随机串' \
PORT=8099 \
uv run zte-router-gateway
```

（国内网络如果连不上 pypi.org，可以加 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`。）

Windows PowerShell 里环境变量要这样设：

```powershell
$env:ROUTER_URL="http://192.168.100.1"
$env:ROUTER_PWD='路由器管理密码'
$env:GW_USER="admin"
$env:GW_PWD='网关网页密码'
$env:APP_TOKEN="换成你的长随机串"
$env:PORT="8099"
uv run zte-router-gateway
```

启动后：

```
router-gateway 已启动
  Web 管理:  http://<本机IP>:8099/
             账号 admin  密码 ******
  APP API :  http://<本机IP>:8099/api/devices
  APP_TOKEN: ******
  路由器凭据只来自 ROUTER_PWD；会话只存内存，掉线后网关自动重新登录
```

打开 `http://<网关IP>:8099/`，会看到网关自己的登录页，输入 `GW_USER` / `GW_PWD`，
进去就是路由器的管理页面——此时网关已经登录好了，不会再出现路由器的登录页。
登录页沿用路由器首页的视觉风格：浅灰背景、蓝色品牌条、白色 ZTE logo、同样的字体和控件。
logo 内嵌在网关里（`/__gw/logo.svg`），路由器不可达时也能正常显示。
固件在根路径默认渲染的是登录视图，所以网关会在根页面注入一行脚本，把浏览器带到
`/#/menu/dashboard`（可用 `GW_WEB_ENTRY` 改，设为空则关闭）。

路由器页面上的退出按钮会被网关接管——桌面左下角的 `.logout_icon`，以及移动端
`.mobile-top` 里文字为“退出系统”的 span。它只清除网关的登录状态，
**不会调用路由器的登出接口**，那个唯一会话继续留给 APP 和后台任务使用。

APP 用同一个地址的 `/api/...`，带 `Authorization: Bearer <APP_TOKEN>`。

### 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ROUTER_URL` | `https://router.java-jar.fun:2280` | 路由器管理地址 |
| `ROUTER_USER` | `admin` | 路由器登录用户名 |
| `ROUTER_PWD` | 空（必填） | 路由器管理密码，只由网关持有 |
| `HOST` / `PORT` | `0.0.0.0` / `8081` | 网关监听地址/端口 |
| `GW_USER` | `admin` | 浏览器访问网关的账号 |
| `GW_PWD` | 随机生成 | 浏览器访问网关的密码；不设置时启动会随机生成并打印 |
| `GW_WEB_ENTRY` | `/#/menu/dashboard` | 打开根路径后自动跳转的 hash 路由；设为空可关闭 |
| `GW_SESSION_TTL_HOURS` | `12` | 网关网页登录有效期（小时），重启后全部失效 |
| `APP_TOKEN` | 随机 24 位 hex | APP 侧 Bearer token（启动时会打印） |
| `HEARTBEAT_MS` | `60000` | 保活间隔，最小 30000 |
| `DEBUG` | 关 | `1` 打印代理日志，`2` 连完整请求头一起打印 |
| `ROUTER_TIMEOUT` | `20` | 单次路由器请求超时（秒） |
| `PROXY_TIMEOUT` | `30` | 网页反代请求超时（秒） |

## 2. 项目结构

```
src/zte_router_gateway/
  config.py              # 环境变量 -> Settings
  errors.py              # RouterError + kind（退避分类依赖它）
  util.py                # URL 编码 / sha256 / SessionTimeout 判定
  transport.py           # 唯一与 aiohttp 耦合的原始 HTTP 层（TLS、IPv4、cookie jar）
  protocol/
    paths.py             # 所有路由器接口路径
    xml.py               # OBJ_xxx / Instance / ParaName 解析
    login.py             # 三步登录 + 假成功验货
    session.py           # 会话状态机：自动登录 / 探活 / 单飞 / 退避
  router/
    models.py            # Device / DeviceListOut / QosState（JSON 契约）
    devices.py           # 设备列表、路由器信息
    qos.py               # 限速读写（限速管理的底座）
    client.py            # RouterClient 门面
  gateway/
    app.py               # FastAPI 装配 + 生命周期
    api.py               # /api/* 路由与 Bearer 鉴权
    webauth.py           # 网关登录页 / 会话 cookie / 中间件鉴权 / 登出
    proxy.py             # 网页反代（使用网关自己的会话）
    keeper.py            # 会话保活 / 掉线自动重新登录
  cli.py                 # serve / login / recover / devices / qos
tests/
  fake_router.py         # 假路由器（模拟登录、设备、限速）
  test_units.py          # 纯函数单测
  test_integration.py    # 假路由器 + 真实传输/API/反代端到端测试
scripts/
  api_smoke.py           # 对运行中的网关做全量 endpoint 冒烟
```

**以后接入网速监测时的扩展点：**

- 监测循环直接调用 `RouterClient.list_devices()` / `get_qos()`；`call()` 内部已经处理
  `_sessionTOKEN`、会话失效检测和自动重新登录，循环里不需要关心会话：

  ```python
  async def monitor(client: RouterClient) -> None:
      while True:
          devices = await client.list_devices()
          for device in devices.devices:
              print(device.mac, device.down_bps, device.up_bps)
          await asyncio.sleep(5)
  ```
- 自己起的后台任务建议只依赖 `RouterClient`，不要直接碰 `Transport` 或 `SessionManager`。
- 要加限速策略（按时段、按设备、临时限速、批量回滚）：新增 `policy/` 包
  （`models.py` + `store.py` + `scheduler.py`），同样只依赖 `RouterClient`。
- 不要在 `gateway/api.py` 里直接拼路由器请求，所有路由器调用都走 `RouterClient`。

## 3. 会话模型

- **网关自己登录**：启动后 `keeper.py` 立即用 `ROUTER_PWD` 登录，之后每隔 `HEARTBEAT_MS/2`
  检查一次；空闲时主动发一次数据请求保活。
- **掉线自动拉起**：任何调用收到 `SessionTimeout` 都会标记会话失效，然后在只读探测确认
  路由器收新会话后重新登录，并重试一次原请求。页面导航前还会先探活一次，避免把路由器的
  登录页当作正常页面透给浏览器。
- **退避**：失败按 5 → 10 → 20 → 30 分钟指数退避；`e_exceed_max_session` 这类硬失败直接
  冷却 30 分钟，登录锁定冷却 1 分钟。绝不高频重试——每次登录尝试都会占住那个唯一名额。
- **只存内存**：SID 和密码都不落盘。凭据只来自环境变量。
- **两套认证互不相干**：浏览器用 `GW_USER` / `GW_PWD` 登录网关（会话 cookie）；
  APP 用 `APP_TOKEN`（Bearer）。网页 cookie 只存在内存，网关重启后需要重新登录。
- **退出按钮只影响浏览器**：路由器页面上的 `.logout_icon` 被注入脚本拦截，跳转
  `/__gw/logout` 清除网关会话，不会动路由器那个共享会话。
- `/api/health` 的 `passwordConfigured` 表示是否配置了路由器密码，`nextRetrySeconds`
  表示距离下一次重试还有多久。

## 4. 命令行

```bash
# 默认命令就是 serve
uv run zte-router-gateway serve --host 0.0.0.0 --port 8099

# 静默 30 分钟后只登录一次并验货（诊断用）
uv run zte-router-gateway login --wait-minutes 30

# 低频探测直到路由器收新会话，然后登录一次（诊断用）
uv run zte-router-gateway recover --max-wait-minutes 90

# 设备列表
uv run zte-router-gateway devices --status online

# 限速（单位 Kbps）
uv run zte-router-gateway qos show  AA:BB:CC:DD:EE:FF
uv run zte-router-gateway qos set   AA:BB:CC:DD:EE:FF --down 10000 --up 2000
uv run zte-router-gateway qos clear AA:BB:CC:DD:EE:FF
```

所有命令都需要 `ROUTER_PWD`（网关自己登录）。`login` / `recover` 只是诊断工具，
命令结束后会话不保留。

## 5. 实测踩过的坑（改代码时别退回去）

1. **网关必须独占 cookie**
   路由器只认一个会话。网页反代必须使用网关 jar 里的 SID，不能透传浏览器带来的
   `Cookie`，也不能把上游的 `Set-Cookie` 交给浏览器——否则浏览器和网关各持一份，
   稍有不同步页面就会拿到 404（白屏 + `jsonToArray` 报错）。

2. **同名请求头不能出现两份**
   设置 `Cookie` / `Content-Length` 时必须先移除大小写不同的同名头，否则一个请求带两份，
   后端解析会错乱。传输层由 `Transport.merge_headers()` 统一做大小写不敏感合并。

3. **所有上游请求使用同一套固定指纹头**
   路由器会校验 UA / Accept-Language 等指纹。登录、`/api/*`、网页反代必须表现得像同一个
   客户端：反代会覆盖浏览器自己的 `User-Agent` / `Accept-Language`，并剥掉浏览器的
   `Authorization`。

4. **写操作要 `_sessionTOKEN`，而且是一次性的**
   每次 POST 前都要现取一次 `loginsceneData/login_token_json` 里的 `_sessionToken`，
   复用上一次的会返回 `页面过期`（`-1452`）。

5. **内网 80 在会话池满时不说真话**
   2280（nginx）会明确返回 `e_exceed_max_session`；内网 80 只给一个
   `login_need_refresh: true` 的假成功。判断登录是否成功，必须拿真实数据接口验货。

6. **绝不要高频自动重登**
   每次登录尝试都会占住/延长那个唯一名额。实测每 30 秒重试一次，11 分钟就把表刷满。
   重新登录必须「先探测、再退避」。

7. **探活也要用同一套指纹头**
   拿另一套 UA 去探测会被判成失效——但会话其实还活着。这个坑会让会话被误清。
   登录、探活、业务请求统一走 `Transport` 的默认头。

8. **登录前先探测路由器收不收**
   `login_entry` 的 GET 是只读的，能提前知道 `e_exceed_max_session`。先探测再决定要不要
   登录，能把无谓的登录尝试降到 0。

9. **`&_=` 必须是真实时间戳**
   路由器会校验这个 cache-buster：`_=0`、`_=1` 这种会被直接判成过期请求，返回
   `404 + SessionTimeout`；用当前毫秒时间戳才正常。调试时用固定值会让整个页面看起来像坏了。

10. **网页认证和路由器认证分开**
    浏览器只用网关的登录页和会话 cookie；浏览器带来的 `Cookie` / `Authorization` 一律剥掉，
    路由器的 `Set-Cookie` 也一律剥掉。`/api/*` 只认 Bearer token，不能跳登录页，
    否则 APP 会收到 HTML 而不是 JSON。

11. **不请求压缩**
    反代会删掉浏览器的 `Accept-Encoding`，让路由器返回未压缩正文。否则 `SessionTimeout`
    文本判定会失效。

## 6. API

`Authorization: Bearer <APP_TOKEN>`（也支持 `?token=`）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 会话状态 |
| GET | `/api/info` | 型号、固件 |
| GET | `/api/devices?status=all\|online\|offline` | 设备列表 |
| GET | `/api/devices/{mac}` | 单设备 |
| GET | `/api/devices/{mac}/qos` | 当前限速 |
| PUT/POST | `/api/devices/{mac}/qos` | 设置限速 `{"downKbps":10000,"upKbps":2000}` |
| DELETE | `/api/devices/{mac}/qos` | 取消限速 |

设备对象：

```json
{
  "mac": "AA:BB:CC:DD:EE:FF", "name": "yoga14s", "ip": "192.168.100.54",
  "online": true, "band": "5G", "wired": false, "rssi": -70,
  "downBps": 393, "upBps": 0, "onlineSeconds": 2822400,
  "lastSeen": null, "brand": null, "model": null, "guest": false
}
```

限速单位 **Kbps**（网页上的 Mbps × 1000），底层是 `localnet_qosdyn_bandwithrule_lua`。
注意固件把 Bandwidth 拼成了 `Bandwith`，字段名不能改。

实测结果：`PUT` 返回 `SUCC` 并生成规则 `IGD.QoSBandwidthRule2`，`DELETE` 后规则消失。

FastAPI 自带 OpenAPI 文档：`http://<机器>:8099/docs`（文档本身不含数据，调用仍需 token）。

## 7. 测试

```bash
uv run pytest
```

测试不依赖真机：`tests/fake_router.py` 起一个假路由器，模拟了登录三步、假成功验货、
设备 XML、限速读写，覆盖：

- 网关自动登录、会话失效后自动重新登录并重试
- `_sessionTOKEN` 一次性校验、设备/限速完整读写
- `/api/*` Bearer 鉴权、参数校验、404
- 网页反代：登录页/cookie 鉴权、登出、使用网关自己的会话、`Set-Cookie` 不泄漏、凭据不转发

### 对运行中的网关做全量冒烟

`scripts/api_smoke.py` 是零依赖的接口冒烟脚本，覆盖全部 endpoint（鉴权、health、info、
设备列表三种过滤、单设备、QoS 读写、404 兜底、网关登录/登出、网页反代、OpenAPI 文档）：

```bash
# 只读（不碰路由器配置）
uv run python scripts/api_smoke.py --token <APP_TOKEN> --web-pwd <GW_PWD> --read-only

# 全量：会临时给一台「离线且无限速规则」的设备设限速，验证后删除并恢复原状
uv run python scripts/api_smoke.py --token <APP_TOKEN> --web-pwd <GW_PWD>
```

写测试优先选离线设备；如果所有设备都有规则，会选最后一台并在结束时恢复原值。
`--mac` 可指定目标，`--down-kbps` / `--up-kbps` 可指定测试速率。

## 8. Android 侧

```kotlin
data class Device(val mac: String, val name: String, val ip: String?,
                  val online: Boolean, val band: String?, val rssi: Int?)

data class DeviceList(val total: Int, val online: Int, val offline: Int, val devices: List<Device>)
data class Qos(val mac: String, val limited: Boolean, val downKbps: Long?, val upKbps: Long?)

interface RouterApi {
    @GET("api/devices") suspend fun devices(@Query("status") s: String = "all"): DeviceList
    @GET("api/devices/{mac}/qos") suspend fun qos(@Path("mac") mac: String): Qos
    @PUT("api/devices/{mac}/qos") suspend fun setQos(@Path("mac") mac: String, @Body b: Map<String, Long>): Map<String, Any>
    @DELETE("api/devices/{mac}/qos") suspend fun clearQos(@Path("mac") mac: String): Map<String, Any>
}

val client = OkHttpClient.Builder()
    .addInterceptor { c -> c.proceed(c.request().newBuilder()
        .header("Authorization", "Bearer $APP_TOKEN").build()) }
    .build()
```

清单里允许内网明文：

```xml
<application android:usesCleartextTraffic="true" ...>
```

## 9. 注意事项

- 网关要跑在常开机器上；它每 60 秒保活一次，避免会话空闲超时。
- `ROUTER_PWD` 是网关自己持有的路由器密码，只从环境变量读取，不写盘、不打印。
- 密码里有 `$` / `!` 等字符时，bash 和 PowerShell 都必须用**单引号**——双引号会触发变量展开，
  表现为登录返回 `e_invalid_user_pwd`。
- `GW_PWD` 不设置时启动会随机生成并打印，重启会变化；长期使用建议显式配置。
- 网页登录使用网关自己的会话 cookie（HttpOnly + SameSite=Lax），不落盘；网关重启后需要重新登录。
  明文 HTTP 下 cookie 和密码都不加密，只应部署在受信任内网；需要跨网访问时请在前面加 HTTPS 反代。
- 本项目对自签证书不校验（`ssl=False`）。
- 路由器 2280 目前对公网开放（`171.83.60.164:2280`），建议关掉端口映射，只走内网。
