"""
kana-ime の初回セットアップ (setup.bat から呼ばれる)

  1. 必要な部品を入れる (requirements.txt)
  2. 変換辞書を作る (build_dict.py)
  3. 動詞・形容詞の活用形を作る (build_verbs.py)
  4. 変換モデル(約74MB)を取得して検証する (download_model.py)
  5. モデルを使える形にする (patch_model.py)
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

STEPS = [
    ("必要な部品を入れています", [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]),
    ("変換辞書を作っています", [sys.executable, "build_dict.py"]),
    ("動詞・形容詞の活用形を作っています (数分かかります)", [sys.executable, "build_verbs.py"]),
    ("変換モデルをダウンロードしています (約74MB)", [sys.executable, "download_model.py"]),
    ("モデルを使える形にしています", [sys.executable, "patch_model.py"]),
]


def main() -> int:
    print("=" * 50)
    print(" kana-ime セットアップ")
    print("=" * 50)
    start = time.time()
    for i, (label, cmd) in enumerate(STEPS, 1):
        print(f"\n[{i}/{len(STEPS)}] {label}...", flush=True)
        result = subprocess.run(cmd, cwd=HERE)
        if result.returncode != 0:
            print(f"\nセットアップに失敗しました ([{i}/{len(STEPS)}] {label})。")
            print("上に出ているメッセージを確認してください。")
            return 1
    print("\n" + "=" * 50)
    print(f" 完了しました ({(time.time() - start) / 60:.1f} 分)。")
    print(" kana-ime.bat をダブルクリックすると起動します。")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
