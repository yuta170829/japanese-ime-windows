"""
整合チェック(reading_check)の評価。 python dev/eval_reading_check.py [件数|all]

データ: Anthy の corpus.1.txt (ファイル先頭に public domain と明記されたもの。
        macOS版 Hitsuji-IME の scripts/download-corpus.sh と同じリビジョン)。
        dev/data/ に保存する (git管理外にすること)。

測るもの
  A. 正解の文がチェックを通るか      -> 誤拒否 (通ってほしいのに落ちる)
  B. 読みを壊した文をチェックが落とせるか -> 誤受理 (落ちてほしいのに通る)
  C. 実際のLLM出力にチェックを入れたとき、正解率がどう変わるか
結果の詳細は dev/data/eval_report.txt (UTF-8) に書く。
"""
import os
import random
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import llm  # noqa: E402
from engine import standard_text  # noqa: E402
from reading_check import matches, normalize  # noqa: E402

REVISION = "cdc16297d8cc096269b108605e9a1cf353866139"
URL = ("https://raw.githubusercontent.com/netsphere-labs/anthy/"
       f"{REVISION}/corpus/corpus.1.txt")
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "anthy-corpus.1.txt")
REPORT = os.path.join(DATA, "eval_report.txt")


def load_cases():
    os.makedirs(DATA, exist_ok=True)
    if not os.path.exists(RAW):
        urllib.request.urlretrieve(URL, RAW)
    lines = open(RAW, encoding="utf-8").read().splitlines()
    if not any("public domain" in l for l in lines[:3]):
        sys.exit("ライセンス表記(public domain)を確認できません")
    allowed = re.compile(r"^[ぁ-んー、。]+$")
    cases = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(" ")
        if len(parts) != 2:
            continue
        reading = "".join(s for s in parts[0].split("|") if s)
        expected = "".join(s for s in parts[1].split("|") if s)
        if reading and expected and allowed.match(reading):
            cases.append((reading, expected))
    return cases


def pct(n, d):
    return f"{n}/{d} ({100.0 * n / d:.1f}%)" if d else "0/0"


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    cases = load_cases()
    n_llm = len(cases) if arg == "all" else min(int(arg), len(cases))
    out = []

    def say(s=""):
        print(s)
        out.append(s)

    say(f"コーパス: {len(cases)} 文 (LLM評価: {n_llm} 文)")

    # ---- A. 正解の文が通るか ----
    t = time.time()
    rejected_gold = [(r, e) for r, e in cases if not matches(e, r)]
    say(f"A. 正解文の受理: {pct(len(cases) - len(rejected_gold), len(cases))}"
        f"  (チェック {len(cases) / (time.time() - t):.0f} 文/秒)")
    out.append("   -- 誤拒否の例 (正解文なのに落ちた) --")
    for r, e in rejected_gold[:25]:
        out.append(f"   {r} | {e}")

    # ---- B. 読みを壊した文を落とせるか ----
    rnd = random.Random(0)
    hira = [chr(c) for c in range(0x3042, 0x3094)]
    near, near_acc = 0, []
    for r, e in cases:
        idx = [i for i, ch in enumerate(e) if "\u3041" <= ch <= "\u3093"]
        if not idx:
            continue
        i = rnd.choice(idx)
        new = rnd.choice([c for c in hira if c != e[i]])
        bad = e[:i] + new + e[i + 1:]
        if normalize(bad) == normalize(e):
            continue
        near += 1
        if matches(bad, r):
            near_acc.append((r, bad))
    say(f"B1. かな1文字を置き換えた文を拒否: {pct(near - len(near_acc), near)}")
    out.append("   -- 誤受理の例 (壊したのに通った) --")
    for r, b in near_acc[:15]:
        out.append(f"   {r} | {b}")
    shifted = [(cases[i][0], cases[(i + 1) % len(cases)][1]) for i in range(len(cases))]
    shifted_acc = [(r, b) for r, b in shifted if matches(b, r)]
    say(f"B2. 別の文の変換結果を拒否: {pct(len(shifted) - len(shifted_acc), len(shifted))}")

    # ---- C. 実際のLLM出力 ----
    w = llm.ZenzWorker(check_reading=False)
    w.start()
    while True:
        item = w.results.get(timeout=180)
        if isinstance(item, llm.Status) and item.state in ("ready", "error"):
            if item.state == "error":
                sys.exit("LLMを起動できません: " + item.message)
            break
    stats = dict(std=0, raw=0, checked=0, rej=0, rej_exact=0, rej_wrong=0,
                 acc_wrong=0, n=0)
    rej_samples, accwrong_samples, rejexact_samples = [], [], []
    t = time.time()
    for reading, expected in cases[:n_llm]:
        raw = "".join(w.generate_raw("", c) for c in llm.split_chunks(reading))
        ok = matches(raw, reading)
        used = raw if ok else standard_text(reading)
        stats["n"] += 1
        stats["std"] += standard_text(reading) == expected
        stats["raw"] += raw == expected
        stats["checked"] += used == expected
        if not ok:
            stats["rej"] += 1
            if raw == expected:
                stats["rej_exact"] += 1
                rejexact_samples.append((reading, raw))
            else:
                stats["rej_wrong"] += 1
                rej_samples.append((reading, raw, expected))
        elif raw != expected:
            stats["acc_wrong"] += 1
            accwrong_samples.append((reading, raw, expected))
    n = stats["n"]
    say(f"C. LLM出力 {n} 文 ({(time.time() - t) / n * 1000:.0f} ms/文)")
    say(f"   標準変換のみ        : 完全一致 {pct(stats['std'], n)}")
    say(f"   LLM (チェックなし)  : 完全一致 {pct(stats['raw'], n)}")
    say(f"   LLM + 整合チェック  : 完全一致 {pct(stats['checked'], n)}")
    say(f"   チェックで除外      : {pct(stats['rej'], n)}")
    say(f"     うち正解だったのに除外 (誤拒否): {stats['rej_exact']}")
    say(f"     うち不正解で除外 (読み不一致)  : {stats['rej_wrong']}")
    say(f"   通ったが不正解 (同音異字など。チェックの守備範囲外): {pct(stats['acc_wrong'], n)}")
    out.append("   -- 除外した出力 (読み | LLM出力 | 正解) --")
    for r, o, e in rej_samples[:30]:
        out.append(f"   {r} | {o} | {e}")
    out.append("   -- 正解なのに除外した出力 --")
    for r, o in rejexact_samples[:20]:
        out.append(f"   {r} | {o}")
    w.stop()

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("詳細:", REPORT)


if __name__ == "__main__":
    main()
