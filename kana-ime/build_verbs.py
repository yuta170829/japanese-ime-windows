"""
build_verbs.py -- 動詞・形容詞の活用形辞書を生成する (初回のみ)

reading_dict.pkl の「読み→漢字」から、送り仮名つきの動詞・イ形容詞を拾い、
MeCab(ipadic) で活用型を判定して、活用形(読み→表記)を verb_dict.pkl に保存する。

  学ぶ(まなぶ) -> 学ばない / 学びます / 学んで / 学んだ / 学べば / 学ぼう ...
"""
from __future__ import annotations

import os
import pickle
import re
from typing import Dict, List, Tuple

import fugashi
import ipadic
import jaconv

# 一段動詞の語幹末尾 (え段・い段のかな)
ICHIDAN_STEM_END = "えけげせぜてでねへべぺめれいきぎしじちにひびみり"

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "reading_dict.pkl")
DST = os.path.join(HERE, "verb_dict.pkl")

KANJI = re.compile(r"[\u4e00-\u9fff]")
HIRA_ONLY = re.compile(r"^[\u3041-\u3096ー]+$")

# 五段活用の行 -> (あ段, い段, う段, え段, お段)
GODAN_ROWS: Dict[str, Tuple[str, str, str, str, str]] = {
    "カ": ("か", "き", "く", "け", "こ"),
    "ガ": ("が", "ぎ", "ぐ", "げ", "ご"),
    "サ": ("さ", "し", "す", "せ", "そ"),
    "タ": ("た", "ち", "つ", "て", "と"),
    "ナ": ("な", "に", "ぬ", "ね", "の"),
    "バ": ("ば", "び", "ぶ", "べ", "ぼ"),
    "マ": ("ま", "み", "む", "め", "も"),
    "ラ": ("ら", "り", "る", "れ", "ろ"),
    "ワ": ("わ", "い", "う", "え", "お"),
}


def te_ta(row: str, ctype: str) -> Tuple[str, str]:
    """五段の音便。(て形の語尾, た形の語尾)"""
    if row == "カ":
        return ("って", "った") if "促音便" in ctype else ("いて", "いた")  # 行く
    if row == "ガ":
        return ("いで", "いだ")
    if row == "サ":
        return ("して", "した")
    if row in ("タ", "ラ", "ワ"):
        return ("って", "った")
    return ("んで", "んだ")  # ナ・バ・マ


def godan_forms(row: str, ctype: str) -> List[str]:
    a, i, u, e, o = GODAN_ROWS[row]
    te, ta = te_ta(row, ctype)
    return [
        a + "ない", a + "なかった", a + "れる", a + "せる", a + "なければ",
        i + "ます", i + "ました", i + "ません", i + "たい", i + "たくない", i,
        te, ta, te + "いる", te + "いた", te + "います", te + "いました",
        e + "ば", e, o + "う",
    ]


ICHIDAN_FORMS = [
    "ない", "なかった", "ます", "ました", "ません", "たい", "たくない", "",
    "て", "た", "ている", "ていた", "ています", "ていました",
    "れば", "よう", "られる", "させる", "なければ",
]

ADJ_FORMS = ["くない", "くなかった", "かった", "くて", "ければ", "く", "さ"]


def make_tagger() -> fugashi.GenericTagger:
    d = ipadic.DICDIR.replace("\\", "/")
    rc = os.path.join(ipadic.DICDIR, "mecabrc").replace("\\", "/")
    return fugashi.GenericTagger(f"-d {d} -r {rc}")


def main() -> None:
    with open(SRC, "rb") as f:
        reading_dict: Dict[str, List[str]] = pickle.load(f)
    tagger = make_tagger()

    out: Dict[str, List[str]] = {}
    count = {"godan": 0, "ichidan": 0, "adj": 0}

    def add(reading: str, surface: str) -> None:
        lst = out.setdefault(reading, [])
        if surface not in lst:
            lst.append(surface)

    def analyze(word: str):
        """単独の1語として解析できたとき (品詞, 活用型, 基本形, 読み) を返す"""
        tokens = list(tagger(word))
        if len(tokens) != 1:
            return None
        feat = tokens[0].feature
        if not isinstance(feat, tuple) or len(feat) < 8 or not feat[7]:
            return None
        return feat[0], feat[4], feat[6], jaconv.kata2hira(feat[7])

    # 一段活用は辞書に「る」抜き(食べ・考え・見)で入っているので、
    # 「る」を足して解析し、一段動詞と判定できたものだけを使う。
    for reading, words in reading_dict.items():
        if not HIRA_ONLY.match(reading) or reading[-1] not in ICHIDAN_STEM_END:
            continue
        for word in words:
            if not KANJI.search(word):
                continue
            # 送り仮名つき(食べ)は末尾のかなが読みと一致すること。漢字だけ(見)はそのまま許可。
            if not KANJI.search(word[-1]) and word[-1] != reading[-1]:
                continue
            r = analyze(word + "る")
            if r and r[0] == "動詞" and r[1] == "一段" and r[2] == word + "る" \
                    and r[3] == reading + "る":
                for suf in ICHIDAN_FORMS:
                    add(reading + suf, word + suf)
                count["ichidan"] += 1

    for reading, words in reading_dict.items():
        if not HIRA_ONLY.match(reading) or len(reading) < 2:
            continue
        last = reading[-1]
        if last not in "うくぐすつぬぶむい":
            continue
        for word in words:
            if len(word) < 2 or not KANJI.search(word) or word[-1] != last:
                continue
            if KANJI.search(word[-1]):
                continue
            r = analyze(word)
            if r is None:
                continue
            pos, ctype, base, yomi = r
            if base != word or yomi != reading:
                continue
            stem_w, stem_r = word[:-1], reading[:-1]
            if pos == "動詞" and ctype.startswith("五段"):
                m = re.match(r"五段・(.)行", ctype)
                if not m or m.group(1) not in GODAN_ROWS:
                    continue
                if last != GODAN_ROWS[m.group(1)][2]:
                    continue
                for suf in godan_forms(m.group(1), ctype):
                    add(stem_r + suf, stem_w + suf)
                count["godan"] += 1
            elif pos == "形容詞" and last == "い":
                for suf in ADJ_FORMS:
                    add(stem_r + suf, stem_w + suf)
                count["adj"] += 1

    with open(DST, "wb") as f:
        pickle.dump(out, f)
    print(f"活用元: 五段 {count['godan']} / 一段 {count['ichidan']} / 形容詞 {count['adj']}")
    print(f"生成した読み: {len(out)} 件 -> {DST}")
    for k in ["いって", "まなんで", "たべました", "かんがえている", "はなしたい", "おもしろかった"]:
        print(" ", k, out.get(k, [])[:4])


if __name__ == "__main__":
    main()
