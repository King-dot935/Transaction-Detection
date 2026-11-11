# -*- coding: utf-8 -*-
"""打包前脱敏：
  1. 把 .env.example 里的真实 key 换成占位符
  2. capture_shots.py 里写死的本机 Chrome 路径改成可配置
  3. 顺手扫一遍还有没有别的地方带 key
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGETS = [ROOT]

PLACEHOLDER_SMALL = "sk-在这里填你的千问key"
PLACEHOLDER_LARGE = "sk-在这里填你的deepseek key"

KEY_RE = re.compile(r"sk-[A-Za-z0-9_.\-]{10,}")


def scrub_env(p: Path):
    if not p.exists():
        return 0
    s = p.read_text(encoding="utf-8")
    n = 0

    def repl(m):
        nonlocal n
        # 已经改过的（含中文）跳过
        n += 1
        return "sk-在这里填你的key"

    secret = r"sk-[A-Za-z0-9_.\-]{10,}"
    s2 = re.sub(r"(LLM_SMALL_KEY=)" + secret, r"\1" + PLACEHOLDER_SMALL, s)
    s2 = re.sub(r"(LLM_LARGE_KEY=)" + secret, r"\1" + PLACEHOLDER_LARGE, s2)
    if s2 != s:
        p.write_text(s2, encoding="utf-8")
        return 1
    return 0


def fix_chrome_path(p: Path):
    if not p.exists():
        return 0
    s = p.read_text(encoding="utf-8")
    old = 'CHROME = r"C:\\Users\\晕晕\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe"'
    new = ('CHROME = os.environ.get("CHROME") or (\n'
           '    r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")')
    if old in s:
        s = s.replace(old, new, 1)
        if "import os" not in s.split("\n\n")[0]:
            s = s.replace("import subprocess", "import os\nimport subprocess", 1)
        p.write_text(s, encoding="utf-8")
        return 1
    return 0


def main():
    print("=== 脱敏 ===")
    for base in TARGETS:
        env = base / ".env.example"
        if scrub_env(env):
            print(f"  [OK] {env} 已换占位符")

    cap = ROOT / "capture_shots.py"
    if fix_chrome_path(cap):
        print(f"  [OK] {cap.name} 的 Chrome 路径改成环境变量")

    print("\n=== 复查（应只剩占位符）===")
    hits = 0
    for base in TARGETS:
        for p in base.rglob("*"):
            if not p.is_file() or "__pycache__" in str(p):
                continue
            if p.suffix not in (".py", ".md", ".example", ".json", ".jsonl", ".html"):
                continue
            try:
                txt = p.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in KEY_RE.finditer(txt):
                val = m.group(0)
                if "在这" in val or "xxx" in val or "你的" in val:
                    continue
                hits += 1
                print(f"  [WARN] {p.name}: {val[:24]}...")
    print(f"  残留真实密钥：{hits}")


if __name__ == "__main__":
    main()
