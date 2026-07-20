#!/usr/bin/env python3
"""Apple WLOC 位置改写插件(Feature B / iOS)。

截 gs-loc.apple.com 的 Wi-Fi 定位请求(设备问"我周围这些 BSSID 在哪"), 回一个把
每个被问的 BSSID 都指到设定坐标的响应 → 设备定位落在该点。

wire 格式(苹果私有, 逆向公开): 头部(locale/identifier 长度前缀)+ protobuf。
  请求 protobuf: field 2 (repeated) = {field 1 = BSSID(MAC 串)}
  响应 protobuf: field 2 (repeated) = {field 1 = BSSID, field 2 = {1=纬度×1e8, 2=经度×1e8, 3=精度}}
  经纬度是 int64 ×1e8(负数按 protobuf int64 两补码 varint)。

⚠️ 头部确切字节需真 iPhone 抓包核对(_HEADER/_split_header 是当前最佳猜测, 留口子在阶段5校准)。
纯 stdlib 手写 protobuf(沿用 parse-geosite.py 的路子), 不引入依赖。
"""

_LOCALE = b"en_US"
_IDENT = b"com.apple.locationd"


# ── protobuf 编码 ──
def _uvarint(n):
    n &= (1 << 64) - 1                       # 负数 → 64 位两补码(protobuf int64 负数编码)
    out = bytearray()
    while True:
        b = n & 0x7f
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field, wt):
    return _uvarint((field << 3) | wt)


def _f_varint(field, n):
    return _tag(field, 0) + _uvarint(n)


def _f_bytes(field, data):
    return _tag(field, 2) + _uvarint(len(data)) + data


# ── protobuf 解码(手写, 同 parse-geosite.py)──
def _rv(b, i):
    s = r = 0
    while True:
        x = b[i]; i += 1; r |= (x & 0x7f) << s
        if not x & 0x80:
            return r, i
        s += 7


def _fields(b):
    i, n, o = 0, len(b), []
    while i < n:
        k, i = _rv(b, i); fn, wt = k >> 3, k & 7
        if wt == 0:
            v, i = _rv(b, i); o.append((fn, wt, v))
        elif wt == 2:
            ln, i = _rv(b, i); o.append((fn, wt, bytes(b[i:i + ln]))); i += ln
        elif wt == 5:
            o.append((fn, wt, bytes(b[i:i + 4]))); i += 4
        elif wt == 1:
            o.append((fn, wt, bytes(b[i:i + 8]))); i += 8
        else:
            raise ValueError("bad wiretype")
    return o


def _svar(n):
    """无符号 varint 值 → 有符号 int64。"""
    return n - (1 << 64) if n >= (1 << 63) else n


# ── 头部 ──
def _header():
    return (b"\x00\x01"
            + len(_LOCALE).to_bytes(2, "big") + _LOCALE
            + len(_IDENT).to_bytes(2, "big") + _IDENT
            + b"\x00\x00\x00\x01\x00\x00")


def _pb_has_wifi(pb):
    """pb 能解析且含 field 2(WiFi 列表)= 认为是有效的 wloc protobuf。"""
    try:
        return any(fn == 2 and wt == 2 for fn, wt, _ in _fields(pb))
    except Exception:                         # noqa: BLE001
        return False


def _split_header(body):
    """跳过头部返回 protobuf。格式待真机核对; 结构化偏移不成立则扫首个能解析出 WiFi 列表的位置。"""
    try:                                      # 结构化: 2 + locale + identifier + 6(0x00000001 0x0000)
        i = 2
        for _ in range(2):
            ln = int.from_bytes(body[i:i + 2], "big"); i += 2 + ln
        i += 6
        if 0 < i <= len(body) and _pb_has_wifi(body[i:]):
            return body[i:]
    except Exception:                         # noqa: BLE001
        pass
    pos = 0                                    # 回退: 扫首个 field-2 tag(0x12) 且能解析出 WiFi 列表
    while True:
        pos = body.find(b"\x12", pos)
        if pos < 0:
            return body
        if _pb_has_wifi(body[pos:]):
            return body[pos:]
        pos += 1


# ── 请求解析 / 响应构造 ──
def parse_request(body):
    """从请求体解析出被问的 BSSID 列表。"""
    pb = _split_header(body)
    bssids = []
    for fn, wt, val in _fields(pb):
        if fn == 2 and wt == 2:              # 每个 WiFi 项
            for f2, w2, v2 in _fields(val):
                if f2 == 1 and w2 == 2:
                    bssids.append(v2.decode("utf-8", "ignore"))
    return bssids


def build_request(bssids):
    """构造一个请求(供测试用, 模拟设备)。"""
    pb = b""
    for m in bssids:
        pb += _f_bytes(2, _f_bytes(1, m.encode()))
    pb += _f_varint(3, 100)                  # numberOfResults
    return _header() + pb


def build_response(bssids, lat, lon, accuracy=50):
    """把每个 BSSID 都指到 (lat, lon) 的响应体。"""
    lat_e8 = int(round(lat * 1e8))
    lon_e8 = int(round(lon * 1e8))
    pb = b""
    for m in bssids:
        loc = _f_varint(1, lat_e8) + _f_varint(2, lon_e8) + _f_varint(3, accuracy)
        pb += _f_bytes(2, _f_bytes(1, m.encode()) + _f_bytes(2, loc))
    return _header() + pb


def parse_response(body):
    """解析响应体 → {bssid: (lat, lon, acc)}(供测试)。"""
    pb = _split_header(body)
    out = {}
    for fn, wt, val in _fields(pb):
        if fn == 2 and wt == 2:
            mac = None; loc = None
            for f2, w2, v2 in _fields(val):
                if f2 == 1 and w2 == 2:
                    mac = v2.decode("utf-8", "ignore")
                elif f2 == 2 and w2 == 2:
                    lat = lon = acc = 0
                    for f3, w3, v3 in _fields(v2):
                        if f3 == 1:
                            lat = _svar(v3)
                        elif f3 == 2:
                            lon = _svar(v3)
                        elif f3 == 3:
                            acc = _svar(v3)
                    loc = (lat / 1e8, lon / 1e8, acc)
            if mac and loc:
                out[mac] = loc
    return out


class WLOCPlugin:
    """接管 gs-loc.apple.com, 把定位改写成设定坐标。"""
    domains = ["gs-loc.apple.com"]

    def __init__(self, lat, lon, accuracy=50):
        self.lat, self.lon, self.accuracy = lat, lon, accuracy

    def handle(self, tls, host, port):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = tls.recv(4096)
            if not chunk:
                break
            data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        clen = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    clen = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    clen = 0
        while len(body) < clen:
            chunk = tls.recv(4096)
            if not chunk:
                break
            body += chunk
        try:
            bssids = parse_request(body)
        except Exception:  # noqa: BLE001
            bssids = []
        rb = build_response(bssids, self.lat, self.lon, self.accuracy)
        tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-protobuf\r\n"
                    b"Content-Length: " + str(len(rb)).encode()
                    + b"\r\nConnection: close\r\n\r\n" + rb)
