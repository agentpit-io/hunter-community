"""Phase 0.3 · oneapi 网关连通性测试

目的：验证在线分析功能依赖的 LLM 网关是否正常工作。

测试项：
  1. 网关可达性（HTTP 连接）
  2. Gemini Flash 普通调用
  3. Gemini Flash + response_format=json_object 模式
  4. Sonnet（如果配置了）作为 F7 Stage A 备选模型

运行：
  cd hermes/api && python scripts/test_oneapi_gateway.py
"""
import json
import os
import sys
import time

# 允许从 api/ 目录直接跑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载 .env（如果存在）
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
if os.path.exists(_env_path):
    for line in open(_env_path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from openai import OpenAI

BASE_URL = os.environ.get("ONE_API_BASE_URL", "http://104.197.139.51:3000/v1")
API_KEY  = os.environ.get("ONE_API_KEY", "")
MODEL    = os.environ.get("ONE_API_MODEL", "gemini-3.5-flash")


def banner(text: str):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def test_1_connectivity():
    banner("Test 1 · 网关连通性")
    import urllib.request
    try:
        req = urllib.request.Request(f"{BASE_URL}/models",
                                     headers={"Authorization": f"Bearer {API_KEY}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        models = [m.get("id") for m in data.get("data", [])]
        print(f"  ✓ 连接成功，网关返回 {len(models)} 个模型")
        if MODEL in models:
            print(f"  ✓ 目标模型 {MODEL} 可用")
        else:
            print(f"  ⚠ 目标模型 {MODEL} 不在列表，前 5 个可用: {models[:5]}")
        return True
    except Exception as e:
        print(f"  ✗ 连接失败: {e}")
        return False


def test_2_simple_chat():
    banner(f"Test 2 · {MODEL} 普通调用")
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=30)
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你是一个简洁的财经助手。"},
                {"role": "user", "content": "请用一句话告诉我'宁德时代'是哪个行业的龙头公司。"},
            ],
            max_tokens=200,
            temperature=0.3,
        )
        elapsed = time.time() - t0
        content = resp.choices[0].message.content
        print(f"  ✓ 调用成功，耗时 {elapsed:.1f}s")
        print(f"  返回: {content[:100]}")
        usage = resp.usage
        if usage:
            print(f"  Tokens: in={usage.prompt_tokens} out={usage.completion_tokens}")
        return True
    except Exception as e:
        print(f"  ✗ 调用失败: {e}")
        return False


def test_3_json_mode():
    banner(f"Test 3 · {MODEL} JSON 模式（在线分析必需）")
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=30)
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "你只能输出 JSON，第一个字符必须是 {，最后一个必须是 }。"},
                {"role": "user", "content": (
                    "根据用户的 thesis 生成 2 条 kill condition，输出格式：\n"
                    '{"suggestions":[{"text":"...","type":"..."}]}\n\n'
                    "thesis: 锂电池渗透率持续提升 + 宁德供应链护城河深"
                )},
            ],
            max_tokens=500,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - t0
        content = resp.choices[0].message.content.strip()
        print(f"  ✓ 调用成功，耗时 {elapsed:.1f}s")
        print(f"  原始返回: {content[:300]}")
        try:
            parsed = json.loads(content)
            print(f"  ✓ JSON 解析成功")
            if "suggestions" in parsed:
                print(f"  ✓ 含 suggestions 字段，共 {len(parsed['suggestions'])} 条")
                for s in parsed["suggestions"]:
                    print(f"    - {s}")
            return True
        except json.JSONDecodeError as e:
            print(f"  ⚠ JSON 解析失败: {e}")
            print(f"  → 在线分析需要靠 4 层 fallback 解析（attribution.py 已有先例，可参考）")
            return False
    except Exception as e:
        print(f"  ✗ 调用失败: {e}")
        return False


def test_4_two_stage_extraction():
    banner("Test 4 · F7 双阶段事实提取（生产级 prompt）")
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=30)

    fake_news = [
        "巨潮：宁德时代 H1 净利润预计 250-280 亿，同比增长 35%-50%",
        "业内专家高度看好宁德长期前景，技术领先优势明显",
        "碳酸锂期货今日跌 8.2%",
        "雪球大 V：宁德必涨，目标价 500 元",
        "机构调研：宁德储能业务增速远超预期",
    ]

    sys_prompt = (
        "你是严格的事实核查员。从给定的新闻中只提取「可独立验证的客观事实」。\n"
        "必须满足：有数字 / 有主体 / 有可查证来源。\n"
        "丢弃：形容词、评价词、预测、模糊主张、营销语。\n"
        "只输出 JSON，第一个字符必须是 {。"
    )
    user_prompt = (
        "新闻列表：\n" + "\n".join(f"{i+1}. {n}" for i, n in enumerate(fake_news)) +
        '\n\n输出格式：{"verifiable_facts":[{"fact":"...","source":"..."}],'
        '"rejected_as_opinion":[{"text":"...","reason":"..."}]}'
    )
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1500,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - t0
        content = resp.choices[0].message.content.strip()
        print(f"  ✓ Stage A 耗时 {elapsed:.1f}s")
        parsed = json.loads(content)
        facts = parsed.get("verifiable_facts", [])
        rejected = parsed.get("rejected_as_opinion", [])
        print(f"  ✓ 提取 {len(facts)} 条事实, 剔除 {len(rejected)} 条")
        print(f"  保留示例: {facts[:2]}")
        print(f"  剔除示例: {rejected[:2]}")
        ideal_keep = 2  # 净利润预告 + 锂价 是可验证事实
        ideal_drop = 3  # 业内看好/必涨/远超预期 是套话
        if len(facts) >= ideal_keep and len(rejected) >= ideal_drop:
            print(f"  ✓ F7 抗投毒能力 OK（保留 ≥{ideal_keep}, 剔除 ≥{ideal_drop}）")
            return True
        else:
            print(f"  ⚠ F7 效果一般，需调 prompt（理想 keep ≥{ideal_keep}, drop ≥{ideal_drop}）")
            return False
    except Exception as e:
        print(f"  ✗ Stage A 失败: {e}")
        return False


if __name__ == "__main__":
    print(f"BASE_URL: {BASE_URL}")
    print(f"MODEL:    {MODEL}")
    print(f"API_KEY:  {'已配置' if API_KEY else '✗ 未配置 (检查 .env)'}")

    if not API_KEY:
        print("\n请先在 hermes/api/.env 配置 ONE_API_KEY")
        sys.exit(1)

    results = {
        "连通性": test_1_connectivity(),
        "普通调用": test_2_simple_chat(),
        "JSON 模式": test_3_json_mode(),
        "F7 双阶段": test_4_two_stage_extraction(),
    }

    banner("总结")
    for name, ok in results.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    all_ok = all(results.values())
    print("\n" + ("✓ Phase 0.3 通过，可继续 Phase 1" if all_ok else "⚠ 有失败项，请调整后再继续"))
    sys.exit(0 if all_ok else 1)
