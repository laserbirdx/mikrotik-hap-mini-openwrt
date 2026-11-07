![OpenWrt on MikroTik hAP mini](assets/banner.webp)

# OpenWrt on MikroTik hAP mini (RB931-2nD)

Installation guide for official OpenWrt 19.07.10 on the MikroTik RouterBOARD hAP mini
(`RB931-2nD`, Qualcomm Atheros QCA9533, **32 MiB RAM**, 16 MiB SPI-NOR flash). OpenWrt is
loaded into RAM over Ethernet (`ether1`) via RouterBOOT netboot (BOOTP + TFTP) and then
written to NOR flash using a low-memory `sysupgrade` procedure.

The firmware images are unmodified official OpenWrt 19.07.10 builds (`ar71xx/mikrotik`,
generic 16 MiB NOR profile `rb-nor-flash-16M`). They are attached to the
[Releases](../../releases/latest) page for convenience and are also available from the
[OpenWrt Archive](https://archive.openwrt.org/releases/19.07.10/targets/ar71xx/mikrotik/).

> **Unofficial guide. You do this at your own risk.** Installing OpenWrt overwrites the
> RouterOS `firmware` partition. Back up your RouterOS license key before flashing (see Step 1).

## Why OpenWrt 19.07.10 (`ar71xx/mikrotik`)?

The hAP mini (`RB931-2nD`, MikroTik `smips` architecture) has only **32 MiB of RAM** alongside
its 16 MiB SPI-NOR flash. OpenWrt 19.07 (`ar71xx/mikrotik`, `rb-nor-flash-16M`) is the final
official OpenWrt release series that supports 32 MiB RAM targets comfortably with LuCI and runs
directly on the RB931-2nD's QCA9533 SoC and 16 MiB NOR flash layout.

## Hardware

Measured on a RouterBOARD 931-2nD running RouterOS `v6.37` / RouterBOOT `3.34.3`:

| Component | Details |
|---|---|
| Model | MikroTik RouterBOARD 931-2nD (`hAP mini`) |
| SoC | Qualcomm Atheros QCA9533, MIPS 24Kc @ 650 MHz |
| RAM | 32 MiB DDR |
| Flash | 16 MiB SPI-NOR |
| Ethernet | 3x 10/100 Mbps (`ether1` WAN / Netboot, `ether2`–`ether3` LAN) |
| Wi-Fi | QCA9533 built-in, 2.4 GHz 802.11b/g/n 2x2 |
| Power | Micro-USB 5 V |
| Stock firmware | RouterOS v6 (`smips`), RouterBOOT `3.34.3` |
| OpenWrt target | `ar71xx/mikrotik`, profile `rb-nor-flash-16M` |

### Flash layout (16 MiB SPI-NOR)

Measured from `/proc/mtd` on the RB931-2nD:

| MTD | Offset | Size | Erase size | Name | Notes |
|---|---|---|---|---|---|
| `mtd0` | `0x000000` | 56 KiB | 64 KiB | `routerboot` | Primary RouterBOOT bootloader (read-only) |
| `mtd1` | `0x00e000` | 4 KiB | 64 KiB | `hard_config` | MAC addresses, Wi-Fi calibration data (read-only) |
| `mtd2` | `0x00f000` | 4 KiB | 64 KiB | `bios` | |
| `mtd3` | `0x010000` | 60 KiB | 64 KiB | `routerboot2` | Backup RouterBOOT bootloader |
| `mtd4` | `0x01f000` | 4 KiB | 4 KiB | `soft_config` | RouterBOOT settings (`boot_device`, `boot_protocol`, etc.) |
| `mtd5` | `0x020000` | 16256 KiB | 64 KiB | `firmware` | OpenWrt kernel + squashfs rootfs + JFFS2 overlay |

## Tools

| File | Use |
|---|---|
| [`tools/netboot_server.py`](tools/netboot_server.py) | Standalone Python 3 BOOTP/DHCP (UDP 67) + RFC 2347 TFTP (UDP 69, `blksize`/`tsize` OACK) server for MikroTik RouterBOOT. Filters by MikroTik MAC OUI and serves the `initramfs` image directly from memory. |

## Installation

### 1. Back up the RouterOS license key

Connect to **Port 2 (`ether2`)** or **Port 3 (`ether3`)** while running RouterOS and export the
factory license key over SSH (or WinBox `System -> License -> Export Key`):

```sh
ssh -o HostKeyAlgorithms=+ssh-rsa -o MACs=+hmac-sha1 admin@192.168.88.1 \
  "/system license print; /system routerboard print"
```

Export and download the `.key` file:

```sh
ssh -o HostKeyAlgorithms=+ssh-rsa -o MACs=+hmac-sha1 admin@192.168.88.1 \
  "/system license output"
scp -O -o HostKeyAlgorithms=+ssh-rsa -o MACs=+hmac-sha1 \
  admin@192.168.88.1:*.key ./
```

### 2. Boot the OpenWrt `initramfs` image via `ether1` (Port 1)

RouterBOOT only performs BOOTP/TFTP netboot on **Port 1 (`ether1` / Internet)**.

1. Set your PC's Ethernet adapter to a static IP:
   - IP address: `192.168.88.253`
   - Subnet mask: `255.255.255.0`
2. Start the bundled BOOTP + TFTP server in an elevated terminal (root / Administrator is required for UDP ports 67 and 69):

   ```sh
   python3 tools/netboot_server.py \
     openwrt-19.07.10-ar71xx-mikrotik-rb-nor-flash-16M-initramfs-kernel.bin \
     --bind-ip 192.168.88.253
   ```

3. Trigger RouterBOOT network boot using **either** method below:
   - **Option A — Over SSH from RouterOS (before moving the cable to Port 1):**

     ```sh
     ssh -o HostKeyAlgorithms=+ssh-rsa -o MACs=+hmac-sha1 admin@192.168.88.1 \
       "/system routerboard settings set boot-protocol=bootp boot-device=try-ethernet-once-then-nand; /system reboot"
     ```

     Immediately move the Ethernet cable to **Port 1 (`ether1`)** as it reboots.
   - **Option B — Using the Reset button (cable plugged into Port 1):**
     Unplug power, hold the **Reset** button, plug power back in, and keep holding for ~15 seconds until the LED stops blinking and turns solid, then release.

4. `netboot_server.py` answers the BOOTP request on `ether1` and transfers the 4.08 MB `initramfs` image over TFTP (`*** FIRMWARE TRANSFER COMPLETE! ***`).
5. Wait ~60 seconds for OpenWrt to finish booting in RAM.

### 3. Low-RAM `sysupgrade` to NOR flash (32 MiB RAM procedure)

Once OpenWrt boots from `initramfs`, **Port 2 (`ether2`)** and **Port 3 (`ether3`)** are bridged into LAN at `192.168.1.1/24` (Port 1 is WAN).

1. Move your Ethernet cable from Port 1 to **Port 2 or Port 3** and set your PC to `192.168.1.2/24` (or DHCP).
2. Because the RB931-2nD has only **32 MiB of RAM** (~26.9 MiB usable in Linux, part of which is already occupied by the `initramfs` rootfs), stop background services and drop kernel caches **before** uploading and flashing the 4.3 MB `sysupgrade` image so the device does not run out of memory:

   ```sh
   # 1. Free RAM on the router (Dropbear in 19.07 uses ssh-rsa)
   ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@192.168.1.1 "
     for s in uhttpd dnsmasq odhcpd rpcd log ntpd; do
       /etc/init.d/\$s stop 2>/dev/null
     done
     sync
     echo 3 > /proc/sys/vm/drop_caches
     free
   "

   # 2. Upload the sysupgrade image to /tmp (tmpfs)
   scp -O -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa \
     openwrt-19.07.10-ar71xx-mikrotik-rb-nor-flash-16M-squashfs-sysupgrade.bin \
     root@192.168.1.1:/tmp/sysupgrade.bin

   # 3. Verify and flash to mtd5 (firmware)
   ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@192.168.1.1 "
     sysupgrade -T /tmp/sysupgrade.bin && sysupgrade -v -n /tmp/sysupgrade.bin
   "
   ```

3. Flashing takes ~90 seconds, followed by a reboot and another ~60 seconds on first boot while OpenWrt formats the `jffs2` overlay (`10.8 MiB` free space on `/overlay`).

## Default configuration after install

| Setting | Value |
|---|---|
| LAN | Port 2 & Port 3 (`eth0.1`), `192.168.1.1/24`, DHCP server enabled |
| WAN | Port 1 (`eth0.2` / Internet), DHCP client |
| Login | `root`, no password (set one first) |
| Web UI | LuCI (`http://192.168.1.1`) |
| Wi-Fi | Disabled by default |
| Free overlay | ~10.8 MiB (`jffs2` on 16 MiB NOR flash) |

## Reverting to RouterOS

RouterBOOT (`mtd0` / `mtd3`) and `hard_config` (`mtd1`) are untouched. To revert to stock RouterOS, connect to Port 1 (`ether1`), hold Reset on power-up to enter Etherboot mode, and flash a RouterOS `smips` `.npk` package with MikroTik **Netinstall**.

## References

- [OpenWrt Techdata: MikroTik RB931-2nD (hAP mini)](https://openwrt.org/toh/hwdata/mikrotik/mikrotik_rb931-2nd_hap_mini)
- [OpenWrt Wiki: MikroTik Common Procedures](https://openwrt.org/toh/mikrotik/common)

## License

This guide is licensed under [CC BY 4.0](LICENSE). The scripts in [`tools/`](tools) are
licensed under the [MIT License](LICENSE-tools).

The firmware images on the Releases page are unmodified official OpenWrt 19.07.10 builds,
licensed under GPL-2.0. Source code:
[openwrt/openwrt @ v19.07.10](https://github.com/openwrt/openwrt/tree/v19.07.10).
