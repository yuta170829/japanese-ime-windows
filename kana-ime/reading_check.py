"""
reading_check.py -- LLM出力と入力の読みの整合チェック

macOS版のZenzaiは、LLMの出力を変換器の候補と突き合わせながら探索するので、
読みと無関係な文字は出てこない。kana-imeはLLMの貪欲生成をそのまま使うため、
代わりに「出力が入力の読みで説明できるか」を確かめる。

  今日は学校に行きます -> 入力 きょうはがっこうにいきます を説明できる   (採用)
  今日は学校に行きません -> 入力と終わりが合わない                       (不採用)

出力の文字位置ごとに、次のどれかで入力の読みを消費できるかを探索する。
  * MeCab(ipadic) の形態素の読み
  * 辞書にある熟語の読み (一人=ひとり, 三日=みっか など。MeCabが細かく切る語を拾う)
  * 漢字1文字ずつの読み
  * かな・記号はそのまま
  * 数字: 値の読みと一致するか (2週間=にしゅうかん、3週間は不一致)。
          二人=ふたり・三日=みっか・四月=しがつ など助数詞つきの特別な読みも扱う。
          小数・先頭が0の桁数字・巨大な数だけは、数詞の読みの並びなら通す
  * 英字: カタカナ語の表記 (LINE=らいん)。文字数が近い読みなら通す
入力の読みをちょうど使い切れたときだけ「整合」とする。

守備範囲: 読みと無関係な文字・活用の違い・脱落や余分な文字・数字の値の違いを落とす。
          同音異字 (橋/箸) は見分けられない (読みが同じなので)。
"""
from __future__ import annotations

import os
import re
import threading
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

import fugashi
import ipadic
import jaconv
import pykakasi

from engine import get_reading_dict

_KANJI = re.compile(r"[\u4e00-\u9fff々〆ヶ]")
_WS = re.compile(r"\s+")
_LATIN = re.compile(r"[A-Za-z]+")
_DIGITS = re.compile(r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?")

# 数詞の読みの並び (いち/に/さん... じゅう/ひゃく/せん/まん と促音・連濁形)
_NUM_PARTS = [
    "ぜろ", "れい", "まる", "いち", "いっ", "いつ", "ひと", "に", "ふた", "さん", "みっ", "み",
    "よん", "よっ", "よ", "し", "ご", "ろく", "ろっ", "む", "むっ", "なな", "しち", "な",
    "はち", "はっ", "や", "やっ", "きゅう", "く", "ここの", "じゅう", "じゅっ", "じっ", "とお",
    "ひゃく", "びゃく", "ぴゃく", "せん", "ぜん", "まん", "おく", "ちょう", "てん", "ぶ",
]
_NUM_RE = re.compile("(?:" + "|".join(sorted(_NUM_PARTS, key=len, reverse=True)) + ")+")

# ー(長音)を、直前のかなの母音として読み替えるための表
_VOWEL_ROWS = {
    "あ": "あかがさざただなはばぱまやゃらわぁゎ",
    "い": "いきぎしじちぢにひびぴみりぃ",
    "う": "うくぐすずつづぬふぶぷむゆゅるぅゔ",
    "え": "えけげせぜてでねへべぺめれぇ",
    "お": "おこごそぞとどのほぼぽもよょろをぉ",
}
_VOWEL_OF = {ch: v for v, chars in _VOWEL_ROWS.items() for ch in chars}

_MAX_WORD = 10  # 辞書の熟語として探す最大文字数

# ---------------------------------------------------------------------------
# 数字の読み (値を検証する)
# ---------------------------------------------------------------------------
# 一の位の読み (音便・別読みを含む)
_ONES = {
    0: {"ぜろ", "れい", "まる"}, 1: {"いち", "いっ"}, 2: {"に"}, 3: {"さん"},
    4: {"よん", "し", "よ"}, 5: {"ご"}, 6: {"ろく", "ろっ"}, 7: {"なな", "しち"},
    8: {"はち", "はっ"}, 9: {"きゅう", "く"},
}
# 十・百・千の位の係数の読み (4/7/9は一般形のみ)
_COEF = {2: "に", 3: "さん", 4: "よん", 5: "ご", 6: "ろく", 7: "なな", 8: "はち", 9: "きゅう"}
_HUNDRED = {1: {"ひゃく"}, 3: {"さんびゃく"}, 6: {"ろっぴゃく", "ろくひゃく"},
            8: {"はっぴゃく", "はちひゃく"}}
_THOUSAND = {1: {"せん", "いっせん"}, 3: {"さんぜん"}, 8: {"はっせん", "はちせん"}}

# 数字+助数詞でだけ起きる特別な読み: 助数詞 -> {値: 数字と助数詞をあわせた読み}
_JOINT = {
    "人": {1: {"ひとり"}, 2: {"ふたり"}},
    "日": {1: {"ついたち"}, 2: {"ふつか"}, 3: {"みっか"}, 4: {"よっか"}, 5: {"いつか"},
          6: {"むいか"}, 7: {"なのか"}, 8: {"ようか"}, 9: {"ここのか"}, 10: {"とおか"},
          14: {"じゅうよっか"}, 20: {"はつか"}, 24: {"にじゅうよっか"}},
    "つ": {1: {"ひとつ"}, 2: {"ふたつ"}, 3: {"みっつ"}, 4: {"よっつ"}, 5: {"いつつ"},
          6: {"むっつ"}, 7: {"ななつ"}, 8: {"やっつ"}, 9: {"ここのつ"}, 10: {"とお"}},
    "歳": {20: {"はたち"}},
}
# 助数詞の前でだけ使える数字の読み (四月=しがつ, 七時=しちじ, 九時=くじ など)
_BEFORE_COUNTER = {
    "月": {4: {"し"}, 7: {"しち"}, 9: {"く"}},
    "時": {4: {"よ"}, 7: {"しち"}, 9: {"く"}},
    "年": {4: {"よ"}, 7: {"しち"}, 9: {"く"}},
    "人": {4: {"よ"}, 7: {"しち"}},
}
_MAX_NUMBER = 10 ** 16


def _under_10000(n: int, last: bool) -> Set[str]:
    """1..9999 の読みの集合。last=True はその数の末尾の位 (一の位に別読みを許す)。"""
    th, rest = divmod(n, 1000)
    h, rest = divmod(rest, 100)
    t, o = divmod(rest, 10)
    parts: List[Set[str]] = []
    if th:
        parts.append(_THOUSAND.get(th) or {_COEF[th] + "せん"})
    if h:
        parts.append(_HUNDRED.get(h) or {_COEF[h] + "ひゃく"})
    if t:
        parts.append({"じゅう"} if t == 1 else {_COEF[t] + "じゅう"})
    if o:
        # 末尾の位は別読み(よ・し・しち・く など)も許す。万・億の前などは一般形だけ。
        parts.append(_ONES[o] if last else {"いち" if o == 1 else _COEF[o]})
    results = {""}
    for part in parts:
        results = {a + b for a in results for b in part}
    return results


def number_readings(value: int) -> Set[str]:
    """整数の読みの集合 (音便を含む)。値が違えば読みも違うので、値の検証に使える。"""
    if value == 0:
        return set(_ONES[0])
    groups = []  # (位の読み, 値)
    for unit, size in (("ちょう", 10 ** 12), ("おく", 10 ** 8), ("まん", 10 ** 4), ("", 1)):
        g, value = divmod(value, size)
        if g:
            groups.append((unit, g))
    results = {""}
    for idx, (unit, g) in enumerate(groups):
        is_last = idx == len(groups) - 1
        part = {r + unit for r in _under_10000(g, last=is_last and unit == "")}
        results = {a + b for a in results for b in part}
    # 語尾の音便: いち→いっ, ろく→ろっ, はち→はっ, じゅう→じゅっ/じっ, ひゃく→ひゃっ (助数詞の前)
    extra = set()
    for s in results:
        for tail, alts in (("いち", ("いっ",)), ("ろく", ("ろっ",)), ("はち", ("はっ",)),
                           ("じゅう", ("じゅっ", "じっ")), ("ひゃく", ("ひゃっ",))):
            if s.endswith(tail):
                extra |= {s[: -len(tail)] + a for a in alts}
    return results | extra


def _int_value(s: str) -> Optional[int]:
    """数字の塊を整数にする。小数・先頭が0の桁数字・大きすぎる数は None (緩い照合に回す)。"""
    if "." in s:
        return None
    digits = s.replace(",", "")
    if len(digits) > 1 and digits.startswith("0"):
        return None
    v = int(digits)
    return v if v < _MAX_NUMBER else None

_local = threading.local()
_kks = pykakasi.kakasi()


def _tagger() -> fugashi.GenericTagger:
    # MeCabのTaggerはスレッドごとに持つ (UIスレッドとLLMワーカーの両方から呼ばれうる)
    t = getattr(_local, "tagger", None)
    if t is None:
        d = ipadic.DICDIR.replace("\\", "/")
        rc = os.path.join(ipadic.DICDIR, "mecabrc").replace("\\", "/")
        t = _local.tagger = fugashi.GenericTagger(f"-d {d} -r {rc}")
    return t


def normalize(reading: str) -> str:
    """読みの表記ゆれをならす: 全角半角・カタカナ・空白・を/お・ぢ/じ・づ/ず・句読点。
    長音「ー」はここでは変えない (照合時に直前の母音として読む)。"""
    s = unicodedata.normalize("NFKC", reading)
    s = _WS.sub("", s)
    s = jaconv.kata2hira(s)
    return (s.replace("を", "お").replace("ぢ", "じ").replace("づ", "ず")
             .replace(",", "、").replace(".", "。").replace("，", "、").replace("．", "。"))


_inverse: Optional[Dict[str, Set[str]]] = None
_inverse_lock = threading.Lock()


def _inverse_dict() -> Dict[str, Set[str]]:
    """表記 -> 読みの集合 (pykakasiの辞書を逆向きに引く。変換エンジンの辞書と同じ元)"""
    global _inverse
    with _inverse_lock:
        if _inverse is None:
            inv: Dict[str, Set[str]] = {}
            for reading, words in get_reading_dict().items():
                n = normalize(reading)
                for w in words:
                    inv.setdefault(w, set()).add(n)
                    # 語幹: 降る(ふる) -> 降(ふ)。入力の途中では送り仮名の前で切れた形が出る
                    k = 0
                    while k < len(w) and "\u3041" <= w[-1 - k] <= "\u3096":
                        k += 1
                    if 0 < k < len(w) and _KANJI.search(w[:-k]) and n.endswith(normalize(w[-k:])):
                        inv.setdefault(w[:-k], set()).add(n[:-k])
            _inverse = inv
        return _inverse


def _consume(unit: str, target: str, pos: int) -> int:
    """読みの断片 unit を target の pos から消費できれば新しい位置、できなければ -1。
    長音「ー」は、直前の文字の母音と同じものとして比べる (こおひい == こーひー)。"""
    for ch in unit:
        if pos >= len(target):
            return -1
        tc = target[pos]
        if tc != ch:
            prev = target[pos - 1] if pos else ""
            if ch == "ー":
                ok = tc == _VOWEL_OF.get(prev, "")
            elif tc == "ー":
                ok = ch == _VOWEL_OF.get(prev, "")
            else:
                ok = False
            if not ok:
                return -1
        pos += 1
    return pos


def _char_readings(ch: str, prev: str, inv: Dict[str, Set[str]]) -> Set[str]:
    """1文字の読み"""
    if ch == "々":
        return set(_char_readings(prev, "", inv)) if prev else set()
    if ch == "ヶ":
        return {"か", "こ", "が", "け"}
    if _KANJI.search(ch):
        out = set(inv.get(ch, set()))
        out |= {normalize(i["hira"]) for i in _kks.convert(ch)}
        return out
    return {normalize(ch)}


def _is_partial_prefix(rest: str, full: str) -> bool:
    """入力の残り rest が、出力の最後の語の読み full の途中までか。
    打ちかけの読み (きにゅ → 記入=きにゅう) と、促音の途中 (いっ → 一=いち) を許す。"""
    core = rest[:-1] if rest.endswith("っ") else rest
    return bool(core) and len(core) < len(full) and _consume(core, full, 0) == len(core)


def matches(text: str, reading: str, partial: bool = False) -> bool:
    """LLMの出力 text が、入力の reading を説明できるか。
    partial=True は、読みが入力の途中で語の途中までしか打たれていない場合
    (最後の語だけ、読みが足りなくてもよい)。"""
    target = normalize(reading)
    if not text or not target:
        return False
    inv = _inverse_dict()
    text = _WS.sub("", unicodedata.normalize("NFKC", text))
    n = len(text)
    if n == 0:
        return False

    # 位置 i から j までを、どんな読みで説明できるか
    edges: List[List[Tuple[int, Set[str]]]] = [[] for _ in range(n)]

    # 1) MeCab の形態素
    cursor = 0
    for tok in _tagger()(text):
        start = text.find(tok.surface, cursor)
        if start < 0:
            continue
        end = start + len(tok.surface)
        cursor = end
        feat = tok.feature
        if isinstance(feat, tuple) and len(feat) > 7 and feat[7] not in (None, "*"):
            edges[start].append((end, {normalize(feat[7])}))

    # 2) 辞書の熟語 (MeCabが「一」「人」のように細かく切る語を拾う)
    for i in range(n):
        for j in range(i + 1, min(n, i + _MAX_WORD) + 1):
            readings = inv.get(text[i:j])
            if readings:
                edges[i].append((j, readings))

    # 3) 1文字ずつ (漢字は辞書にある全ての読み。かな・記号はそのまま)
    for i, ch in enumerate(text):
        if ch.isascii() and ch.isalnum():
            continue  # 英数字は下で塊として扱う
        r = _char_readings(ch, text[i - 1] if i else "", inv)
        if r:
            edges[i].append((i + 1, r))

    reach: List[Set[int]] = [set() for _ in range(n + 1)]
    reach[0].add(0)
    tlen = len(target)
    partial_ok = False
    for i in range(n):
        if not reach[i]:
            continue
        for pos in reach[i]:
            for j, readings in edges[i]:
                for r in readings:
                    p2 = _consume(r, target, pos)
                    if p2 >= 0:
                        reach[j].add(p2)
                    elif partial and j == n and _is_partial_prefix(target[pos:], r):
                        partial_ok = True  # 最後の語が、読みの途中までで終わっている
            m = _DIGITS.match(text, i)
            if m:  # 数字の塊
                digits, end = m.group(), m.end()
                if target.startswith(digits, pos):  # 入力にも同じ数字がある
                    reach[end].add(pos + len(digits))
                value = _int_value(digits)
                if value is None:
                    # 小数・桁数字・巨大な数は値まで検証せず、数詞の読みの並びなら通す
                    for p2 in range(pos + 1, min(tlen, pos + 8 * len(digits)) + 1):
                        if _NUM_RE.fullmatch(target, pos, p2):
                            reach[end].add(p2)
                else:
                    counter = text[end] if end < n else ""
                    readings = number_readings(value) | _BEFORE_COUNTER.get(counter, {}).get(value, set())
                    for r in readings:
                        p2 = _consume(r, target, pos)
                        if p2 >= 0:
                            reach[end].add(p2)
                    for r in _JOINT.get(counter, {}).get(value, ()):  # 二人=ふたり など
                        p2 = _consume(r, target, pos)
                        if p2 >= 0:
                            reach[end + 1].add(p2)
            m = _LATIN.match(text, i)
            if m:  # 英字の塊: 入力にも同じ綴りがあるか、文字数が近い かな の並びなら通す
                size = len(m.group())
                if target.lower().startswith(m.group().lower(), pos):
                    reach[m.end()].add(pos + size)
                for p2 in range(pos + max(1, (size + 1) // 2), min(tlen, pos + 2 * size + 1) + 1):
                    piece = target[pos:p2]
                    if all("\u3041" <= c <= "\u3096" or c == "ー" for c in piece):
                        reach[m.end()].add(p2)
    return tlen in reach[n] or partial_ok
