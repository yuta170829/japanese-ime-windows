"""
LLMの別案(n-best)の評価。 python dev/eval_alternatives.py [件数|all]

Anthy corpus.1 の文のうち、LLMの最短の出力(整合チェック後)が正解と違う文について、
別案5件のどこかに正解が入るかを数える (recall@1 / @3 / @5)。
データは dev/eval_reading_check.py と同じ (dev/data/ に保存済み)。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import llm  # noqa: E402
from engine import standard_text  # noqa: E402
from eval_reading_check import load_cases  # noqa: E402


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    cases = load_cases()
    if arg != "all":
        cases = cases[: int(arg)]
    w = llm.ZenzWorker(fallback=standard_text)
    w.start()
    while True:
        s = w.results.get(timeout=180)
        if isinstance(s, llm.Status) and s.state in ("ready", "error"):
            if s.state == "error":
                sys.exit(s.message)
            break
    wrong, right = 0, 0
    hit = {1: 0, 3: 0, 5: 0}
    ms_total, none_alts = 0.0, 0
    examples = []
    for reading, expected in cases:
        main_text, _ = w._convert(reading, "")
        if main_text == expected:
            right += 1
            continue
        wrong += 1
        t = time.perf_counter()
        alts = w.alternatives(reading, "", 5)
        ms_total += (time.perf_counter() - t) * 1000
        none_alts += not alts
        for k in hit:
            if expected in alts[:k]:
                hit[k] += 1
        if expected in alts and len(examples) < 25:
            examples.append((reading, main_text, expected, alts.index(expected) + 1))
    n = len(cases)
    summary = [
        f"対象 {n} 文: 最短の出力が正解 {right} / 不正解 {wrong}",
        f"不正解 {wrong} 文のうち、別案に正解が入る率:",
        f"  上位1件: {hit[1]}/{wrong} ({100.0 * hit[1] / wrong:.1f}%)",
        f"  上位3件: {hit[3]}/{wrong} ({100.0 * hit[3] / wrong:.1f}%)",
        f"  上位5件: {hit[5]}/{wrong} ({100.0 * hit[5] / wrong:.1f}%)",
        f"別案が1件も出なかった文: {none_alts}/{wrong}",
        f"別案の生成時間: 平均 {ms_total / wrong:.0f} ms/文",
        f"完全一致: 最短のみ {100.0 * right / n:.1f}% / 別案の上位3件まで見ると "
        f"{100.0 * (right + hit[3]) / n:.1f}%",
    ]
    print(len(summary), "行の集計を書き出します")
    out = summary + ["-- 別案で正解が出た例 (読み | 最短の出力 | 正解 | 何番目) --"] + [
        f"  {r} | {m} | {e} | {k}" for r, m, e, k in examples]
    path = os.path.join(HERE, "data", "alternatives_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("詳細:", ascii(path))
    w.stop()


if __name__ == "__main__":
    main()
