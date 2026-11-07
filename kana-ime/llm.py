"""
llm.py -- zenz-v3.2-small による遅延補正 (llama-cpp-python, CPU)

macOS版 NeuralWorker と同じ考え方:
  * 推論は別スレッドで常に1件ずつ。実行中に来た入力は最新の1件にまとめる。
  * 結果に入力世代ID(req_id)を付け、古い結果はUI側で捨てる。
  * 出力を読みへ戻して入力の読みと照合し(reading_check)、不一致のチャンクは
    標準変換に差し替える。LLMが読みと無関係な文字を出しても画面に出さない。
  * 長い読みは句読点・最大100文字で分割し、前の変換結果を次の文脈に使う。
  * 同じ(文脈, 読み)の結果は最大128件キャッシュする。
  * 入力文章は外部へ送らない(すべてローカル推論)。
"""
from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_ORIGINAL = os.path.join(HERE, "models", "zenz-v3.2-small-Q5_K_M.gguf")
MODEL_COMPAT = os.path.join(HERE, "models", "zenz-compat.gguf")

ALT_TOPK = 8             # 各トークンで見る上位候補の数
ALT_BRANCHES = 5         # 別案のために枝分かれさせる位置の数 (確率差が小さい順)
ALT_MAX_GAP = 6.0        # 最短の出力との確率差(対数)がこれ以内の分岐だけ試す
_BAD_TOKENS = {"", "</s>", "<s>", "[UNK]", "[PAD]"}

MAX_CHUNK = 100          # 1回に渡す読みの最大文字数
CONTEXT_CHARS = 40       # モデルが読む左文脈の長さ
SENTENCE_END = "。！？!?\n"
_SPECIALS = "\uEE00\uEE01\uEE02"


# ---------------------------------------------------------------------------
# 前処理 (llama不要。単体テスト可能)
# ---------------------------------------------------------------------------
def split_chunks(reading: str) -> List[str]:
    """句読点の後ろ、または最大100文字で分割する"""
    chunks: List[str] = []
    cur = ""
    for ch in reading:
        cur += ch
        if ch in "、。！？!?" or len(cur) >= MAX_CHUNK:
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)
    return chunks


def sentence_fragment(text: str) -> str:
    """現在の文の左側だけを返す。完了した前文は切り捨てる
    (前の話題が「硬貨」でも、次の文の「効果」を奪わないため)"""
    cut = max(text.rfind(c) for c in SENTENCE_END)
    return text[cut + 1:]


def build_prompt(context: str, reading: str) -> str:
    import jaconv
    prompt = "\uEE00" + jaconv.hira2kata(reading) + "\uEE01"
    context = context[-CONTEXT_CHARS:]
    return ("\uEE02" + context + prompt) if context else prompt


def _may_be_incomplete(chunks: List[str], idx: int) -> bool:
    """このチャンクの読みが打ちかけ(最後の語が途中)の可能性があるか。
    入力の途中は最後のチャンクだけが未完成で、句読点で終わるチャンクは完成している。"""
    return idx == len(chunks) - 1 and not chunks[idx].endswith(tuple("、。！？!?"))


def roman_to_hira_lazy(roman: str):
    from roman import roman_to_hira
    return roman_to_hira(roman)


def has_japanese(text: str) -> bool:
    return any("\u3041" <= c <= "\u309f" or "\u30a0" <= c <= "\u30ff" for c in text)


# ---------------------------------------------------------------------------
# モデルの準備
# ---------------------------------------------------------------------------
def ensure_model() -> Tuple[Optional[str], str]:
    """読み込めるモデルのパスと、使えないときの理由を返す"""
    if os.path.exists(MODEL_COMPAT):
        return MODEL_COMPAT, ""
    if not os.path.exists(MODEL_ORIGINAL):
        return None, "モデル未取得 (python download_model.py)"
    try:
        import patch_model
        if patch_model.main() == 0 and os.path.exists(MODEL_COMPAT):
            return MODEL_COMPAT, ""
    except Exception as e:  # noqa: BLE001
        return None, f"モデル変換に失敗: {e}"
    return None, "モデル変換に失敗 (python patch_model.py)"


# ---------------------------------------------------------------------------
# ワーカー
# ---------------------------------------------------------------------------
@dataclass
class Result:
    req_id: int
    reading: str
    text: str
    milliseconds: float
    rejected: int = 0   # 読みと整合せず、標準変換に差し替えたチャンク数
    typo_reading: str = ""  # タイプミスを直したとき、直した読み (text はこの読みの変換)
    typo_roman: str = ""    # タイプミスを直したローマ字


@dataclass
class Alternatives:
    req_id: int
    reading: str
    texts: List[str]        # [LLMの最良, 別案...] (確率の高い順)
    milliseconds: float


@dataclass
class Status:
    state: str      # 'loading' | 'ready' | 'error'
    message: str = ""


class ZenzWorker:
    """UIスレッドをブロックしない推論ワーカー。結果は self.results から取り出す。"""

    def __init__(self, fallback: Optional[Callable[[str], str]] = None,
                 check_reading: bool = True) -> None:
        """fallback: 読みと整合しなかったチャンクの代わりに使う標準変換 (読み -> 表記)。
        check_reading=False なら整合チェックをしない (評価用)。"""
        self.results: "queue.Queue[object]" = queue.Queue()
        self._cond = threading.Condition()
        self._latest: Optional[Tuple[int, str, str]] = None
        self._alt: Optional[Tuple[int, str, str, int]] = None   # 別案の要求 (変換より後回し)
        self._fixes: List[Tuple[str, str]] = []   # (ミスのあるローマ字, 直したローマ字)
        self._stop = False
        self._llm = None
        self._fallback = fallback
        self._check = check_reading
        self._matches: Optional[Callable[..., bool]] = None
        # (文脈, 読み) -> (採用した表記, 整合チェックを通ったか)
        self._cache: Dict[Tuple[str, str, bool], Tuple[str, bool]] = {}
        self._thread = threading.Thread(target=self._run, name="zenz", daemon=True)

    # ---- 公開API ----
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()

    def submit(self, req_id: int, reading: str, context: str,
               roman: str = "", fix_typos: bool = False) -> None:
        """最新の1件だけを保持する(古い未処理の要求は上書きされる)。
        fix_typos=True なら、変換のあと手が止まっている間にタイプミスも探す (roman が必要)。"""
        with self._cond:
            self._latest = (req_id, reading, context, roman, fix_typos)
            self._cond.notify()

    def forget_typos(self) -> None:
        """覚えているタイプミスの直し方を捨てる (ユーザーがEscで補正を取り消したとき)"""
        with self._cond:
            self._fixes = []

    # 一度直した箇所は覚えておき、続けて打った入力にも同じ直し方を当てる
    # (毎回探し直すと、探し終わるまで表示がミスのある変換に戻ってしまう)
    def _known_fix(self, roman: str):
        import typo
        for orig, fixed in reversed(self._fixes):
            if roman.startswith(orig) and orig:
                new_roman = fixed + roman[len(orig):]
                r, _p = roman_to_hira_lazy(new_roman)
                return typo.Correction(new_roman, r, 0.0)
        return None

    def _remember_fix(self, roman: str, fixed: str) -> None:
        self._fixes = [(o, f) for o, f in self._fixes if not roman.startswith(o)][-7:]
        self._fixes.append((roman, fixed))

    def submit_alternatives(self, req_id: int, reading: str, context: str, n: int = 3) -> None:
        """LLMの別案を要求する。変換の要求があればそちらを先に処理する。"""
        with self._cond:
            self._alt = (req_id, reading, context, n)
            self._cond.notify()

    def cancel_pending(self) -> None:
        with self._cond:
            self._latest = None
            self._alt = None

    # ---- 内部 ----
    def _load(self) -> bool:
        path, reason = ensure_model()
        if path is None:
            self.results.put(Status("error", reason))
            return False
        try:
            from llama_cpp import Llama
        except Exception as e:  # noqa: BLE001
            self.results.put(Status("error", f"llama-cpp-python が使えません: {e}"))
            return False
        try:
            self._llm = Llama(model_path=path, n_ctx=512, n_batch=512,
                              n_threads=max(2, (os.cpu_count() or 4) // 2),
                              logits_all=True,  # 別案のために各トークンの上位候補が要る
                              verbose=False)
            if self._check:
                import reading_check
                reading_check.matches("あ", "あ")  # 辞書とMeCabを先に読み込む
                self._matches = reading_check.matches
            self._generate("", "あ")  # ウォームアップ
        except Exception as e:  # noqa: BLE001
            self.results.put(Status("error", f"モデルを読み込めません: {e}"))
            return False
        return True

    def generate_raw(self, context: str, chunk: str) -> str:
        """整合チェック前のLLM出力そのもの (評価用)"""
        prompt = build_prompt(context, chunk)
        tokens = self._llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)
        budget = max(8, min(len(chunk) * 2 + 8, 512 - len(tokens) - 1))
        out = self._llm.create_completion(tokens, max_tokens=budget,
                                          temperature=0.0, stop=[])
        text = out["choices"][0]["text"]
        for s in _SPECIALS:
            text = text.replace(s, "")
        return text.strip() or chunk

    def _generate(self, context: str, chunk: str, partial: bool = False) -> Tuple[str, bool]:
        """(採用する表記, 整合チェックを通ったか)。通らなければ標準変換に差し替える。
        partial=True は、読みが打ちかけで最後の語が途中のとき (最後のチャンクだけ)。"""
        key = (context[-CONTEXT_CHARS:], chunk, partial)
        if key in self._cache:
            return self._cache[key]
        text = self.generate_raw(context, chunk)
        accepted = True
        if self._matches is not None and not self._matches(text, chunk, partial=partial):
            accepted = False
            text = self._fallback(chunk) if self._fallback else chunk
        if len(self._cache) >= 128:
            self._cache.clear()
        self._cache[key] = (text, accepted)
        return text, accepted

    # ---- タイプミス補正のための、読みの採点 ----
    def reading_logprobs(self, reading: str) -> Tuple[float, List[float]]:
        """zenz が読み(カタカナ)そのものに付ける対数確率。(合計, かな1文字ごと)。
        読みの終わりの記号 (\\uEE01) は採点に入れない (打ちかけの読みを不利にしないため)。
        直前の評価と共通の先頭部分は計算し直さない。"""
        import jaconv
        import numpy as np
        kata = jaconv.hira2kata(reading)
        toks = self._llm.tokenize(("\uEE00" + kata).encode("utf-8"), add_bos=False, special=True)
        ids = list(self._llm.input_ids[: self._llm.n_tokens])
        common = 0
        while common < min(len(ids), len(toks)) and ids[common] == toks[common]:
            common += 1
        common = min(common, len(toks) - 1)       # 最後のトークンは必ず評価し直す
        self._llm.n_tokens = common
        self._llm.eval(toks[common:])
        logits = np.asarray(self._llm.scores[: len(toks)], dtype=np.float64)
        m = logits.max(axis=1, keepdims=True)
        logz = (m + np.log(np.exp(logits - m).sum(axis=1, keepdims=True))).ravel()
        per_token = [float(logits[i - 1, toks[i]] - logz[i - 1]) for i in range(1, len(toks))]
        # トークンをかなの位置に割り振る (複数文字のトークンは均等に分ける)
        per_char: List[float] = []
        for i, lp in zip(range(1, len(toks)), per_token):
            piece = self._llm.detokenize([toks[i]]).decode("utf-8", errors="ignore")
            width = max(1, len(piece))
            per_char.extend([lp / width] * width)
        per_char = (per_char + [0.0] * len(reading))[: len(reading)]
        return float(sum(per_token)), per_char

    def _kana_token(self, kata_char: str) -> Optional[int]:
        cache = self.__dict__.setdefault("_tok_cache", {})
        if kata_char not in cache:
            ids = self._llm.tokenize(kata_char.encode("utf-8"), add_bos=False, special=False)
            cache[kata_char] = ids[0] if len(ids) == 1 else None
        return cache[kata_char]

    def analyze_reading(self, reading: str):
        """読みを1回だけLLMに通し、かなごとの対数確率と、各位置の「次の文字」の確率表を返す。
        候補の採点 (score_variants) はこれを土台に、変わった部分だけを計算し直す。"""
        import jaconv
        import numpy as np
        kata = jaconv.hira2kata(reading)
        toks = self._llm.tokenize(("\uEE00" + kata).encode("utf-8"), add_bos=False, special=True)
        ids = list(self._llm.input_ids[: self._llm.n_tokens])
        common = 0
        while common < min(len(ids), len(toks)) and ids[common] == toks[common]:
            common += 1
        self._llm.n_tokens = min(common, len(toks) - 1)
        self._llm.eval(toks[self._llm.n_tokens:])
        logits = np.asarray(self._llm.scores[: len(toks)], dtype=np.float64)
        m = logits.max(axis=1, keepdims=True)
        rows = logits - (m + np.log(np.exp(logits - m).sum(axis=1, keepdims=True)))
        per_char = [float(rows[i - 1, toks[i]]) for i in range(1, len(toks))]
        char_level = len(toks) - 1 == len(kata)   # 1トークン = かな1文字か
        return {"reading": reading, "kata": kata, "toks": toks, "rows": rows,
                "per_char": per_char if char_level else self.reading_logprobs(reading)[1],
                "char_level": char_level}

    def score_variants(self, a, readings: List[str], keep: int = 12, tail: int = 4,
                       cancelled: Optional[Callable[[], bool]] = None) -> Dict[str, float]:
        """候補の読みごとに、元の読みからの対数確率の差を返す (大きいほど自然)。
        1) 変わった最初の文字の確率は、元の読みの確率表から計算なしで分かる。これで keep 件に絞る
        2) 残りは「変わった部分 + その後ろ tail 文字」だけを計算し直す (前半は使い回す)"""
        import jaconv
        import numpy as np
        orig = a["reading"]
        if not a["char_level"]:   # 1トークン=1文字でない読みは、全体を採点し直す (まれ)
            base = sum(a["per_char"])
            return {r: self.reading_logprobs(r)[0] - base for r in readings[:keep]}
        items = []
        for r in readings:
            p = 0
            while p < min(len(r), len(orig)) and r[p] == orig[p]:
                p += 1
            s = 0
            while s < min(len(r), len(orig)) - p and r[-1 - s] == orig[-1 - s]:
                s += 1
            suffix = orig[len(orig) - s:] if s else ""
            r_win = r[p:len(r) - s] + suffix[:tail]
            o_win = orig[p:len(orig) - s] + suffix[:tail]
            toks = [self._kana_token(c) for c in jaconv.hira2kata(r_win)]
            if any(t is None for t in toks):
                continue
            first = float(a["rows"][p, toks[0]]) if toks else 0.0
            items.append((first, r, p, toks, o_win))
        items.sort(key=lambda x: -x[0])
        items = items[:keep]
        out: Dict[str, float] = {}
        # 前半を共有したまま計算できるよう、変わる位置が後ろのものから順に計算する
        for first, r, p, toks, o_win in sorted(items, key=lambda x: -x[2]):
            if cancelled and cancelled():
                break
            lp = first
            if len(toks) > 1:
                self._llm.n_tokens = 1 + p
                self._llm.eval(toks[:-1])
                logits = np.asarray(self._llm.scores[1 + p: 1 + p + len(toks) - 1], dtype=np.float64)
                mm = logits.max(axis=1, keepdims=True)
                logz = (mm + np.log(np.exp(logits - mm).sum(axis=1, keepdims=True))).ravel()
                lp += float(sum(logits[k, toks[k + 1]] - logz[k] for k in range(len(toks) - 1)))
            out[r] = lp - sum(a["per_char"][p:p + len(o_win)])
        return out

    def correct_typo(self, roman: str, cancelled: Optional[Callable[[], bool]] = None):
        """タイプミスを直せそうなら typo.Correction、なければ None"""
        import typo
        return typo.correct(roman, self, cancelled=cancelled)

    # ---- 別案 (n-best) ----
    def _decode_lp(self, prompt: str, chunk_len: int):
        """最短(貪欲)の出力と、各トークンの確率・上位候補を返す"""
        tokens = self._llm.tokenize(prompt.encode("utf-8"), add_bos=False, special=True)
        budget = max(8, min(chunk_len * 2 + 8, 512 - len(tokens) - 1))
        out = self._llm.create_completion(tokens, max_tokens=budget, temperature=0.0,
                                          stop=[], logprobs=ALT_TOPK)
        choice = out["choices"][0]
        lp = choice["logprobs"]
        return choice["text"], lp["tokens"], lp["token_logprobs"], lp["top_logprobs"]

    def _chunk_alternatives(self, context: str, chunk: str) -> List[Tuple[float, str]]:
        """1チャンクの別案を (最短の出力との対数確率の差, 表記) で返す。差は0以下。
        最短の出力で確率差が小さい位置だけ別のトークンに替え、続きを作り直す。"""
        base = build_prompt(context, chunk)
        text, toks, lps, tops = self._decode_lp(base, len(chunk))
        if "".join(toks) != text or len(toks) != len(lps):
            return []  # 複数バイトのトークンがあって文字に戻せない
        deviations = []
        for t, (tok, lp, top) in enumerate(zip(toks, lps, tops)):
            for alt, alt_lp in (top or {}).items():
                if alt == tok or alt in _BAD_TOKENS or "\ufffd" in alt:
                    continue
                deviations.append((lp - alt_lp, t, alt, alt_lp))
        deviations.sort()
        main_score = sum(lps)
        found: Dict[str, float] = {}
        for gap, t, alt, alt_lp in deviations[:ALT_BRANCHES]:
            if gap > ALT_MAX_GAP:
                break
            prefix = "".join(toks[:t]) + alt
            rest, _rt, rest_lps, _top = self._decode_lp(base + prefix, len(chunk))
            full = prefix + rest
            score = sum(lps[:t]) + alt_lp + sum(rest_lps)
            if full != text and score > found.get(full, float("-inf")):
                found[full] = score
        return [(s - main_score, f) for f, s in found.items()]

    def alternatives(self, reading: str, context: str, n: int = 3,
                     include_best: bool = False) -> List[str]:
        """読み全体のLLM別案を、確率の高い順に最大 n 件返す。
        読みと整合しない案は捨てる。チャンクが複数あるときは、1チャンクだけ替えた案にする。
        include_best=True なら、先頭にLLMの最良の結果 (整合チェック後) も入れる。"""
        parts: List[Tuple[str, str, bool]] = []   # (読み, 採用した表記, 別案を作れるか)
        so_far = ""
        chunks = split_chunks(reading)
        for idx, chunk in enumerate(chunks):
            if not has_japanese(chunk):
                parts.append((chunk, chunk, False))
                so_far += chunk
                continue
            text, ok = self._generate(sentence_fragment(context + so_far), chunk,
                                      partial=_may_be_incomplete(chunks, idx))
            parts.append((chunk, text, ok))
            so_far += text
        main = "".join(t for _c, t, _o in parts)
        scored: List[Tuple[float, str]] = []
        so_far = ""
        for idx, (chunk, text, ok) in enumerate(parts):
            if ok and has_japanese(chunk):
                partial = _may_be_incomplete(chunks, idx)
                for delta, alt in self._chunk_alternatives(
                        sentence_fragment(context + so_far), chunk):
                    if self._matches is not None and not self._matches(alt, chunk, partial=partial):
                        continue
                    full = "".join(alt if i == idx else t for i, (_c, t, _o) in enumerate(parts))
                    scored.append((delta, full))
            so_far += text
        out: List[str] = []
        for _delta, full in sorted(scored, key=lambda x: -x[0]):
            if full != main and full not in out:
                out.append(full)
            if len(out) >= n:
                break
        return ([main] + out) if include_best else out

    def _convert(self, reading: str, context: str) -> Tuple[str, int]:
        """(変換結果, 整合チェックで標準変換へ差し替えたチャンク数)"""
        text = ""
        rejected = 0
        chunks = split_chunks(reading)
        for idx, chunk in enumerate(chunks):
            if not has_japanese(chunk):
                text += chunk
                continue
            part, ok = self._generate(sentence_fragment(context + text), chunk,
                                      partial=_may_be_incomplete(chunks, idx))
            text += part
            rejected += 0 if ok else 1
        return text, rejected

    def _run(self) -> None:
        self.results.put(Status("loading", "LLM 準備中（入力できます）"))
        if not self._load():
            return
        self.results.put(Status("ready", "LLM 準備完了"))
        while True:
            with self._cond:
                while self._latest is None and self._alt is None and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                if self._latest is None:  # 変換の要求がなければ別案を作る
                    alt_req, self._alt = self._alt, None
                    request = None
                else:
                    request, self._latest = self._latest, None
            if request is None:
                req_id, reading, context, n = alt_req
                start = time.perf_counter()
                try:
                    texts = self.alternatives(reading, context, n, include_best=True)
                except Exception as e:  # noqa: BLE001
                    self.results.put(Status("error", f"別案の生成に失敗: {e}"))
                    continue
                self.results.put(Alternatives(req_id, reading, texts,
                                              (time.perf_counter() - start) * 1000))
                continue
            req_id, reading, context, roman, fix_typos = request
            start = time.perf_counter()
            try:
                # 1) 変換を先に返す。前に直したミスが入力に残っていれば、同じ直し方を当てる
                fix = self._known_fix(roman) if fix_typos and roman else None
                text, rejected = self._convert(fix.reading if fix else reading, context)
            except Exception as e:  # noqa: BLE001
                self.results.put(Status("error", f"推論に失敗: {e}"))
                continue
            self.results.put(Result(req_id, reading, text,
                                    (time.perf_counter() - start) * 1000, rejected,
                                    fix.reading if fix else "", fix.roman if fix else ""))
            if not (fix_typos and roman):
                continue

            # 2) 手が止まっている間にタイプミスを探す。新しい入力が来たら途中でやめる
            def cancelled() -> bool:
                return self._latest is not None or self._stop

            if cancelled():
                continue
            try:
                start = time.perf_counter()
                c = self.correct_typo(fix.roman if fix else roman, cancelled=cancelled)
                if c is None or cancelled():
                    continue
                text, rejected = self._convert(c.reading, context)
            except Exception as e:  # noqa: BLE001
                self.results.put(Status("error", f"タイプミスの補正に失敗: {e}"))
                continue
            if cancelled():
                continue
            self._remember_fix(roman, c.roman)
            self.results.put(Result(req_id, reading, text,
                                    (time.perf_counter() - start) * 1000, rejected,
                                    c.reading, c.roman))
