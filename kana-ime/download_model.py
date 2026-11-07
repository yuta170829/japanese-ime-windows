"""zenz-v3.2-small (Q5_K_M, 約74MB) を models/ に取得してSHA-256を検証する。"""
import hashlib
import os
import sys
import urllib.request

REVISION = "c67e03e07d215c869f591b274c1631170d3e11fe"
URL = (f"https://huggingface.co/Miwa-Keita/zenz-v3.2-small-gguf/resolve/"
       f"{REVISION}/ggml-model-Q5_K_M.gguf")
SHA256 = "29c223d4c23327b80fd13ebb5ab2555057a46317997d5da391584ffbef0db673"
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "models", "zenz-v3.2-small-Q5_K_M.gguf")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    if os.path.exists(DEST) and sha256(DEST) == SHA256:
        print("モデルは取得済みです:", DEST)
        return 0
    tmp = DEST + ".part"
    print("ダウンロード中:", URL)
    urllib.request.urlretrieve(URL, tmp)
    if sha256(tmp) != SHA256:
        os.remove(tmp)
        print("SHA-256が一致しません。ダウンロードを破棄しました。")
        return 1
    os.replace(tmp, DEST)
    print("完了:", DEST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
