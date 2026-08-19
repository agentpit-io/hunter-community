# AKShare MCP

把 AKShare 变成一个**你自己跑的能力**,接进 Hunter。

---

## 为什么不是「数据源」

数据源的模型是一条记录 = 一个 `(市场, 数据类型, 接口地址)`。

AKShare 有**一千多个函数** —— 龙虎榜、十大股东、券商研报、公司治理、
南向资金、宏观、期货、基金……按数据源建模就得枚举一千多条,做不到。

Hunter 原来只从里面挑了 6 条进目录,而那 6 条是**我们**用得上的,不是**你**
用得上的。你想查「北向资金持股明细」而我们没挑,你就没辙。

改成能力之后,模型拿到三个工具,自己去找、自己去调:

```
akshare_search("龙虎榜")                    → 有哪些函数能查这个?
akshare_signature("stock_lhb_detail_em")   → 它要什么参数?
akshare_call("stock_lhb_detail_em", {...}) → 调它
```

**不需要我们提前枚举。**

## 为什么要你自己跑

AKShare 是 Python 库不是 HTTP 服务,没法在表单里填一个 URL。而且它多数
接口的上游在国内,从境外容器直连经常打不通。

---

## 跑起来

```bash
cd tools/akshare-mcp
docker build -t akshare-mcp .
docker run -d -p 8931:8931 --name akshare-mcp akshare-mcp
```

镜像大约 1.5G(AKShare 自己拖着 pandas/lxml/beautifulsoup4 一大串),
首次构建要几分钟。

验证:

```bash
curl -N http://localhost:8931/sse
# 有 SSE 流出来就是起来了(它不会自己结束,Ctrl-C 退出)
```

## 接进 Hunter

能力 → **接入一个工具** → 填:

| 字段 | 值 |
|---|---|
| 传输方式 | `sse` |
| 地址 | `http://你的地址:8931/sse` |
| 需要 key | 不勾 |

Hunter 跑在 Docker 里的话,`localhost` 指的是 Hunter 自己的容器 ——
要填**宿主机地址**(Linux 上是 `172.17.0.1`,Docker Desktop 上是
`host.docker.internal`),或者把两个服务放进同一个 compose 网络。

> ⚠️ **不要填 Hunter 官方的任何地址。**这个服务存在的意义就是让你不依赖我们。

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `AKSHARE_MCP_HOST` | `0.0.0.0` | 监听地址 |
| `AKSHARE_MCP_PORT` | `8931` | 端口 |
| `AKSHARE_MAX_ROWS` | `200` | 单次返回最大行数 |

`AKSHARE_MAX_ROWS` 不要调太大:AKShare 有些接口一次几万行,原样塞进模型
上下文会把真正有用的东西挤掉。**被截断时返回里会明确说**,不会假装那就是全部。

---

## 安全

`akshare_call` **只能调 `akshare` 模块里公开的、可调用的属性**:

- 名字带 `_` 前缀的拒绝
- 带 `.` 的(想访问子模块)拒绝
- 不存在或不可调用的拒绝
- 不做 `eval` / `exec` / 动态 import

它不是通用代码执行入口。

**但仍然不要暴露到公网。**这个服务没有鉴权,而 AKShare 会向第三方站点发
请求 —— 公网上任何人都能借你的机器发请求。绑内网,或者加一层带鉴权的反代。

---

## 常见问题

**调用超时 / 连接被拒**
多数 AKShare 接口的上游在国内。如果这台机器在境外,大部分接口都会失败 ——
把它跑在国内的机器上,或者给容器配代理。

**函数名找不到**
AKShare 的命名多是英文缩写,硬猜会一直失败:龙虎榜是 `stock_lhb_detail_em`
不是 `stock_dragon_tiger`。**先 `akshare_search` 再 `akshare_call`。**

**返回的列名是中文**
AKShare 的原样返回,我们不改列名 —— 改了之后你对着它的文档会对不上。
