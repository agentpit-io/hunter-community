#!/usr/bin/env python3
"""给**已经装好**的用户 SKILL 补齐缺失的文档类附属文件。

## 为什么需要它

2026-09-07 之前,`skill_install.install()` 只搬 SKILL.md,作者拆在
`references/*.md`、`templates/*.md` 里的方法论一个都没装。模型读到
「数据源规则见 `references/data-sources.md`」就去找,找不到就空转、
而且不报错 —— 用户看到的是"这个 skill 点了没反应"。

`9a304be` 修了安装路径,但那**只对以后新装的生效**。已经装在
`user-skills/` 里的存量得靠这个脚本补。

## 用法

代码不在镜像里(scripts/ 不参与 COPY),拷进容器跑:

    C=$(docker ps -qf name=community-api)
    docker cp scripts/repair_skill_assets.py $C:/tmp/
    docker exec $C python /tmp/repair_skill_assets.py --dry-run   # 先看要动什么
    docker exec $C python /tmp/repair_skill_assets.py

## 边界

- **只新增文件,绝不改 SKILL.md** —— 用户可能改过正文,重装会丢。
- 只补文档类。脚本(.py/.sh/.js/.ts)仍然一律不装,理由见
  `skill_install` 模块开头那条安全线;它们由 `blocked_refs` 单独报出,
  UI 上会写明为什么。
- 只处理 `origin: github:owner/repo@ref` 的。手动新建、或者早期没记
  origin 的补不了 —— 会在报告里列出来,让人知道是**补不了**而不是漏了。
- 写盘走 `skill_files.save_assets()`,路径穿越与体积上限的兜底都在那里,
  这里不重复实现。
"""
from __future__ import annotations

import io
import sys
import tarfile
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

from app.services import skill_files  # noqa: E402

_UA = {"User-Agent": "hunter-community-skill-repair"}
_TIMEOUT = 60
_MAX_TARBALL = 50 * 1024 * 1024


def _parse_origin(origin: str) -> tuple[str, str, str] | None:
    """`github:owner/repo@ref` → (owner, repo, ref)"""
    if not origin.startswith("github:"):
        return None
    rest = origin[len("github:"):]
    ref = "main"
    if "@" in rest:
        rest, ref = rest.rsplit("@", 1)
    if "/" not in rest:
        return None
    owner, repo = rest.split("/", 1)
    return owner, repo, ref


def _fetch_tar(owner: str, repo: str, ref: str) -> tarfile.TarFile | None:
    # 分支拿不到就退一步试 tag —— 与 skill_install 保持一致
    urls = [f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{ref}",
            f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/tags/{ref}"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for url in urls:
        try:
            with opener.open(urllib.request.Request(url, headers=_UA), timeout=_TIMEOUT) as r:
                raw = r.read(_MAX_TARBALL + 1)
            if len(raw) > _MAX_TARBALL:
                print(f"    仓库过大,跳过")
                return None
            return tarfile.open(fileobj=io.BytesIO(raw))
        except Exception:
            continue
    return None


def _find_member(tf: tarfile.TarFile, rel: str) -> bytes | None:
    """在 tarball 里找这个相对路径对应的文件。

    **按后缀匹配而不是拼绝对路径** —— origin 只记了 repo@ref,没记 SKILL.md
    当初在仓库里的哪个目录,拼不出准确路径。按 `/{rel}` 结尾找,命中唯一就用它;
    命中多个说明仓库里有同名文件,不猜、跳过(宁可少补一个,也不要放错内容)。
    """
    want = "/" + rel.lstrip("/")
    hits = [n for n in tf.getnames() if n.endswith(want)]
    if len(hits) != 1:
        return None
    try:
        f = tf.extractfile(hits[0])
        return f.read() if f is not None else None
    except Exception:
        return None


def _locate_skill_dir(tf: tarfile.TarFile, slug: str) -> str | None:
    """SKILL.md 在 tarball 里的所在目录(去掉顶层 repo-ref/)。

    仓库里可能有多个 SKILL.md(一个仓装了好几个 skill),用目录名跟 slug
    对一下挑出属于这个 skill 的那个。slug 是安装时把名字做过替换的
    (`-` → `_`),所以两边都归一化再比。挑不出唯一的就返回 None ——
    宁可只补 missing_refs 里点名的,也不要把别的 skill 的文件搬进来。
    """
    names = [n for n in tf.getnames() if n.endswith("/SKILL.md")]
    if not names:
        return None
    def norm(x: str) -> str:
        return x.lower().replace("-", "_").replace(" ", "_")
    for n in names:
        parts = n.split("/")
        if len(parts) < 3:          # repo-ref/SKILL.md → 在仓库根,没有子目录
            continue
        if norm(parts[-2]) == norm(slug):
            return "/".join(parts[1:-1])
    return None


def _subtree_docs(tf: tarfile.TarFile, base: str) -> dict[str, bytes]:
    """base 目录下的全部文档类文件,键是相对 base 的路径。"""
    root = tf.getnames()[0].split("/")[0]
    prefix = f"{root}/{base}/"
    out: dict[str, bytes] = {}
    for name in tf.getnames():
        if not name.startswith(prefix):
            continue
        rel = name[len(prefix):]
        if not rel or rel == "SKILL.md" or rel.endswith("/"):
            continue
        if skill_files.is_exec_ref(rel) or not rel.lower().endswith(skill_files.DOC_EXTS):
            continue
        try:
            f = tf.extractfile(name)
            if f is not None:
                out[rel] = f.read()
        except Exception:
            continue
    return out


def main() -> int:
    dry = "--dry-run" in sys.argv
    d = skill_files.USER_SKILLS_DIR
    if not d.is_dir():
        print(f"用户 SKILL 目录不存在: {d}")
        return 1

    fixed = skipped = 0
    for sub in sorted(p for p in d.iterdir() if p.is_dir()):
        f = sub / "SKILL.md"
        if not f.is_file():
            continue
        fm, body = skill_files._parse_frontmatter(f.read_text(encoding="utf-8"))
        miss = skill_files.missing_refs(sub, body, limit=99)
        if not miss:
            continue

        h = fm.get("hunter") or {}
        origin = str(fm.get("origin")
                     or (h.get("origin") if isinstance(h, dict) else "") or "")
        parsed = _parse_origin(origin)
        print(f"\n[{sub.name}] 缺 {len(miss)} 个: {', '.join(miss[:6])}"
              + (" …" if len(miss) > 6 else ""))
        if not parsed:
            print(f"    补不了 —— origin 不是 github({origin or '空'})")
            skipped += 1
            continue

        owner, repo, ref = parsed
        print(f"    来源 {owner}/{repo}@{ref}")
        if dry:
            continue
        tf = _fetch_tar(owner, repo, ref)
        if tf is None:
            print("    下载失败,跳过")
            skipped += 1
            continue

        got: dict[str, bytes] = {}

        # 先按 SKILL.md 在仓库里的位置整个子树搬 —— 和 skill_install._collect_assets
        # 的 ① 一个道理:正则只认反引号包着的引用,作者写 markdown 粗体
        # (`- **configuration.md** - 配置详解`)就抓不到,那些文件不会出现在
        # missing_refs 里,只补 missing_refs 会漏掉一大半。
        base = _locate_skill_dir(tf, sub.name)
        if base:
            print(f"    SKILL.md 在仓库的 {base}/ 下,整个子树一起补")
            got.update(_subtree_docs(tf, base))

        for rel in miss:
            if rel in got:
                continue
            data = _find_member(tf, rel)
            if data is None:
                # 仓库里确实没有 —— 多半是正文代码示例里的文件名
                # (wtpy 的 configbt.yaml 就是 engine.init() 的参数),
                # 不是该随 SKILL 附带的东西。如实说明,别让人以为是我们漏装。
                print(f"    仓库里也没有: {rel} (可能是正文提到的外部文件,不是附件)")
                continue
            got[rel] = data
        if not got:
            skipped += 1
            continue
        written = skill_files.save_assets(sub.name, got)
        print(f"    补上 {len(written)} 个")
        fixed += 1

        left = skill_files.missing_refs(sub, body, limit=99)
        print(f"    剩余缺失: {left or '无 ✅'}")

    print(f"\n{'(dry-run) ' if dry else ''}补好 {fixed} 个,跳过 {skipped} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
