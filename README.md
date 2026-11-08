# 中兴 SR6110 单会话复用网关

网关自己登录并独占路由器唯一 Web 会话：网页管理用网关账号，外部调用用 Bearer token；
同时提供设备列表、限速（含定时到期）、网速采样与历史曲线。

## 启动

```bash
uv sync
ROUTER_URL='http://192.168.100.1' ROUTER_PWD='路由器密码' \
GW_USER='admin' GW_PWD='网关密码' APP_TOKEN='长随机串' PORT=8099 \
uv run zte-router-gateway
```

- 管理页面：`http://<IP>:8099/`
- 网速监控：`http://<IP>:8099/monitor`
- 接口文档：`http://<IP>:8099/docs`
- 命令行：`uv run zte-router-gateway --help`

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `ROUTER_URL` | `https://router.java-jar.fun:2280` | 路由器地址 |
| `ROUTER_USER` | `admin` | 路由器用户名 |
| `ROUTER_PWD` | 必填 | 路由器密码，仅网关持有 |
| `HOST` / `PORT` | `0.0.0.0` / `8081` | 监听地址/端口 |
| `GW_USER` / `GW_PWD` | `admin` / 随机 | 网关网页账号 |
| `APP_TOKEN` | 随机 | API Bearer token |
| `GW_SESSION_TTL_HOURS` | `720` | 网页登录有效期（30 天） |
| `GW_WEB_ENTRY` | `/#/menu/dashboard` | 登录后进入的路由器页面 |
| `GW_DB_PATH` | `data/gateway.db` | SQLite 路径 |
| `MONITOR_INTERVAL_SECONDS` | `5` | 采样间隔 |
| `MONITOR_RETENTION_DAYS` | `7` | 采样保留天数 |
| `SCHEDULER_INTERVAL_SECONDS` | `2` | 限速到期检查间隔 |
| `HEARTBEAT_MS` | `60000` | 会话保活间隔 |
| `DEBUG` | 关 | `1` 输出代理日志 |

`ROUTER_TIMEOUT` / `PROXY_TIMEOUT` 可覆盖默认的 20 / 30 秒。

## API

`Authorization: Bearer <APP_TOKEN>`，或已登录的网关网页会话。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 会话状态 |
| GET | `/api/info` | 型号、固件 |
| GET | `/api/sysinfo` | 运行时间、WAN IPv4、IPv4 在线时长、DNS、备用 DNS |
| GET | `/api/devices?status=all\|online\|offline` | 设备列表 |
| GET | `/api/devices/{mac}` | 单设备 |
| GET | `/api/devices/{mac}/qos` | 当前限速 |
| PUT/POST | `/api/devices/{mac}/qos` | 立即限速 `{"downKbps":10000,"upKbps":2000}` |
| DELETE | `/api/devices/{mac}/qos` | 取消限速 |
| GET | `/api/monitor/devices` | 采样库设备 |
| GET | `/api/monitor/series?macs=a,b&from=ms&to=ms` | 历史曲线 |
| GET | `/api/qos/schedules` | 限速记录 |
| POST | `/api/qos/schedules` | 定时限速：`durationMinutes` 或 `permanent: true` |
| DELETE | `/api/qos/schedules/{id}` | 取消限速 |

- `downBps` / `upBps`：**B/s**；页面显示 KB/s（÷1000）。
- 限速：**Kbps**；时间：**Unix 毫秒**。
- 会话与密码只存内存，网关重启后需重新登录；采样写入 SQLite，默认保留 7 天。
- 监控页是 `gateway/static/monitor.html`，改完刷新浏览器即生效，无需重启服务。
- 密码含 `$` 等字符时，启动命令必须用**单引号**。
- 局域网访问需在系统防火墙放行网关端口。

## 实测踩过的坑（改代码时别退回去）

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
    否则调用方会收到 HTML 而不是 JSON。

11. **不请求压缩**
    反代会删掉浏览器的 `Accept-Encoding`，让路由器返回未压缩正文。否则 `SessionTimeout`
    文本判定会失效。

## 冒烟

```bash
uv run python scripts/api_smoke.py --token <APP_TOKEN> --web-pwd <GW_PWD> --read-only
```
