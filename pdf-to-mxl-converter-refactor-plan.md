# pdf-to-mxl-converter 改修方針

> 対象リポジトリ: https://github.com/NbtKmy/pdf-to-mxl-converter
> 関連プロジェクト: omr_xml_editor (本プロジェクト) — このアプリの出力を消費するブラウザエディタ
> 文書日: 2026-05-03 (更新版)

---

## 改訂履歴

- **2026-05-03 初版**: 出力を `.mxl` から「画像と measure 座標を内包した単一 `.mei`」に置き換える計画として起草
- **2026-05-03 更新**: Phase 1 実装中に初の実 PDF (2 ページ楽譜) を投入したところ、Audiveris のページ認識・小節認識ともに荒れる箇所が多く、measure 単位 facsimile 紐付けの労力対効果がバランスしないと判明。**MEI 単独返却 + 画像はユーザー手元のものを omr_xml_editor 側で別途読み込む構成** に de-scope。§3 / §6 / §8 / §9 を該当差分で書き換え

---

## 1. ゴール

現状 (Phase 1 完了): **PDF → 単一 `.mei`** を返す Web アプリ (`.mxl` も互換オプションで取得可能)
将来 (Phase 2 / 3): **多様な楽譜入力 (PDF / PNG / JPG / IIIF) → 単一 `.mei`**

### 主な変更点

1. **入力フォーマットの拡張** (Phase 2 / 3): PDF に加え PNG / JPG / IIIF (Image URL or Manifest URL) を受け付ける
2. **出力フォーマットの変更** (Phase 1 完了): 単独 `.mxl` ではなく、Verovio による MusicXML→MEI 変換で `.mei` を返す。`.mxl` (MusicXML) もラジオで選択可能な互換オプションとして残す
3. **エディタとの疎結合な統合**: omr_xml_editor が MEI をドラッグ&ドロップで読み込み、**ユーザー手元の元 PDF / 画像と並べて人手で校正** する運用

### 改修しない方針

- 既存の Audiveris Docker コンテナ構成は維持 (`audiveris.dockerfile` / `docker-compose.yml`)
- Flask + Docker SDK の基本骨格は維持
- 認証・ユーザー管理などは引き続き持たない (ローカル実行ツールのまま)
- エディタ機能は持たせない (omr_xml_editor 側に切り分け)
- **measure 単位の facsimile 紐付けは持たせない** (OMR 精度のばらつきと労力対効果の都合 — 詳細は §3)

---

## 2. 入力フォーマット対応 (Phase 2 / 3 で実装予定)

| フォーマット | 受け取り方 | Audiveris への投入 |
|---|---|---|
| **PDF** | ファイルアップロード (Phase 1 で実装済み) | そのまま `/input/*` |
| **PNG / JPG** | ファイルアップロード (Phase 2) | そのまま `/input/*` (Audiveris は画像入力対応) |
| **複数画像** | ZIP アップロード or マルチファイル選択 (Phase 2) | 解凍/個別保存後に `/input/*` (1 ファイル = 1 ページ) |
| **IIIF Image API URL** | URL 入力 (Phase 3) | サーバー側でダウンロード → `/input/page-N.jpg` |
| **IIIF Presentation Manifest URL** | URL 入力 (Phase 3) | manifest を取得・解析 → 各ページの画像 URL を取得 → 全ページを `/input/page-N.jpg` |

### IIIF の扱いの詳細

- **Image API**: `https://server/iiif/{id}/full/full/0/default.jpg` 形式 → `requests.get` で 1 枚ダウンロード
- **Presentation API v2 / v3**: manifest JSON を取得 → `sequences[0].canvases[*].images[0].resource.@id` (v2) または `items[*].items[0].items[0].body.id` (v3) からページ画像 URL を抽出
- 画像サイズは `full/full` (最大解像度) で取得。OMR 精度を優先
- 著作権・利用規約: ユーザーがアクセス権を持つ前提。サーバーは User-Agent をきちんと付ける程度

### 入力受付 UI (Flask 側)

- ファイルアップロード (drag & drop): PDF / PNG / JPG / ZIP
- URL 入力フィールド: IIIF Image URL or Manifest URL
- どちらか一方を提出 (両方提出はエラー)

---

## 3. 出力フォーマット (Phase 1 完了 — 当初案から方針変更)

### 単一 MEI ファイル (`.mei`、facsimile なし)

Verovio Python binding が MusicXML から生成した MEI をそのまま返却する。ファイル単独で完結し、画像や座標情報は持たない。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<mei xmlns="http://www.music-encoding.org/ns/mei">
  <meiHead>...</meiHead>
  <music>
    <body><mdiv><score><section>
      <measure n="1">...</measure>
      <measure n="2">...</measure>
      ...
    </section></score></mdiv></body>
  </music>
</mei>
```

### 互換オプション

- UI 上のラジオで `MusicXML (.mxl, raw Audiveris output)` を選択すると Audiveris 出力の `.mxl` をそのまま返す経路を残す
- バックエンドは `output_format=mei|mxl` で分岐

### 当初案からの変更点 (なぜ facsimile を諦めたか)

初の実 PDF テスト (2 ページ楽譜) で:

- Audiveris のページ認識自体が安定しない
- 小節レベルの認識も部分的に大きく崩れる (Verovio が `std::out_of_range` でクラッシュするほど MusicXML が乱れていた)
- 崩れた箇所の measure stack 情報は信用できないため、座標と楽譜を紐付けても誤った位置をハイライトすることになる

労力対効果がバランスしないと判断し、画像同期は omr_xml_editor 側で **ユーザーの元 PDF と MEI を並べる手動運用** に置き換えた。

---

## 4. アーキテクチャ概要

```
Browser (Flask UI)
  │
  ├─ PDF upload                     (Phase 1 実装済み)
  ├─ ZIP / 単独画像 upload          (Phase 2)
  └─ IIIF URL                       (Phase 3)
        │
        ▼
Flask backend
        │
        ├─ 入力種別判定
        ├─ IIIF: manifest 解析 + 画像ダウンロード   (Phase 3)
        ├─ ZIP: 解凍                                  (Phase 2)
        └─ 配置
        ▼
   /input/ ディレクトリにファイル群
        │
        ▼
docker exec audiveris -batch -export -output /output /input/*
        │
        ▼
   /output/ に  *.mxl  が出力
        │
        ▼
Flask backend (post-process)
        │
        ├─ format=mxl → .mxl をそのままレスポンス
        └─ format=mei → Verovio (subprocess) で MusicXML → MEI 変換
        ▼
   レスポンス: 単一 score.mei  (Content-Type: application/vnd.mei+xml)
              or score.mxl   (application/vnd.recordare.musicxml)
```

---

## 5. Audiveris CLI

### 現行

```python
cmd = '/bin/sh -c "/Audiveris/bin/Audiveris -batch -export -output /output /input/*"'
```

`.mxl` だけ取得すれば足りるので `-save` フラグは付けていない (当初案では `-save` を追加して `.omr` を解析する予定だったが §6 で de-scope)。

### 注意点

- 複数ファイルを同時に渡すと **1 つの Book** にまとめて処理される (sheet 1, sheet 2, ...)
- ページの順序を保証するためファイル名は `page-001.png`, `page-002.png` のようにゼロパディングする (Phase 2 で重要)

---

## 6. ~~`.omr` 解析: measure bbox の抽出~~ (de-scope)

当初は Audiveris の `.omr` プロジェクトファイル (`-save` で生成) から `sheet#N.xml` を読んで measure bbox を取り出す予定だったが、§3 で記述した OMR 精度の問題から実装しないことに決定。

`.omr` を解析しなくても済むので Audiveris CLI からも `-save` を外している。「OMR がきれいに出る楽譜限定で測定したい」と再評価したくなったら、git 履歴 (branch `dev`、Phase 1 中盤) に初版実装が残っているのでそこから復活させる。

---

## 7. MusicXML → MEI 変換

### 採用: Verovio Python binding (subprocess 分離)

- `pip install verovio` (5.1.0 を pin)
- `subprocess.run([sys.executable, "-c", worker, mxl_path])` で **別プロセス** として実行
- worker は `tk = verovio.toolkit(); tk.loadFile(mxl_path); print(tk.getMEI({"scoreBased": True}))` のみ

### サブプロセス分離が必須な理由

Verovio の MusicXML importer は、Audiveris が出力する崩れた MusicXML (大量の "Adding 'Beam' to a 'Chord'" / "Chord starting point has not been found" / 多数の未解決 ties / 多数の未マッチ spanning element) を読み込ませると、まれに **uncaught な C++ 例外 (`std::out_of_range` の `map::at` など) でプロセス全体を落とす**。

サブプロセスで隔離すれば、クラッシュは非ゼロ exit code として親 Flask に返るだけで済む。ユーザーには「Verovio が読めなかった、MusicXML 形式 (`.mxl`) で再試行してほしい」と flash で通知する。

### 注意点

- Verovio の MusicXML→MEI は完全往復ではない (意味的に同等な範囲で変換)
- ARM64 (Apple Silicon) では PyPI に wheel がないため、Docker イメージで `cmake` / `swig` / `build-essential` をインストールしてソースビルドする (`flask.dockerfile`)
- 同じ MusicXML でも Verovio のクラッシュ可否が再現せず非決定的に見える場合があったため、リトライ運用は今のところ非推奨 (再投入してもいいが利用者には MusicXML フォールバックを案内する)

---

## 8. ~~MEI への facsimile 注入~~ (de-scope)

§3 / §6 と同じ理由で実装しない。Verovio の出力をそのまま返す。

---

## 9. ~~ベース画像の選択~~ (de-scope)

画像を MEI に埋め込まない方針なので不要。omr_xml_editor 側でユーザーが手元の PDF / 画像を直接読み込む。

---

## 10. UI / API 変更

### Flask ルート

| メソッド | URL | 用途 |
|---|---|---|
| GET | `/` | ホーム (ファイルアップロード UI) |
| POST | `/` | 変換実行 (フォーム送信) |
| GET | `/health` | ヘルスチェック |

Phase 2 / 3 で必要なら `/convert` 等に分割する。

### POST / のリクエスト

- `img`: PDF ファイル (Phase 1)
- `output_format`: `mei` (既定) または `mxl`
- (Phase 2): `files` (複数画像) / `zip`
- (Phase 3): `iiif_url`

### レスポンス

- 成功 (`mei`): `application/vnd.mei+xml`、ファイルダウンロード (`Content-Disposition: attachment; filename="<basename>.mei"`)
- 成功 (`mxl`): `application/vnd.recordare.musicxml`、ファイルダウンロード
- 失敗: 元のフォームに redirect、flash メッセージで失敗段を通知 (将来 JSON エラー API も検討)

### UI

- ダーク + グラスモーフィズム
- カスタム D&D ドロップゾーン (PDF 受け入れ表示・ファイル名表示)
- 出力フォーマットのセグメント型ピル (MEI / MusicXML)
- Audiveris 処理中の音符モチーフローダー (送信時 download_token cookie でディスミス)

---

## 11. 実装フェーズ案 (進捗反映)

### Phase 1: 基本動作の改修 (PDF → MEI) ✅ 完了 (2026-05-03)

- [x] Python 3.8 → 3.12 (`flask.dockerfile`)
- [x] `requirements.txt` 更新 (Flask 系の version 上げ + `verovio` 追加)
- [x] `src/converter/` パッケージ新設 (`audiveris_runner`, `mei_writer`)
- [x] Verovio による MusicXML → MEI 変換 (subprocess 分離)
- [x] レスポンスを `.mei` に切替、`.mxl` 互換オプションを維持
- [x] UI 刷新 (ダーク + ガラス + 音符モチーフ)
- [x] `/health` エンドポイント
- [ ] omr_xml_editor 側で MEI が読み込めることを確認 (継続中)

**当初案から de-scope したもの**: `-save` フラグ追加、`.omr` 解析、facsimile 注入、PDF→PNG レンダリング (`pymupdf`)、`lxml` 依存 (§3 / §6 / §8 / §9 参照)

### Phase 2: 画像入力対応 (未着手)

- [ ] PNG / JPG 単独アップロードを受け付け
- [ ] ZIP アップロード (複数画像) のサポート
- [ ] ファイル名のゼロパディング・ページ順保証
- [ ] エラーメッセージの改善 (どの段で失敗したか)

### Phase 3: IIIF 対応 (未着手)

- [ ] IIIF Image URL の受付・ダウンロード
- [ ] IIIF Presentation Manifest v2 / v3 のパース
- [ ] User-Agent ヘッダの設定
- [ ] 著作権警告 UI
- (当初案にあった「MEI に IIIF URL を `<graphic type="source">` として記録」は §9 と合わせて de-scope)

### Phase 4: 仕上げ (未着手)

- [ ] CLI モード (`python -m converter`)
- [ ] 単体テスト (mei_writer サブプロセスのエラーパス、audiveris_runner)
- [ ] README 更新 (新しい入力フォーマットと使い方)
- [ ] 動作確認用のサンプル MEI を omr_xml_editor 側に配備

---

## 12. テスト戦略

### 単体テスト

| 対象 | テスト内容 |
|---|---|
| `mei_writer.musicxml_to_mei()` | 既知の `.mxl` から有効な MEI 文字列が返ること、Verovio クラッシュ時に `RuntimeError` が投げられること、タイムアウト発火 |
| `audiveris_runner.run_audiveris()` | コンテナ未起動時のエラーハンドリング、非ゼロ exit の扱い |
| 入力種別ディスパッチ (Phase 2 / 3) | 各種ファイル/URL で正しい処理パスが選ばれること |
| IIIF manifest パーサ (Phase 3) | v2 / v3 の代表的サンプル manifest を入力 → 期待されるページ URL リスト |

### 結合テスト

`tests/fixtures/` に各入力種別のサンプルを 1 つずつ:

- `simple.pdf` (短い 1〜2 ページの PDF)
- `simple.png` (Phase 2)
- `multipage.zip` (Phase 2)
- `iiif-image-url.txt` / `iiif-manifest-url.txt` (Phase 3)

各サンプルに対し:
1. POST /
2. レスポンスが MEI として valid であること (XML 検証)
3. measure 数が期待値であること

### omr_xml_editor 側との結合確認

- 出力した MEI をエディタにドロップ
- Verovio で描画される
- ユーザーが **手元の元 PDF / 画像と並べて校正** できる

---

## 13. スコープ外 / 将来課題

- **note レベルの座標**: 対象外 (measure 座標も de-scope したので)
- **measure 単位の facsimile 紐付け**: de-scope (§6 / §8)
- **画像の MEI 埋め込み**: de-scope (§9)
- **複数 MEI のマージ**: 対象外
- **ユーザー認証 / 履歴管理**: 対象外
- **MEI から MusicXML への往復編集**: omr_xml_editor 側の責務
- **OMR がきれいに通る楽譜限定で measure 座標を出すハイブリッド方式**: 将来案。Audiveris の confidence 情報を見て高信頼な小節だけ zone 化することは技術的に可能

---

## 14. リスクと対策

| リスク | 影響 | 対策 |
|---|---|---|
| Verovio が Audiveris の崩れた MusicXML で C++ クラッシュ | プロセスダウン | サブプロセス分離 (実装済み)。クラッシュ時は flash で `.mxl` 形式での再試行を案内 |
| Audiveris がエラーで `.mxl` を出さない | 変換失敗 | 終了コード・stderr を flash 表示 |
| 巨大 PDF (10MB+) で memory / 時間超過 | OOM, タイムアウト | Verovio subprocess は 180 秒タイムアウト。Audiveris 側のタイムアウトは Phase 4 で検討 |
| Verovio の MusicXML→MEI で情報損失 | 編集後の MusicXML 出力が劣化 | 既知の損失を README で警告。深刻なら music21 / xslt への切替を検討 |
| ARM64 マシンで verovio wheel が無くソースビルド失敗 | Docker build 失敗 | `flask.dockerfile` で `cmake` / `swig` / `build-essential` を導入済み |

---

## 15. 移行パス

- Phase 1 完了時点でデフォルトレスポンスは `.mei`、互換のため UI で MusicXML を選択すれば `.mxl` も取得可能
- 旧 API (`.mxl` 直接取得) を使い続けたい場合はラジオで MusicXML を選択するだけ

---

## 16. 連絡事項

omr_xml_editor 側で必要となる入力スキーマ:

- 単一 `.mei` ファイル (Verovio の `getMEI({scoreBased: true})` 出力そのまま)
- `<facsimile>` セクションは **このアプリからは出さない**
  - エディタ側は MEI 単独描画 + 「ユーザーが手元の PDF / 画像を別タブ・別ウィンドウで開いて並べる」フローを前提に作る
  - 将来 facsimile 出力を復活させた場合に備え、`<facsimile>` があれば使う・無ければ無視という後方互換に作っておくのが無難

両プロジェクトの **MEI 形式の互換性は 2026-05-03 に確定**: Verovio 出力ベースの素の MEI。
