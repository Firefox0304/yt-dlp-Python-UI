這個資料夾放介面使用的字型（Noto Sans TC，SIL Open Font License 1.1，授權全文見 OFL.txt）。

- 程式啟動時只會把這裡的 .ttf 註冊成「只有本程式看得到」的字型，不會安裝到系統，程式關閉後即消失。
- 只放了靜態的 Regular 與 Bold。請不要放入 VariableFont（可變字型）：Windows 版 Tk 只會讀到它的預設實例（Thin，最細），整個介面會變得很細。
- 刪除這個資料夾的字型也沒關係，程式會自動改用系統字型。

設定檔（config/settings.json）的 font_registration：
- "private"（預設）：字型只有本程式看得到。
- "session"（實驗）：這次 Windows 登入期間對所有程式可見，程式關閉時會自動移除。
  只在「日誌顯示套用成功、畫面卻仍是別的字型」時才需要試試看；若程式異常結束，字型會留到登出或重新開機。
