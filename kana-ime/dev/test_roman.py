"""ローマ字変換のテスト。 python dev/test_roman.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from roman import finalize, has_raw, hira_to_roman, roman_to_hira  # noqa: E402

CASES = [  # (ローマ字, かな, 打ちかけ)
    ("kyouhagakkouniikimasu", "きょうはがっこうにいきます", ""),
    ("konnichiha", "こんにちは", ""),
    ("konnnichiha", "こんにちは", ""),
    ("minna", "みんな", ""),
    ("kin'enn", "きんえん", ""),
    ("kin'en", "きんえ", "n"),          # 最後の n はまだ打ちかけ (確定で ん になる)
    ("kinnenn", "きんねん", ""),
    ("benkyou", "べんきょう", ""),
    ("shinbunn", "しんぶん", ""),
    ("onna", "おんな", ""),
    ("annai", "あんない", ""),
    ("hon'ya", "ほんや", ""),
    ("matcha", "まっちゃ", ""),
    ("kitte", "きって", ""),
    ("fairu", "ふぁいる", ""),
    ("disuku", "ぢすく", ""),
    ("dhisuku", "でぃすく", ""),
    ("ko-hi-", "こーひー", ""),
    ("wo", "を", ""),
    ("xtu", "っ", ""),
    ("jaa", "じゃあ", ""),
    ("tomodach", "ともだ", "ch"),     # 打ちかけ (以前は ともだっっ になっていた)
    ("ky", "", "ky"),
    ("kan", "か", "n"),
    ("kk", "っ", "k"),
    ("tomodachj", "ともだch", "j"),    # タイプミスは英字のまま見える
    ("gakkpu", "がっkぷ", ""),
    ("kyou,ashita.", "きょう、あした。", ""),
]
fails = 0
for roman, hira, pending in CASES:
    got = roman_to_hira(roman)
    if got != (hira, pending):
        fails += 1
        print("NG", roman, ascii(got), "expected", ascii((hira, pending)))
assert finalize("kan") == "かん"
assert has_raw("tomodachjq") and not has_raw("tomodachi")

# 正しい読み -> ローマ字 -> 読み が元に戻る (Anthyコーパスの読み全部)
sys.path.insert(0, HERE)
from eval_reading_check import load_cases  # noqa: E402

bad = []
cases = load_cases()
for reading, _ in cases:
    if roman_to_hira(hira_to_roman(reading)) != (reading, ""):
        bad.append(reading)
if bad:
    fails += 1
    print("NG roundtrip", len(bad), ascii(bad[:5]))
print(f"cases {len(CASES) - (fails if fails <= len(CASES) else 0)}/{len(CASES)}, "
      f"roundtrip {len(cases) - len(bad)}/{len(cases)}")
sys.exit(1 if fails else 0)
