# taskbar-gauge（工作列硬體儀表）

[English](README.md) ・ **繁體中文**

> 📖 **圖文教學：** https://shao-en.com/blog/taskbar-gauge-guide.html ——如果你不知道該如何操作，請把這篇文章交給你的 Claude 或 ChatGPT。

Windows 11 小工具：在工作列時鐘（系統匣）左側畫出三組開口同心圓儀表（缺口朝正下方）：

- **CPU % ＋ RAM**
- **GPU % ＋ VRAM**（僅支援 NVIDIA，透過 NVML）
- **硬碟**忙碌度（最多 4 顆實體碟，依代號排序，外圈為第一顆）

滑鼠移上去會顯示完整數字，按右鍵可結束程式。

## 需求

- Windows 11（Windows 10 未測試）
- Python 3，且 `pythonw` 在 `PATH` 中（開發環境為 Python 3.12）
- 套件（`requirements.txt`）：`PySide6-Essentials`、`nvidia-ml-py`、`psutil`、`pywin32`
- 要顯示 GPU 儀表需要 NVIDIA 顯示卡（其他廠牌不支援）

## 安裝

```powershell
pip install -r requirements.txt
```

## 使用

雙擊 `啟動.bat`（以 `pythonw taskbar_gauge.py` 在背景執行）。同一時間只會有一個實例（具名 mutex）。

要開機自動啟動：執行 `安裝開機啟動.bat`，會在目前使用者的「啟動」資料夾建立捷徑（不需要系統管理員權限）。`移除開機啟動.bat` 可移除。

## 運作原理

Windows 11 沒有 deskband，所以本工具為每個工作列各開一個透明、不搶焦點、置頂的工具視窗：

- 主工作列：以 `Shell_TrayWnd` 內 `TrayNotifyWnd` 的左緣定位。
- 副工作列（`Shell_SecondaryTrayWnd`）沒有該子視窗，改用距右緣的固定偏移（`secondary_right_offset_dip`）。
- 每隔 `reposition_ms` 重新置頂並重算位置；前景有全螢幕視窗時，該螢幕的儀表會隱藏。座標使用實體像素，並依各螢幕 DPI 換算。
- 硬碟忙碌度＝100 − PDH `\PhysicalDisk(*)\% Idle Time`（與工作管理員同一來源）。

## 設定

編輯 `taskbar_gauge.py` 旁的 `config.json`，改完重新啟動程式。缺少的欄位會使用預設值。

| 欄位 | 預設 | 說明 |
|---|---|---|
| `update_ms` | `1000` | 取樣間隔 |
| `reposition_ms` | `500` | 重新定位／置頂的間隔 |
| `animation` | `true` | 數值變化是否有動畫 |
| `theme` | `"auto"` | `auto`、`light` 或 `dark` |
| `max_disks` | `4` | 顯示的實體硬碟數 |
| `gauge_pad_dip` | `2` | 儀表四周留白（DIP） |
| `gauge_gap_dip` | `12` | 儀表之間的間距（DIP） |
| `primary_tray_gap_dip` | `6` | 主工作列上與系統匣的距離（DIP） |
| `secondary_right_offset_dip` | `125` | 副工作列上距右緣的偏移（DIP） |
| `show_on` | `"primary"` | `all`、`primary` 或 `secondary` |
| `hide_when_fullscreen` | `true` | 前景有全螢幕視窗時隱藏 |
| `gpu_index` | `0` | 讀取第幾張 NVIDIA 顯示卡 |

## 已知限制

- 副工作列的偏移是固定值；若更改時鐘格式或系統匣寬度，請調整 `secondary_right_offset_dip`。
- 深色／淺色主題只在取樣時偵測。
- GPU 只支援 NVIDIA。

## 疑難排解

儀表消失時，先看程式旁的 `error.log`。計時器回呼與繪圖都包了 try/except，錯誤會寫進該檔（同一個錯誤 60 秒內最多記一次；超過 512 KB 會輪替成 `error.log.old`）。

## 解除安裝

執行 `移除開機啟動.bat` 移除開機捷徑，對儀表按右鍵結束程式，再刪除整個資料夾即可。

## 校驗下載檔

到 [Releases](https://github.com/shaoen901109/taskbar-gauge/releases) 下載 `taskbar-gauge-v1.0.0.zip` 和 `SHA256SUMS.txt`（版本號以你實際下載的為準）。

1. 用 PowerShell 比對指紋（SHA256，檔案的數位指紋）：

   ```powershell
   Get-FileHash .\taskbar-gauge-v1.0.0.zip -Algorithm SHA256
   ```

   輸出的 `Hash` 要和 `SHA256SUMS.txt` 裡的字串一致（大小寫不同沒關係）。

2. 驗證來源（需安裝 [GitHub CLI](https://cli.github.com/)）：

   ```powershell
   gh attestation verify .\taskbar-gauge-v1.0.0.zip --repo shaoen901109/taskbar-gauge
   ```

   顯示驗證成功，代表這個 zip 是 GitHub Actions 從本 repo 原始碼建出來的，中途沒有被替換。

GitHub 上標示 **Verified** 的 commit，是作者本人簽章。

## 技術來源與致謝

| 用途 | 元件 | 授權 |
|---|---|---|
| 介面與繪圖 | [PySide6 (Qt for Python)](https://doc.qt.io/qtforpython-6/) | LGPL-3.0／GPL-3.0／商業授權 |
| 系統數據（CPU、RAM、磁碟） | [psutil](https://github.com/giampaolo/psutil) | BSD-3-Clause |
| NVIDIA GPU 數據（NVML） | [nvidia-ml-py](https://pypi.org/project/nvidia-ml-py/) | BSD-3-Clause |
| Windows API 呼叫 | [pywin32](https://github.com/mhammond/pywin32) | PSF |

開發過程使用 [Claude Code](https://claude.com/claude-code)（Anthropic）協助。

## 作者

- 網站：https://shaoenstudio.com
- 聯絡信箱：shaoenstudio@gmail.com
- GitHub：[@shaoen901109](https://github.com/shaoen901109)

## 授權

[MIT](LICENSE)
