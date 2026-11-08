import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import socket
import ssl
import hashlib
import re
import threading

VERSION = "0020"
COPYRIGHT = "© Qwen3.7 & TerAnYu"

PATTERN_ESCAPED = re.compile(r'(\\[0-9A-Fa-f]{2})+')
PATTERN_RAW_CP1251 = re.compile(rb'[\xc0-\xff]')


def decode_escaped(match):
    try:
        return bytes.fromhex(match.group(0).replace('\\', '')).decode('cp1251')
    except Exception:
        return match.group(0)


def is_valid_utf8_cyrillic(text):
    try:
        raw = text.encode('latin-1')
        decoded = raw.decode('utf-8')
        return any('\u0400' <= ch <= '\u04ff' for ch in decoded)
    except (UnicodeDecodeError, UnicodeEncodeError):
        return False


def decode_raw_cp1251(text):
    try:
        if is_valid_utf8_cyrillic(text):
            return None
        raw = text.encode('latin-1')
        if not PATTERN_RAW_CP1251.search(raw):
            return None
        try:
            decoded_utf8 = raw.decode('utf-8')
            if any('\u0400' <= ch <= '\u04ff' for ch in decoded_utf8):
                return None
        except UnicodeDecodeError:
            pass
        decoded = raw.decode('cp1251')
        return decoded if decoded != text else None
    except Exception:
        return None


class MikrotikAPI:
    def __init__(self, host, port=8728, user='admin', password='', use_ssl=False):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.use_ssl = use_ssl
        self.sock = None
        self._buf = b''
        self._lock = threading.Lock()

    def connect(self):
        raw_sock = socket.create_connection((self.host, self.port), timeout=15)
        raw_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self.use_ssl:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
            try:
                ctx.set_ciphers('ALL:@SECLEVEL=0')
            except ssl.SSLError:
                ctx.set_ciphers('DEFAULT:COMPLEMENTOFALL:@SECLEVEL=0')
            self.sock = ctx.wrap_socket(raw_sock, server_hostname=self.host)
        else:
            self.sock = raw_sock

        self._send('/login', f'=name={self.user}', f'=password={self.password}')
        resp = self._recv_sentence()
        if '!done' not in resp:
            chal = next((w[5:] for w in resp if w.startswith('=ret=')), None)
            if chal:
                md5 = hashlib.md5(
                    b'\x00' + self.password.encode('utf-8') + bytes.fromhex(chal)
                ).hexdigest()
                self._send('/login', f'=name={self.user}', f'=response=00{md5}')
                resp = self._recv_sentence()
        if '!done' not in resp:
            err = next((w.split('=', 1)[1] for w in resp if w.startswith('=message=')), str(resp))
            raise ConnectionError(f"Auth failed: {err}")

    def run(self, *words):
        with self._lock:
            self._buf = b''
            self._send(*words)
            results = []
            while True:
                sentence = self._recv_sentence()
                if not sentence:
                    break
                tag = sentence[0]
                if tag == '!re':
                    item = {}
                    for w in sentence[1:]:
                        if w.startswith('=') and '=' in w[1:]:
                            k, v = w[1:].split('=', 1)
                            item[k] = v
                        elif '=' in w:
                            k, v = w.split('=', 1)
                            item[k] = v
                    results.append(item)
                elif tag == '!trap':
                    msg = next((w.split('=', 1)[1] for w in sentence if w.startswith('=message=')), str(sentence))
                    raise RuntimeError(f"Trap: {msg}")
                elif tag == '!fatal':
                    raise RuntimeError(f"Fatal: {sentence}")
                elif tag == '!done':
                    break
            return results

    def _send(self, *words):
        data = b''
        for w in words:
            encoded = w.encode('utf-8')
            data += self._encode_length(len(encoded)) + encoded
        data += b'\x00'
        self.sock.sendall(data)

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("Connection closed unexpectedly")
            self._buf += chunk
        result = self._buf[:n]
        self._buf = self._buf[n:]
        return result

    def _recv_sentence(self):
        words = []
        while True:
            length = self._decode_length_safe()
            if length == 0:
                break
            if length > 1048576:
                raise ConnectionError(f"Protocol desync: word length {length} exceeds limit")
            data = self._recv_exact(length)
            words.append(data.decode('latin-1'))
        return words

    def _decode_length_safe(self):
        b = self._recv_exact(1)
        c = b[0]
        if c < 0x80:
            return c
        elif c < 0xC0:
            b2 = self._recv_exact(1)
            return ((c & 0x3F) << 8) | b2[0]
        elif c < 0xE0:
            b2 = self._recv_exact(2)
            return ((c & 0x1F) << 16) | (b2[0] << 8) | b2[1]
        else:
            b2 = self._recv_exact(3)
            return ((c & 0x0F) << 24) | (b2[0] << 16) | (b2[1] << 8) | b2[2]

    @staticmethod
    def _encode_length(l):
        if l < 0x80:
            return bytes([l])
        elif l < 0x4000:
            return bytes([(l >> 8) | 0x80, l & 0xFF])
        elif l < 0x200000:
            return bytes([(l >> 16) | 0xC0, (l >> 8) & 0xFF, l & 0xFF])
        else:
            raise ValueError("Word too long")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
            self._buf = b''

    @property
    def connected(self):
        return self.sock is not None


class CopyableScrolledText(scrolledtext.ScrolledText):
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.bind("<Control-c>", lambda e: self.event_generate("<<Copy>>"))
        self.bind("<Control-a>", lambda e: self.tag_add(tk.SEL, "1.0", tk.END))
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Копировать", command=self._copy)
        self.menu.add_command(label="Выделить всё", command=self._select_all)
        self.bind("<Button-3>", self._show_menu)

    def _copy(self):
        try:
            text = self.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.clipboard_clear()
            self.clipboard_append(text)
        except tk.TclError:
            pass

    def _select_all(self):
        self.tag_add(tk.SEL, "1.0", tk.END)

    def _show_menu(self, event):
        self.menu.post(event.x_root, event.y_root)


class MikrotikDecoderApp:
    SECTIONS = [
        "/system/identity",
        "/system/note",
        "/system/scheduler",
        "/system/script",
        "/system/logging/action",
        "/user",
        "/user/group",
        "/interface/list/member",
        "/interface/bridge/port",
        "/interface/vlan",
        "/interface/pppoe-client",
        "/interface/l2tp-client",
        "/interface/gre",
        "/interface/wireguard/peers",
        "/ip/firewall/filter",
        "/ip/firewall/nat",
        "/ip/firewall/mangle",
        "/ip/firewall/raw",
        "/ip/firewall/address-list",
        "/ip/dhcp-server/network",
        "/ip/dhcp-server/lease",
        "/ip/dns/static",
        "/ip/route",
        "/ip/pool",
        "/ip/service",
        "/tool/e-mail",
        "/tool/netwatch",
        "/snmp/community",
        "/ppp/profile",
        "/ppp/secret",
        "/queue/simple",
        "/queue/tree",
        "/routing/bgp/connection",
        "/routing/ospf/instance",
        "/caps-man/configuration",
    ]

    SKIP_FIELDS = {
        '.id', 'id', 'dynamic', 'inactive', 'running', 'disabled', 'enabled',
        'invalid', 'bytes', 'packets', 'log', 'log-prefix',
        'run-count', 'last-started', 'nextid',
    }
    DANGEROUS_FIELDS = {'source'}

    def __init__(self, root):
        self.root = root
        self.root.title(f"MikroTik Cyrillic Fixer v{VERSION}")
        self.root.geometry("1100x750")
        self.api = None
        self.found = []
        self.check_vars = {}
        self.anchor_iid = None
        self.sort_col = None
        self.sort_reverse = False
        self.selection_order = []
        self._current_decoded_sources = []
        self._build_ui()

    def _build_ui(self):
        cf = ttk.LabelFrame(self.root, text="Подключение")
        cf.pack(fill=tk.X, padx=5, pady=5)
        r = ttk.Frame(cf)
        r.pack(fill=tk.X, padx=5, pady=3)

        ttk.Label(r, text="Host:").pack(side=tk.LEFT)
        self.e_host = ttk.Entry(r, width=16)
        self.e_host.insert(0, "192.168.88.1")
        self.e_host.pack(side=tk.LEFT, padx=2)

        ttk.Label(r, text="Port:").pack(side=tk.LEFT, padx=(6, 0))
        self.e_port = ttk.Entry(r, width=6)
        self.e_port.insert(0, "8728")
        self.e_port.pack(side=tk.LEFT, padx=2)

        ttk.Label(r, text="User:").pack(side=tk.LEFT, padx=(6, 0))
        self.e_user = ttk.Entry(r, width=12)
        self.e_user.insert(0, "admin")
        self.e_user.pack(side=tk.LEFT, padx=2)

        ttk.Label(r, text="Pass:").pack(side=tk.LEFT, padx=(6, 0))
        self.e_pass = ttk.Entry(r, width=16, show="*")
        self.e_pass.insert(0, "")
        self.e_pass.pack(side=tk.LEFT, padx=2)

        self.ssl_var = tk.BooleanVar(value=False)
        self.ssl_cb = ttk.Checkbutton(r, text="API-SSL (игнор. сертификат)", variable=self.ssl_var,
                                        command=self._on_ssl_toggle)
        self.ssl_cb.pack(side=tk.LEFT, padx=(10, 0))

        btn_row = ttk.Frame(cf)
        btn_row.pack(fill=tk.X, padx=5, pady=5)

        self.connect_btn = ttk.Button(btn_row, text="Подключиться", command=self.do_connect)
        self.connect_btn.pack(side=tk.LEFT, padx=2)

        self.disconnect_btn = ttk.Button(btn_row, text="Отключиться", command=self.do_disconnect, state=tk.DISABLED)
        self.disconnect_btn.pack(side=tk.LEFT, padx=2)

        self.scan_btn = ttk.Button(btn_row, text="Сканировать", command=self.scan, state=tk.DISABLED)
        self.scan_btn.pack(side=tk.LEFT, padx=2)

        right_info = ttk.Frame(btn_row)
        right_info.pack(side=tk.RIGHT, padx=5)

        self.version_label = ttk.Label(right_info, text=f"v{VERSION}", foreground="gray")
        self.version_label.pack(anchor=tk.E)

        self.copyright_label = ttk.Label(right_info, text=COPYRIGHT, foreground="gray")
        self.copyright_label.pack(anchor=tk.E)

        self.conn_status = ttk.Label(right_info, text="● Не подключено", foreground="gray")
        self.conn_status.pack(anchor=tk.E)

        pw = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        pw.pack(expand=True, fill=tk.BOTH, padx=5, pady=5)

        lf = ttk.LabelFrame(pw, text="Найденные проблемы")
        pw.add(lf, weight=1)

        top_bar = ttk.Frame(lf)
        top_bar.pack(fill=tk.X, padx=2, pady=2)

        self.select_all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top_bar, text="Выбрать все", variable=self.select_all_var,
            command=self._toggle_select_all
        ).pack(side=tk.LEFT)

        self.selected_count_label = ttk.Label(top_bar, text="Выбрано: 0")
        self.selected_count_label.pack(side=tk.RIGHT)

        cols = ("chk", "sec", "orig", "dec")
        self.tree = ttk.Treeview(
            lf, columns=cols, show="headings", selectmode="extended"
        )
        self.tree.heading("chk", text="✔", command=lambda: self._sort_by_column("chk"))
        self.tree.heading("sec", text="Раздел", command=lambda: self._sort_by_column("sec"))
        self.tree.heading("orig", text="Исходный", command=lambda: self._sort_by_column("orig"))
        self.tree.heading("dec", text="Декодированный", command=lambda: self._sort_by_column("dec"))

        self.tree.column("chk", width=30, minwidth=30, stretch=False, anchor=tk.CENTER)
        self.tree.column("sec", width=160, minwidth=100, stretch=False)
        self.tree.column("orig", width=400, minwidth=150, stretch=True)
        self.tree.column("dec", width=400, minwidth=150, stretch=True)

        self.tree.pack(expand=True, fill=tk.BOTH)

        self.tree.bind("<ButtonPress-1>", self._on_button_press)
        self.tree.bind("<B1-Motion>", self._on_b1_motion)
        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<ButtonRelease-1>", self._on_click)
        self.tree.bind("<KeyPress-space>", self._on_space)
        self.tree.bind("<<TreeviewSelect>>", self._on_selection_changed)

        pf = ttk.LabelFrame(pw, text="Команда (предпросмотр выбранных строк)")
        pw.add(pf, weight=1)

        self.txt = CopyableScrolledText(pf, height=8, font=("Consolas", 10))
        self.txt.pack(expand=True, fill=tk.BOTH, padx=2, pady=2)
        self.txt.tag_configure("r", foreground="red")
        self.txt.tag_configure("g", foreground="green")
        self.txt.tag_configure("warn", foreground="orange")
        self.txt.tag_configure("header", foreground="blue", font=("Consolas", 10, "bold"))
        self.txt.tag_configure("code", foreground="#8B0000", background="#FFFACD")

        br = ttk.Frame(pf)
        br.pack(fill=tk.X, padx=2, pady=2)

        self.copy_btn = ttk.Button(br, text="📋 Копировать", command=self._copy_command, state=tk.DISABLED)
        self.copy_btn.pack(side=tk.RIGHT, padx=(2, 0))

        self.fix_btn = ttk.Button(br, text="✔ Применить выбранные", command=self.apply_selected, state=tk.DISABLED)
        self.fix_btn.pack(side=tk.RIGHT)

        self.lbl = ttk.Label(br, text="")
        self.lbl.pack(side=tk.LEFT)

    # ==================== Управление подключением ====================

    def _on_ssl_toggle(self):
        if self.ssl_var.get():
            self.e_port.delete(0, tk.END)
            self.e_port.insert(0, "8729")
        else:
            self.e_port.delete(0, tk.END)
            self.e_port.insert(0, "8728")

    def _set_connected_state(self, connected):
        if connected:
            self.connect_btn.config(state=tk.DISABLED)
            self.disconnect_btn.config(state=tk.NORMAL)
            self.scan_btn.config(state=tk.NORMAL)
            self.ssl_cb.config(state=tk.DISABLED)
            self.conn_status.config(text="● Подключено", foreground="green")
            self.e_host.config(state=tk.DISABLED)
            self.e_port.config(state=tk.DISABLED)
            self.e_user.config(state=tk.DISABLED)
            self.e_pass.config(state=tk.DISABLED)
        else:
            self.connect_btn.config(state=tk.NORMAL)
            self.disconnect_btn.config(state=tk.DISABLED)
            self.scan_btn.config(state=tk.DISABLED)
            self.ssl_cb.config(state=tk.NORMAL)
            self.conn_status.config(text="● Не подключено", foreground="gray")
            self.e_host.config(state=tk.NORMAL)
            self.e_port.config(state=tk.NORMAL)
            self.e_user.config(state=tk.NORMAL)
            self.e_pass.config(state=tk.NORMAL)

    def do_connect(self):
        self.connect_btn.config(state=tk.DISABLED)
        self.ssl_cb.config(state=tk.DISABLED)
        self.conn_status.config(text="● Подключение...", foreground="orange")
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        try:
            port = int(self.e_port.get())
            self.api = MikrotikAPI(
                self.e_host.get(), port,
                self.e_user.get(), self.e_pass.get(),
                use_ssl=self.ssl_var.get()
            )
            self.api.connect()
            self.root.after(0, lambda: self._set_connected_state(True))
        except Exception as e:
            err_msg = str(e)
            self.root.after(0, lambda: messagebox.showerror("Ошибка подключения", err_msg))
            self.root.after(0, lambda: self._set_connected_state(False))

    def do_disconnect(self):
        if self.api:
            self.api.close()
            self.api = None
        self._set_connected_state(False)
        self.lbl.config(text="Отключено")

    # ==================== Сортировка ====================

    def _sort_by_column(self, col):
        if self.sort_col == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_col = col
            self.sort_reverse = False

        arrows = {"chk": "✔", "sec": "Раздел", "orig": "Исходный", "dec": "Декодированный"}
        for c in arrows:
            suffix = ""
            if c == col:
                suffix = " ▼" if self.sort_reverse else " ▲"
            self.tree.heading(c, text=arrows[c] + suffix)

        items = []
        for iid in self.tree.get_children(""):
            values = self.tree.item(iid, "values")
            checked = self.check_vars[iid].get() if iid in self.check_vars else False
            items.append((iid, values, checked))

        if col == "chk":
            items.sort(key=lambda x: x[2], reverse=not self.sort_reverse)
        else:
            col_idx = {"sec": 1, "orig": 2, "dec": 3}[col]
            items.sort(
                key=lambda x: str(x[1][col_idx]).lower(),
                reverse=self.sort_reverse
            )

        selected_iids = set(self.tree.selection())
        for index, (iid, values, _) in enumerate(items):
            self.tree.move(iid, "", index)

        valid_sel = [s for s in selected_iids if s in self.check_vars]
        if valid_sel:
            self.tree.selection_set(valid_sel)

    # ==================== Блокировка ресайза chk ====================

    def _is_chk_separator(self, x, y):
        region = self.tree.identify_region(x, y)
        col = self.tree.identify_column(x)
        return region == "separator" and col == "#1"

    def _on_button_press(self, event):
        if self._is_chk_separator(event.x, event.y):
            return "break"

    def _on_b1_motion(self, event):
        if self._is_chk_separator(event.x, event.y):
            return "break"

    def _on_double_click(self, event):
        if self._is_chk_separator(event.x, event.y):
            return "break"

    # ==================== Чекбоксы и выделение ====================

    def _get_all_iids(self):
        return list(self.check_vars.keys())

    def _iid_index(self, iid):
        iids = self._get_all_iids()
        try:
            return iids.index(iid)
        except ValueError:
            return -1

    def _set_check(self, iid, value):
        if iid in self.check_vars:
            self.check_vars[iid].set(value)
            self.tree.set(iid, "chk", "☑" if value else "")

    def _toggle_check(self, iid):
        if iid in self.check_vars:
            new_val = not self.check_vars[iid].get()
            self._set_check(iid, new_val)

    def _on_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self.check_vars:
            return

        col = self.tree.identify_column(event.x)

        if col == "#1" and not (event.state & 0x4) and not (event.state & 0x1):
            self._toggle_check(iid)
            self._update_selected_count()
            return

    def _on_space(self, event):
        selected = self.tree.selection()
        if not selected:
            return

        first = selected[0]
        if first not in self.check_vars:
            return
        new_state = not self.check_vars[first].get()

        for iid in selected:
            self._set_check(iid, new_state)

        self._update_selected_count()
        return "break"

    def _on_selection_changed(self, event=None):
        sel = self.tree.selection()
        current_set = set(sel)
        self.selection_order = [iid for iid in self.selection_order if iid in current_set]
        for iid in sel:
            if iid not in self.selection_order:
                self.selection_order.append(iid)

        self._show_preview_multi()
        self._update_selected_count()

    def _toggle_select_all(self):
        val = self.select_all_var.get()
        for iid in self.check_vars:
            self._set_check(iid, val)
        if val:
            all_iids = self._get_all_iids()
            if all_iids:
                self.tree.selection_set(all_iids)
                self.selection_order = list(all_iids)
        else:
            self.tree.selection_remove(*self._get_all_iids())
            self.selection_order.clear()
        self._update_selected_count()

    def _update_selected_count(self):
        count = sum(1 for v in self.check_vars.values() if v.get())
        total = len(self.check_vars)
        self.selected_count_label.config(text=f"Выбрано: {count} / {total}")
        self.fix_btn.config(state=tk.NORMAL if count > 0 else tk.DISABLED)

        if total > 0 and count == total:
            self.select_all_var.set(True)
        else:
            self.select_all_var.set(False)

    def _get_checked_indices(self):
        result = []
        for iid, var in self.check_vars.items():
            if var.get():
                idx = self._iid_index(iid)
                if idx >= 0:
                    result.append(idx)
        return sorted(result)

    # ==================== Предпросмотр (мульти) ====================

    def _show_preview_multi(self):
        self.txt.delete("1.0", tk.END)
        self._current_decoded_sources = []

        if not self.selection_order:
            self.copy_btn.config(state=tk.DISABLED)
            return

        valid_order = [iid for iid in self.selection_order if iid in self.check_vars]
        if not valid_order:
            self.copy_btn.config(state=tk.DISABLED)
            return

        indices = []
        for iid in valid_order:
            idx = self._iid_index(iid)
            if idx >= 0:
                indices.append(idx)

        if len(indices) == 1:
            idx = indices[0]
            sec, item, orig, decoded = self.found[idx]
            set_words, warnings = self._build_set_cmd(sec, item)
            cmd_display = ' '.join(set_words)

            self.txt.insert(tk.END, "# БЫЛО:\n")
            self.txt.insert(tk.END, f"{orig}\n\n", "r")
            if warnings:
                for w in warnings:
                    self.txt.insert(tk.END, f"{w}\n", "warn")
                self.txt.insert(tk.END, "\n")

            has_source_only = (len(set_words) <= 2 and 'source' in item and self._has_bad_encoding(item['source']))
            if has_source_only:
                decoded_source = self._fix_value(item['source'])
                self._current_decoded_sources.append(decoded_source)
                self.txt.insert(tk.END, "# ДЕКОДИРОВАННЫЙ SOURCE (скопируйте в WinBox/SSH):\n", "header")
                self.txt.insert(tk.END, decoded_source + "\n", "code")
            else:
                self.txt.insert(tk.END, "# КОМАНДА:\n")
                self.txt.insert(tk.END, cmd_display, "g")
        else:
            all_orig = []
            all_cmds = []
            all_warnings = []
            source_items = []

            for i, idx in enumerate(indices):
                sec, item, orig, decoded = self.found[idx]
                set_words, warnings = self._build_set_cmd(sec, item)
                cmd_display = ' '.join(set_words)

                all_orig.append((i + 1, sec, orig))
                has_source_only = (len(set_words) <= 2 and 'source' in item and self._has_bad_encoding(item['source']))
                if has_source_only:
                    decoded_src = self._fix_value(item['source'])
                    source_items.append((i + 1, sec, item.get('name', '?'), decoded_src))
                    self._current_decoded_sources.append(decoded_src)
                else:
                    all_cmds.append((i + 1, cmd_display))
                if warnings:
                    all_warnings.extend(warnings)

            self.txt.insert(tk.END, f"# БЫЛО ({len(indices)} записей):\n", "header")
            for num, sec, orig in all_orig:
                self.txt.insert(tk.END, f"[{num}] {sec}: ", "header")
                self.txt.insert(tk.END, f"{orig}\n", "r")
            self.txt.insert(tk.END, "\n")

            if all_warnings:
                for w in all_warnings:
                    self.txt.insert(tk.END, f"{w}\n", "warn")
                self.txt.insert(tk.END, "\n")

            if all_cmds:
                self.txt.insert(tk.END, f"# КОМАНДЫ ({len(all_cmds)}):\n", "header")
                for num, cmd in all_cmds:
                    self.txt.insert(tk.END, f"[{num}] ", "header")
                    self.txt.insert(tk.END, f"{cmd}\n", "g")
                self.txt.insert(tk.END, "\n")

            if source_items:
                self.txt.insert(tk.END, f"# ДЕКОДИРОВАННЫЕ SOURCE ({len(source_items)}) — скопируйте в WinBox/SSH:\n", "header")
                for num, sec, name, code in source_items:
                    self.txt.insert(tk.END, f"\n[{num}] {sec} name={name}:\n", "header")
                    self.txt.insert(tk.END, code + "\n", "code")

        self.copy_btn.config(state=tk.NORMAL)

    def _copy_command(self):
        if self._current_decoded_sources:
            clean_parts = []
            for src in self._current_decoded_sources:
                cleaned = re.sub(r'\n{2,}', '\n', src.strip())
                clean_parts.append(cleaned)
            content = '\n'.join(clean_parts)
        else:
            content = self.txt.get("1.0", tk.END).strip()

        if content:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.lbl.config(text="📋 Скопировано!")
            self.root.after(2000, lambda: self.lbl.config(text=""))

    # ==================== Кодировки ====================

    def _has_bad_encoding(self, value):
        if PATTERN_ESCAPED.search(value):
            return True
        if decode_raw_cp1251(value) is not None:
            return True
        return False

    def _fix_value(self, value):
        fixed = PATTERN_ESCAPED.sub(decode_escaped, value)
        raw_fixed = decode_raw_cp1251(fixed)
        if raw_fixed is not None:
            return raw_fixed
        return fixed

    def _build_set_cmd(self, sec, item):
        item_id = item.get('.id', item.get('id', ''))
        set_words = [f'{sec}/set']
        if item_id:
            set_words.append(f'=.id={item_id}')

        warnings = []
        for k, v in item.items():
            if k in self.SKIP_FIELDS:
                continue
            if not self._has_bad_encoding(v):
                continue
            if k in self.DANGEROUS_FIELDS:
                warnings.append(f"⚠ Поле '{k}' пропущено (многострочный код)")
                continue
            decoded_v = self._fix_value(v)
            set_words.append(f'={k}={decoded_v}')

        return set_words, warnings

    # ==================== Сканирование ====================

    def scan(self):
        if not self.api or not self.api.connected:
            messagebox.showwarning("Внимание", "Сначала подключитесь к устройству")
            return
        self.scan_btn.config(state=tk.DISABLED)
        self.lbl.config(text="Сканирование...")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            self.found.clear()

            for sec in self.SECTIONS:
                try:
                    items = self.api.run(sec + "/print")
                except RuntimeError:
                    continue
                except ConnectionError:
                    try:
                        self.api.close()
                        self.api.connect()
                        items = self.api.run(sec + "/print")
                    except Exception:
                        continue
                for item in items:
                    has_problem = any(self._has_bad_encoding(v) for v in item.values())
                    if has_problem:
                        raw_line = ' '.join(f"{k}={v}" for k, v in item.items())
                        decoded_line = ' '.join(
                            f"{k}={self._fix_value(v)}" for k, v in item.items()
                        )
                        self.found.append((sec, item, raw_line, decoded_line))

            self.root.after(0, self._fill_tree)
        except Exception as e:
            err_msg = str(e)
            self.root.after(0, lambda msg=err_msg: messagebox.showerror("Ошибка сканирования", msg))
        finally:
            self.root.after(0, lambda: self.scan_btn.config(state=tk.NORMAL))
            count = len(self.found)
            self.root.after(0, lambda c=count: self.lbl.config(text=f"Найдено: {c}"))

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.check_vars.clear()
        self.select_all_var.set(False)
        self.anchor_iid = None
        self.sort_col = None
        self.sort_reverse = False
        self.selection_order.clear()
        self._current_decoded_sources = []

        self.tree.heading("chk", text="✔")
        self.tree.heading("sec", text="Раздел")
        self.tree.heading("orig", text="Исходный")
        self.tree.heading("dec", text="Декодированный")

        for i, (sec, _, orig, dec) in enumerate(self.found):
            iid = str(i)
            var = tk.BooleanVar(value=False)
            self.check_vars[iid] = var
            self.tree.insert("", tk.END, iid=iid, values=("", sec, orig, dec))

        self._update_selected_count()
        self.txt.delete("1.0", tk.END)
        self.copy_btn.config(state=tk.DISABLED)

    # ==================== Применение ====================

    def apply_selected(self):
        if not self.api or not self.api.connected:
            messagebox.showwarning("Внимание", "Подключение потеряно. Переподключитесь.")
            return

        indices = self._get_checked_indices()
        if not indices:
            return

        self.fix_btn.config(state=tk.DISABLED)
        self.lbl.config(text=f"Применение {len(indices)} из {len(self.found)}...")
        threading.Thread(target=self._apply_worker, args=(indices,), daemon=True).start()

    def _apply_worker(self, indices):
        applied = 0
        errors = []
        failed_indices = set()

        for idx in reversed(indices):
            sec, item, _, _ = self.found[idx]
            set_words, warnings = self._build_set_cmd(sec, item)

            if len(set_words) <= 2:
                errors.append(f"[{sec}] Нет исправляемых полей (только source?)")
                failed_indices.add(idx)
                continue

            try:
                self.api.run(*set_words)
                applied += 1
            except Exception as e:
                err_msg = str(e)
                errors.append(f"[{sec}] {err_msg}")
                failed_indices.add(idx)

        success_indices = [i for i in indices if i not in failed_indices]
        for idx in sorted(success_indices, reverse=True):
            self.found.pop(idx)

        def finish():
            self._fill_tree()
            status_parts = [f"✔ Применено: {applied}"]
            if errors:
                status_parts.append(f"✘ Ошибок: {len(errors)}")
                messagebox.showwarning("Результаты", "\n".join(errors))
            self.lbl.config(text=" | ".join(status_parts))
            self.fix_btn.config(state=tk.NORMAL)

        self.root.after(0, finish)


if __name__ == "__main__":
    root = tk.Tk()
    app = MikrotikDecoderApp(root)
    root.mainloop()