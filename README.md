# japanese-ime-windows

Windows で、AI (ローカルで動く小さな言語モデル zenz) を使って日本語を入力するための2つのものをまとめています。

| フォルダ | 内容 | 向いている人 |
|---|---|---|
| [`kana-ime/`](kana-ime/) | 自作の変換アプリ。辞書ですぐ変換し、AI が後から直す。タイプミスも自動で直す | 手軽に試したい人 (Python があれば動く) |
| [`azooKey-Windows導入ガイド/`](azooKey-Windows導入ガイド/) | [azooKey-Windows](https://github.com/fkunn1326/azooKey-Windows) を、すべてのアプリで使える日本語入力として使えるようにする手順と補助スクリプト | 普段の入力を AI 変換にしたい人 |

## 紹介動画 (kana-ime)

[`videos/kana-ime紹介.mp4`](videos/kana-ime紹介.mp4) (約46秒) … 実際の画面の録画です。
AI の補正 (オレンジ色)、候補と AI の別案、タイプミスの自動補正を紹介しています。

## ダウンロード

このページの緑の「**Code**」ボタン →「**Download ZIP**」で、すべてをまとめてダウンロードできます。
展開したら、使いたい方のフォルダの説明を読んでください。

- kana-ime: [`kana-ime/はじめにお読みください.md`](kana-ime/はじめにお読みください.md)
- azooKey-Windows: [`azooKey-Windows導入ガイド/azooKey-Windows導入ガイド.md`](azooKey-Windows導入ガイド/azooKey-Windows導入ガイド.md)

## 2つの違い

| | kana-ime | azooKey-Windows |
|---|---|---|
| 使える場所 | kana-ime の画面の中だけ (コピーして貼り付け) | Windows のすべてのアプリ |
| 必要なもの | Python 3.10 以上 | 管理者権限、スマート アプリ コントロールのオフ (オンの場合) |
| AI の動き | 辞書の変換をすぐ出し、AI の補正は後から届く (打つのを待たせない) | 打つたびに AI の計算を待つ |
| タイプミスの補正 | あり | なし |
| 作者 | このリポジトリ | fkunn1326 さん (本体はこのリポジトリに含まれていません) |

どちらも、入力した文章を外部に送りません。AI はパソコンの中だけで動きます。

## 注意

- どちらも試作です。変換の精度は Microsoft IME などの製品より低いことがあります
- azooKey-Windows は試作版 (v0.1.0-alpha1) です。ガイドの「先に知っておいてほしいこと」を必ず読んでください
- AI のモデル (zenz-v3.2-small、Apache-2.0、Miwa-Keita) と辞書は、このリポジトリに含まれていません。セットアップのときに配布元から取得します
