# RTESEditor — Claude Code プロジェクト設定

## アプリ短縮名
`rteseditor`

---

## リリースノート方針

GitHub Release のリリースノートには **ユーザーが直接体験できる変化** のみを記載する。

### 記載するもの
- 新機能（新しいタブ、パネル、操作など）
- ユーザーが気づく UI の変化（プログレスバー表示、ウィンドウ状態の記憶など）
- バグ修正でユーザーに影響があるもの

### 記載しないもの
- 内部モジュールの分離・リファクタリング（例: `version.py` の分離、クラス設計変更）
- コンポーネント名や内部クラス名を含む実装詳細（例: `RecordGrid`、`ConflictGrid`、`field_fmts オーバーライド`）
- ファイル構成・インポート構造の変更
- テスト・ビルドスクリプトの修正

### 判断基準
> 「エンドユーザーがアプリを使っていて気づく変化か？」→ Yes なら記載、No なら省く。

---

## リリース手順

1. worktree ブランチで実装・コミット
2. `master` へマージ
3. タグ作成: `git tag vX.Y.Z -m "RTESEditor vX.Y.Z"`
4. EXE ビルド: `cd RTESEditor && python -m PyInstaller RTESEditor.spec --noconfirm`
5. push: `git push origin master && git push origin vX.Y.Z`
6. GitHub Release 作成: `gh release create vX.Y.Z dist/RTESEditor.exe --title "..." --notes "..."`
   - 添付ファイルは **`RTESEditor.exe`** 単体（ZIPなし）
   - リリースノートはユーザー向けの内容のみ（上記方針に従う）

---

## ビルド

```powershell
cd RTESEditor
python -m PyInstaller RTESEditor.spec --noconfirm
# 出力: RTESEditor/dist/RTESEditor.exe
```
