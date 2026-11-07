#!/usr/bin/env python3
"""
Minimal BOOTP/DHCP + TFTP (RFC 2347 OACK) server for MikroTik RouterBOOT netboot.

Usage:
    python3 tools/netboot_server.py <initramfs-kernel.bin> [--bind-ip 192.168.88.253] [--mac-prefix 64:d1:54]
"""

import argparse
import os
import socket
import struct
import sys
import threading
import time

MIKROTIK_OUIS = (
    bytes.fromhex("000c42"),
    bytes.fromhex("085531"),
    bytes.fromhex("18fd74"),
    bytes.fromhex("2cc81b"),
    bytes.fromhex("488f5a"),
    bytes.fromhex("48a98a"),
    bytes.fromhex("4c5e0c"),
    bytes.fromhex("64d154"),
    bytes.fromhex("6c3b6b"),
    bytes.fromhex("744d28"),
    bytes.fromhex("789a18"),
    bytes.fromhex("b869f4"),
    bytes.fromhex("c4ad34"),
    bytes.fromhex("cc2de0"),
    bytes.fromhex("d401c3"),
    bytes.fromhex("d4ca6d"),
    bytes.fromhex("dc2c6e"),
    bytes.fromhex("e48d8c"),
    bytes.fromhex("f41e57"),
)

FIRMWARE_DATA = b""
BIND_IP = None
MAC_PREFIXES = MIKROTIK_OUIS


def get_eth_ip():
    if BIND_IP:
        return BIND_IP
    try:
        ips = socket.gethostbyname_ex(socket.gethostname())[2]
        for ip in ips:
            if ip.startswith("192.168.88.") or ip.startswith("169.254."):
                return ip
    except Exception:
        pass
    return "192.168.88.253"


def parse_dhcp_options(opts_bytes):
    opts = {}
    i = 0
    while i < len(opts_bytes):
        code = opts_bytes[i]
        if code == 0:
            i += 1
            continue
        if code == 255:
            break
        if i + 1 >= len(opts_bytes):
            break
        length = opts_bytes[i + 1]
        val = opts_bytes[i + 2 : i + 2 + length]
        opts[code] = val
        i += 2 + length
    return opts


def build_bootp_reply(req_data, server_ip):
    if len(req_data) < 236:
        return None
    op, htype, hlen, hops, xid, secs, flags = struct.unpack("!BBBBIHH", req_data[:12])
    if op != 1:
        return None
    ciaddr = req_data[12:16]
    chaddr = req_data[28:44]
    mac = chaddr[:6]

    if MAC_PREFIXES and not any(mac.startswith(p) for p in MAC_PREFIXES):
        print(f"[BOOTP/DHCP] Ignoring non-matching MAC {mac.hex(':')}", flush=True)
        return None

    parts = server_ip.split(".")
    offered_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.200"
    subnet_mask = "255.255.0.0" if server_ip.startswith("169.254.") else "255.255.255.0"

    magic = b"\x63\x82\x53\x63"
    req_opts = {}
    if len(req_data) >= 240 and req_data[236:240] == magic:
        req_opts = parse_dhcp_options(req_data[240:])

    msg_type = req_opts.get(53, None)
    if msg_type:
        mtype = msg_type[0]
        print(f"[BOOTP/DHCP] DHCP (type={mtype}) from {mac.hex(':')} -> offering {offered_ip}", flush=True)
    else:
        print(f"[BOOTP/DHCP] Pure BOOTP from {mac.hex(':')} -> offering {offered_ip} (TFTP {server_ip})", flush=True)

    yiaddr = socket.inet_aton(offered_ip)
    siaddr = socket.inet_aton(server_ip)
    giaddr = b"\x00\x00\x00\x00"

    sname = b"OpenWrtBoot".ljust(64, b"\x00")
    bootfile = b"initramfs.bin".ljust(128, b"\x00")

    reply = struct.pack("!BBBBIHH", 2, htype, hlen, 0, xid, 0, flags)
    reply += ciaddr + yiaddr + siaddr + giaddr + chaddr + sname + bootfile + magic

    if msg_type:
        if msg_type[0] == 1:
            reply += b"\x35\x01\x02"
        else:
            reply += b"\x35\x01\x05"

    reply += b"\x36\x04" + siaddr
    reply += b"\x33\x04" + struct.pack("!I", 3600)
    reply += b"\x01\x04" + socket.inet_aton(subnet_mask)
    reply += b"\x03\x04" + siaddr
    ip_bytes = server_ip.encode("ascii")
    reply += bytes([66, len(ip_bytes)]) + ip_bytes
    bf_bytes = b"initramfs.bin"
    reply += bytes([67, len(bf_bytes)]) + bf_bytes
    reply += b"\xff"
    if len(reply) < 300:
        reply = reply.ljust(300, b"\x00")
    return reply


def handle_tftp_transfer(client_addr, rrq_data, bind_ip):
    parts = rrq_data[2:].split(b"\x00")
    filename = parts[0].decode("utf-8", errors="ignore") if len(parts) > 0 else ""
    mode = parts[1].decode("utf-8", errors="ignore") if len(parts) > 1 else "octet"
    options = {}
    opt_tokens = parts[2:]
    i = 0
    while i < len(opt_tokens) - 1:
        if opt_tokens[i]:
            k = opt_tokens[i].decode("utf-8", errors="ignore").lower()
            v = opt_tokens[i + 1].decode("utf-8", errors="ignore")
            options[k] = v
        i += 2

    print(f"[TFTP] RRQ from {client_addr}: file='{filename}' mode='{mode}' options={options}", flush=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind_ip, 0))
    sock.settimeout(2.0)

    blksize = 512
    accepted_opts = {}
    if "blksize" in options:
        try:
            blksize = int(options["blksize"])
            accepted_opts["blksize"] = str(blksize)
        except ValueError:
            blksize = 512
    if "tsize" in options:
        accepted_opts["tsize"] = str(len(FIRMWARE_DATA))
    if "timeout" in options:
        accepted_opts["timeout"] = options["timeout"]

    try:
        if accepted_opts:
            oack = b"\x00\x06"
            for k, v in accepted_opts.items():
                oack += k.encode("ascii") + b"\x00" + v.encode("ascii") + b"\x00"
            print(f"[TFTP] Sending OACK {accepted_opts} to {client_addr}", flush=True)
            for attempt in range(5):
                sock.sendto(oack, client_addr)
                try:
                    ack_data, _ = sock.recvfrom(1024)
                    if len(ack_data) >= 4 and ack_data[:2] == b"\x00\x04":
                        ack_blk = struct.unpack("!H", ack_data[2:4])[0]
                        if ack_blk == 0:
                            break
                except socket.timeout:
                    print(f"[TFTP] Timeout waiting for OACK ACK (attempt {attempt + 1})", flush=True)

        total_len = len(FIRMWARE_DATA)
        offset = 0
        block_num = 1
        while True:
            chunk = FIRMWARE_DATA[offset : offset + blksize]
            pkt = struct.pack("!HH", 3, block_num & 0xFFFF) + chunk
            acked = False
            for attempt in range(8):
                sock.sendto(pkt, client_addr)
                try:
                    ack_data, _ = sock.recvfrom(1024)
                    if len(ack_data) >= 4 and ack_data[:2] == b"\x00\x04":
                        ack_blk = struct.unpack("!H", ack_data[2:4])[0]
                        if ack_blk == (block_num & 0xFFFF):
                            acked = True
                            break
                except socket.timeout:
                    continue
            if not acked:
                print(f"[TFTP] Failed to get ACK for block {block_num} at offset {offset}", flush=True)
                return
            offset += len(chunk)
            if block_num % 500 == 0 or len(chunk) < blksize:
                pct = (offset / total_len) * 100.0
                print(f"[TFTP] Progress: {offset}/{total_len} bytes ({pct:.1f}%)", flush=True)
            if len(chunk) < blksize:
                print("[TFTP] *** FIRMWARE TRANSFER COMPLETE! ***", flush=True)
                break
            block_num += 1
    finally:
        sock.close()


def bootp_loop():
    cur_ip = None
    while True:
        sock = None
        try:
            cur_ip = get_eth_ip()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((cur_ip, 67))
            sock.settimeout(2.0)
            print(f"[BOOTP] Listening on {cur_ip}:67", flush=True)
            while True:
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    new_ip = get_eth_ip()
                    if new_ip != cur_ip:
                        break
                    continue
                reply = build_bootp_reply(data, cur_ip)
                if reply:
                    sock.sendto(reply, ("255.255.255.255", 68))
        except Exception:
            time.sleep(0.3)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass


def tftp_loop():
    cur_ip = None
    while True:
        sock = None
        try:
            cur_ip = get_eth_ip()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((cur_ip, 69))
            sock.settimeout(2.0)
            print(f"[TFTP] Listening on {cur_ip}:69", flush=True)
            while True:
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    new_ip = get_eth_ip()
                    if new_ip != cur_ip:
                        break
                    continue
                if len(data) >= 4 and data[:2] == b"\x00\x01":
                    t = threading.Thread(target=handle_tftp_transfer, args=(addr, data, cur_ip), daemon=True)
                    t.start()
        except Exception:
            time.sleep(0.3)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass


def main():
    global FIRMWARE_DATA, BIND_IP, MAC_PREFIXES
    parser = argparse.ArgumentParser(description="MikroTik RouterBOOT BOOTP/DHCP + TFTP server")
    parser.add_argument("image", help="Path to the OpenWrt initramfs-kernel.bin file")
    parser.add_argument("--bind-ip", help="Local interface IP to bind to (default: auto-detect 192.168.88.x / 169.254.x.x)")
    parser.add_argument("--mac-prefix", help="Optional MAC prefix filter in hex (e.g. 64:d1:54 or any)")
    args = parser.parse_args()

    if not os.path.isfile(args.image):
        print(f"Error: file not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    with open(args.image, "rb") as f:
        FIRMWARE_DATA = f.read()

    BIND_IP = args.bind_ip
    if args.mac_prefix:
        if args.mac_prefix.lower() == "any":
            MAC_PREFIXES = ()
        else:
            cleaned = args.mac_prefix.replace(":", "").replace("-", "").strip()
            MAC_PREFIXES = (bytes.fromhex(cleaned),)

    print(f"[*] Loaded firmware image: {args.image} ({len(FIRMWARE_DATA)} bytes)", flush=True)

    t1 = threading.Thread(target=bootp_loop, daemon=True)
    t2 = threading.Thread(target=tftp_loop, daemon=True)
    t1.start()
    t2.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] Stopping server.")


if __name__ == "__main__":
    main()
