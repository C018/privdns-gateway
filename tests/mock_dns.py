#!/usr/bin/env python3
"""极简 UDP DNS mock(仅供 tests/dns-policy-test.sh 当"上游"用):
A 查询一律回一个固定 IP, 其它类型回 NOERROR 空应答。不依赖任何第三方库。
用法: mock_dns.py <listen_port> <answer_ip>
"""
import socket
import struct
import sys


def build_response(query, answer_ip):
    if len(query) < 12:
        return None
    qid = query[:2]
    # 跳过 header(12B)解析问题段: QNAME(labels..0) + QTYPE(2) + QCLASS(2)
    i = 12
    while i < len(query) and query[i] != 0:
        i += query[i] + 1
    i += 1
    if i + 4 > len(query):
        return None
    qtype = struct.unpack(">H", query[i:i + 2])[0]
    question = query[12:i + 4]
    flags = b"\x81\x80"                       # QR=1, RD=1, RA=1, RCODE=0(NOERROR)
    if qtype == 1:                            # A
        ancount = 1
        answer = (b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4)
                  + socket.inet_aton(answer_ip))
    else:                                     # AAAA/HTTPS/其它: 空应答
        ancount = 0
        answer = b""
    header = qid + flags + struct.pack(">HHHH", 1, ancount, 0, 0)
    return header + question + answer


def main():
    port = int(sys.argv[1])
    answer_ip = sys.argv[2]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))
    while True:
        try:
            data, addr = s.recvfrom(2048)
        except OSError:
            break
        resp = build_response(data, answer_ip)
        if resp:
            s.sendto(resp, addr)


if __name__ == "__main__":
    main()
