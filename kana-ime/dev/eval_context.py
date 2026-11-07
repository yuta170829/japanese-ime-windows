"""
前の文を文脈に渡すと、同音異字の選択が良くなるか (手作りの小規模評価)。
python dev/eval_context.py

helpful   : 前の文が手がかりになる文
misleading: 前の文の話題が、次の文の正解を邪魔しうる文 (例: 前が硬貨、次は効果)
件数が少ないので、結果は目安であって統計的な結論ではない。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import llm  # noqa: E402

HELPFUL = [  # (前の文, 読み, 正解)
    ("川に大きな橋が架かっている。", "はしをわたる", "橋を渡る"),
    ("ご飯を食べる準備をした。", "はしをつかう", "箸を使う"),
    ("実験でこの薬品を混ぜた。", "かがくはんのうがおきた", "化学反応が起きた"),
    ("ニュートンは万有引力を発見した。", "かがくのしんぽ", "科学の進歩"),
    ("この工場は部品を作っている。", "きかいがこしょうした", "機械が故障した"),
    ("せっかくのチャンスだったのに。", "きかいをのがした", "機会を逃した"),
    ("財布の中に百円玉が入っている。", "こうかをいれる", "硬貨を入れる"),
    ("この薬は頭痛によく効く。", "こうかがある", "効果がある"),
    ("機械の動作がおかしい。", "いじょうがはっせいした", "異常が発生した"),
    ("会議はこれで終わりです。", "いじょうです", "以上です"),
    ("不正の責任を問う。", "せきにんをついきゅうする", "責任を追及する"),
    ("テストの問題を解いた。", "かいとうをかく", "解答を書く"),
    ("アンケートの質問に答えた。", "かいとうをおくる", "回答を送る"),
    ("お茶を淹れた。", "あついおちゃをのむ", "熱いお茶を飲む"),
]
MISLEADING = [
    ("財布に硬貨が三枚入っている。", "このくすりはこうかがある", "この薬は効果がある"),
    ("彼は橋の上で写真を撮った。", "ごはんのときははしをつかう", "ご飯のときは箸を使う"),
    ("機械の操作を覚えた。", "いいきかいだからのがすな", "いい機会だから逃すな"),
    ("試験の解答を提出した。", "あんけーとにかいとうする", "アンケートに回答する"),
    ("異常な暑さが続いている。", "かいぎはこれでいじょうです", "会議はこれで以上です"),
    ("お湯が熱い。", "なつはあついひがつづく", "夏は暑い日が続く"),
    ("医師に相談した。", "じぶんのいしをつたえる", "自分の意志を伝える"),
    ("科学の授業があった。", "くすりでかがくはんのうがおきた", "薬で化学反応が起きた"),
]

VARIANTS = {
    "文脈なし": lambda prev: "",
    "現在の動作(現在の文の左側だけ)": lambda prev: llm.sentence_fragment(prev),
    "前の文を含む直前40文字": lambda prev: prev[-40:],
    "前の文を含む直前80文字": lambda prev: prev[-80:],
}


def main():
    w = llm.ZenzWorker(check_reading=False)
    w.start()
    while True:
        item = w.results.get(timeout=180)
        if isinstance(item, llm.Status) and item.state in ("ready", "error"):
            if item.state == "error":
                sys.exit(item.message)
            break
    # 80文字版は llm.CONTEXT_CHARS (=40) で切られるので、評価中だけ広げる
    out = []

    def say(s=""):
        print(s)
        out.append(s)

    for name, make in VARIANTS.items():
        llm.CONTEXT_CHARS = 80 if "80" in name else 40
        rows = {"helpful": [], "misleading": []}
        for kind, cases in (("helpful", HELPFUL), ("misleading", MISLEADING)):
            for prev, reading, expected in cases:
                got = w.generate_raw(make(prev), reading)
                rows[kind].append((prev, reading, expected, got))
        h = sum(g == e for _, _, e, g in rows["helpful"])
        m = sum(g == e for _, _, e, g in rows["misleading"])
        say(f"{name}: 手がかりあり {h}/{len(HELPFUL)}  誤解を招く前文あり {m}/{len(MISLEADING)}")
        out.append("   -- 不正解 (前の文 | 読み | 正解 | 出力) --")
        for kind in rows:
            for prev, r, e, g in rows[kind]:
                if g != e:
                    out.append(f"   [{kind}] {prev} | {r} | {e} | {g}")
    w.stop()
    path = os.path.join(HERE, "data", "context_report.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("詳細:", path)


if __name__ == "__main__":
    main()
