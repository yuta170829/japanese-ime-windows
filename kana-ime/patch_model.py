"""
zenz GGUF を標準のllama.cppで読めるように複製を作る。

zenzは前処理方式 tokenizer.ggml.pre = 'gpt2-small-japanese-char' を指定するが、
これはazooKey同梱のllama.cppだけが知っている名前で、素のllama.cpp
(llama-cpp-python) は読み込みを拒否する。トークナイザは文字単位のBPEなので、
'default' に置き換えた複製を models/zenz-compat.gguf に作る。元ファイルは変更しない。
"""
import os
import sys

import gguf
from gguf.scripts.gguf_new_metadata import MetadataDetails, copy_with_new_metadata

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "models", "zenz-v3.2-small-Q5_K_M.gguf")
DST = os.path.join(HERE, "models", "zenz-compat.gguf")


def main() -> int:
    if not os.path.exists(SRC):
        print("先に download_model.py を実行してください。")
        return 1
    reader = gguf.GGUFReader(SRC, "r")
    arch = reader.get_field(gguf.Keys.General.ARCHITECTURE).contents()
    writer = gguf.GGUFWriter(DST, arch=arch, endianess=reader.endianess)
    new_meta = {
        "tokenizer.ggml.pre": MetadataDetails(gguf.GGUFValueType.STRING, "default"),
    }
    copy_with_new_metadata(reader, writer, new_meta, [])
    print("作成しました:", DST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
