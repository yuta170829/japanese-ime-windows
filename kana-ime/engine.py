"""
engine.py -- 日本語変換エンジン

ローマ字 → ひらがな → 漢字変換を行う。

変換戦略:
  1. roman.py でローマ字→ひらがな
  2. 内蔵文法辞書 + pykakasi逆引き辞書 を合成
  3. コスト付きDPで分割。助詞・助動詞は専用辞書で高優先度に扱う
  4. Segment（文節）のリストと候補を返す
"""
from __future__ import annotations

import difflib
import os
import pickle
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 辞書パス
# ---------------------------------------------------------------------------
_DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reading_dict.pkl")
_VERB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verb_dict.pkl")
_reading_dict: Optional[Dict[str, List[str]]] = None


_rd_lock = threading.Lock()


def get_reading_dict() -> Dict[str, List[str]]:
    global _reading_dict
    with _rd_lock:
        if _reading_dict is None:
            with open(_DICT_PATH, "rb") as f:
                _reading_dict = pickle.load(f)
        return _reading_dict


# ---------------------------------------------------------------------------
# 内蔵文法辞書
# 助詞・助動詞・動詞活用・一般的な語を手動で登録し高精度化する
# コスト: 小さいほど優先。文法語は語彙語より優先させる
# ---------------------------------------------------------------------------

# (読み, [候補...], コスト加点)
# コスト加点が負の値 = その語を積極的に選ぶ。正の値 = 消極的に選ぶ
_GRAMMAR_ENTRIES: List[Tuple[str, List[str], float]] = [
    # 助詞
    ("は",   ["は"],       -3.0),
    ("が",   ["が"],       -3.0),
    ("を",   ["を"],       -3.0),
    ("に",   ["に"],       -3.0),
    ("で",   ["で"],       -3.0),
    ("と",   ["と"],       -3.0),
    ("の",   ["の"],       -3.0),
    ("も",   ["も"],       -3.0),
    ("へ",   ["へ"],       -3.0),
    ("から", ["から"],     -3.0),
    ("まで", ["まで"],     -3.0),
    ("より", ["より"],     -3.0),
    ("だけ", ["だけ"],     -2.5),
    ("など", ["など"],     -2.5),
    ("って", ["って"],     -2.0),
    ("では", ["では"],     -3.5),
    ("には", ["には"],     -3.5),
    ("をも", ["をも"],     -3.0),
    ("とは", ["とは"],     -3.0),
    ("への", ["への"],     -2.5),
    ("からの", ["からの"], -3.0),
    # 助動詞・動詞語尾
    ("ます", ["ます"],     -3.0),
    ("ません", ["ません"], -3.0),
    ("ました", ["ました"], -3.0),
    ("ませんでした", ["ませんでした"], -3.0),
    ("です", ["です"],     -3.0),
    ("でした", ["でした"], -3.0),
    ("ている", ["ている"], -3.0),
    ("ていた", ["ていた"], -3.0),
    ("てある", ["てある"], -2.5),
    ("ておく", ["ておく"], -2.5),
    ("てみる", ["てみる"], -2.5),
    ("てしまう", ["てしまう"], -2.5),
    ("ている", ["ている"], -3.0),
    ("てください", ["てください"], -3.0),
    ("ない", ["ない"],     -2.5),
    ("なかった", ["なかった"], -2.5),
    ("れる", ["れる"],     -2.0),
    ("られる", ["られる"], -2.0),
    ("せる", ["せる"],     -2.0),
    ("させる", ["させる"], -2.0),
    ("たい", ["たい"],     -2.5),
    ("たかった", ["たかった"], -2.5),
    ("だった", ["だった"], -2.5),
    ("だろう", ["だろう"], -2.5),
    ("でしょう", ["でしょう"], -2.5),
    ("かもしれない", ["かもしれない"], -2.5),
    ("なければならない", ["なければならない"], -2.0),
    # 動詞活用（よく使う語）
    ("いく",   ["行く"],   -2.5),
    ("いき",   ["行き"],   -2.5),
    ("いって", ["行って"], -2.5),
    ("いった", ["行った"], -2.5),
    ("いきます", ["行きます"], -3.0),
    ("くる",   ["来る"],   -2.5),
    ("くるとき", ["来るとき"], -2.5),
    ("みる",   ["見る"],   -2.0),
    ("いる",   ["いる"],   -2.5),
    ("ある",   ["ある"],   -2.5),
    ("なる",   ["なる"],   -2.5),
    ("する",   ["する"],   -2.5),
    ("できる", ["できる"], -2.5),
    ("おもう", ["思う"],   -2.0),
    ("おもい", ["思い"],   -2.0),
    ("かく",   ["書く"],   -2.0),
    ("かいた", ["書いた"], -2.0),
    ("よむ",   ["読む"],   -2.0),
    ("よんだ", ["読んだ"], -2.0),
    ("きく",   ["聞く"],   -2.0),
    ("きいた", ["聞いた"], -2.0),
    ("はなす", ["話す"],   -2.0),
    ("はなした", ["話した"], -2.0),
    ("つかう", ["使う"],   -2.0),
    ("もちいる", ["用いる"], -2.0),
    ("しらべる", ["調べる"], -2.0),
    ("かんがえる", ["考える"], -2.0),
    ("わかる", ["分かる"], -2.0),
    ("わかった", ["分かった"], -2.0),
    ("おこなう", ["行う"], -2.0),
    ("おこなった", ["行った"], -1.5),
    # 代名詞・接続詞
    ("わたし", ["私"],     -2.5),
    ("わたしは", ["私は"], -3.0),
    ("ぼく",   ["僕"],     -2.0),
    ("かれ",   ["彼"],     -2.0),
    ("かのじょ", ["彼女"], -2.0),
    ("これ",   ["これ"],   -2.0),
    ("それ",   ["それ"],   -2.0),
    ("あれ",   ["あれ"],   -2.0),
    ("この",   ["この"],   -2.0),
    ("その",   ["その"],   -2.0),
    ("あの",   ["あの"],   -2.0),
    ("どの",   ["どの"],   -2.0),
    ("ここ",   ["ここ"],   -2.0),
    ("そこ",   ["そこ"],   -2.0),
    ("あそこ", ["あそこ"], -2.0),
    ("しかし", ["しかし"], -2.0),
    ("また",   ["また"],   -2.0),
    ("そして", ["そして"], -2.0),
    ("それで", ["それで"], -2.0),
    ("だから", ["だから"], -2.0),
    ("でも",   ["でも"],   -2.5),
    ("もし",   ["もし"],   -2.0),
    ("もしも", ["もしも"], -2.0),
    # 時間・場所
    ("きょう",   ["今日"],   -2.5),
    ("きょうは", ["今日は"], -3.0),
    ("あした",   ["明日"],   -2.5),
    ("きのう",   ["昨日"],   -2.5),
    ("ことし",   ["今年"],   -2.5),
    ("いま",     ["今"],     -2.5),
    ("まえ",     ["前"],     -2.0),
    ("あと",     ["後"],     -2.0),
    ("うえ",     ["上"],     -2.0),
    ("した",     ["下"],     -1.5),  # 「した」は動詞「した」も多いので低め
    ("ひだり",   ["左"],     -2.0),
    ("みぎ",     ["右"],     -2.0),
    ("にほん",   ["日本"],   -2.5),
    ("にほんご", ["日本語"], -3.0),
    ("とうきょう", ["東京"], -2.5),
    # よく使う名詞
    ("がっこう", ["学校"],   -2.5),
    ("かいしゃ", ["会社"],   -2.5),
    ("ひと",     ["人"],     -2.0),
    ("こと",     ["こと"],   -2.0),
    ("もの",     ["もの"],   -2.0),
    ("とき",     ["時"],     -2.0),
    ("ところ",   ["ところ"], -2.0),
    ("ばあい",   ["場合"],   -2.0),
    ("もんだい", ["問題"],   -2.0),
    ("けっか",   ["結果"],   -2.0),
    ("ほうほう", ["方法"],   -2.0),
    ("ばしょ",   ["場所"],   -2.0),
    ("しごと",   ["仕事"],   -2.0),
    ("べんきょう", ["勉強"], -2.0),
    ("りょうり", ["料理"],   -2.0),
    ("えいご",   ["英語"],   -2.0),
    ("まなぶ",   ["学ぶ"],   -2.0),
    ("せんせい", ["先生"],   -2.0),
    ("がくせい", ["学生"],   -2.0),
    ("ともだち", ["友達"],   -2.0),
    ("かぞく",   ["家族"],   -2.0),
    ("こども",   ["子ども"], -2.0),
    ("おとな",   ["大人"],   -2.0),
    ("おとこ",   ["男"],     -2.0),
    ("おんな",   ["女"],     -2.0),
    ("くに",     ["国"],     -2.0),
    ("まち",     ["町"],     -2.0),
    ("いえ",     ["家"],     -2.0),
    ("へや",     ["部屋"],   -2.0),
    ("みず",     ["水"],     -2.0),
    ("たべもの", ["食べ物"], -2.0),
    ("のみもの", ["飲み物"], -2.0),
    # 形容詞
    ("おおきい", ["大きい"], -2.0),
    ("ちいさい", ["小さい"], -2.0),
    ("あたらしい", ["新しい"], -2.0),
    ("ふるい",   ["古い"],   -2.0),
    ("たのしい", ["楽しい"], -2.0),
    ("むずかしい", ["難しい"], -2.0),
    ("やさしい", ["優しい"], -2.0),
    ("おもしろい", ["面白い"], -2.0),
    ("たいせつ", ["大切"],   -2.0),
    ("じゅうよう", ["重要"], -2.0),
    # 複合語・熟語（分割されやすいものを明示登録）
    ("べんきょうしています", ["勉強しています"], -3.5),
    ("べんきょうしていた", ["勉強していた"],  -3.5),
    ("べんきょうして",   ["勉強して"],     -3.0),
    ("べんきょうする",   ["勉強する"],     -2.5),
    ("していた",   ["していた"],   -2.5),
    ("している",   ["している"],   -2.5),
    ("していない", ["していない"], -2.5),
    ("しています", ["しています"], -3.0),
    ("きかいがくしゅう",  ["機械学習"],  -3.0),
    ("このこと",   ["このこと"],   -2.5),
    ("このことを", ["このことを"], -3.0),
    ("もうすこし", ["もう少し"],   -3.0),
    ("わかりました",  ["分かりました"],  -3.0),
    ("わかります",    ["分かります"],    -3.0),
    ("わかった",      ["分かった"],      -2.5),
    ("わかって",      ["分かって"],      -2.5),
    ("わかり",        ["分かり"],        -2.0),
    ("わすれないで",  ["忘れないで"],    -3.0),
    ("ほんけんきゅうでは", ["本研究では"], -3.5),
    ("ほんけんきゅう",    ["本研究"],     -3.0),
    ("きかい",            ["機械"],       -2.0),
    ("がくしゅう",        ["学習"],       -2.0),
    ("きかいがくしゅうを", ["機械学習を"], -3.5),
    ("だいじょうぶです",  ["大丈夫です"], -3.0),
    ("ありがとうございます", ["ありがとうございます"], -3.0),
    ("よろしくおねがいします", ["よろしくお願いします"], -3.0),
    ("おねがいします",   ["お願いします"], -3.0),
    ("すみません",       ["すみません"],  -3.0),
    ("はじめまして",     ["はじめまして"], -3.0),
    ("おはようございます", ["おはようございます"], -3.0),
    ("こんにちは",       ["こんにちは"],  -3.0),
    ("こんばんは",       ["こんばんは"],  -3.0),
    ("ことを",           ["ことを"],      -2.5),
    ("ことが",           ["ことが"],      -2.5),
    ("ことは",           ["ことは"],      -2.5),
    ("によって",         ["によって"],    -2.5),
    ("において",         ["において"],    -2.5),
    ("にかんして",       ["に関して"],    -2.5),
    ("にもかかわらず",   ["にもかかわらず"], -2.5),
    ("したがって",       ["したがって"],  -2.0),
    ("たとえば",         ["例えば"],      -2.0),
    ("つまり",           ["つまり"],      -2.0),
    ("なぜなら",         ["なぜなら"],    -2.0),
    ("さらに",           ["さらに"],      -2.0),
    ("しかしながら",     ["しかしながら"], -2.5),
    ("ということ",       ["ということ"], -2.5),
    ("というのは",       ["というのは"],  -2.5),
    ("もうすこしだけ",   ["もう少しだけ"], -3.5),
    ("きをつけて",       ["気をつけて"],  -2.5),
    ("おもいます",       ["思います"],    -2.5),
    ("おもいました",     ["思いました"],  -2.5),
    ("いいます",         ["言います"],    -2.5),
    ("いいました",       ["言いました"],  -2.5),
    ("おこなわれる",     ["行われる"],    -2.5),
    ("おこなわれた",     ["行われた"],    -2.5),
    ("もちいる",         ["用いる"],      -2.0),
    ("もちいられる",     ["用いられる"],  -2.0),
    # konokooto / konokouto 両形式に対応
    ("このこおと",       ["このこと"],    -3.0),
    ("このこうと",       ["このこと"],    -3.0),
    ("このこおとを",     ["このことを"],  -3.5),
    ("このこうとを",     ["このことを"],  -3.5),
    # 「なさい」命令形
    ("なさい",           ["なさい"],      -2.5),
    ("しなさい",         ["しなさい"],    -2.5),
    ("みなさい",         ["見なさい"],    -2.5),
    ("いきなさい",       ["行きなさい"],  -2.5),
    ("わかりなさい",     ["分かりなさい"], -2.5),
    # 「がくしゅう」が「が」+「くしゅう」に割れる対策
    ("きかいがくしゅうを", ["機械学習を"], -4.0),
    ("きかいがくしゅうに", ["機械学習に"], -4.0),
    ("きかいがくしゅうは", ["機械学習は"], -4.0),
    # 「べんきょうしています」対策: 全体エントリのbonus強化済み
    # 「しています」単体も高優先に
    ("しています",       ["しています"],  -3.5),
    ("していました",     ["していました"], -3.5),
    # 「て形」+「いる」の続き（活用形辞書は 行って+いる のように分割して扱う）
    ("います",           ["います"],      -3.0),
    ("いました",         ["いました"],    -3.0),
    ("いません",         ["いません"],    -3.0),
    ("いない",           ["いない"],      -2.5),
    ("いた",             ["いた"],        -1.5),
    ("いて",             ["いて"],        -1.0),
    ("ある",             ["ある"],        -2.5),
    ("あります",         ["あります"],    -3.0),
    ("ありません",       ["ありません"],  -3.0),
    ("ありました",       ["ありました"],  -3.0),
    ("ください",         ["ください"],    -3.0),
    ("くださいました",   ["くださいました"], -3.0),
    ("ところ",           ["ところ"],      -2.0),
    ("ながら",           ["ながら"],      -2.5),
    ("ので",             ["ので"],        -3.0),
    ("のに",             ["のに"],        -2.5),
    ("けど",             ["けど"],        -2.5),
    ("けれど",           ["けれど"],      -2.5),
    ("けれども",         ["けれども"],    -2.5),
    ("ために",           ["ために"],      -2.5),
    ("ようです",         ["ようです"],    -2.5),
    ("そうです",         ["そうです"],    -2.5),
    ("らしい",           ["らしい"],      -2.0),
    ("いつも",           ["いつも"],      -3.0),
    ("いつ",             ["いつ"],        -2.5),
    ("どう",             ["どう"],        -2.0),
    ("なに",             ["何"],          -2.0),
    ("なにか",           ["何か"],        -2.5),
    ("だれ",             ["誰"],          -2.0),
]

# 読みをキーにした辞書を構築
_GRAMMAR_DICT: Dict[str, Tuple[List[str], float]] = {}
for _reading, _words, _bonus in _GRAMMAR_ENTRIES:
    _GRAMMAR_DICT[_reading] = (_words, _bonus)


def _build_merged_dict() -> Dict[str, Tuple[List[str], float]]:
    """文法辞書 + pykakasi逆引き辞書を統合した辞書を返す"""
    base = get_reading_dict()
    merged: Dict[str, Tuple[List[str], float]] = {}

    # pykakasi辞書をベースに（コスト加点 0）
    for reading, words in base.items():
        merged[reading] = (words, 0.0)

    # 動詞・形容詞の活用形（build_verbs.py が生成）。語幹だけの短い断片に
    # 負けないよう、わずかに加点し、基本辞書より先に候補へ並べる。
    if os.path.exists(_VERB_PATH):
        with open(_VERB_PATH, "rb") as f:
            for reading, words in pickle.load(f).items():
                existing = merged.get(reading, ([], 0.0))[0]
                merged[reading] = (list(dict.fromkeys(words + existing)), -1.0)

    # 文法辞書で上書き（優先候補・コスト加点を設定）
    for reading, (words, bonus) in _GRAMMAR_DICT.items():
        if reading in merged:
            # 文法辞書の候補を先頭に置き、残りをマージ
            existing = merged[reading][0]
            combined = list(dict.fromkeys(words + existing))
            merged[reading] = (combined, bonus)
        else:
            merged[reading] = (words, bonus)

    return merged


_merged_dict: Optional[Dict[str, Tuple[List[str], float]]] = None


_dict_lock = threading.Lock()


def get_merged_dict() -> Dict[str, Tuple[List[str], float]]:
    global _merged_dict
    with _dict_lock:  # UIスレッドとLLMワーカーの両方から呼ばれる
        if _merged_dict is None:
            _merged_dict = _build_merged_dict()
        return _merged_dict


# ---------------------------------------------------------------------------
# ローマ字 → ひらがな
# ---------------------------------------------------------------------------
# ローマ字 -> ひらがな は roman.py (一般的なIMEと同じ規則。打ちかけは英字のまま残す)
from roman import roman_to_hira  # noqa: E402,F401  (他のモジュールもここから使う)


# ---------------------------------------------------------------------------
# 変換候補クラス
# ---------------------------------------------------------------------------
@dataclass
class Segment:
    """変換の1文節"""
    reading: str
    candidates: List[str] = field(default_factory=list)
    selected: int = 0

    @property
    def text(self) -> str:
        return self.candidates[self.selected] if self.candidates else self.reading


# ---------------------------------------------------------------------------
# DPによる分割
# ---------------------------------------------------------------------------
_MAX_WORD_LEN = 12  # 最大マッチ長（長めに取る）


def _segment_hira(hira: str) -> List[Segment]:
    """
    ひらがなをDP最適分割してSegmentリストを返す。

    コスト計算 (小さいほど優先):
      new_cost = prev_cost - length * weight
      weight = 1.0 + grammar_bonus_per_char
        grammar_bonus_per_char: 文法辞書の語は文字当たりのボーナスを上乗せ
        例: 「は」(1文字, bonus_pc=3.0) → weight=4.0 → -4.0
            「きょ」(2文字, 辞書なし bonus_pc=0) → weight=1.0 → -2.0
        → 「は」の1文字が「きょ」の2文字より優先される場面を回避しつつ
          「きょうは」(5文字, bonus_pc=0.6) → -5×1.6=-8.0 が
          「きょ」+「う」+「は」= -2 + -1 + -4 = -7.0 より優先される
    """
    d = get_merged_dict()
    n = len(hira)
    if n == 0:
        return []

    dp: List[Optional[Tuple[float, int, List[str]]]] = [None] * (n + 1)
    dp[0] = (0.0, -1, [])

    for i in range(n):
        if dp[i] is None:
            continue
        cost_i = dp[i][0]

        for length in range(min(_MAX_WORD_LEN, n - i), 0, -1):
            sub = hira[i: i + length]
            if sub in d:
                words, bonus = d[sub]
                # コスト計算:
                #   base: length^1.6 → 長い語ほど指数的に有利（分割を抑制）
                #   grammar_bonus: 文法語（助詞・助動詞）はさらに加点
                base = length ** 1.6
                grammar_bonus = max(0.0, -bonus)  # bonus が負の語だけ加算
                new_cost = cost_i - (base + grammar_bonus)
                j = i + length
                if dp[j] is None or new_cost < dp[j][0]:
                    dp[j] = (new_cost, i, words)

        # マッチなし1文字（低優先: 1文字辞書ヒットより低くする）
        j = i + 1
        c = hira[i]
        new_cost = cost_i - 0.2
        if dp[j] is None or new_cost < dp[j][0]:
            dp[j] = (new_cost, i, [c])

    # パスを逆順に復元
    if dp[n] is None:
        return [Segment(reading=hira, candidates=[hira])]

    segments: List[Segment] = []
    pos = n
    while pos > 0:
        entry = dp[pos]
        if entry is None:
            break
        _, prev, words = entry
        sub = hira[prev:pos]
        # 読みひらがなも候補末尾に追加（Escで戻せる）
        cands = list(dict.fromkeys(words + [sub]))
        segments.append(Segment(reading=sub, candidates=cands))
        pos = prev

    segments.reverse()
    return segments


def standard_text(reading: str) -> str:
    """ひらがなの読みを標準変換(辞書+DP)した最有力の表記にする"""
    return "".join(s.text for s in _segment_hira(reading))


def _diff_parts(standard: str, neural: str) -> List[Tuple[str, str]]:
    """標準変換とLLM結果を文字単位で比べ、LLMが書き換えた箇所を 'neural' にする"""
    parts: List[Tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(None, standard, neural, autojunk=False)
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete" or j1 == j2:
            continue
        kind = "std" if tag == "equal" else "neural"
        if parts and parts[-1][1] == kind:
            parts[-1] = (parts[-1][0] + neural[j1:j2], kind)
        else:
            parts.append((neural[j1:j2], kind))
    return parts


# ---------------------------------------------------------------------------
# 入力状態管理
# ---------------------------------------------------------------------------
@dataclass
class ConversionState:
    """変換中の全状態を保持する"""
    committed: str = ""
    roman_input: str = ""
    segments: List[Segment] = field(default_factory=list)
    selecting: bool = False
    active_clause: int = 0
    # LLM(zenz)の補正結果。neural_reading の範囲に対する変換結果 neural_text
    neural_reading: str = ""
    neural_text: str = ""
    typo_reading: str = ""    # タイプミスを直した読み (neural_text はこの読みの変換)
    typo_roman: str = ""      # タイプミスを直したローマ字
    llm_first: bool = False   # 全文候補の第1候補がLLMの結果か
    touched: bool = False     # 候補を開いたあと、ユーザーが選択を動かしたか
    selection_reading: str = ""  # 候補を開いたときに変換していた読み (別案の照合用)

    # ---- 読み取り専用プロパティ ----

    @property
    def hira_complete(self) -> str:
        hira, _ = roman_to_hira(self.roman_input)
        return hira

    @property
    def roman_pending(self) -> str:
        _, pending = roman_to_hira(self.roman_input)
        return pending

    def _neural_active(self) -> bool:
        return bool(self.neural_text) and not self.selecting \
            and self.hira_complete.startswith(self.neural_reading)

    def display_parts(self) -> List[Tuple[str, str]]:
        """未確定部分を (文字列, 種別) の列で返す。
        種別: 'std' 標準変換 / 'neural' LLMが書き換えた箇所 / 'pending' 未完成のローマ字"""
        parts: List[Tuple[str, str]] = []
        if self._neural_active():
            head = "".join(s.text for s in _segment_hira(self.neural_reading))
            parts += _diff_parts(head, self.neural_text)
            tail = self.hira_complete[len(self.neural_reading):]
            if tail:
                parts.append(("".join(s.text for s in _segment_hira(tail)), "std"))
        else:
            parts = [(s.text, "std") for s in self.segments]
        if self.roman_pending:
            parts.append((self.roman_pending, "pending"))
        return parts

    @property
    def marked(self) -> str:
        return "".join(text for text, _ in self.display_parts())

    # ---- LLM補正 ----

    def apply_neural(self, text: str, expected_reading: str,
                     typo_reading: str = "", typo_roman: str = "") -> bool:
        """LLMの結果を反映する。読みが変わっていたり候補選択中なら捨てる。
        標準変換と文字列が変わったときだけ True。
        typo_reading があれば、text はタイプミスを直した読み typo_reading の変換。"""
        if self.selecting or not text or not expected_reading \
                or self.hira_complete != expected_reading:
            return False
        standard = "".join(s.text for s in self.segments)
        if text == standard and not typo_reading:
            # 補正がないなら保持しない。保持すると、続けて打ったときに読みを境界で
            # 区切って別々に標準変換するので、(きょ|う → 居|右) 標準変換が悪くなる。
            self.clear_neural()
            return False
        self.neural_reading, self.neural_text = expected_reading, text
        self.typo_reading, self.typo_roman = typo_reading, typo_roman
        return True

    def clear_neural(self) -> None:
        self.neural_reading = self.neural_text = ""
        self.typo_reading = self.typo_roman = ""

    @property
    def typo_fixed(self) -> bool:
        """表示中の変換が、タイプミスを直した読みのものか"""
        return bool(self.typo_reading) and self._neural_active()

    def effective_reading(self) -> str:
        """実際に変換している読み (タイプミスを直していれば直した読み)"""
        hira = self.hira_complete
        if self.typo_fixed:
            return self.typo_reading + hira[len(self.neural_reading):]
        return hira

    def whole_sentence_selection(self) -> bool:
        """候補が全文1つにまとまっている状態か (LLM補正を引き継いだとき、
        またはLLMが追いつく前に候補を開いたとき)"""
        return self.selecting and len(self.segments) == 1 \
            and self.segments[0].reading == self.hira_complete

    def add_alternatives(self, texts: List[str], reading: str) -> int:
        """LLMの候補 (texts[0] が最良、続きが別案) を全文候補に差し込む。追加した件数を返す。

        * 第1候補がLLM補正のとき: 別案をその直後に入れる
        * 第1候補が標準変換のとき (LLMが追いつく前にSpaceを押した): LLMの候補を先頭に入れる。
          まだ候補を動かしていなければ、LLMの最良を選択状態にする
        ユーザーが自分で選んだ候補は動かさない。"""
        if not self.whole_sentence_selection() or reading != (self.selection_reading or self.hira_complete):
            return 0
        seg = self.segments[0]
        chosen = seg.candidates[seg.selected]
        fresh = [t for t in texts if t not in seg.candidates]
        if not fresh:
            return 0
        if self.llm_first:
            seg.candidates = seg.candidates[:1] + fresh + seg.candidates[1:]
            seg.selected = seg.candidates.index(chosen)
        else:
            seg.candidates = fresh + seg.candidates
            seg.selected = 0 if not self.touched else seg.candidates.index(chosen)
            self.llm_first = True
        return len(fresh)

    def select_candidate(self, index: int) -> bool:
        """マウスで候補を選ぶ"""
        if not self.selecting or not self.segments:
            return False
        seg = self.segments[self.active_clause]
        if not 0 <= index < len(seg.candidates):
            return False
        seg.selected = index
        self.touched = True
        return True

    def _freeze_neural(self) -> None:
        """候補選択に入る前に、表示中のLLM補正を第一候補として引き継ぐ。
        補正が標準変換と違うときは全文を1つの文節にまとめる。"""
        self.llm_first = False
        self.touched = False
        self.selection_reading = self.effective_reading()
        if self.neural_text and self._neural_active():
            hira = self.hira_complete
            standard = "".join(s.text for s in self.segments)
            if self.neural_reading != hira:
                # LLMの補正が入力の途中までしか追いついていない。継ぎはぎの表示
                # (補正済みの頭 + 別々に変換した残り) は候補にせず、全文の標準変換を
                # 第1候補にして、LLMの候補が届くのを待つ。
                self.segments = [Segment(reading=hira,
                                         candidates=list(dict.fromkeys([standard, hira])))]
            else:
                shown = "".join(t for t, k in self.display_parts() if k != "pending")
                if shown != standard:
                    self.segments = [Segment(
                        reading=hira,
                        candidates=list(dict.fromkeys([shown, standard, hira])))]
                    self.llm_first = True
        self.clear_neural()

    @property
    def candidates(self) -> List[str]:
        if not self.selecting or not self.segments:
            return []
        return self.segments[self.active_clause].candidates

    @property
    def selected_index(self) -> int:
        if not self.selecting or not self.segments:
            return 0
        return self.segments[self.active_clause].selected

    # ---- 内部更新 ----

    def _refresh(self) -> None:
        hira, _ = roman_to_hira(self.roman_input)
        self.segments = _segment_hira(hira) if hira else []
        self.selecting = False
        self.active_clause = 0
        # 追加入力は補正を保つ。補正済みの読みが変わったとき(削除など)は捨てる。
        if self.neural_text and (not hira or not hira.startswith(self.neural_reading)):
            self.clear_neural()

    # ---- 入力操作 ----

    def insert(self, char: str) -> None:
        """ローマ字1文字を追加"""
        if self.selecting and self.segments:
            # 選択中に文字入力 → 確定して続ける
            self.committed += self._final_text()
            self.roman_input = ""
            self.selecting = False
            self.clear_neural()
        char = char.replace(",", "、").replace(".", "。")
        self.roman_input += char
        self._refresh()

    def backspace(self) -> None:
        if self.roman_input:
            self.roman_input = self.roman_input[:-1]
            self._refresh()
        elif self.committed:
            self.committed = self.committed[:-1]

    def _final_text(self) -> str:
        """確定する文字列。末尾の打ちかけの n は ん にする (honn と打たなくてよい)"""
        text = self.marked
        pending = self.roman_pending
        if pending and pending.lower() == "n" and text.endswith(pending):
            text = text[: -len(pending)] + "ん"
        return text

    def commit(self) -> None:
        if self.roman_input:
            self.committed += self._final_text()
            self.roman_input = ""
            self.segments = []
            self.selecting = False
            self.clear_neural()
        else:
            self.committed += "\n"

    def use_reading(self) -> None:
        """Esc: ひらがなに戻す"""
        self.selecting = False
        self.clear_neural()
        for s in self.segments:
            s.selected = 0
        self.active_clause = 0

    def start_selection(self) -> None:
        """Space: 候補選択開始 / 次候補へ"""
        if not self.segments:
            return
        if not self.selecting:
            self._freeze_neural()
            self.selecting = True
            self.active_clause = 0
        else:
            self.cycle(1)

    def cycle(self, delta: int = 1) -> None:
        if not self.segments:
            return
        clause = self.segments[self.active_clause]
        clause.selected = (clause.selected + delta) % len(clause.candidates)
        self.touched = True

    def move_clause(self, delta: int) -> None:
        if not self.segments:
            return
        if not self.selecting:
            self._freeze_neural()
            self.selecting = True
        self.active_clause = max(0, min(len(self.segments) - 1,
                                        self.active_clause + delta))

    def clear(self) -> None:
        self.committed = ""
        self.roman_input = ""
        self.segments = []
        self.selecting = False
        self.active_clause = 0
        self.clear_neural()


# ---------------------------------------------------------------------------
# 簡易テスト
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [
        ("kyouhagakkouniikimasu",           "今日は学校に行きます"),
        ("honkenkyuudehakikaigakushuu",     "本研究では機械学習"),
        ("nihongo",                          "日本語"),
        ("watashihanihongo",                "私は日本語"),
        ("konokotowo wasurenaide",          "このことを忘れないで"),  # を=wo と打つ必要あり
        ("benkyoushiteimasu",               "勉強しています"),
        ("sorehadaijoubudesu",              "それは大丈夫です"),
        ("mousukosiwakarinasai",            "もう少し分かりなさい"),  # nasui→なすい問題回避
        ("kinou gakkou de tomodachi to hanashita", "昨日学校で友達と話した"),
        ("korehamondaidesu",                "これは問題です"),
    ]
    state = ConversionState()
    ok = 0
    for roman, expected in tests:
        state.clear()
        for c in roman:
            state.insert(c)
        result = state.marked
        hit = expected in result or result == expected
        if hit:
            ok += 1
        status = "✓" if hit else "△"
        print(f"{status} {roman}")
        print(f"  期待: {expected}")
        print(f"  結果: {result}")
        segs = [(s.reading, s.candidates[:2]) for s in state.segments]
        print(f"  分割: {segs}")
        print()
    print(f"結果: {ok}/{len(tests)} 件")
