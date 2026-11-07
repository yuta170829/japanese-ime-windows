"""
main.py -- kana-ime 起動エントリ

ローマ字で日本語を書く変換プレイグラウンド (Windows / Python / tkinter)。
Hitsuji-IME Playground (macOS) と同じ考え方で、
  1. 標準変換(辞書+動的計画法)を即時に表示し、
  2. ローカルのzenz-v3.2-smallが少し遅れて未確定文を補正する(LLMが直した箇所はオレンジ)。

キー操作:
  ローマ字入力   : 文字キー
  Space          : 文節候補選択開始 / 次の候補
  Shift+Space    : 前の候補
  ↑ / ↓        : 候補を上下に選択
  ← / →        : 文節を移動
  Enter          : 全文確定 / 改行
  Backspace      : 末尾削除
  Escape         : 読みに戻す(LLM補正も解除)
  Ctrl+Delete    : 文章を消去
"""
from __future__ import annotations

import os
import queue
import ctypes
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import llm
from engine import ConversionState, standard_text

APP_NAME = "kana-ime"

# ---------------------------------------------------------------------------
# 自動保存パス
# ---------------------------------------------------------------------------
_SAVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autosave.txt")


def load_saved() -> str:
    try:
        with open(_SAVE_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def save_text(text: str) -> None:
    with open(_SAVE_PATH, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------------------
# テーマ定数
# ---------------------------------------------------------------------------
BG          = "#1e1e2e"   # ウィンドウ背景
EDITOR_BG   = "#181825"   # エディタ背景
TEXT_FG     = "#cdd6f4"   # 通常テキスト
ACCENT      = "#89b4fa"   # アクセント（未確定の変換 / 下線）
UNCONVERTED = "#6c7086"   # 未完成ローマ字
STATUS_FG   = "#a6adc8"   # ステータスバー
CAND_BG     = "#313244"   # 候補ウィンドウ背景
CAND_SEL_BG = "#45475a"   # 候補選択背景
CAND_FG     = "#cdd6f4"
ORANGE      = "#fab387"   # LLMが補正した箇所
CURSOR      = "#89b4fa"

def _roman_diff(before: str, after: str, margin: int = 4) -> str:
    """ローマ字のどこを直したかを短く見せる (例: …tomxra → …tomara)"""
    p = 0
    while p < min(len(before), len(after)) and before[p] == after[p]:
        p += 1
    s = 0
    while s < min(len(before), len(after)) - p and before[-1 - s] == after[-1 - s]:
        s += 1
    lo = max(0, p - margin)
    b_end, a_end = len(before) - s, len(after) - s
    head = "…" if lo > 0 else ""
    tail_b = before[b_end:b_end + margin]
    tail_a = after[a_end:a_end + margin]
    return f"{head}{before[lo:b_end]}{tail_b} → {head}{after[lo:a_end]}{tail_a}"


# display_parts() の種別 -> Text タグ
_TAG_OF_KIND = {"std": "marked", "neural": "neural", "pending": "pending"}


# ---------------------------------------------------------------------------
# メインアプリケーション
# ---------------------------------------------------------------------------
class KanaImeApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.configure(bg=BG)
        self.root.geometry("900x650")
        self.root.minsize(700, 500)

        mono_font = tkfont.Font(family="Yu Gothic UI", size=11)
        small_font = tkfont.Font(family="Yu Gothic UI", size=10)
        editor_font = tkfont.Font(family="Yu Gothic UI", size=20)

        self._save_after_id: str | None = None

        # ---- LLM補正の状態 ----
        self.neural_var = tk.BooleanVar(value=True)
        self.typo_var = tk.BooleanVar(value=True)   # タイプミスの自動補正 (LLMが必要)
        self.typo_refused = ""   # Escで補正を断った入力。これで始まる間は直さない
        self.typo_fixes = 0      # 直した回数
        self.llm_state = "loading"      # loading | ready | error
        self.neural_status = "LLM 準備中（入力できます）"
        self.serial = 0                 # 入力世代。古い結果は捨てる
        self.completed = 0              # 推論完了回数
        self.corrections = 0            # 表示に反映した補正回数
        self.rejected = 0               # 読みの整合チェックで標準変換に戻した回数
        self.last_inference = ""

        self.state = ConversionState()
        self.state.committed = load_saved()

        self._build_ui(mono_font, small_font, editor_font)
        self._bind_keys()
        self._refresh_display()

        # 読みと整合しないLLM出力は、標準変換の結果に差し替える
        self.worker = llm.ZenzWorker(fallback=standard_text)
        self.worker.start()
        self.root.after(40, self._poll_worker)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------
    def _build_ui(self, mono_font, small_font, editor_font) -> None:
        root = self.root

        # ---- ヘッダ ----
        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=20, pady=(16, 0))

        tk.Label(header, text=APP_NAME, font=("Yu Gothic UI", 18, "bold"),
                 fg=TEXT_FG, bg=BG).pack(side="left")

        tk.Checkbutton(header, text="タイプミスを自動補正", variable=self.typo_var,
                       command=self._on_toggle_neural, font=small_font,
                       fg=TEXT_FG, bg=BG, selectcolor=CAND_BG,
                       activebackground=BG, activeforeground=TEXT_FG,
                       takefocus=0).pack(side="right", padx=(12, 0))
        tk.Checkbutton(header, text="軽量LLMで補正", variable=self.neural_var,
                       command=self._on_toggle_neural, font=small_font,
                       fg=TEXT_FG, bg=BG, selectcolor=CAND_BG,
                       activebackground=BG, activeforeground=TEXT_FG,
                       takefocus=0).pack(side="right")

        self.status_label = tk.Label(header, text="", font=small_font,
                                     fg=STATUS_FG, bg=BG)
        self.status_label.pack(side="right", padx=12)

        # LLMの状態 (準備中 / 推論中 / 補正 · ○ms)
        llm_row = tk.Frame(root, bg=BG)
        llm_row.pack(fill="x", padx=20, pady=(2, 0))
        self.llm_dot = tk.Canvas(llm_row, width=10, height=10, bg=BG,
                                 highlightthickness=0)
        self.llm_dot.pack(side="left", pady=2)
        self._dot = self.llm_dot.create_oval(2, 2, 8, 8, fill=UNCONVERTED, outline="")
        self.llm_label = tk.Label(llm_row, text="", font=small_font,
                                  fg=STATUS_FG, bg=BG, anchor="w")
        self.llm_label.pack(side="left", padx=6)

        # ---- エディタエリア ----
        # 下の部品を先に確保してからエディタを広げる (後から詰めるとウィンドウの外に押し出される)
        editor_frame = tk.Frame(root, bg=ACCENT, bd=1)

        inner = tk.Frame(editor_frame, bg=EDITOR_BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self.text_widget = tk.Text(
            inner, font=editor_font, bg=EDITOR_BG, fg=TEXT_FG,
            insertbackground=CURSOR, selectbackground=ACCENT, selectforeground=BG,
            wrap="word", relief="flat", padx=20, pady=16,
            height=6,          # 要求する高さを小さくし、実際の高さはウィンドウに合わせて伸ばす
            state="disabled",  # キー入力は直接受け付けず、手動で制御
            cursor="arrow",
        )
        # ネイティブのスクロールバーは暗い配色にできないので、色を指定できる clam テーマを使う
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Kana.Vertical.TScrollbar", background=CAND_SEL_BG, troughcolor=EDITOR_BG,
                        bordercolor=EDITOR_BG, arrowcolor=TEXT_FG, lightcolor=CAND_SEL_BG,
                        darkcolor=CAND_SEL_BG)
        style.map("Kana.Vertical.TScrollbar", background=[("active", UNCONVERTED)])
        scrollbar = ttk.Scrollbar(inner, command=self.text_widget.yview,
                                  style="Kana.Vertical.TScrollbar")
        self.text_widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.text_widget.pack(fill="both", expand=True)

        tw = self.text_widget
        tw.tag_configure("committed", foreground=TEXT_FG)
        tw.tag_configure("marked", foreground=ACCENT, underline=True)
        tw.tag_configure("neural", foreground=ORANGE, underline=True)
        tw.tag_configure("pending", foreground=UNCONVERTED, underline=True)
        tw.tag_configure("cursor_block", foreground=ACCENT)  # 細い縦線 (背景は塗らない)

        # ---- 候補ウィンドウ（Toplevel） ----
        self.cand_win = tk.Toplevel(root)
        self.cand_win.withdraw()
        self.cand_win.overrideredirect(True)
        self.cand_win.transient(root)          # メインウィンドウの後ろに隠れないようにする
        self.cand_win.attributes("-topmost", True)
        self.cand_win.configure(bg=CAND_BG)
        self.cand_listbox = tk.Listbox(
            self.cand_win, font=mono_font, bg=CAND_BG, fg=CAND_FG,
            selectbackground=CAND_SEL_BG, selectforeground=CAND_FG,
            relief="flat", bd=1, activestyle="none", width=28, height=8,
        )
        self.cand_listbox.pack(fill="both", expand=True, padx=4, pady=4)
        self.cand_listbox.configure(takefocus=0, exportselection=False, cursor="hand2")
        # クリックで選ぶ / ダブルクリックで確定。どちらもキー入力の先はメイン画面に戻す
        self.cand_listbox.bind("<ButtonRelease-1>", self._on_candidate_click)
        self.cand_listbox.bind("<Double-Button-1>", self._on_candidate_double_click)
        self.cand_win.bind("<FocusIn>", lambda e: self.root.after(1, self.root.focus_force))

        # ---- 読みバー ----
        reading_frame = tk.Frame(root, bg=BG)
        self.reading_label = tk.Label(reading_frame, text="", font=small_font,
                                      fg=STATUS_FG, bg=BG, anchor="w")
        self.reading_label.pack(side="left")

        # ---- フッタ ----
        footer = tk.Frame(root, bg=BG)

        hint = ("Space: 候補  ↑↓: 選択  ←→: 文節  Enter: 確定  "
                "Esc: 読みに戻す  Backspace: 削除  Ctrl+Del: 消去")
        tk.Label(footer, text=hint, font=small_font,
                 fg=STATUS_FG, bg=BG, anchor="w").pack(side="left")

        legend = tk.Frame(footer, bg=BG)
        legend.pack(side="right")
        tk.Frame(legend, bg=ORANGE, width=10, height=2).pack(side="left", pady=(8, 0))
        tk.Label(legend, text=" LLMが補正した箇所", font=small_font,
                 fg=STATUS_FG, bg=BG).pack(side="left")

        diag = tk.Frame(root, bg=BG)
        self.diag_label = tk.Label(diag, text="", font=small_font, fg=STATUS_FG,
                                   bg=BG, anchor="w", justify="left")
        self.diag_label.pack(side="left")
        btn_frame = tk.Frame(diag, bg=BG)
        btn_frame.pack(side="right")
        for label, cmd in (("コピー", self._copy_text), ("消去", self._clear_all)):
            tk.Button(btn_frame, text=label, font=small_font, bg=CAND_BG, fg=TEXT_FG,
                      activebackground=CAND_SEL_BG, relief="flat", padx=8,
                      takefocus=0, command=cmd).pack(side="left", padx=4)

        # 配置: 下の部品から順に確保し、残りをエディタに割り当てる
        diag.pack(side="bottom", fill="x", padx=20, pady=(0, 12))
        footer.pack(side="bottom", fill="x", padx=20, pady=(4, 2))
        reading_frame.pack(side="bottom", fill="x", padx=20)
        editor_frame.pack(fill="both", expand=True, padx=20, pady=10)

        root.focus_set()

    # ------------------------------------------------------------------
    # キーバインド
    # ------------------------------------------------------------------
    def _bind_keys(self) -> None:
        root = self.root
        root.bind("<Key>", self._on_key)
        root.bind("<BackSpace>", self._on_backspace)
        root.bind("<Return>", self._on_return)
        root.bind("<KP_Enter>", self._on_return)
        root.bind("<Escape>", self._on_escape)
        root.bind("<space>", self._on_space)
        root.bind("<Shift-space>", self._on_shift_space)
        root.bind("<Up>", self._on_up)
        root.bind("<Down>", self._on_down)
        root.bind("<Left>", self._on_left)
        root.bind("<Right>", self._on_right)
        root.bind("<Control-Delete>", self._on_ctrl_delete)

    # ------------------------------------------------------------------
    # キーハンドラ
    # ------------------------------------------------------------------
    def _after_edit(self, schedule_llm: bool = True) -> None:
        """入力が変わった後の共通処理: 再描画 → LLM要求 → 保存予約"""
        if schedule_llm:
            self._schedule_neural()
        self._refresh_display()
        self._schedule_save()

    def _on_key(self, event: tk.Event) -> str:
        char = event.char
        if not char or ord(char) < 32 or event.state & 0x4:  # 制御文字・Ctrl
            return "break"
        if len(char) == 1 and char.isascii():
            self.state.insert(char)
            self._after_edit()
        return "break"

    def _on_backspace(self, event: tk.Event) -> str:
        self.state.backspace()
        self._after_edit()
        return "break"

    def _on_return(self, event: tk.Event) -> str:
        self.state.commit()
        self._after_edit()
        return "break"

    def _on_escape(self, event: tk.Event) -> str:
        if self.state.typo_fixed:
            # タイプミスの補正を断った: この入力の間は、もう直さない
            self.typo_refused = self.state.roman_input
            self.worker.forget_typos()
        self.state.use_reading()
        self.serial += 1  # 実行中の推論結果が戻ってきても採用しない
        self.worker.cancel_pending()
        self.neural_status = "標準変換（Escで補正を解除）"
        self._refresh_display()
        return "break"

    def _on_space(self, event: tk.Event) -> str:
        if not self.state.roman_input:
            self.state.committed += " "
            self._after_edit(schedule_llm=False)
            return "break"
        was_selecting = self.state.selecting
        self.state.start_selection()
        if not was_selecting:
            # 候補を開いた瞬間だけ: 実行中のLLM補正の結果は採用しない
            self.serial += 1
            self.worker.cancel_pending()
            self.neural_status = "候補選択中（LLM補正は一時停止）"
            # 全文の候補が開いたら、LLMの別案を後から候補に足す
            if (self.llm_state == "ready" and self.neural_var.get()
                    and self.state.whole_sentence_selection()):
                self.worker.submit_alternatives(self.serial, self.state.selection_reading,
                                                self.state.committed)
                self.neural_status = "候補選択中 · LLMの別案を生成中…"
        self._refresh_display()
        return "break"

    def _on_shift_space(self, event: tk.Event) -> str:
        if self.state.selecting:
            self.state.cycle(-1)
            self._refresh_display()
        return "break"

    def _on_up(self, event: tk.Event) -> str:
        return self._on_shift_space(event)

    def _on_down(self, event: tk.Event) -> str:
        if self.state.selecting:
            self.state.cycle(1)
            self._refresh_display()
        return "break"

    def _on_left(self, event: tk.Event) -> str:
        if self.state.selecting:
            self.state.move_clause(-1)
            self._refresh_display()
        return "break"

    def _on_right(self, event: tk.Event) -> str:
        if self.state.selecting:
            self.state.move_clause(1)
            self._refresh_display()
        return "break"

    def _on_candidate_click(self, event: tk.Event) -> str:
        """候補をクリック: その候補を選ぶ (確定はEnterかダブルクリック)"""
        self.state.select_candidate(self.cand_listbox.nearest(event.y))
        self._refresh_display()
        self.root.focus_force()
        return "break"

    def _on_candidate_double_click(self, event: tk.Event) -> str:
        """候補をダブルクリック: その候補で確定する"""
        if self.state.select_candidate(self.cand_listbox.nearest(event.y)):
            self.state.commit()
            self._after_edit()
        self.root.focus_force()
        return "break"

    def _on_ctrl_delete(self, event: tk.Event) -> str:
        self._clear_all()
        return "break"

    # ------------------------------------------------------------------
    # LLM補正
    # ------------------------------------------------------------------
    def _on_toggle_neural(self) -> None:
        if not self.typo_var.get():
            self.worker.forget_typos()
            if self.state.typo_fixed:
                self.state.clear_neural()
        if not self.neural_var.get():
            self.state.clear_neural()
            self.worker.cancel_pending()
            self.serial += 1
        self._schedule_neural()
        self._refresh_display()

    def _schedule_neural(self) -> None:
        """最新の読みをLLMへ要求する。実行中の古い結果は世代IDで捨てる。"""
        self.serial += 1
        s = self.state
        if not self.neural_var.get():
            s.clear_neural()
            self.neural_status = "標準変換のみ"
            return
        reading = s.hira_complete
        if s.selecting or not reading:
            self.worker.cancel_pending()
            if self.llm_state == "ready":
                self.neural_status = "LLM 待機中"
            return
        if self.llm_state != "ready":
            return  # 準備中・利用不可の間は標準変換を続ける
        if self.typo_refused and not s.roman_input.startswith(self.typo_refused):
            self.typo_refused = ""
        fix = self.typo_var.get() and not self.typo_refused
        self.worker.submit(self.serial, reading, s.committed, roman=s.roman_input, fix_typos=fix)
        self.neural_status = "LLM 推論中"

    def _poll_worker(self) -> None:
        changed = False
        try:
            while True:
                changed |= self._handle(self.worker.results.get_nowait())
        except queue.Empty:
            pass
        if changed:
            self._refresh_display()
        self.root.after(40, self._poll_worker)

    def _handle(self, item: object) -> bool:
        """ワーカーからの通知を処理する。画面の再描画が必要なら True。"""
        if isinstance(item, llm.Status):
            self.llm_state = item.state
            if item.state == "error":
                self.neural_status = f"LLMを利用できません（標準変換を継続）: {item.message}"
            else:
                self.neural_status = item.message
            if item.state == "ready":
                self._schedule_neural()  # 準備中に入力された分を補正する
            return True
        if isinstance(item, llm.Alternatives):
            if item.req_id != self.serial or not self.state.selecting:
                return False  # 候補を閉じた・入力が進んだあとの古い別案は捨てる
            added = self.state.add_alternatives(item.texts, item.reading)
            self.neural_status = (f"候補選択中 · LLMの別案 {added}件を追加 ({item.milliseconds:.0f} ms)"
                                  if added else "候補選択中 · LLMの別案はありません")
            return True
        if isinstance(item, llm.Result):
            self.completed += 1
            self.rejected += item.rejected
            self.last_inference = (f"{item.reading} → {item.text} · "
                                   f"{item.milliseconds:.0f} ms")
            if item.rejected:
                self.last_inference += f" · 読み不一致 {item.rejected}件を標準変換に差し替え"
            s = self.state
            if (item.req_id != self.serial or not self.neural_var.get()
                    or s.selecting):
                return True  # 古い結果は診断表示にだけ記録し、本文には反映しない
            was_fixed = s.typo_fixed
            if s.apply_neural(item.text, item.reading, item.typo_reading, item.typo_roman):
                self.corrections += 1
                self.neural_status = f"LLM 補正 · {item.milliseconds:.0f} ms"
            else:
                self.neural_status = f"LLM 一致 · {item.milliseconds:.0f} ms"
            if s.typo_fixed:
                if not was_fixed:
                    self.typo_fixes += 1
                self.neural_status = (f"タイプミスを補正: {_roman_diff(s.roman_input, s.typo_roman)}"
                                      f" (Escで取り消し) · {item.milliseconds:.0f} ms")
            if item.rejected:
                self.neural_status += " · 読み不一致の部分は標準変換"
            return True
        return False

    # ------------------------------------------------------------------
    # 表示更新
    # ------------------------------------------------------------------
    def _refresh_display(self) -> None:
        s = self.state
        tw = self.text_widget

        tw.configure(state="normal")
        tw.delete("1.0", "end")
        if s.committed:
            tw.insert("end", s.committed, "committed")
        if s.roman_input:
            for text, kind in s.display_parts():
                tw.insert("end", text, _TAG_OF_KIND[kind])
        tw.insert("end", "▏", "cursor_block")
        tw.see("end")
        tw.configure(state="disabled")

        if s.roman_input:
            pending = f" ({s.roman_pending})" if s.roman_pending else ""
            if s.typo_fixed:
                text = (f"読み: {s.effective_reading()}{pending}   "
                        f"(入力: {s.hira_complete} → タイプミスを補正)")
            else:
                text = f"読み: {s.hira_complete}{pending}"
            self.reading_label.configure(text=text)
        else:
            self.reading_label.configure(
                text="ローマ字で入力してください  例: kyouhagakkouniikimasu")

        self.status_label.configure(text=f"{len(s.committed) + len(s.marked)} 文字  自動保存")

        # LLM表示
        self.llm_label.configure(text=self.neural_status)
        if not self.neural_var.get() or self.llm_state == "error":
            color = UNCONVERTED
        elif self.llm_state == "loading":
            color = STATUS_FG
        else:
            color = ACCENT
        self.llm_dot.itemconfigure(self._dot, fill=color)
        diag = (f"zenz-v3.2-small · 推論完了 {self.completed}回 / 表示補正 {self.corrections}回"
                f" / タイプミス補正 {self.typo_fixes}回 / 読み不一致で除外 {self.rejected}件")
        if self.last_inference:
            diag += f"\n直近の推論: {self.last_inference}"
        self.diag_label.configure(text=diag)

        self._update_candidates()

    def _update_candidates(self) -> None:
        s = self.state
        cands = s.candidates
        if not cands or not s.selecting:
            self.cand_win.withdraw()
            return

        lb = self.cand_listbox
        lb.delete(0, "end")
        for i, c in enumerate(cands):
            lb.insert("end", f"  {i + 1}. {c}")
        lb.selection_clear(0, "end")
        lb.selection_set(s.selected_index)
        lb.see(s.selected_index)

        h = min(len(cands), 8)
        lb.configure(height=h)
        self.cand_win.deiconify()
        self.cand_win.lift()

        # 未確定文の末尾 (カーソル) のすぐ下に出す。画面の下にはみ出すときは上に出す。
        tw = self.text_widget
        self.cand_win.update_idletasks()
        box = tw.bbox("end-1c")
        cx, cy, ch = (box[0], box[1], box[3]) if box else (24, 24, 30)
        win_w, win_h = self.cand_win.winfo_reqwidth(), self.cand_win.winfo_reqheight()
        x = tw.winfo_rootx() + cx
        y = tw.winfo_rooty() + cy + ch + 6
        if y + win_h > self.root.winfo_screenheight():
            y = tw.winfo_rooty() + cy - win_h - 6
        # アプリのウィンドウの右端を越えないようにする (画面の外にも出さない)
        right_limit = min(self.root.winfo_rootx() + self.root.winfo_width() - 24,
                          self.root.winfo_screenwidth())
        x = max(0, min(x, right_limit - win_w))
        self.cand_win.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------
    # ボタン操作
    # ------------------------------------------------------------------
    def _copy_text(self) -> None:
        text = self.state.committed + self.state.marked
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_label.configure(text="コピーしました")
        self.root.after(1500, self._refresh_display)

    def _clear_all(self) -> None:
        self.state.clear()
        save_text("")
        self._schedule_neural()
        self._refresh_display()

    # ------------------------------------------------------------------
    # 自動保存 / 終了
    # ------------------------------------------------------------------
    def _schedule_save(self) -> None:
        if self._save_after_id:
            self.root.after_cancel(self._save_after_id)
        self._save_after_id = self.root.after(
            1000, lambda: save_text(self.state.committed))

    def _on_close(self) -> None:
        save_text(self.state.committed)
        self.worker.stop()
        self.root.destroy()


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------
def main() -> None:
    try:  # 高DPIの画面で文字がぼやけないようにする (Windows)
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    root = tk.Tk()
    KanaImeApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
