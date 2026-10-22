# modules/instance.py
"""單一實例保護：同一個資料夾的程式只能同時執行一份。

做法是綁定本機 127.0.0.1 的專屬連接埠當作「鎖」：行程結束（甚至當機）時系統會自動釋放，
不會像鎖檔一樣殘留。第二次啟動時會通知第一個實例把視窗拉到前面，然後自己安靜地結束。
"""
import hashlib
import os
import socket
import threading

_MAGIC = b"yt-dlp-python-UI"
_PORT_BASE = 49152
_PORT_SPAN = 16000


class SingleInstance:
    def __init__(self, key):
        digest = hashlib.md5(os.path.abspath(str(key)).lower().encode("utf-8")).hexdigest()
        self.port = _PORT_BASE + int(digest[:8], 16) % _PORT_SPAN
        self._sock = None
        self._activate = threading.Event()

    def acquire(self):
        """回傳 True 代表可以繼續啟動；False 代表已有實例在執行（且已通知它）。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":
            # POSIX：避免剛結束的連線（TIME_WAIT）擋住重新綁定。
            # Windows 絕對不能設 SO_REUSEADDR，否則會允許兩個行程同時綁定同一個連接埠。
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", self.port))
            sock.listen(5)
        except OSError:
            sock.close()
            # 連接埠被占用：確認對方真的是本程式；若是別的程式剛好占用，就放行不擋使用者。
            return not self._notify_existing()
        self._sock = sock
        threading.Thread(target=self._serve, daemon=True).start()
        return True

    def _notify_existing(self):
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1.5) as conn:
                conn.sendall(_MAGIC + b"\n")
                conn.settimeout(1.5)
                return conn.recv(64).startswith(_MAGIC)
        except OSError:
            return False

    def _serve(self):
        sock = self._sock
        while sock is not None:
            try:
                conn, _ = sock.accept()
            except OSError:
                return  # release() 關閉了 socket
            with conn:
                try:
                    conn.settimeout(1.0)
                    if conn.recv(64).startswith(_MAGIC):
                        conn.sendall(_MAGIC + b" ok\n")
                        self._activate.set()
                except OSError:
                    pass

    def consume_activation(self):
        """是否有人嘗試再開一份？（讀取後會清除旗標）"""
        if self._activate.is_set():
            self._activate.clear()
            return True
        return False

    def release(self):
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                # 先 shutdown 才能叫醒卡在 accept() 的執行緒（Linux 只 close 不會釋放連接埠；
                # Windows 對監聽中的 socket 呼叫 shutdown 會報錯，忽略即可，close 本身就會叫醒）。
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
