# 公文簽核快速貼上工具

在公文簽核系統裡，**框住公文關鍵字**（或從**常用擬辦詞庫**搜尋一句），對應的批示用語就自動進剪貼簿，按 `Ctrl+V` 貼上即可。使用 Windows 內建 OCR，**離線、免費、不需 AI**。

> 完整說明與操作示範動畫請開啟 **`index.html`**（或啟用 GitHub Pages 後直接線上瀏覽）。

## 功能

- **圈選辨識（Ctrl+Alt+Q）**：框住公文關鍵字 → 本機 OCR 辨識 → 依規則自動把對應批示放進剪貼簿。
- **詞庫速選（Ctrl+Alt+W）**：跳出常用擬辦詞庫，搜尋即時篩選，點一句即複製。
- **可自訂**：關鍵字、批示、詞庫都能在程式裡新增／編輯／刪除，並可匯入 `.txt`。
- **人工確認學習**：模糊比對後先預覽完整批示，勾選確認才會記住 OCR 誤讀；主視窗可刪除錯誤記憶。
- **選取後自動貼上（可選）**：勾選後命中即自動貼上（Q、W 通用），貼上時機可選「馬上貼／延遲0.5秒／延遲2秒」。

## 需求

- Windows 10／11
- 已安裝「繁體中文 OCR」（系統功能，見下方）
- 自行執行原始碼時需 Python 3.10 以上

## 安裝繁體中文 OCR（必做一次）

以系統管理員開啟 PowerShell：

```powershell
# 確認是否已安裝
Get-WindowsCapability -Online | Where-Object Name -like "*OCR*zh*"
# 若 zh-TW 顯示 NotPresent，執行安裝
Add-WindowsCapability -Online -Name "Language.OCR~~~zh-TW~0.0.1.0"
```

## 執行（開發／自用）

```bat
pip install -r requirements.txt
python signing_tool.py
```

> 建議以「系統管理員身分」執行，全域快捷鍵才會生效。

## 打包成 exe

雙擊 `build.bat`，完成後會產生 `SigningHelper.exe` 與可分享的 `share` 資料夾。

## 使用

- **Ctrl+Alt+Q**：畫面變暗後拖曳框住公文關鍵字 → 命中後到欄位 `Ctrl+V`。
- **Ctrl+Alt+W**：跳出詞庫，搜尋後點一句即複製 → `Ctrl+V`。
- 公文字太小讀不到時，把檢視器顯示放大到 150~200% 再框最有效。
- OCR 命中後先在預覽視窗核對、修改，再按「複製上方文字」。關閉視窗不會複製或自動貼上。
- 換版前先備份整個使用資料夾，尤其是 `rules.json`、`phrases.json`、`learned.json`，避免用新版範例覆蓋自訂資料。

## 隱私

- `learned.json`（自動學習記憶）可能累積實際公文片段，已列入 `.gitignore`，不會上傳。
- 預設不再寫入診斷截圖與 OCR 文字；僅設定 `SIGNING_HELPER_DEBUG_CAPTURE=1` 才保存 `last_capture.png`、`last_ocr.txt`。舊版產生的檔案請自行檢查並清理。
- 自訂規則或詞庫時，請勿放入真實姓名或單位內部資訊後再公開。

## 檔案

| 檔案 | 用途 |
|---|---|
| `signing_tool.py` | 主程式 |
| `rules.json` | 圈選辨識規則（關鍵字→批示） |
| `phrases.json` | 常用擬辦詞庫 |
| `requirements.txt` | 套件清單 |
| `build.bat` | 一鍵打包成 exe |
| `index.html` | 說明與操作示範動畫 |

## 授權

MIT License（見 `LICENSE`）。
