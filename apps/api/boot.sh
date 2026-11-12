#!/bin/sh
# api 容器入口 · 三件事:① 密钥就位 ② 数据库迁移 ③ 起 uvicorn
#
# 为什么要有这个脚本:云平台和「git clone 就 up」的本地用户都不会先去写 .env,
# 而 JWT_SECRET 缺了整个加密链就用不了、数据库缺表接口就 500。这两件事必须在
# 进程起来之前做完,而且要能在**只读仓库、没有 shell 交互**的环境里自动完成。
set -e

# ── ① 密钥(见 3.2)────────────────────────────────────────────
#
# 支持文件挂载读取 LLM_API_KEY（HCA I1 §1.1）：当 LLM_API_KEY 为空而
# LLM_API_KEY_FILE 或 HCA_LLM_API_KEY_FILE 可读时，自动从文件加载。
_llm_key_file="${LLM_API_KEY_FILE:-${HCA_LLM_API_KEY_FILE:-}}"
if [ -z "${LLM_API_KEY:-}" ] && [ -n "$_llm_key_file" ]; then
    if [ -r "$_llm_key_file" ]; then
        LLM_API_KEY="$(tr -d '\r\n' < "$_llm_key_file")"
        export LLM_API_KEY
        echo "[boot] 模型 key 取自文件 $_llm_key_file（长度 ${#LLM_API_KEY}）"
    else
        echo "[boot] WARN LLM_API_KEY_FILE=$_llm_key_file 不可读（不存在或无权限）" >&2
    fi
fi

# 优先级:**环境变量非空 → hunter_secrets 卷里的 secrets.env → 本次生成**。
#
# ⚠️ 判断一律用「非空」而不是「已定义」:compose 的 `${JWT_SECRET:-}` 会把
# 「用户没设」变成**空字符串**注入容器,`[ -z ]` 才认得出来。
#
# 为什么要落到卷里而不是每次现生成:JWT_SECRET 派生了加密已存 key 的 AES 密钥
# (app/utils/crypto.py),每次重启换一个的话,用户上次填的平台 key / 大模型 key
# 全部解不开,而且所有人的登录态失效。卷是这套部署里唯一保留的共享卷,只存两行。
# opencode 以只读方式挂同一个卷,靠 scripts/opencode/load-secrets.sh 读同一份值
# (它的 hunter-auth 插件要用 JWT_SECRET 验签)。
#
# 日志里**绝不打印密钥本身**,只说来源和长度。

HUNTER_SECRETS_DIR="${HUNTER_SECRETS_DIR:-/opt/hunter-secrets}"
HUNTER_SECRETS_FILE="$HUNTER_SECRETS_DIR/secrets.env"

# .env.example 里的示例值。照抄它 = 把公开字符串当密钥用。
_JWT_SAMPLE='change-me-in-production-please'

# 随机串。api 镜像是 python:3.11-slim —— 实测 /usr/bin/openssl **在**
# (`docker run --rm --entrypoint sh ...api:latest -c 'which openssl'` → /usr/bin/openssl),
# 但基础镜像换个 tag 就可能没有,所以留了 python3 的退路(它一定在)。
# base64 里的 `+` `/` `=` 去掉,免得值在 `.`(source)这个文件时被 shell 解释。
_gen_chunk() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 48 | tr -d '\n=+/'
    else
        python3 -c 'import secrets;print(secrets.token_urlsafe(48))'
    fi
}

_gen_secret() {
    _v=''
    while [ "${#_v}" -lt 48 ]; do
        _v="$_v$(_gen_chunk)"
    done
    printf '%s' "$_v" | cut -c1-64
}

# 从 secrets.env 里取一行的值。**不用 `.`(source)** —— source 会把文件里的值
# 盖掉环境变量里的值,而优先级正好相反。
_read_secret() {
    [ -r "$HUNTER_SECRETS_FILE" ] || return 0
    sed -n "s/^$1=//p" "$HUNTER_SECRETS_FILE" | head -n 1
}

_jwt_src=''
_int_src=''

if [ -n "${JWT_SECRET:-}" ]; then
    _jwt_src='环境变量'
fi
if [ -n "${HUNTER_INTERNAL_KEY:-}" ]; then
    _int_src='环境变量'
fi

if [ -z "$_jwt_src" ] || [ -z "$_int_src" ]; then
    if [ -r "$HUNTER_SECRETS_FILE" ]; then
        if [ -z "$_jwt_src" ]; then
            JWT_SECRET="$(_read_secret JWT_SECRET)"
            [ -n "$JWT_SECRET" ] && _jwt_src='密钥卷'
        fi
        if [ -z "$_int_src" ]; then
            HUNTER_INTERNAL_KEY="$(_read_secret HUNTER_INTERNAL_KEY)"
            [ -n "$HUNTER_INTERNAL_KEY" ] && _int_src='密钥卷'
        fi
    fi
fi

if [ -z "$_jwt_src" ]; then
    JWT_SECRET="$(_gen_secret)"
    _jwt_src='本次生成'
fi
if [ -z "$_int_src" ]; then
    HUNTER_INTERNAL_KEY="$(_gen_secret)"
    _int_src='本次生成'
fi

export JWT_SECRET HUNTER_INTERNAL_KEY

# 把「这把密钥是从哪来的」也导出去。向导第 1 步(setup_probe._secret_source)要报这个，
# 而它在 api 进程里**推不出来**:上面那个 export 之后 os.environ 一定非空,而
# _persist_secrets 又会把环境变量来的值也写回密钥卷文件 —— 靠比对文件内容永远得到
# 「来自卷」。只有这里知道真相。
HUNTER_SECRET_SRC_JWT_SECRET="$(case "$_jwt_src" in 环境变量) echo env;; 密钥卷) echo volume;; *) echo generated;; esac)"
HUNTER_SECRET_SRC_HUNTER_INTERNAL_KEY="$(case "$_int_src" in 环境变量) echo env;; 密钥卷) echo volume;; *) echo generated;; esac)"
export HUNTER_SECRET_SRC_JWT_SECRET HUNTER_SECRET_SRC_HUNTER_INTERNAL_KEY

# 把当前生效的两个值写回卷,让下次重启、以及 opencode / web 容器拿到同一份。
# **两个都写**(哪怕来自环境变量):这样用户以后从 .env 里删掉它们,值也不会变,
# 已加密的 key 仍然解得开。内容没变就不动文件,免得每次重启都改 mtime。
#
# 文件格式是最朴素的 `KEY=value` 每行一个,能被 sh 的 `.`(source)直接读 ——
# 值只含 [A-Za-z0-9](生成时已去掉 base64 的 `+` `/` `=`),不需要引号也不会被
# shell 解释。别往里加注释、空格或引号,读它的那几个脚本都按这个格式来。
#
# **属主 1001:1001 + 权限 640,不是随手定的**,三个容器的运行用户不一样:
#   · api      镜像没有 USER 指令        → root(uid 0)  ← 写这个文件的是它
#   · opencode 镜像 USER=hunter          → uid 1001     ← 只读挂载,要读它
#   · web      镜像 USER=nextjs(-u 1001)→ uid 1001     ← 同上
# root 怎么都读得到,所以属主给 1001、组内可读(640)正好覆盖另外两个容器。
# 留 600 + root 属主的话,opencode 验不了签、web 调 /api/internal/* 401,
# 表现是「对话莫名 401」「上传图片没反应」,极难往密钥文件上想。
_persist_secrets() {
    _want="JWT_SECRET=$JWT_SECRET
HUNTER_INTERNAL_KEY=$HUNTER_INTERNAL_KEY"
    if [ -r "$HUNTER_SECRETS_FILE" ] && [ "$(cat "$HUNTER_SECRETS_FILE")" = "$_want" ]; then
        _perm_note="$(_perm_of "$HUNTER_SECRETS_FILE")"
        return 0
    fi
    mkdir -p "$HUNTER_SECRETS_DIR" 2>/dev/null || return 1
    _tmp="$HUNTER_SECRETS_FILE.tmp.$$"
    ( umask 077; printf '%s\n' "$_want" > "$_tmp" ) 2>/dev/null || return 1
    if chown 1001:1001 "$_tmp" 2>/dev/null && chmod 640 "$_tmp" 2>/dev/null; then
        _perm_note="属主 1001:1001 · 权限 640"
    else
        # 只读挂载、平台不给 chown(K8s 上常见)、或者本来就不是 root 在跑。
        # 降级成 644:所有容器都读得到。宁可放宽到「同机进程可读」,也不能让
        # 服务之间密钥对不上 —— 那会表现成一堆莫名其妙的 401。
        chmod 644 "$_tmp" 2>/dev/null || true
        _perm_note="⚠ chown 1001 失败 · 降级为权限 644(opencode/web 才读得到)"
        echo "[boot] WARN  无法把 $HUNTER_SECRETS_FILE 的属主改成 1001:1001," >&2
        echo "[boot] WARN    已降级为 644 让 opencode(uid 1001)与 web(uid 1001)能读。" >&2
    fi
    mv "$_tmp" "$HUNTER_SECRETS_FILE" 2>/dev/null || { rm -f "$_tmp" 2>/dev/null; return 1; }
    return 0
}

# 已存在的文件也要报一下权限,免得「上一次降级成 644」这件事没人知道
_perm_of() {
    _m="$(ls -l "$1" 2>/dev/null | cut -c1-10)"
    printf '沿用已有文件(%s)' "${_m:-权限未知}"
}

if _persist_secrets; then
    _persist_note="密钥卷可写($HUNTER_SECRETS_FILE · ${_perm_note:-已写入})"
    # 写成功了不等于写进了**卷**。没挂 hunter_secrets 时 mkdir 会在容器可写层上
    # 成功,而可写层 recreate 就没 —— 症状是「每次 docker compose up -d 之后
    # 所有 key 都解不开、所有人退出登录」,几乎不可能往这里想。
    # 判据见仓内 CLAUDE.md「挂了卷不等于状态都进卷了 · 用 df 逐个查」。
    case "$(df -P "$HUNTER_SECRETS_DIR" 2>/dev/null | tail -n 1 | awk '{print $1}')" in
        overlay|overlayfs|none)
            # ⚠️ 只有当密钥**不是**全部来自环境变量时才告警。
            #
            # 云平台(Zeabur / Sealos / 1Panel 之外的 K8s 场景)不支持跨服务共享卷,
            # 模板改成由平台生成随机值、注入成环境变量给 api / opencode / web 三家 ——
            # 这时 /opt/hunter-secrets 落在可写层上是**正常且无害**的:下次重启平台
            # 还会注入同样的值,密钥根本不会变。
            #
            # M3 实测(2026-09-18,Zeabur 等价 compose 从空卷启动):这里会无条件打出
            # 「已保存的 key 会全部解不开、登录全部失效」,而当时两把密钥都来自环境变量,
            # 一个字都不成立。云用户看到这行只会白白吓一跳、或者去找一个根本不存在的卷。
            if [ "$_jwt_src" = '环境变量' ] && [ "$_int_src" = '环境变量' ]; then
                echo "[boot] INFO  $HUNTER_SECRETS_DIR 在容器可写层上(没挂 hunter_secrets 卷)," >&2
                echo "[boot] INFO    但两把密钥都来自环境变量 —— 重启后值不变,不影响已保存的 key。" >&2
                _persist_note="缓存在容器可写层($HUNTER_SECRETS_FILE)· 密钥以环境变量为准"
            else
                echo "[boot] WARN  $HUNTER_SECRETS_DIR 在容器可写层上,没有挂 hunter_secrets 卷。" >&2
                echo "[boot] WARN    密钥只活到下次 recreate;之后已保存的 key 会全部解不开、登录全部失效。" >&2
                echo "[boot] WARN    处理:确认 compose 里 api 挂了 hunter_secrets:$HUNTER_SECRETS_DIR," >&2
                echo "[boot] WARN    或(云平台上)由模板把 JWT_SECRET 与 HUNTER_INTERNAL_KEY 注入成环境变量。" >&2
                _persist_note="⚠ 写在容器可写层上($HUNTER_SECRETS_FILE)· 没挂 hunter_secrets 卷 · 重启后密钥会变"
            fi
            ;;
    esac
else
    # 只读挂载 / 属主不对 / 压根没挂卷。**不退出** —— 内存里的值足够这一次启动跑起来。
    _persist_note="⚠ 写不进 $HUNTER_SECRETS_FILE(只读挂载?属主不对?没挂 hunter_secrets 卷?)"
    echo "[boot] ERROR 密钥写不进 $HUNTER_SECRETS_DIR —— 本次用内存里的值继续启动。" >&2
    if [ "$_jwt_src" = '本次生成' ]; then
        echo "[boot] ERROR   后果:**重启后 JWT_SECRET 会变**,届时已保存的平台 key /" >&2
        echo "[boot] ERROR   大模型 key 全部解不开(它派生加密用的 AES 密钥),登录也会全部失效。" >&2
        echo "[boot] ERROR   处理:在 .env 里显式设置 JWT_SECRET 与 HUNTER_INTERNAL_KEY," >&2
        echo "[boot] ERROR   或确认 compose 里 api 对 hunter_secrets 卷有写权限(不是 :ro)。" >&2
    fi
fi

# 弱密钥检测 —— **只告警,不自动轮换**。
# 轮换等于把已加密的 key 全部变成解不开的乱码、并让所有人退出登录;
# 这个代价必须由用户知情后自己承担,不能由一次重启悄悄替他决定。
if [ "$JWT_SECRET" = "$_JWT_SAMPLE" ]; then
    echo "[boot] ERROR JWT_SECRET 用的是 .env.example 里的示例值(公开字符串)。" >&2
    echo "[boot] ERROR   任何人都能伪造登录凭证;它还派生了加密已存 key 的 AES 密钥。" >&2
    echo "[boot] ERROR   处理:在 .env 里换成随机值(openssl rand -base64 48)后重启。" >&2
    echo "[boot] ERROR   ⚠ 换了之后**已保存的平台 key / 大模型 key 需要重新填**(密文解不开)," >&2
    echo "[boot] ERROR   所有人也要重新登录 —— 所以这里只告警,不替你自动轮换。" >&2
elif [ "${#JWT_SECRET}" -lt 32 ]; then
    echo "[boot] ERROR JWT_SECRET 只有 ${#JWT_SECRET} 个字符(建议 ≥48,至少 32)。" >&2
    echo "[boot] ERROR   太短的密钥可被离线爆破,而它派生了加密已存 key 的 AES 密钥。" >&2
    echo "[boot] ERROR   处理与后果同上;这里只告警,不自动轮换。" >&2
fi

echo "[boot] 密钥就位 · JWT_SECRET 来源=$_jwt_src 长度=${#JWT_SECRET}" \
     "· HUNTER_INTERNAL_KEY 来源=$_int_src 长度=${#HUNTER_INTERNAL_KEY} · $_persist_note"

# ── ② 数据库迁移(见 3.4)──────────────────────────────────────
# 由 M1 子任务 B 提供 app/migrate.py;失败必须让容器起不来,
# 缺表的实例「看着是健康的、一点就 500」比直接起不来难排查得多。
python -m app.migrate

# ── ②a 智能炒股 fin_paper_rw 角色口令自动同步 ────────────────────
# 0023_fin_core.sql 创建了 NOLOGIN 的 fin_paper_rw 角色。当环境变量提供了
# FIN_PAPER_PASSWORD 时，自动用幂等参数化 SQL 启用 LOGIN 并同步口令，无需手工敲脚本。
if [ -n "${FIN_PAPER_PASSWORD:-}" ]; then
    python - <<'PYEOF'
import os
import psycopg2
from psycopg2 import sql

db_url = os.environ.get("DATABASE_URL", "")
pwd = os.environ.get("FIN_PAPER_PASSWORD", "")
if db_url and pwd:
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE fin_paper_rw WITH LOGIN PASSWORD {}").format(
                    sql.Literal(pwd)
                )
            )
        print("[boot] fin_paper_rw 角色 LOGIN 口令已同步（长度 %d）" % len(pwd))
    finally:
        conn.close()
PYEOF
fi

# ── ②b 升级提示:用户 SKILL 卷是空的 ───────────────────────────
# v1.0.x 把 ./user-skills 和 ./data-packages 直接 bind mount 给 api;v1.1.0 起改成
# api 自己的具名卷(云平台上没有仓库目录,挂不了)。直接升级的话新卷是空的,
# 用户装过的 SKILL 会从界面上消失 —— 文件一个都没丢,只是容器看不到了。
#
# 这里检测不到宿主机上那个老目录(它已经不挂进来了),所以只能提示,不能自动搬。
# 但**空卷**这个信号本身足够准:全新安装也是空的,那时这句提示无害;
# 老用户看到它就知道该去跑那个脚本了。比让他自己发现「SKILL 不见了」强得多。
USER_SKILLS_DIR="${HUNTER_USER_SKILLS_DIR:-/opt/hunter-user-skills}"
PACKAGE_DIR="${HUNTER_PACKAGE_DIR:-/opt/hunter-packages}"

# 两个目录自己建出来。默认 compose 上它们是各自的具名卷、本来就存在,这一行是白做的;
# **Railway 上不是** —— 那里一个服务只能挂一个卷(官方文档明写的限制),所以 api 的两份
# 数据只能塞进同一个卷的两个子目录,而子目录在空卷里并不存在。不建的话向导第 1 步
# 会把两项都报成红色的「目录不存在」,而其实只差一个 mkdir。
mkdir -p "$USER_SKILLS_DIR" "$PACKAGE_DIR" 2>/dev/null || true

if [ -d "$USER_SKILLS_DIR" ] && [ -z "$(ls -A "$USER_SKILLS_DIR" 2>/dev/null)" ]; then
    echo "[boot] 提示:用户 SKILL 目录 $USER_SKILLS_DIR 是空的。"
    echo "[boot]   全新安装可忽略这句。**如果你是从 v1.0.x 升级、之前在界面里装过 SKILL**,"
    echo "[boot]   它们还在部署目录的 user-skills/ 下,只是 v1.1.0 起改用具名卷了。"
    echo "[boot]   在部署目录执行一次:bash scripts/migrate-volumes.sh"
fi

# ── ③ 启动 ────────────────────────────────────────────────────
# 监听地址默认 0.0.0.0(IPv4)。Railway 的**老环境**(2025-10-16 之前创建)私有网络
# 是 IPv6-only,绑 0.0.0.0 的服务在那里互相连不上 —— 官方给的办法就是改绑 `::`。
# 见 https://docs.railway.com/networking/private-networking/how-it-works
# 留成变量、默认值不变:本地与其他平台一个字都不用改。
#
# 不直接 `uvicorn --host` 的原因见 bind.py 的文件头(一句话:`--host ::` 起出来的
# socket **只收 IPv6**,IPv4 侧全是 Connection refused,而日志里一切正常)。
exec python bind.py
