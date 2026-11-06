# ETF 成分股看板

在 iPhone 上看 0050、0056、00878 成分股的現價、本益比、目標價，以及 1、3、5、7、14、28、56 個交易日前的股價。

每個交易日台灣時間 18:30，GitHub 會自動抓一次資料，你打開 app 看到的就是當天收盤。

## 一次性設定（約 15 分鐘，用電腦操作比較順手）

**1. 建立 GitHub 專案**
註冊 github.com → 右上角「+」→ New repository → 取個名字（例如 `etf-board`）→ 選 **Public** → Create。

**2. 上傳檔案**
在專案頁點「uploading an existing file」，把解壓縮後的**所有檔案和資料夾**拖進去 → Commit changes。
確認 `.github/workflows/update.yml` 有出現在正確的路徑。如果 `.github` 資料夾沒傳上去（有些電腦會隱藏它），就點 Add file → Create new file，檔名打 `.github/workflows/update.yml`，把檔案內容貼上。

**3. 設定密碼**
Settings → Secrets and variables → Actions → New repository secret
名稱 `APP_PASSWORD`，值：你要用來打開 app 的密碼（建議 10 個字以上，不要和其他帳號重複）

沒設 `APP_PASSWORD` 的話，程式會停下來不更新，避免資料未加密就公開。

**4. 第一次抓資料**
Actions 分頁 → 若有提示就按啟用 → 左邊點「更新股價資料」→ Run workflow。
第一次要回補約 60 個交易日的股價，大約 5～8 分鐘；之後每天只抓當天，1 分鐘內完成。跑完專案裡會多出 `data.json`。

**5. 開啟網站**
Settings → Pages → Source 選 Deploy from a branch，Branch 選 `main`、資料夾 `/ (root)` → Save。
一兩分鐘後上方會出現網址，像 `https://你的帳號.github.io/etf-board/`。

**6. 加到 iPhone 主畫面**
用 Safari 打開那個網址 → 輸入密碼解鎖 → 分享按鈕 → 加入主畫面。
勾選「在這台裝置記住」之後就不用每次輸入；要重新上鎖，點右上角「鎖定」。

## 資料來源

全部是免金鑰的公開資料：
- 股價：臺灣證券交易所、證券櫃檯買賣中心的每日收盤行情
- 本益比、殖利率、股價淨值比：證交所、櫃買中心 OpenAPI（政府資料開放授權條款，公開使用時需註明出處，app 底部已標示）
- 法人目標價（平均、中位數、最低、最高、分析師人數）：Yahoo 財經的分析師共識，透過 yfinance 套件抓取。Yahoo 的資料限個人使用，公開給別人用之前要考慮授權問題，可在 `config.json` 把 `analyst_targets` 改成 `false` 關掉
- 成分股：MoneyDJ ETF 持股頁（公開給別人用之前，建議改用投信官網資料或手動名單，見下方「常見調整」）

## 使用方式

- 上方切換 ETF；用「排序」選單選要排的欄位，旁邊的按鈕切換大到小／小到大。也可以直接點欄位標題排序
- 「法人均價、中位數、最低」是分析師目標價的統計，下面的小字是分析師人數和最高價；「均價空間」是法人均價相對現價的漲幅空間
- 上方的「法人目標空間」是整檔 ETF 依持股權重平均的均價空間
- 「股價 / 漲跌幅」切換歷史欄位的主要數字；色塊紅色代表從那天到現在上漲、綠色代表下跌
- 點「我的目標價」欄填入你自己參考的目標價，會自動算上漲空間。目標價只存在你的手機上
- 加權本益比用持股權重計算，排除虧損（無本益比）的個股

## 在 app 裡管理 ETF

點 ETF 分頁最右邊的「＋」，就能直接新增或移除 ETF，不用回電腦改檔案。app 會幫你修改 `config.json` 並觸發一次更新，約 2～5 分鐘後新的 ETF 就會出現。

第一次使用要先連上 GitHub，需要一組權杖（只做一次）：

1. 到 GitHub → 右上角頭像 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token
2. Token name 隨意（例如 `etf-board`），Expiration 選一年
3. Repository access 選 **Only select repositories**，只勾你的 ETF 專案
4. Permissions → Repository permissions 設定：
   - **Contents**：Read and write
   - **Actions**：Read and write
5. Generate token，複製 `github_pat_` 開頭的那串
6. 回到 app 點「＋」，貼上權杖，專案欄位會自動帶入，按「儲存並測試」

權杖會用你的 app 密碼加密後，只存在這台裝置；按「鎖定」後要重新輸入密碼才能再用。權杖過期或換了 app 密碼，app 會請你重新貼一次。

可以加的是台股 ETF（含主動式，例如 00981A）。債券型、海外型 ETF 的持股不是台股個股，加了也不會有內容。

## 常見調整

**用電腦增減 ETF**：也可以直接編輯 `config.json` 的 `etfs`，例如加入 `"00919": ""`（名稱留空會自動補上），存檔後到 Actions 再跑一次。

**成分股抓不完整**：app 若提示「只抓到前十大持股」，可以在 `config.json` 手動填完整名單：
```json
"manual_holdings": { "0050": ["2330", "2454", "2317"] }
```

**「天前」的算法**：以交易日計算，5 天前就是往前數第 5 個開盤日。要改天數就改 `config.json` 的 `lookback`。

## 密碼與加密

- 每次更新時，`data.json` 會用你的密碼加密（PBKDF2-SHA256 + AES-256-GCM）。別人就算拿到網址或翻專案，也只看得到亂碼。
- 密碼不會存在任何地方；手機「記住」的是由密碼算出來的解密金鑰。
- **改密碼**：到 Secrets 修改 `APP_PASSWORD`，再到 Actions 跑一次。所有裝置都要重新輸入新密碼。
- **忘記密碼**：沒辦法找回，直接在 Secrets 設一個新的，再跑一次即可。
- 網頁本身（`index.html`）還是公開的，別人打開只會看到密碼畫面。

## 注意

- 專案是 Public，但資料已加密。`config.json`（你追蹤哪些 ETF）沒有加密，別人看得到清單。
- 歷史股價快取（`prices_cache.json`）存在 GitHub Actions 的快取裡，不會出現在專案中。連續 7 天沒跑（例如過年長假）會被清掉，下次會自動重新回補，只是會跑久一點。
- 如果 Actions 紀錄出現「被證交所暫時封鎖」之類的錯誤，通常隔一陣子再跑就好。
- 目標價沒有免費的自動來源，所以採手動輸入。
- 資料僅供參考，不構成投資建議。
