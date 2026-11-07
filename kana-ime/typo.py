"""
typo.py -- ローマ字のタイプミス補正 (候補の生成と判定。採点はLLMが行う)

流れ:
  1. 入力の読みを LLM(zenz) に通し、かな1文字ずつの対数確率を得る
  2. 怪しい箇所 = かなにならない英字 + 確率が特に低いかな。その周りのローマ字だけを
     1文字直した候補を作る (隣のキーへの置き換え・削除・挿入・入れ替え)
  3. 候補の読みを LLM で採点し、元より十分に良ければ置き換える
     採点 = 読みの対数確率 + LENGTH_BONUS × かなの数 (短い読みが有利になる偏りを打ち消す)

判定の基準値 (LENGTH_BONUS, MIN_GAIN など) は dev/eval_typo.py の評価で決めた。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from roman import VOWELS, convert_pieces, roman_to_hira

# QWERTY 配列の隣のキー
_ROWS = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
_POS = {c: (r, i) for r, row in enumerate(_ROWS) for i, c in enumerate(row)}

# ---- 判定の基準値 (dev/eval_typo.py で決めた) ----
LENGTH_BONUS = 2.5     # かな1文字あたりの加点
MIN_GAIN = 6.0         # これだけ採点が上がったら直す
MIN_GAIN_RAW = 0.0     # 英字が残っている(かなにならない)ときは、より積極的に直す
HOT_LOGPROB = -5.0     # この対数確率より低いかなを「怪しい」とみなす
# 評価 (各200件): ミスの文 110件を直せた / 8件を別の読みに変えた。
#                 正しい文・打っている途中の文 400件で、誤って直したのは 0件
HOT_TOP = 3            # 怪しい箇所として見る、確率の低いかなの数
MAX_VARIANTS = 160     # 1回に採点する候補の上限


def neighbors(c: str) -> List[str]:
    if c not in _POS:
        return []
    r, i = _POS[c]
    out = []
    for dr in (-1, 0, 1):
        for di in (-1, 0, 1):
            rr, ii = r + dr, i + di
            if (dr, di) != (0, 0) and 0 <= rr < 3 and 0 <= ii < len(_ROWS[rr]):
                out.append(_ROWS[rr][ii])
    return out


@dataclass
class Correction:
    roman: str          # 直したローマ字
    reading: str        # 直した読み
    gain: float         # 採点の伸び


def _char_to_piece(pieces) -> List[int]:
    """読みの各かなが、どのローマ字の断片から来たか"""
    owner: List[int] = []
    for idx, (kana, *_rest) in enumerate(pieces):
        owner.extend([idx] * len(kana))
    return owner


def hot_spans(roman: str, char_logprobs: Optional[Sequence[float]]) -> List[Tuple[int, int]]:
    """直す候補を作る範囲 (ローマ字の位置)。英字のまま残った文字と、確率の低いかな。"""
    pieces, _pending = convert_pieces(roman)
    spans: Set[Tuple[int, int]] = set()
    for kana, s, e, kind in pieces:
        if kind == "raw":
            spans.add((s, e))
    if char_logprobs:
        owner = _char_to_piece(pieces)
        ranked = sorted(range(len(char_logprobs)), key=lambda k: char_logprobs[k])
        for k in ranked[:HOT_TOP]:
            if char_logprobs[k] < HOT_LOGPROB and k < len(owner):
                _kana, s, e, _kind = pieces[owner[k]]
                spans.add((s, e))
    return sorted(spans)


def variants(roman: str, spans: List[Tuple[int, int]]) -> List[str]:
    """範囲のまわりのローマ字を1文字だけ直した候補"""
    _hira, pending = roman_to_hira(roman)
    limit = len(roman) - len(pending)   # 打ちかけの部分は直さない
    out: List[str] = []
    seen = {roman}

    def add(v: str) -> None:
        if v not in seen:
            seen.add(v)
            out.append(v)

    for s, e in spans:
        lo, hi = max(0, s - 1), min(limit, e + 1)
        for j in range(lo, hi):
            c = roman[j]
            for nb in neighbors(c):                       # 隣のキーを打った
                add(roman[:j] + nb + roman[j + 1:])
            add(roman[:j] + roman[j + 1:])                # 余分に打った
            if j + 1 < limit:                             # 2文字を逆に打った
                add(roman[:j] + roman[j + 1] + roman[j] + roman[j + 2:])
        for j in range(lo, min(limit, hi) + 1):           # 1文字打ち忘れた
            near = set(VOWELS) | {"n", "y", "h"}
            if j > 0:
                near |= {roman[j - 1]} | set(neighbors(roman[j - 1]))
            if j < len(roman):
                near |= set(neighbors(roman[j]))
            for c in sorted(near):
                add(roman[:j] + c + roman[j:])
    return out


PREFILTER = 24         # LLMで計算し直す候補の数 (最初の1文字の確率で絞る)
# PREFILTER の比較 (各200件、dev/eval_typo.py):
#    8: 直せた 80 / 誤変更 16 / 誤補正 0/400 / 中央値 0.18秒
#   12: 直せた 95 / 誤変更  7 / 誤補正 0/400 / 中央値 0.24秒
#   24: 直せた 102 / 誤変更 2 / 誤補正 0/400 / 中央値 0.39秒  <- 誤変更が少ないこれを使う


def candidate_readings(roman: str, spans: List[Tuple[int, int]]) -> Dict[str, str]:
    """直した候補の 読み -> ローマ字。打ちかけが変わるもの・英字を増やすものは除く"""
    reading, pending = roman_to_hira(roman)
    pieces, _ = convert_pieces(roman)
    raw_count = sum(p[3] == "raw" for p in pieces)
    out: Dict[str, str] = {}
    for v in variants(roman, spans):
        r, p = roman_to_hira(v)
        if p != pending or not r or r == reading or r in out:
            continue
        v_raw = sum(x[3] == "raw" for x in convert_pieces(v)[0])
        if (raw_count and v_raw >= raw_count) or (not raw_count and v_raw):
            continue   # 英字が減らない / 正しいかなを英字に壊す 直し方は使わない
        out[r] = v
        if len(out) >= MAX_VARIANTS:
            break
    return out


def correct(roman: str, scorer, cancelled: Optional[Callable[[], bool]] = None,
            prefilter: Optional[int] = None) -> Optional[Correction]:
    """タイプミスを直せそうなら Correction、直さないなら None。
    scorer は analyze_reading / score_variants を持つもの (llm.ZenzWorker)。
    cancelled() が True になったら途中でやめる (新しい入力が来たとき)。"""
    reading, _pending = roman_to_hira(roman)
    if not reading:
        return None
    has_raw = any(p[3] == "raw" for p in convert_pieces(roman)[0])
    a = scorer.analyze_reading(reading)
    spans = hot_spans(roman, a["per_char"])
    if not spans:
        return None
    cands = candidate_readings(roman, spans)
    if not cands:
        return None
    deltas = scorer.score_variants(a, list(cands), keep=prefilter or PREFILTER, cancelled=cancelled)
    if cancelled and cancelled():
        return None
    best: Optional[Correction] = None
    for r, d in deltas.items():
        gain = d + LENGTH_BONUS * (len(r) - len(reading))
        if best is None or gain > best.gain:
            best = Correction(cands[r], r, gain)
    if best is None:
        return None
    need = MIN_GAIN_RAW if has_raw else MIN_GAIN
    return best if best.gain > need else None
