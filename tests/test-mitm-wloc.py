#!/usr/bin/env python3
"""Apple WLOC 插件回归: protobuf 编解码往返 + 坐标改写(含负坐标)+ 插件 HTTP 处理。

⚠️ 测的是编解码逻辑与坐标映射; gs-loc 头部的确切字节需真 iPhone 抓包核对(阶段5校准)。
"""
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy" / "bot"))
import mitm_wloc  # noqa: E402

pass_n = 0
def ok(m):
    global pass_n; print("[OK]  ", m); pass_n += 1


def approx(a, b, eps=1e-6):
    return abs(a - b) < eps


def main():
    macs = ["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"]

    # 请求编解码往返
    req = mitm_wloc.build_request(macs)
    assert mitm_wloc.parse_request(req) == macs; ok("请求 protobuf 往返(BSSID 列表)")

    # 响应: 每个 BSSID 都被指到设定坐标(东京)
    lat, lon = 35.6812, 139.7671
    resp = mitm_wloc.build_response(macs, lat, lon, accuracy=42)
    got = mitm_wloc.parse_response(resp)
    assert set(got) == set(macs); ok("响应含所有被问 BSSID")
    for m in macs:
        la, lo, ac = got[m]
        assert approx(la, lat) and approx(lo, lon) and ac == 42, got[m]
    ok("每个 BSSID 都改写到设定坐标 + 精度")

    # 负坐标(旧金山 lon 为负 / 南半球 lat 为负)往返
    resp2 = mitm_wloc.build_response(["00:00:00:00:00:01"], -33.8688, -151.2093)
    la, lo, _ = mitm_wloc.parse_response(resp2)["00:00:00:00:00:01"]
    assert approx(la, -33.8688) and approx(lo, -151.2093), (la, lo)
    ok("负坐标(南纬/西经)int64 两补码往返正确")

    # 头部容错: 若头部字节不符, 扫描回退仍能解析出 BSSID
    body = b"\x99\x99garbage-header" + mitm_wloc.build_request(macs)[len(mitm_wloc._header()):]
    assert mitm_wloc.parse_request(body) == macs; ok("头部不符时扫描回退仍解析出 BSSID")

    # 插件 HTTP 处理: 喂一个 POST 请求, 读回 200 + 改写响应
    a, b = socket.socketpair()
    a.settimeout(5); b.settimeout(5)
    rb = mitm_wloc.build_request(macs)
    b.sendall(b"POST /clls/wloc HTTP/1.1\r\nHost: gs-loc.apple.com\r\nContent-Length: "
              + str(len(rb)).encode() + b"\r\n\r\n" + rb)
    plugin = mitm_wloc.WLOCPlugin(lat, lon)
    t = threading.Thread(target=plugin.handle, args=(a, "gs-loc.apple.com", 443)); t.start()
    resp_raw = b""
    while b"\r\n\r\n" not in resp_raw:
        resp_raw += b.recv(4096)
    head, _, body2 = resp_raw.partition(b"\r\n\r\n")
    clen = int([l.split(b":")[1].strip() for l in head.split(b"\r\n")
                if l.lower().startswith(b"content-length:")][0])
    while len(body2) < clen:
        body2 += b.recv(4096)
    t.join(5)
    assert b"200 OK" in head; ok("插件返回 HTTP 200")
    pr = mitm_wloc.parse_response(body2)
    assert set(pr) == set(macs) and all(approx(pr[m][0], lat) and approx(pr[m][1], lon) for m in macs)
    ok("插件端到端: POST gs-loc 请求 → 改写坐标响应")
    a.close(); b.close()

    print(f"\n通过 {pass_n} 项断言")


if __name__ == "__main__":
    main()
