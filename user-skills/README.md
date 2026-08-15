# 你自己的 SKILL 放这里

这个目录里的 SKILL 会和我们内置的 29 个一起加载,**同名时你的覆盖我们的**。

## 加一个 SKILL

1. 从网上下载(或自己写)一个标准 SKILL —— 就是一个目录 + 一个 `SKILL.md`:

   ```
   user-skills/
     my-skill/
       SKILL.md
   ```

2. `SKILL.md` 的格式(Anthropic Agent Skills 标准 · opencode 原生支持):

   ```markdown
   ---
   name: my-skill
   description: 一句话说明什么时候该用它 —— 模型据此判断要不要调用
   ---

   # 正文写方法论
   分几步、先看什么后看什么、注意什么。
   ```

   `name` 不填就用目录名。**网上下载的标准 skill 不用改一个字**。

3. `docker compose restart opencode api`

## 想让它用我们的数据和工具

在 frontmatter 里加一段 `hunter:`(标准加载器会忽略它,不影响兼容):

```yaml
---
name: my-skill
description: ...
hunter:
  display_name: 我的分析法
  icon: "🔍"
  category: 综合分析
  needs_tools:
    - uzi_stock_deep_analysis
    - hunter_cap_kpred
---
```

可用的工具名见侧栏「工具箱」。用到我们的数据源与工具需要平台 key ——
没填 key 时这些 SKILL 仍会显示,点了会提示去申请。
