#!/usr/bin/env python3
"""无头 Chrome CDP 截图工具：打开页面 → 执行 JS 序列 → 截图保存。

用法: python3 scripts/cdp_shot.py <url> <out.png> [操作脚本名]
操作脚本: planner（规划页搜索 159938 并预览回测）/ none（只截图）
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request

URL, OUT = sys.argv[1], sys.argv[2]
SCENARIO = sys.argv[3] if len(sys.argv) > 3 else 'none'
PORT = 9223


def connect():
    for _ in range(20):
        try:
            tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json'))
            page = [t for t in tabs if t.get('type') == 'page' and URL.split('#')[0] in t.get('url', '')]
            if page:
                break
        except Exception:
            time.sleep(0.5)
    else:
        sys.exit('找不到目标页面')
    path = page[0]['webSocketDebuggerUrl'].split(str(PORT), 1)[1]
    s = socket.create_connection(('127.0.0.1', PORT))
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    resp = b''
    while b'\r\n\r\n' not in resp:
        resp += s.recv(4096)
    assert b'101' in resp.split(b'\r\n')[0]
    return s


class CDP:
    def __init__(self, sock):
        self.s = sock
        self.mid = 0

    def _send(self, d):
        data = d.encode()
        mask = os.urandom(4)
        n = len(data)
        h = b'\x81'
        if n < 126:
            h += bytes([0x80 | n])
        elif n < 65536:
            h += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            h += bytes([0x80 | 127]) + struct.pack('>Q', n)
        self.s.sendall(h + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _recv(self):
        def rd(n):
            b = b''
            while len(b) < n:
                c = self.s.recv(n - len(b))
                if not c:
                    raise ConnectionError('closed')
                b += c
            return b
        out = b''
        while True:
            b1, b2 = rd(2)
            fin, n = b1 & 0x80, b2 & 0x7F
            if n == 126:
                n = struct.unpack('>H', rd(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', rd(8))[0]
            out += rd(n)
            if fin:
                return out

    def cmd(self, method, params=None):
        self.mid += 1
        self._send(json.dumps({'id': self.mid, 'method': method, 'params': params or {}}))
        while True:
            m = json.loads(self._recv())
            if m.get('id') == self.mid:
                return m.get('result', {})

    def ev(self, expr):
        r = self.cmd('Runtime.evaluate', {'expression': expr, 'returnByValue': True})
        if 'exceptionDetails' in r:
            return 'JS-EXC: ' + json.dumps(r['exceptionDetails'])[:300]
        return r.get('result', {}).get('value')


def scenario_planner(cdp):
    print('输入:', cdp.ev("""(() => {
      const sec = document.querySelector('section.page.on');
      const input = sec && sec.querySelector('input[placeholder*="510300"]');
      if (!input) return 'input-not-found';
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, '159938');
      input.dispatchEvent(new Event('input', {bubbles: true}));
      return 'typed';
    })()"""))
    time.sleep(2.5)
    print('选中:', cdp.ev("""(() => {
      const sec = document.querySelector('section.page.on');
      const row = sec && sec.querySelector('.row');
      if (!row) return 'no-result';
      row.click(); return 'picked';
    })()"""))
    print('等待档位表与回测渲染…')
    time.sleep(16)


def main():
    cdp = CDP(connect())
    time.sleep(2.5)
    if SCENARIO == 'planner':
        scenario_planner(cdp)
    shot = cdp.cmd('Page.captureScreenshot', {'format': 'png'})
    with open(OUT, 'wb') as f:
        f.write(base64.b64decode(shot['data']))
    print('已保存', OUT)


if __name__ == '__main__':
    main()
