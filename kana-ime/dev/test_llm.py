"""zenz補正の検証 (ヘッドレス)。 python dev/test_llm.py"""
import os
import queue
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import llm  # noqa: E402
from engine import ConversionState, roman_to_hira  # noqa: E402

# 前処理の単体テスト
assert llm.split_chunks("あいう、えお。かき") == ["あいう、", "えお。", "かき"]
assert len(llm.split_chunks("あ" * 250)) == 3
assert llm.sentence_fragment("前の文。いまの文") == "いまの文"
assert llm.sentence_fragment("終わった。") == ""
assert llm.build_prompt("", "あ") == "\uEE00ア\uEE01"
assert llm.build_prompt("大きな", "あ") == "\uEE02大きな\uEE00ア\uEE01"
print("preprocess OK")

ALTERNATIVES = {  # 表記ゆれとして許容する別表記
    "朝ご飯を食べました": {"朝ごはんを食べました"},
    "昨日は映画を見ました": {"昨日は映画を観ました"},
    "本を読んだあと": {"本を読んだ後"},
}
CASES = [
    ("asagohanwotabemashita", "朝ご飯を食べました"),
    ("honwoyondaato", "本を読んだあと"),
    ("tomodachinitegamiwokakimasu", "友達に手紙を書きます"),
    ("kinouhaeigawomimashita", "昨日は映画を見ました"),
    ("mainichihashitteimasu", "毎日走っています"),
    ("kanojohaitsumowarau", "彼女はいつも笑う"),
    ("kyouhagakkouniikimasu", "今日は学校に行きます"),
    ("honkenkyuudehakikaigakushuuwomochiiru", "本研究では機械学習を用いる"),
]

w = llm.ZenzWorker()
w.start()


def wait(kind, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        try:
            item = w.results.get(timeout=1)
        except queue.Empty:
            continue
        if isinstance(item, kind):
            return item
        if isinstance(item, llm.Status) and item.state == "error":
            print("ERROR", ascii(item.message))
            sys.exit(2)
    print("timeout")
    sys.exit(2)


t0 = time.time()
wait(llm.Status) if False else None
while True:
    item = w.results.get(timeout=120)
    if isinstance(item, llm.Status):
        if item.state == "error":
            print("ERROR", ascii(item.message))
            sys.exit(2)
        if item.state == "ready":
            break
print(f"model ready in {time.time() - t0:.1f}s")

ok = 0
for i, (roman, expected) in enumerate(CASES, 1):
    reading, _ = roman_to_hira(roman)
    w.submit(i, reading, "")
    r = wait(llm.Result)
    hit = r.text == expected or r.text in ALTERNATIVES.get(expected, set())
    ok += hit
    print(("OK " if hit else "NG "), f"{r.milliseconds:.0f}ms", ascii(r.text), "" if hit else "expected " + ascii(expected))
print(f"{ok}/{len(CASES)}")

# 整合チェック: LLMが読みと無関係な文字を出したら、標準変換に差し替える
from engine import standard_text  # noqa: E402

w2 = llm.ZenzWorker(fallback=standard_text)
w2.start()
while True:
    st = w2.results.get(timeout=120)
    if isinstance(st, llm.Status) and st.state == "ready":
        break
w2.generate_raw = lambda ctx, chunk: "全然関係ない文章です"  # 幻覚を模擬
reading = "きょうはいいてんきですね"
w2.submit(900, reading, "")
bad = None
while bad is None:
    item = w2.results.get(timeout=30)
    if isinstance(item, llm.Result):
        bad = item
check_ok = bad.rejected == 1 and bad.text == standard_text(reading)
print("consistency fallback:", "OK" if check_ok else "NG", ascii(bad.text), "rejected =", bad.rejected)
w2.generate_raw = lambda ctx, chunk: "明日は晴れるでしょう"  # 読みと合う正しい出力は通す
w2.submit(901, "あしたははれるでしょう", "")
good = None
while good is None:
    item = w2.results.get(timeout=30)
    if isinstance(item, llm.Result):
        good = item
check_ok = check_ok and good.rejected == 0 and good.text == "明日は晴れるでしょう"
print("consistency pass-through:", "OK" if good.rejected == 0 else "NG", ascii(good.text))
w2.stop()

# 別案 (n-best): 意志/意思のような同音異字が別案に出る。最短の出力は含まれない。
alt_main, _r = w._convert("じぶんのいしをつたえる", "")
alts = w.alternatives("じぶんのいしをつたえる", "", 3)
alt_ok = ("自分の意志を伝える" in alts or "自分の意思を伝える" in alts) \
    and alt_main not in alts and 1 <= len(alts) <= 3 and len(set(alts)) == len(alts)
print("alternatives:", "OK" if alt_ok else "NG", ascii(alts))

# 最新1件にまとまるか: 連続投入しても、最後の要求の結果が必ず返る
for i in range(100, 105):
    w.submit(i, "きょうはがっこうにいきます", "")
last = None
t = time.time()
while time.time() - t < 15:
    try:
        item = w.results.get(timeout=1)
    except queue.Empty:
        continue
    if isinstance(item, llm.Result):
        last = item.req_id
        if last == 104:
            break
print("coalesce: last result id =", last, "(expect 104)")
w.stop()
sys.exit(0 if ok == len(CASES) and last == 104 and check_ok and alt_ok else 1)
