"""アプリ全体の結合テスト (キー入力 -> 標準変換 -> LLM補正 -> 候補選択 -> 確定)。
python dev/test_app.py"""
import os
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import main  # noqa: E402

main._SAVE_PATH = os.path.join(tempfile.mkdtemp(), "autosave.txt")  # 本物の保存先を触らない


class Ev:
    def __init__(self, char="", state=0):
        self.char, self.state = char, state


root = tk.Tk()
root.withdraw()
app = main.KanaImeApp(root)
fails = []


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if until and until():
            return True
        time.sleep(0.01)
    return until() is True if until else True


def check(name, cond, detail=""):
    print(("OK " if cond else "NG "), name, detail)
    if not cond:
        fails.append(name)


def type_roman(s):
    for c in s:
        app._on_key(Ev(c))
        root.update()


def shown():
    return app.text_widget.get("1.0", "end").replace("▏", "").strip()


check("title", root.title() == "kana-ime", ascii(root.title()))
check("model ready", pump(60, lambda: app.llm_state == "ready"), app.llm_state)

# 1) 標準変換は即時に出る (LLM結果を待たない)
type_roman("tomodachinitegamiwokakimasu")
std_text = shown()
check("standard shown immediately", len(std_text) > 0, ascii(std_text))

# 2) 遅れてLLMが補正し、書き換えた箇所に 'neural' タグが付く
check("llm corrected", pump(20, lambda: app.corrections >= 1), f"corrections={app.corrections}")
final = shown()
check("llm text", final == "友達に手紙を書きます", ascii(final))
check("neural tag present", len(app.text_widget.tag_ranges("neural")) > 0)
check("changed from standard", final != std_text)

# 3) 追加入力しても補正済みの部分は保持される
type_roman("a")
check("kept while typing", shown().startswith("友達に手紙を書きます"), ascii(shown()))
app._on_backspace(Ev())
pump(10, lambda: app.state.neural_text != "" and app.llm_label.cget("text").startswith("LLM"))

# 4) Space: 補正結果が第一候補として引き継がれる
app._on_space(Ev())
check("selecting", app.state.selecting)
check("neural is first candidate", app.state.candidates[0] == "友達に手紙を書きます",
      ascii(app.state.candidates[:2]))
n_cands = len(app.state.segments[0].candidates)
check("alternatives arrive", pump(30, lambda: len(app.state.segments[0].candidates) > n_cands),
      f"{n_cands} -> {len(app.state.segments[0].candidates)}")
check("first candidate unchanged", app.state.candidates[0] == "友達に手紙を書きます"
      and app.state.selected_index == 0)
check("status mentions alternatives", "別案" in app.llm_label.cget("text"),
      ascii(app.llm_label.cget("text")))
alts = app.state.candidates[1:-2]   # [LLM補正, *別案, 標準変換, 読み]
check("alternatives differ from first", len(alts) >= 1 and "友達に手紙を書きます" not in alts, ascii(alts[:3]))
app._on_space(Ev())                  # 2回目のSpaceは次の候補へ(別案を失わない)
check("space cycles into alternatives", app.state.selected_index == 1
      and app.state.candidates[1] == alts[0])
app._on_shift_space(Ev())            # 先頭に戻す
check("llm paused while selecting", shown() == "友達に手紙を書きます")

# 5) Enter で確定
app._on_return(Ev())
check("committed", app.state.committed == "友達に手紙を書きます", ascii(app.state.committed))

# 6) LLMオフなら標準変換のみ
app.neural_var.set(False)
app._on_toggle_neural()
before = app.corrections
type_roman("kinouhaeigawomimashita")
pump(2.0)
check("llm off keeps standard", app.corrections == before and not app.state.neural_text)

# 7) オンに戻すと補正される
app.neural_var.set(True)
app._on_toggle_neural()
check("llm on again", pump(20, lambda: app.state.neural_text != ""), app.neural_status)
app._on_return(Ev())

# 8) Esc は補正を解除する
type_roman("kyouhagakkouniikimasu")
pump(20, lambda: app.state.neural_text != "")
app._on_escape(Ev())
check("esc clears neural", app.state.neural_text == "")
pump(1.0)
check("esc not reapplied", app.state.neural_text == "")

# 9) LLMが読みと無関係な文字を出したら、画面には標準変換を出し、除外件数を数える
app._on_return(Ev())
app.worker.generate_raw = lambda ctx, chunk: "全然関係ない文章です"  # 幻覚を模擬
rejected_before = app.rejected
type_roman("kyouhaiitenkidesune")
check("rejected counted", pump(20, lambda: app.rejected > rejected_before), f"rejected={app.rejected}")
from engine import standard_text  # noqa: E402
check("shows standard, not hallucination", shown().endswith(standard_text("きょうはいいてんきですね")),
      ascii(shown()))
check("no neural tag", len(app.text_widget.tag_ranges("neural")) == 0)
check("status mentions mismatch",
      pump(20, lambda: "読み不一致" in app.llm_label.cget("text")),
      ascii(app.llm_label.cget("text")))

app.worker.generate_raw = type(app.worker).generate_raw.__get__(app.worker)  # 模擬を元に戻す
app._on_escape(Ev())
app._on_return(Ev())
app.state.committed = ""
app._refresh_display()

# 10) 打ちかけの子音は英字のまま見える (以前は「ともだっっ」に化けていた)
type_roman("tomodach")
check("pending consonants shown as letters", shown().endswith("ch") and "っ" not in shown(), ascii(shown()))
type_roman("i")
pump(20, lambda: app.state.neural_text != "" or app.llm_label.cget("text").startswith("LLM 一致"))
app._on_return(Ev())
app.state.committed = ""

# 11) 候補をマウスでクリックして選ぶ / ダブルクリックで確定する
type_roman("kinouhaeigawomimashita")
pump(20, lambda: app.state.neural_text != "")
app._on_space(Ev())
pump(30, lambda: "別案" in app.llm_label.cget("text") and "生成中" not in app.llm_label.cget("text"))
app._refresh_display()
root.update()
lb = app.cand_listbox
target = 1
box = lb.bbox(target)
if box is None:   # 画面を隠して動かしているので位置が取れない。クリック位置->行 の計算だけ差し替える
    lb.nearest = lambda y: target
    box = (0, 0, 0, 0)


class Click:
    def __init__(self, y):
        self.y = y


app._on_candidate_click(Click(box[1] + box[3] // 2))
check("click selects candidate", app.state.selected_index == target, f"index={app.state.selected_index}")
check("click shows candidate", shown() == app.state.candidates[target], ascii(shown()))
picked = app.state.candidates[target]
app._on_candidate_double_click(Click(box[1] + box[3] // 2))
check("double click commits", app.state.committed == picked and not app.state.roman_input,
      ascii(app.state.committed))
app.state.committed = ""
app._refresh_display()

# 12) LLMが追いつく前にSpace: 継ぎはぎの表示は候補にせず、届いたLLMの候補を先頭にして選ぶ
type_roman("ashitasonogyoushanitsutaetekuda")
pump(20, lambda: app.state.neural_text != "")
type_roman("sai")          # LLMはまだ「…くだ」までしか知らない
app._on_space(Ev())
first = app.state.candidates[0] if app.state.candidates else ""
check("stale preview not first candidate", "割" not in first, ascii(first))
pump(30, lambda: "別案" in app.llm_label.cget("text") and "生成中" not in app.llm_label.cget("text"))
check("llm best arrives and is selected", app.state.selected_index == 0
      and app.state.candidates[0].endswith("ください"), ascii(app.state.candidates[:2]))
app._on_escape(Ev())
app._on_return(Ev())
app.state.committed = ""

# 13) タイプミスを自動で直す (tomxranakatta -> tomaranakatta)
type_roman("darenomenimotomxranakatta")
check("typo fixed", pump(30, lambda: app.state.typo_fixed), app.neural_status)
check("typo reading", app.state.effective_reading() == "だれのめにもとまらなかった",
      ascii(app.state.effective_reading()))
check("typo status", "タイプミス" in app.llm_label.cget("text"), ascii(app.llm_label.cget("text")))
check("no raw letters shown", not any(c.isascii() and c.isalpha() for c in shown()), ascii(shown()))
check("typo counted", app.typo_fixes >= 1)
# 13b) 続けて打っても直したままになる
type_roman("ne")
pump(20, lambda: app.state.neural_text != "" and app.state.neural_reading == app.state.hira_complete)
check("fix kept while typing", app.state.typo_fixed and "m" not in shown() and "x" not in shown(),
      ascii(shown()))
# 13c) Esc で取り消すと、その入力の間はもう直さない
app._on_escape(Ev())
check("esc undoes typo fix", not app.state.typo_fixed)
type_roman("e")
pump(3.0)
check("refused fix stays off", not app.state.typo_fixed, app.neural_status)
app._on_return(Ev())
app.state.committed = ""

# 14) 正しく打った文は直さない
before = app.typo_fixes
type_roman("kyouhagakkouniikimasu")
pump(4.0)
check("clean input not changed", app.typo_fixes == before and not app.state.typo_fixed)

app._on_close()
print("FAILED:", fails if fails else "none")
sys.exit(1 if fails else 0)
