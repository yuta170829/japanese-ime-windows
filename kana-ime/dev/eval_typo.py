"""
タイプミス補正の評価。 python dev/eval_typo.py [件数]

  typo   : 正しい読みのローマ字に1文字のミス (隣のキー・抜け・余分・入れ替え) を入れた文
           -> 元の読みに戻せた / 別の読みに変えてしまった / 直さなかった
  clean  : 正しく打った文 -> 直してしまった率 (誤補正。低いほど良い)
  prefix : 正しく打っている途中の文 -> 直してしまった率
実際の補正処理 (typo.correct) を、LLMで計算し直す候補の数 (PREFILTER) を変えて比べる。
結果は dev/data/typo_report.txt。

基準値 (LENGTH_BONUS / MIN_GAIN / HOT_LOGPROB) は、全候補を採点する版で
組み合わせを総当たりして決めた (誤補正 0件のまま、直せた数が多い組)。
"""
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import llm  # noqa: E402
import typo  # noqa: E402
from engine import standard_text  # noqa: E402
from eval_reading_check import load_cases  # noqa: E402
from roman import hira_to_roman, roman_to_hira  # noqa: E402


def inject(roman, rnd):
    for _ in range(20):
        i = rnd.randrange(len(roman))
        op = rnd.choice(["sub", "del", "ins", "swap"])
        c = roman[i]
        if not c.isalpha():
            continue
        if op == "sub" and typo.neighbors(c):
            return roman[:i] + rnd.choice(typo.neighbors(c)) + roman[i + 1:], op
        if op == "del":
            return roman[:i] + roman[i + 1:], op
        if op == "ins" and typo.neighbors(c):
            return roman[:i] + rnd.choice(typo.neighbors(c) + [c]) + roman[i:], op
        if op == "swap" and i + 1 < len(roman) and roman[i + 1] != c:
            return roman[:i] + roman[i + 1] + roman[i] + roman[i + 2:], op
    return None, None


def build_sets(n, rnd):
    cases = load_cases()
    rnd.shuffle(cases)
    typos, cleans, prefixes = [], [], []
    for reading, _ in cases:
        if len(typos) >= n:
            break
        bad, op = inject(hira_to_roman(reading), rnd)
        if bad is None:
            continue
        r, p = roman_to_hira(bad)
        if r == reading or p:
            continue
        typos.append((bad, reading, op))
    for reading, _ in cases[-n:]:
        roman = hira_to_roman(reading)
        cleans.append((roman, reading, None))
        cut = roman[: rnd.randrange(2, max(3, len(roman)))]
        r, _p = roman_to_hira(cut)
        if r:
            prefixes.append((cut, r, None))
    return typos, cleans, prefixes


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    typos, cleans, prefixes = build_sets(n, random.Random(7))
    w = llm.ZenzWorker(fallback=standard_text)
    w.start()
    while True:
        s = w.results.get(timeout=180)
        if isinstance(s, llm.Status) and s.state in ("ready", "error"):
            break
    lines = [f"typo {len(typos)} / clean {len(cleans)} / prefix {len(prefixes)} 件",
             f"基準値: LENGTH_BONUS={typo.LENGTH_BONUS} MIN_GAIN={typo.MIN_GAIN} "
             f"HOT_LOGPROB={typo.HOT_LOGPROB}"]
    examples = []
    for prefilter in (8, 12, 24):
        res = {}
        times = []
        by_op = {}
        for name, data in (("typo", typos), ("clean", cleans), ("prefix", prefixes)):
            fixed = wrong = 0
            for roman, truth, op in data:
                orig, _ = roman_to_hira(roman)
                t = time.perf_counter()
                c = typo.correct(roman, w, prefilter=prefilter)
                times.append((time.perf_counter() - t) * 1000)
                got = c.reading if c else orig
                if got != orig:
                    if got == truth and name == "typo":
                        fixed += 1
                    else:
                        wrong += 1
                if name == "typo":
                    d = by_op.setdefault(op, [0, 0, 0])
                    d[0 if got == truth else 1 if got != orig else 2] += 1
                    if prefilter == 12 and got == truth and len(examples) < 15:
                        examples.append(f"  {roman} -> {c.roman}  ({orig} -> {truth})")
            res[name] = (fixed, wrong)
        times.sort()
        lines.append(
            f"PREFILTER={prefilter}: typo 直せた {res['typo'][0]}/{len(typos)} 誤変更 {res['typo'][1]} | "
            f"clean 誤補正 {res['clean'][1]}/{len(cleans)} | prefix 誤補正 {res['prefix'][1]}/{len(prefixes)} | "
            f"時間 中央値 {times[len(times) // 2]:.0f} ms / 90% {times[int(len(times) * 0.9)]:.0f} ms / "
            f"最大 {times[-1]:.0f} ms")
        lines.append(f"  ミスの種類別 [直せた, 誤変更, そのまま]: {by_op}")
    lines.append("-- 直せた例 (PREFILTER=12) --")
    lines += examples
    w.stop()
    path = os.path.join(HERE, "data", "typo_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("書き出しました")


if __name__ == "__main__":
    main()
