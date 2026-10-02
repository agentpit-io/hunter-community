#!/usr/bin/env python3
"""一键部署模板的静态校验 · 可重复运行

做四件事：
  1. YAML 语法（三个模板 + 1Panel 的 data.yml，多文档的按多文档解析）
  2. Zeabur 模板对官方 JSON Schema 的校验
     （https://schema.zeabur.app/template.json，内嵌 $ref 到 prebuilt.json）
  3. Sealos 模板的结构自检：必填字段、`defaults.app_name` 必须含 random()、
     引用到的 `${{ inputs.x }}` / `${{ defaults.x }}` 必须真的定义过
  4. 1Panel 应用包的结构自检（有就查，没有就跳过）

用法：
    python3 deploy/tools/validate-templates.py            # 离线，schema 用本地缓存
    python3 deploy/tools/validate-templates.py --online   # 重新下载 Zeabur schema

Zeabur schema 不联网时用 deploy/tools/schema-cache/ 下的副本；下载日期写在
缓存目录的 FETCHED.txt 里。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(__file__).resolve().parent / "schema-cache"
ZEABUR_SCHEMAS = {
    "template.json": "https://schema.zeabur.app/template.json",
    "prebuilt.json": "https://schema.zeabur.app/prebuilt.json",
}

PASS, FAIL = [], []


def ok(msg: str) -> None:
    PASS.append(msg)
    print(f"  ✅ {msg}")


def bad(msg: str) -> None:
    FAIL.append(msg)
    print(f"  ❌ {msg}")


def skip(msg: str) -> None:
    print(f"  ⏭  {msg}")


# ── Sealos 的 ${{ ... }} 会让 YAML 解析器在 `key: ${{ x }}` 上没问题（是字符串），
#    但 `${{ if(...) }}` 这类独占一行的条件渲染块不是合法 YAML —— 官方模板都这么写，
#    校验时按官方渲染器的做法先把条件行剥掉。
COND_LINE = re.compile(r"^\s*\$\{\{\s*(if|else|endif)\b.*\}\}\s*$", re.M)


def load_yaml_docs(path: Path, strip_cond: bool = False):
    text = path.read_text(encoding="utf-8")
    if strip_cond:
        text = COND_LINE.sub("", text)
    return list(yaml.safe_load_all(text))


# ══════════════════════════════════════════════════════════════════════
def check_yaml_syntax() -> None:
    print("\n[1/6] YAML 语法")
    targets = [
        (ROOT / "deploy/zeabur/template.yaml", False),
        (ROOT / "deploy/sealos/index.yaml", True),
        (ROOT / "deploy/railway/services.yaml", False),
        (ROOT / "deploy/coolify/docker-compose.yml", False),
        (ROOT / "deploy/dokploy/docker-compose.yml", False),
        (ROOT / "docker-compose.yml", False),
    ]
    targets += [(p, False) for p in sorted((ROOT / "deploy/1panel").rglob("*.yml"))]
    targets += [(p, False) for p in sorted((ROOT / "deploy/1panel").rglob("*.yaml"))]
    for path, strip in targets:
        if not path.exists():
            skip(f"{path.relative_to(ROOT)} 不存在")
            continue
        try:
            docs = load_yaml_docs(path, strip)
            ok(f"{path.relative_to(ROOT)} 解析通过（{len(docs)} 个文档）")
        except Exception as exc:  # noqa: BLE001
            bad(f"{path.relative_to(ROOT)} 解析失败：{exc}")


# ══════════════════════════════════════════════════════════════════════
def fetch_schemas(online: bool) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, url in ZEABUR_SCHEMAS.items():
        local = CACHE / name
        if online or not local.exists():
            with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310
                local.write_bytes(resp.read())
            print(f"  ↓ 已下载 {url} → {local.relative_to(ROOT)}")
        out[url] = json.loads(local.read_text(encoding="utf-8"))
    return out


def check_zeabur(online: bool) -> None:
    print("\n[2/6] Zeabur 模板 · 官方 JSON Schema 校验")
    path = ROOT / "deploy/zeabur/template.yaml"
    if not path.exists():
        skip("deploy/zeabur/template.yaml 不存在")
        return
    try:
        import jsonschema
        from jsonschema import Draft7Validator
        from referencing import Registry, Resource
    except ImportError:
        bad("缺 jsonschema/referencing，跑 `pip install jsonschema` 后重试")
        return

    store = fetch_schemas(online)
    registry = Registry().with_resources(
        [(url, Resource.from_contents(doc)) for url, doc in store.items()]
    )
    schema = store[ZEABUR_SCHEMAS["template.json"]]
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        for e in errors[:20]:
            bad(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}")
    else:
        ok(f"template.yaml 通过 {ZEABUR_SCHEMAS['template.json']}（jsonschema {jsonschema.__version__}）")

    # schema 管不到的自查：引用的 ${VAR} 必须有人 expose，且引用方声明了依赖
    check_zeabur_refs(doc)


def check_zeabur_refs(doc: dict) -> None:
    services = doc["spec"]["services"]
    exposed = {}  # var -> service name
    for svc in services:
        for k, v in (svc["spec"].get("env") or {}).items():
            if isinstance(v, dict) and v.get("expose"):
                exposed[k] = svc["name"]
    tmpl_vars = {v["key"] for v in doc["spec"].get("variables") or []}
    # Zeabur 内建特殊变量（deploy/special-variables，2026-09-18 查）
    builtin = {
        "PASSWORD", "CONTAINER_HOSTNAME", "PORT", "PORT_FORWARDED_HOSTNAME",
    }
    ref = re.compile(r"\$\{([A-Z0-9_]+)\}")
    for svc in services:
        own_ports = {p["id"].upper() + "_PORT" for p in (svc["spec"].get("ports") or [])}
        own_env = set((svc["spec"].get("env") or {}).keys())
        deps = set(svc.get("dependencies") or [])
        blobs = [str(v.get("default", "")) for v in (svc["spec"].get("env") or {}).values()]
        blobs += [i["content"] for i in (svc["spec"].get("instructions") or [])]
        for blob in blobs:
            for var in ref.findall(blob):
                if var in builtin or var in own_ports or var in own_env or var in tmpl_vars:
                    continue
                if var.startswith("ZEABUR_"):
                    continue
                if var not in exposed:
                    bad(f"服务 {svc['name']} 引用了 ${{{var}}}，但没有任何服务 expose 它")
                elif exposed[var] != svc["name"] and exposed[var] not in deps:
                    bad(
                        f"服务 {svc['name']} 引用 ${{{var}}}（由 {exposed[var]} expose），"
                        f"但没把 {exposed[var]} 写进 dependencies"
                    )
    # 依赖成环检查（官方最佳实践：A 依赖 B，B 不能依赖 A）
    dep = {s["name"]: set(s.get("dependencies") or []) for s in services}
    seen, stack = set(), []

    def visit(n: str) -> bool:
        if n in stack:
            bad(f"依赖成环：{' → '.join(stack[stack.index(n):] + [n])}")
            return False
        if n in seen:
            return True
        seen.add(n)
        stack.append(n)
        good = all(visit(d) for d in dep.get(n, ()))
        stack.pop()
        return good

    if all(visit(n) for n in dep):
        ok("依赖图无环、所有 ${VAR} 引用都有对应的 expose + dependencies")


# ══════════════════════════════════════════════════════════════════════
SEALOS_REQUIRED = ["title", "url", "gitRepo", "author", "description", "icon", "categories"]
SEALOS_CATEGORIES = {
    "ai", "database", "tool", "low-code", "blog", "storage",
    "frontend", "backend", "dev-ops", "monitor", "game",
}


def check_sealos() -> None:
    print("\n[3/6] Sealos 模板 · 结构自检")
    path = ROOT / "deploy/sealos/index.yaml"
    if not path.exists():
        skip("deploy/sealos/index.yaml 不存在")
        return
    docs = [d for d in load_yaml_docs(path, strip_cond=True) if d]
    tmpl = next((d for d in docs if d.get("kind") == "Template"), None)
    if not tmpl:
        bad("找不到 kind: Template 的文档")
        return
    spec = tmpl["spec"]
    missing = [f for f in SEALOS_REQUIRED if not spec.get(f)]
    if missing:
        bad(f"Template.spec 缺必填字段：{missing}")
    else:
        ok(f"Template.spec 必填字段齐全（{len(SEALOS_REQUIRED)} 项）")

    badcat = [c for c in spec.get("categories", []) if c not in SEALOS_CATEGORIES]
    if badcat:
        bad(f"categories 里有仓库不认的值：{badcat}")
    else:
        ok(f"categories={spec.get('categories')} 全部在官方枚举里")

    app_name = (spec.get("defaults") or {}).get("app_name", {}).get("value", "")
    if "random(" not in str(app_name):
        bad("defaults.app_name 必须含 ${{ random(N) }}（官方 example.md 明写，否则报错）")
    else:
        ok(f"defaults.app_name = {app_name}")

    # 资源文件里引用的 defaults / inputs 必须定义过
    text = path.read_text(encoding="utf-8")
    defined = set((spec.get("defaults") or {}).keys())
    inputs = set((spec.get("inputs") or {}).keys())
    undef = set()
    for kind, name in re.findall(r"\$\{\{\s*(defaults|inputs)\.([A-Za-z0-9_]+)", text):
        pool = defined if kind == "defaults" else inputs
        if name not in pool:
            undef.add(f"{kind}.{name}")
    if undef:
        bad(f"引用了未定义的变量：{sorted(undef)}")
    else:
        ok(f"defaults({len(defined)}) / inputs({len(inputs)}) 引用全部有定义")

    kinds = [d.get("kind") for d in docs]
    if kinds[-1] != "App":
        bad(f"最后一个资源应为 kind: App（官方 template.yaml 注释：must be the last resource），实际是 {kinds[-1]}")
    else:
        ok(f"资源顺序正确，共 {len(docs)} 个文档：{kinds}")

    for asset in ("README.md", "README_zh.md", "logo.png", "website-screenshot.webp"):
        p = ROOT / "deploy/sealos" / asset
        (ok if p.exists() else bad)(f"配套文件 {asset} {'存在' if p.exists() else '缺失'}")

    check_sealos_k8s(docs)


def check_sealos_k8s(docs: list) -> None:
    """把模板渲染成真 K8s YAML，交给 kubeconform 按官方 OpenAPI schema 校验。

    CRD（Template / Cluster / App）没有公开 schema，跳过；其余标准资源必须过。
    没装 kubeconform 就跳过并说明 —— 这一项抓到过真问题（volumeClaimTemplates
    的 annotations 值渲染成裸数字，`-strict` 判非法，真集群上 API server 也会拒），
    所以值得单独装一个二进制来跑。
    """
    import shutil
    import subprocess
    import tempfile

    exe = shutil.which("kubeconform") or ("/tmp/kubeconform" if Path("/tmp/kubeconform").exists() else None)
    if not exe:
        skip("没找到 kubeconform，跳过 K8s schema 校验（装法："
             "curl -sSL https://github.com/yannh/kubeconform/releases/latest/download/"
             "kubeconform-linux-amd64.tar.gz | tar -xz -C /tmp kubeconform）")
        return

    vals = {
        "defaults.app_name": "huntercode-abcd1234", "defaults.app_host": "huntercode-abcd1234",
        "defaults.jwt_secret": "x" * 48, "defaults.internal_key": "y" * 48,
        "defaults.setup_token_gen": "z" * 16, "inputs.setup_token": "z" * 16,
        "defaults.fin_paper_password": "p" * 24,
        "inputs.session_volume_size": "5", "inputs.data_volume_size": "3",
        "SEALOS_NAMESPACE": "ns-demo", "SEALOS_CLOUD_DOMAIN": "sealos.io",
        "SEALOS_CERT_SECRET_NAME": "wildcard-cert", "SEALOS_SERVICE_ACCOUNT": "sa-demo",
    }
    std_groups = ("v1", "apps", "networking.k8s.io", "rbac.authorization.k8s.io")
    std = [d for d in docs if d.get("apiVersion", "").split("/")[0] in std_groups]

    def render(node):
        if isinstance(node, dict):
            return {render(k): render(v) for k, v in node.items()}
        if isinstance(node, list):
            return [render(x) for x in node]
        if not isinstance(node, str):
            return node

        def rep(m):
            e = m.group(1).strip()
            if e in vals:
                return vals[e]
            r = re.fullmatch(r"random\((\d+)\)", e)
            return "r" * int(r.group(1)) if r else m.group(0)

        return re.sub(r"\$\{\{\s*(.+?)\s*\}\}", rep, node)

    rendered = [render(d) for d in std]
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write("\n---\n".join(yaml.safe_dump(d, allow_unicode=True) for d in rendered))
        path = f.name
    r = subprocess.run([exe, "-kubernetes-version", "1.30.0", "-strict", "-summary", path],
                       capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip().splitlines()
    if r.returncode == 0:
        ok(f"kubeconform -strict（K8s 1.30 官方 schema）· {len(rendered)} 个标准资源全部通过")
    else:
        for line in out[:8]:
            bad(f"kubeconform: {line[:180]}")


# ══════════════════════════════════════════════════════════════════════
def check_1panel() -> None:
    print("\n[4/6] 1Panel 应用包 · 结构自检")
    base = ROOT / "deploy/1panel/hunter-community"
    if not base.exists():
        skip("deploy/1panel/hunter-community 不存在")
        return
    root_data = base / "data.yml"
    (ok if root_data.exists() else bad)(f"data.yml {'存在' if root_data.exists() else '缺失'}")
    versions = [p for p in base.iterdir() if p.is_dir()]
    if not versions:
        bad("没有版本目录")
        return
    for v in versions:
        for f in ("docker-compose.yml", "data.yml"):
            p = v / f
            (ok if p.exists() else bad)(f"{v.name}/{f} {'存在' if p.exists() else '缺失'}")
        compose = v / "docker-compose.yml"
        if compose.exists():
            doc = yaml.safe_load(compose.read_text(encoding="utf-8"))
            svcs = set((doc.get("services") or {}).keys())
            want = {"postgres", "redis", "api", "web", "opencode", "llm-shim"}
            if want <= svcs:
                ok(f"{v.name}/docker-compose.yml 六个服务齐全")
            else:
                bad(f"{v.name}/docker-compose.yml 缺服务：{sorted(want - svcs)}")
            # 只看**真的有 build: 段**的服务，不要拿字符串去 grep 全文 ——
            # 文件顶上那句「不要写 build:」的中文警告会被误判成违规。
            builders = [n for n, s in (doc.get("services") or {}).items() if "build" in (s or {})]
            if builders:
                bad(f"{v.name}/docker-compose.yml 这些服务有 build: 段：{builders}（云/面板环境没有仓库目录）")
            else:
                ok(f"{v.name}/docker-compose.yml 没有服务带 build: 段")
            # 同理查仓库内文件的 bind mount（相对路径挂到镜像里的代码位置）
            repo_mounts = [
                f"{n}:{m}" for n, s in (doc.get("services") or {}).items()
                for m in ((s or {}).get("volumes") or [])
                if isinstance(m, str) and m.startswith(("./", "../")) and "/data/" not in m
            ]
            if repo_mounts:
                bad(f"{v.name}/docker-compose.yml 有可疑的相对路径挂载：{repo_mounts}")
            else:
                ok(f"{v.name}/docker-compose.yml 的相对挂载都在 ./data/ 下（面板备份打得进去）")


# ══════════════════════════════════════════════════════════════════════
IMAGES = ("api", "web", "opencode", "llm-shim")


def check_railway() -> None:
    print("\n[5/6] Railway 手工搭建清单 · 结构自检")
    f = ROOT / "deploy/railway/services.yaml"
    if not f.exists():
        skip("deploy/railway/services.yaml 不存在")
        return
    doc = yaml.safe_load(f.read_text(encoding="utf-8"))
    svcs = {s["name"]: s for s in doc.get("services") or []}

    want = {"postgres", "redis", "llmshim", "api", "opencode", "web"}
    (ok if want <= set(svcs) else bad)(
        f"六个服务齐全：{sorted(svcs)}" if want <= set(svcs) else f"少了服务：{sorted(want - set(svcs))}")

    # 服务名不能带连字符 —— 引用变量 ${{名.变量}} 里带连字符能不能解析官方没写
    bads = [n for n in svcs if "-" in n]
    (ok if not bads else bad)("服务名都不带连字符（引用变量安全）" if not bads
                              else f"服务名带连字符，引用变量可能解析不了：{bads}")

    # 只有 web 对外
    pub = [n for n, v in svcs.items() if v.get("public")]
    (ok if pub == ["web"] else bad)("只有 web 对外暴露" if pub == ["web"] else f"对外的服务不对：{pub}")

    # 一个服务只能挂一个卷（Railway 硬限制）—— 清单里 volume 是单数字段，这里防手滑写成列表
    multi = [n for n, v in svcs.items() if isinstance(v.get("volume"), list)]
    (ok if not multi else bad)("每个服务至多一个卷（Railway 限制）" if not multi
                               else f"这些服务写了多个卷：{multi}")

    env = {n: {k: str(v.get("value", "")) for k, v in (s.get("variables") or {}).items()}
           for n, s in svcs.items()}

    # PGDATA 必须是挂载点的子目录（ext4 的 lost+found 会让 initdb 拒绝）
    mp = (svcs["postgres"].get("volume") or {}).get("mountPath", "")
    pgdata = env["postgres"].get("PGDATA", "")
    (ok if pgdata.startswith(mp + "/") else bad)(
        f"PGDATA 是挂载点的子目录（{pgdata}）" if pgdata.startswith(mp + "/")
        else f"PGDATA={pgdata!r} 不是 {mp} 的子目录 —— postgres 会因 lost+found 无限重启")

    # 非 root 镜像 + 卷 → 必须 RAILWAY_RUN_UID=0
    for n in ("postgres", "redis", "opencode"):
        v = env[n].get("RAILWAY_RUN_UID")
        (ok if v == "0" else bad)(f"{n} 设了 RAILWAY_RUN_UID=0" if v == "0"
                                  else f"{n} 没设 RAILWAY_RUN_UID=0 —— 卷是 root 属主的，写不进去")

    # api 的卷不能遮住镜像自带的静态数据目录
    amp = (svcs["api"].get("volume") or {}).get("mountPath", "")
    (ok if amp != "/opt/hunter-data" else bad)(
        f"api 的卷挂在 {amp}（没遮住镜像的 /opt/hunter-data）" if amp != "/opt/hunter-data"
        else "api 的卷挂在 /opt/hunter-data，会把镜像自带的向导预设遮掉")

    # 两个必设、且最容易被漏掉的跨服务地址
    for n, k in (("api", "LLM_SHIM_URL"), ("api", "OPENCODE_URL"),
                 ("opencode", "HERMES_API_URL"), ("web", "HERMES_API_URL"),
                 ("web", "OPENCODE_URL")):
        v = env[n].get(k, "")
        (ok if v.startswith("http") else bad)(f"{n}.{k} 已设" if v.startswith("http")
                                              else f"{n}.{k} 没设或不是 http 地址")

    # 三家必须同值的密钥
    same = (env["opencode"].get("JWT_SECRET") == "${{api.JWT_SECRET}}"
            and env["opencode"].get("HUNTER_INTERNAL_KEY") == "${{api.HUNTER_INTERNAL_KEY}}"
            and env["web"].get("HUNTER_INTERNAL_KEY") == "${{api.HUNTER_INTERNAL_KEY}}")
    (ok if same else bad)("opencode / web 用引用变量取 api 的那把密钥（不会填错）" if same
                          else "密钥没用引用变量 —— 三处填不一致就会「服务全绿但一对话 401」")

    (ok if env["api"].get("HUNTER_SINGLE_USER") == "0" else bad)(
        "api 关掉了单用户模式（公网必须）" if env["api"].get("HUNTER_SINGLE_USER") == "0"
        else "api 的 HUNTER_SINGLE_USER 不是 0 —— 公网上谁打开谁就是 admin")

    tok = env["api"].get("HUNTER_SETUP_TOKEN", "")
    (ok if tok.startswith("${{secret(") else bad)(
        "初始化口令由 secret() 生成" if tok.startswith("${{secret(")
        else f"HUNTER_SETUP_TOKEN={tok!r} —— 必须由变量函数生成，不能写死也不能留空")


def check_paas_compose() -> None:
    print("\n[6/6] Coolify / Dokploy compose · 结构自检与一致性")
    a = ROOT / "deploy/coolify/docker-compose.yml"
    b = ROOT / "deploy/dokploy/docker-compose.yml"
    if not (a.exists() and b.exists()):
        skip("Coolify / Dokploy 的 compose 不齐")
        return
    ca, cb = (yaml.safe_load(f.read_text(encoding="utf-8")) for f in (a, b))

    want = {"web", "api", "opencode", "llm-shim", "postgres", "redis"}
    for name, c in (("coolify", ca), ("dokploy", cb)):
        got = set(c.get("services") or {})
        (ok if want <= got else bad)(f"{name}：六个服务齐全" if want <= got
                                     else f"{name}：少了 {sorted(want - got)}")
        builds = [n for n, v in (c["services"] or {}).items() if v.get("build")]
        (ok if not builds else bad)(f"{name}：没有服务带 build:" if not builds
                                    else f"{name}：这些服务带了 build:{builds}")
        # 宿主端口映射 —— 对外靠平台反代，映射端口等于把库挂到公网 IP 上
        ports = [n for n, v in (c["services"] or {}).items() if v.get("ports")]
        (ok if not ports else bad)(f"{name}：没有宿主端口映射（对外交给平台反代）" if not ports
                                   else f"{name}：这些服务映射了宿主端口：{ports}")
        # 相对路径挂载 —— 平台上没有本仓源码
        rel = [f"{n}:{m}" for n, v in (c["services"] or {}).items()
               for m in (v.get("volumes") or []) if isinstance(m, str) and m.startswith(".")]
        (ok if not rel else bad)(f"{name}：没有相对路径挂载" if not rel
                                 else f"{name}：有相对路径挂载(平台上没有本仓源码)：{rel}")
        # 三家共享密钥卷
        shared = [n for n in ("api", "web", "opencode")
                  if any("hunter_secrets" in m for m in (c["services"][n].get("volumes") or []))]
        (ok if len(shared) == 3 else bad)(
            f"{name}：api/web/opencode 三家共挂 hunter_secrets 卷" if len(shared) == 3
            else f"{name}：只有 {shared} 挂了密钥卷 —— 少一家就会一对话 401")
        (ok if (c["services"]["api"]["environment"].get("HUNTER_SINGLE_USER") == "0") else bad)(
            f"{name}：关掉了单用户模式" if c["services"]["api"]["environment"].get("HUNTER_SINGLE_USER") == "0"
            else f"{name}：HUNTER_SINGLE_USER 不是 0")

    # 两份文件必须只差「随机值谁生成 / 域名怎么绑」那几处，别的不许漂
    def norm(c):
        out = {}
        for n, v in c["services"].items():
            out[n] = {"image": v.get("image"), "volumes": v.get("volumes"),
                      "depends_on": v.get("depends_on"),
                      "env_keys": sorted(set(v.get("environment") or {}) - {"SERVICE_FQDN_WEB_3000"})}
        return out
    (ok if norm(ca) == norm(cb) else bad)(
        "两份 compose 的镜像/卷/依赖/环境变量名完全一致（不会各自漂移）" if norm(ca) == norm(cb)
        else "两份 compose 出现了不该有的差异，逐项对一下")
    (ok if sorted(ca.get("volumes") or {}) == sorted(cb.get("volumes") or {}) else bad)(
        "两份 compose 的卷清单一致" if sorted(ca.get("volumes") or {}) == sorted(cb.get("volumes") or {})
        else "两份 compose 的卷清单不一致")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true", help="重新下载 Zeabur 官方 schema")
    args = ap.parse_args()

    print("=" * 70)
    print("一键部署模板静态校验")
    print("=" * 70)
    check_yaml_syntax()
    check_zeabur(args.online)
    check_sealos()
    check_1panel()
    check_railway()
    check_paas_compose()

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  ❌ {f}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
