"""
実際のウィンドウを表示して、操作しながらスクリーンショットを撮る。
python dev/screenshots.py   ->  dev/data/screens/*.png

アプリを本物の画面に出し、キー入力のハンドラを呼んで操作する(人の手の代わり)。
保存先は一時ファイルに差し替えるので、実際の自動保存は変わらない。
"""
import ctypes
import os
import sys
import tempfile
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

try:  # 高DPI画面でぼやけないように (撮る座標と描画を物理ピクセルに揃える)
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:  # noqa: BLE001
    pass

from PIL import ImageGrab  # noqa: E402

import main  # noqa: E402

main._SAVE_PATH = os.path.join(tempfile.mkdtemp(), "autosave.txt")
OUT = os.path.join(HERE, "data", "screens")
os.makedirs(OUT, exist_ok=True)


class Ev:
    def __init__(self, char="", state=0):
        self.char, self.state = char, state


root = tk.Tk()
app = main.KanaImeApp(root)
root.geometry("940x680+80+60")
root.attributes("-topmost", True)


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if until and until():
            return True
        time.sleep(0.01)
    return bool(until() if until else True)


def shot(name):
    pump(0.4)
    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    img = ImageGrab.grab(bbox=(x - 8, y - 40, x + w + 8, y + h + 8))
    path = os.path.join(OUT, name)
    img.save(path)
    print("saved", name, img.size)


def typ(s):
    for c in s:
        app._on_key(Ev(c))
        root.update()


pump(1.0)
shot("01_launch.png")                       # 起動直後 (LLM準備中のことがある)
pump(60, lambda: app.llm_state == "ready")
pump(0.5)
shot("02_ready.png")                        # LLM準備完了

app.neural_var.set(False)                   # 対比のため、いったんLLMを切って標準変換だけを撮る
app._on_toggle_neural()
typ("tomodachinitegamiwokakimasu")
shot("03_standard.png")                     # 標準変換だけ
app.neural_var.set(True)
app._on_toggle_neural()
pump(20, lambda: app.state.neural_text != "")
shot("04_llm_corrected.png")                # LLMが補正 (オレンジ)
app._on_return(Ev())                        # 補正後の文を確定
typ("kinouhaeigawomimashita")
pump(20, lambda: app.state.neural_text != "")

app._on_space(Ev())
shot("05_candidates.png")                   # 候補を開いた直後
pump(30, lambda: "別案" in app.llm_label.cget("text") and "生成中" not in app.llm_label.cget("text"))
shot("06_alternatives.png")                 # LLMの別案が候補に追加された
app._on_down(Ev())
shot("07_pick_alternative.png")             # 別案を選択中

app._on_return(Ev())
typ("kyouhaharegahutteimasu")               # 次の入力 (標準変換→LLM補正)
pump(20, lambda: app.state.neural_text != "")
app._on_return(Ev())
shot("08_committed.png")                    # 確定済み文章が並ぶ

app._on_return(Ev())
app.state.committed = ""
typ("darenomenimotomxranakat")             # 途中にタイプミス (tomxra)
shot("09_typo_raw.png")                     # 直す前 (英字が残る)
typ("ta")
pump(30, lambda: app.state.typo_fixed)
shot("10_typo_fixed.png")                   # 自動で直った

print("status:", ascii(app.neural_status))
app._on_close()
