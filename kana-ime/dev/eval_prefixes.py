"""
打ちかけ(入力途中)の読みでも整合チェックが誤って落とさないかを確かめる。
python dev/eval_prefixes.py

実際の入力は、1文字打つごとに「そこまでの読み」がLLMに渡る。完成した文だけでなく、
途中の読みでの除外率を調べる。結果は dev/data/prefix_report.txt。
データ: Anthy corpus.1 の先頭 150 文。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import llm  # noqa: E402
from eval_reading_check import load_cases  # noqa: E402
from reading_check import matches  # noqa: E402


def main():
    n_sent = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    cases = load_cases()[:n_sent]
    w = llm.ZenzWorker(check_reading=False)
    w.start()
    while True:
        s = w.results.get(timeout=180)
        if isinstance(s, llm.Status) and s.state in ("ready", "error"):
            if s.state == "error":
                sys.exit(s.message)
            break
    total = rej = 0
    full_total = full_rej = 0
    samples = []
    for reading, _expected in cases:
        for end in range(1, len(reading) + 1):
            prefix = reading[:end]
            if not llm.has_japanese(prefix):
                continue
            out = w.generate_raw("", prefix)
            # 実際の使い方と同じ: 句読点で終わらない最後のチャンクは打ちかけとして見る
            ok = matches(out, prefix, partial=not prefix.endswith(tuple("、。！？!?")))
            total += 1
            rej += not ok
            if end == len(reading):
                full_total += 1
                full_rej += not ok
            if not ok and len(samples) < 40:
                samples.append((prefix, out))
    lines = [
        f"{len(cases)} 文の全ての打ちかけ位置: {total} 通り",
        f"  除外: {rej} ({100.0 * rej / total:.2f}%)",
        f"  うち完成した文: {full_rej}/{full_total}",
        "-- 除外された例 (打ちかけの読み | LLMの出力) --",
    ] + [f"  {p} | {o}" for p, o in samples]
    path = os.path.join(HERE, "data", "prefix_report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("書き出しました")
    w.stop()


if __name__ == "__main__":
    main()
