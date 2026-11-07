"""
pykakasi の kanwadict4.db から 読み -> [漢字語, ...] の逆引き辞書を構築して
pickle で保存する。
"""
import pykakasi
import pickle
import os
import json

pkg_dir = os.path.dirname(pykakasi.__file__)
db_path = os.path.join(pkg_dir, "data", "kanwadict4.db")

with open(db_path, "rb") as f:
    data = pickle.load(f)

# data: { int(漢字コード): { "漢字語": [("よみ", None), ...], ... }, ... }
# 読み(ひらがな) -> [(漢字語, 長さスコア), ...] の辞書を構築
reading_to_words: dict[str, list[str]] = {}

for code_key, word_dict in data.items():
    for word, readings in word_dict.items():
        for reading_tuple in readings:
            reading = reading_tuple[0]  # ひらがな/カタカナ
            # カタカナをひらがなに変換
            hira = "".join(
                chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c
                for c in reading
            )
            if hira not in reading_to_words:
                reading_to_words[hira] = []
            if word not in reading_to_words[hira]:
                reading_to_words[hira].append(word)

# 確認
test_keys = ["きょう", "がっこう", "にほんご", "きかい", "まなぶ", "こうきょう"]
for k in test_keys:
    words = reading_to_words.get(k, [])
    print(f"{k}: {words[:5]}")

# 保存
out_path = os.path.join(os.path.dirname(__file__), "reading_dict.pkl")
with open(out_path, "wb") as f:
    pickle.dump(reading_to_words, f)

print(f"\n辞書エントリ数: {len(reading_to_words)}")
print(f"保存先: {out_path}")
