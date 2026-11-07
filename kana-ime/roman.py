"""
roman.py -- ローマ字 -> ひらがな (一般的なIMEと同じ規則)

jaconv.alphabet2kana をやめて自前にした理由:
  * 打ちかけの子音を「っ」に変えてしまう (tomodach -> ともだっっ)。
    普通に打っている途中でも表示が化け、タイプミスも見えなくなる。
  * 「konnichiha -> こんいちは」「minna -> みんあ」になる。

規則:
  * 表の綴りを最長一致で変換する (kya -> きゃ, shi -> し, tsu -> つ, fa -> ふぁ, xtu -> っ ...)
  * 同じ子音の重なりは「っ」(kka -> っか, tcha -> っちゃ)。n は除く
  * ん: nn / n' / 子音の前の n。「nn + 母音」は ん + な行 (konnichiha -> こんにちは, minna -> みんな)
  * 入力の最後の、まだ綴りの途中の部分 (k, ky, sh, n など) は pending として英字のまま返す
  * どの綴りにもならない文字 (タイプミス) は、英字のまま残す (例: tomodachj -> ともだch + j)
"""
from __future__ import annotations

from typing import Dict, List, Tuple

VOWELS = "aiueo"

_BASE: Dict[str, str] = {
    "a": "あ", "i": "い", "u": "う", "e": "え", "o": "お",
    "ka": "か", "ki": "き", "ku": "く", "ke": "け", "ko": "こ",
    "ga": "が", "gi": "ぎ", "gu": "ぐ", "ge": "げ", "go": "ご",
    "sa": "さ", "si": "し", "shi": "し", "su": "す", "se": "せ", "so": "そ",
    "za": "ざ", "zi": "じ", "ji": "じ", "zu": "ず", "ze": "ぜ", "zo": "ぞ",
    "ta": "た", "ti": "ち", "chi": "ち", "tu": "つ", "tsu": "つ", "te": "て", "to": "と",
    "da": "だ", "di": "ぢ", "du": "づ", "de": "で", "do": "ど",
    "na": "な", "ni": "に", "nu": "ぬ", "ne": "ね", "no": "の",
    "ha": "は", "hi": "ひ", "hu": "ふ", "fu": "ふ", "he": "へ", "ho": "ほ",
    "ba": "ば", "bi": "び", "bu": "ぶ", "be": "べ", "bo": "ぼ",
    "pa": "ぱ", "pi": "ぴ", "pu": "ぷ", "pe": "ぺ", "po": "ぽ",
    "ma": "ま", "mi": "み", "mu": "む", "me": "め", "mo": "も",
    "ya": "や", "yu": "ゆ", "yo": "よ", "ye": "いぇ",
    "ra": "ら", "ri": "り", "ru": "る", "re": "れ", "ro": "ろ",
    "wa": "わ", "wi": "うぃ", "wu": "う", "we": "うぇ", "wo": "を",
    "nn": "ん", "n'": "ん", "xn": "ん",
    "ca": "か", "ci": "し", "cu": "く", "ce": "せ", "co": "こ",
    "qa": "くぁ", "qi": "くぃ", "qu": "く", "qe": "くぇ", "qo": "くぉ",
    "fa": "ふぁ", "fi": "ふぃ", "fe": "ふぇ", "fo": "ふぉ", "fyu": "ふゅ",
    "va": "ゔぁ", "vi": "ゔぃ", "vu": "ゔ", "ve": "ゔぇ", "vo": "ゔぉ",
    "tsa": "つぁ", "tsi": "つぃ", "tse": "つぇ", "tso": "つぉ",
    "tha": "てゃ", "thi": "てぃ", "thu": "てゅ", "the": "てぇ", "tho": "てょ",
    "dha": "でゃ", "dhi": "でぃ", "dhu": "でゅ", "dhe": "でぇ", "dho": "でょ",
    "twu": "とぅ", "dwu": "どぅ",
    "wha": "うぁ", "whi": "うぃ", "whe": "うぇ", "who": "うぉ",
    "sha": "しゃ", "shu": "しゅ", "she": "しぇ", "sho": "しょ",
    "cha": "ちゃ", "chu": "ちゅ", "che": "ちぇ", "cho": "ちょ",
    "ja": "じゃ", "ju": "じゅ", "je": "じぇ", "jo": "じょ",
    "xa": "ぁ", "xi": "ぃ", "xu": "ぅ", "xe": "ぇ", "xo": "ぉ",
    "la": "ぁ", "li": "ぃ", "lu": "ぅ", "le": "ぇ", "lo": "ぉ",
    "xya": "ゃ", "xyu": "ゅ", "xyo": "ょ", "lya": "ゃ", "lyu": "ゅ", "lyo": "ょ",
    "xtu": "っ", "xtsu": "っ", "ltu": "っ", "ltsu": "っ",
    "xwa": "ゎ", "lwa": "ゎ", "xka": "ゕ", "xke": "ゖ",
}
# 拗音 (kya/sya/tya/... と jya/zya/cya)
for _c, _i in {"k": "き", "g": "ぎ", "s": "し", "z": "じ", "j": "じ", "t": "ち", "c": "ち",
               "d": "ぢ", "n": "に", "h": "ひ", "b": "び", "p": "ぴ", "m": "み", "r": "り"}.items():
    for _v, _small in (("a", "ゃ"), ("i", "ぃ"), ("u", "ゅ"), ("e", "ぇ"), ("o", "ょ")):
        _BASE.setdefault(_c + "y" + _v, _i + _small)

TABLE = _BASE
_MAXKEY = max(len(k) for k in TABLE)
_PREFIXES = {k[:i] for k in TABLE for i in range(1, len(k))}
_SOKUON = set("bcdfghjklmpqrstvwxyz")   # 重ねると「っ」になる子音 (n は除く)
_PUNCT = {",": "、", ".": "。", "-": "ー", "[": "「", "]": "」", "~": "〜", "/": "・"}

# 1要素: (かな, ローマ字の開始位置, 終了位置, 種別)  種別: 'kana' / 'raw' (タイプミスなどの英字)
Piece = Tuple[str, int, int, str]


def convert_pieces(roman: str) -> Tuple[List[Piece], str]:
    """ローマ字をかなの断片に分ける。(断片, 末尾の打ちかけ)"""
    s = roman.lower()
    n = len(s)
    out: List[Piece] = []
    i = 0
    while i < n:
        c = s[i]
        if not ("a" <= c <= "z" or c == "'"):
            out.append((_PUNCT.get(c, c), i, i + 1, "kana"))
            i += 1
            continue
        # nn + 母音/y: 最初の n だけを ん にして、次の n から読み直す (minna -> みんな)
        if s.startswith("nn", i) and i + 2 < n and s[i + 2] in VOWELS + "y":
            out.append(("ん", i, i + 1, "kana"))
            i += 1
            continue
        for length in range(min(_MAXKEY, n - i), 0, -1):
            piece = s[i:i + length]
            if piece in TABLE:
                out.append((TABLE[piece], i, i + length, "kana"))
                i += length
                break
        else:
            rest = s[i:]
            if rest in _PREFIXES:            # 入力の最後で、綴りの途中
                return out, roman[i:]
            nxt = s[i + 1] if i + 1 < n else ""
            if c in _SOKUON and (nxt == c or (c == "t" and nxt == "c")):
                out.append(("っ", i, i + 1, "kana"))
            elif c == "n" and nxt and nxt not in VOWELS + "yn'":
                out.append(("ん", i, i + 1, "kana"))
            else:
                out.append((roman[i], i, i + 1, "raw"))   # どの綴りにもならない
            i += 1
    return out, ""


def roman_to_hira(roman: str) -> Tuple[str, str]:
    """(かな, 末尾の打ちかけのローマ字)。かなに変換できない文字は英字のまま残す。"""
    pieces, pending = convert_pieces(roman)
    return "".join(p[0] for p in pieces), pending


def finalize(roman: str) -> str:
    """確定するとき: 末尾の打ちかけの n は ん にする"""
    hira, pending = roman_to_hira(roman)
    if pending.lower() == "n":
        return hira + "ん"
    return hira + pending


def has_raw(roman: str) -> bool:
    """かなにならない英字 (タイプミスの可能性が高い) を含むか"""
    return any(p[3] == "raw" for p in convert_pieces(roman)[0])


# ---------------------------------------------------------------------------
# ひらがな -> ローマ字 (評価用: 正しい読みから、人が打つローマ字を作る)
# ---------------------------------------------------------------------------
_PREFERRED = {}
for _k, _v in sorted(TABLE.items(), key=lambda kv: (len(kv[0]), kv[0])):
    if _k in ("nn", "n'", "xn", "wu", "qu", "cu", "ca", "co", "ci", "ce", "si", "zi", "ti",
              "tu", "hu", "di", "du") or _k.startswith(("l", "c", "q")):
        continue
    _PREFERRED.setdefault(_v, _k)
_PREFERRED.update({"し": "shi", "ち": "chi", "つ": "tsu", "ふ": "fu", "じ": "ji",
                   "ぢ": "di", "づ": "du", "ゃ": "xya", "ゅ": "xyu", "ょ": "xyo",
                   "ぁ": "xa", "ぃ": "xi", "ぅ": "xu", "ぇ": "xe", "ぉ": "xo", "ゎ": "xwa"})
_PUNCT_REV = {v: k for k, v in _PUNCT.items()}


def hira_to_roman(hira: str) -> str:
    """ひらがなを、roman_to_hira で元に戻るローマ字にする"""
    out: List[str] = []
    i = 0
    while i < len(hira):
        ch = hira[i]
        if ch == "っ":
            nxt = hira_to_roman(hira[i + 1:i + 3])[:1] if i + 1 < len(hira) else ""
            out.append(nxt if nxt and nxt in _SOKUON else "xtu")
            i += 1
            continue
        if ch == "ん":
            rest = hira[i + 1:]
            nxt = hira_to_roman(rest[:2])[:1] if rest else ""
            if not rest:
                out.append("nn")
            elif nxt and nxt in VOWELS + "y":
                out.append("n'")   # ん + 母音 (きんえん = kin'en)。nn だと ん + な行 になる
            else:
                out.append("n")
            i += 1
            continue
        for length in (2, 1):
            piece = hira[i:i + length]
            if piece in _PREFERRED:
                out.append(_PREFERRED[piece])
                i += length
                break
        else:
            out.append(_PUNCT_REV.get(ch, ch))
            i += 1
    return "".join(out)
